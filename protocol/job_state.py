"""Per-proxy job-terminal replies observed by the existing passive receiver."""
from collections import deque
import copy
import struct

from protocol.decode_capture import decode_datagram
from protocol.job_packets import decode_list, decode_description, decode_request
from job_types import JobDescriptionError, JobListing, JobStateSnapshot, ObservedJobDescription


class JobState:
    def __init__(self) -> None:
        self.generation = 0
        self.opened_at: float = 0
        self.list_seen: float = 0
        self.closed = True
        self.jobs: dict[int, JobListing] = {}
        self.descriptions: dict[int, ObservedJobDescription] = {}
        self.description_errors: dict[int, JobDescriptionError] = {}
        self.accepted: dict[int, float] = {}
        self.removed: set[int] = set()
        self.limit_at: float = 0
        self.rejected_at: float = 0
        self.rejection = self.error = ''
        self.seen: deque[tuple[bool, bytes]] = deque(maxlen=256)

    def observe(self, payload: bytes, outgoing: bool, now: float) -> None:
        identity = (outgoing, payload)
        if identity in self.seen:
            return
        self.seen.append(identity)
        packet = decode_datagram(payload)
        for frame in packet.get('frames', [packet]):
            opcode = frame['opcode']
            body = bytes.fromhex(frame.get('body_hex', ''))
            if outgoing:
                body = body[8:]
            try:
                if outgoing and opcode == '0x004e':
                    request = decode_request(body)
                    if request['action'] == 6:
                        self.generation += 1
                        self.opened_at, self.list_seen, self.closed = now, 0, False
                        self.jobs, self.descriptions, self.accepted, self.removed = {}, {}, {}, set()
                        self.description_errors = {}
                        self.limit_at = self.rejected_at = 0
                        self.rejection = self.error = ''
                    elif request['action'] == 9:
                        self.closed = True
                elif not outgoing and opcode == '0x0093':
                    for job in decode_list(body):
                        if job['id'] not in self.removed:
                            self.jobs[job['id']] = job
                    self.list_seen = now
                elif not outgoing and opcode == '0x0094':
                    value = decode_description(body)
                    observed: ObservedJobDescription = {**value, 'seen': now}
                    self.descriptions[value['id']] = observed
                    self.description_errors.pop(value['id'], None)
                elif not outgoing and opcode in ('0x0095', '0x0096'):
                    if len(body) != 4:
                        raise ValueError('Unexpected job reply size')
                    job_id = struct.unpack('<I', body)[0]
                    if opcode == '0x0096':
                        self.accepted[job_id] = now
                    else:
                        self.removed.add(job_id)
                        self.jobs.pop(job_id, None)
                elif not outgoing and opcode == '0x00a5':
                    message = frame.get('chat', {}).get('message', '')
                    lower = message.casefold()
                    if 'job limit reached' in lower:
                        self.limit_at = now
                    if any(text in lower for text in ('job unavailable', 'job expired', 'insufficient space in cargo')):
                        self.rejected_at, self.rejection = now, message
            except ValueError as error:
                if not outgoing and opcode == '0x0094':
                    job_id = struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0
                    self.descriptions.pop(job_id, None)
                    self.description_errors[job_id] = dict(seen=now, reason=str(error),
                        availability=body[4] if len(body) > 4 else None, body_prefix=body[:64].hex())
                else:
                    self.error = f'{opcode}: {error}'

    def snapshot(self) -> JobStateSnapshot:
        return copy.deepcopy(JobStateSnapshot(generation=self.generation, opened_at=self.opened_at,
            list_seen=self.list_seen, closed=self.closed, jobs=self.jobs, descriptions=self.descriptions,
            description_errors=self.description_errors,
            accepted=self.accepted, removed=self.removed, limit_at=self.limit_at,
            rejected_at=self.rejected_at, rejection=self.rejection, error=self.error))
