"""Decode job-terminal captures and prepare client frames; never sends packets."""
import argparse
import json
from pathlib import Path
import re
import struct

from protocol.decode_capture import decode_datagram
from job_types import JobDescription, JobListing, JobRequest


class Reader:
    def __init__(self, body: bytes) -> None:
        self.body, self.offset = body, 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.body):
            raise ValueError('Truncated job packet')
        value, self.offset = self.body[self.offset:end], end
        return value

    def u32(self) -> int:
        return struct.unpack('<I', self.take(4))[0]

    def string(self) -> str:
        end = self.body.index(0, self.offset)
        if end - self.offset > 8192:
            raise ValueError('Oversized job string')
        value = self.body[self.offset:end].decode('cp1252')
        self.offset = end + 1
        return value

    def finish(self) -> None:
        if self.offset != len(self.body):
            raise ValueError('Unexpected trailing job data')


def decode_list(body: bytes) -> list[JobListing]:
    reader = Reader(body)
    count = reader.u32()
    if count > 512:
        raise ValueError('Unsupported job-list count')
    jobs: list[JobListing] = []
    for _ in range(count):
        job: JobListing = dict(id=reader.u32(), type=reader.u32(), unknown=reader.u32(),
            level=reader.u32(), title=reader.string(), sponsor=reader.string(), reward=reader.string())
        if not job['id'] or job['type'] not in (0, 1, 2):
            raise ValueError('Unsupported job ID/type')
        jobs.append(job)
    reader.finish()
    return jobs


def decode_description(body: bytes) -> JobDescription:
    reader = Reader(body)
    identity = reader.u32()
    available = reader.take(1)[0]
    if available not in (0, 1):
        raise ValueError('Invalid job availability')
    result: JobDescription = dict(id=identity, available=bool(available), title=reader.string(), description=reader.string())
    reader.finish()
    return result


def decode_request(body: bytes) -> JobRequest:
    if len(body) != 9:
        raise ValueError('Invalid starbase request size')
    player, target, action = struct.unpack('<IIB', body)
    return dict(player=player, target=target, action=action)


def build_request(player: int, target: int, action: int) -> bytes:
    """Build a client-side frame, without UDP sequence/session or transmission."""
    if action not in (6, 7, 8, 9):
        raise ValueError('Only observed job-terminal actions are supported')
    if not 0 < player <= 0xffffffff or not 0 <= target <= 0xffffffff:
        raise ValueError('Invalid player/job ID')
    if action in (7, 8) and target == 0:
        raise ValueError('Description/accept requires a current job ID')
    return struct.pack('<HHIIB', 13, 0x004e, player, target, action)


def low_job_matches(description: JobDescription) -> bool:
    return (description.get('available') is True
            and description.get('title') in ('Launch a Satellite', 'Dispose Galactic Garbage')
            and re.search(r'\b(Puck|Ariel)\b.*\bUranus\b', description.get('description', ''), re.I) is not None)


def inspect_capture(path):
    jobs, descriptions, pending, pending_description = {}, {}, {}, {}
    accepts, requests, deletes, errors, seen = [], [], [], [], set()
    chunks = 0
    for line in Path(path).open(encoding='utf-8'):
        if not line.endswith('\n'):
            break  # The passive recorder may still be appending the final row.
        row = json.loads(line)
        identity = (row['source'], row['destination'], row['payload_hex'])
        if identity in seen:
            continue
        seen.add(identity)
        decoded = decode_datagram(bytes.fromhex(row['payload_hex']))
        for frame in decoded.get('frames', [decoded]):
            opcode = frame['opcode']
            body = bytes.fromhex(frame.get('body_hex', ''))
            if row['outgoing']:
                body = body[8:]  # UDP owner/sequence wrapper, absent from client frame.
            try:
                if row['outgoing'] and opcode == '0x004e':
                    command = dict(decode_request(body), time=row['time'])
                    requests.append(command)
                    if command['action'] == 8:
                        pending[command['target']] = row['time']
                    elif command['action'] == 7:
                        pending_description[command['target']] = row['time']
                elif not row['outgoing'] and opcode == '0x0093':
                    chunks += 1
                    for job in decode_list(body):
                        jobs[job['id']] = job
                elif not row['outgoing'] and opcode == '0x0094':
                    description = decode_description(body)
                    requested = pending_description.pop(description['id'], None)
                    if requested is not None:
                        description['reply_seconds'] = row['time'] - requested
                    descriptions[description['id']] = description
                elif not row['outgoing'] and opcode in ('0x0095', '0x0096'):
                    if len(body) != 4:
                        raise ValueError('Unexpected job reply size')
                    job_id = struct.unpack('<I', body)[0]
                    if opcode == '0x0095':
                        deletes.append(job_id)
                    elif job_id in pending:
                        accepts.append(dict(id=job_id, reply_seconds=row['time'] - pending.pop(job_id)))
            except ValueError as error:
                errors.append(dict(time=row['time'], opcode=opcode, error=str(error)))
    return dict(list_chunks=chunks, listed_jobs=len(jobs), jobs=list(jobs.values()),
                descriptions=list(descriptions.values()), requests=requests,
                acceptance_replies=accepts, removed_ids=sorted(set(deletes)), errors=errors,
                low_job_matches=[item['id'] for item in descriptions.values() if low_job_matches(item)],
                description_candidates=[job['id'] for job in jobs.values() if job['type'] == 2
                    and job['title'] in ('Launch a Satellite', 'Dispose Galactic Garbage')],
                mode='read-only; client frames can be built but no transmission is implemented')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inspect_capture(args.capture)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ('jobs', 'descriptions', 'requests')}, indent=2))
