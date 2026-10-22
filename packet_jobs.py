"""Job-ID pickup with reply correlation; UI pickup is retained by PlutoRunner."""
from collections.abc import Callable
import time
from typing import TypeVar

from job_policy import JobPolicy
from job_sender import submit, JobPacketUnavailable, JobPacketUncertain
from job_types import (CommandStamp, JobContext, JobDescriptionError, JobMonitor, JobSender, JobSnapshot,
                       ObservedJobDescription, PacketJobController)
from player import PLAYER
from util import log_event

T = TypeVar('T')


class PacketJobPicker:
    def __init__(self, controller: PacketJobController, policy: JobPolicy, target: int, *,
                 sender: JobSender = submit, on_accept: Callable[[int], None] | None = None,
                 before_accept: Callable[[], None] | None = None) -> None:
        self.controller, self.policy, self.target, self.sender = controller, policy, target, sender
        self.on_accept = on_accept or (lambda count: PLAYER.set_pos(f'earthJOB_jc_{count}'))
        self.before_accept = before_accept or (lambda: None)
        self.monitor: JobMonitor | None = None
        self.context: JobContext | None = None
        self.deadline: float = 0

    def state(self) -> JobSnapshot:
        if self.monitor is None:
            raise JobPacketUnavailable('Owned job capture is unavailable')
        value = self.monitor.job_snapshot()
        if value is None:
            raise JobPacketUnavailable('Owned job capture is unavailable')
        if self.context is not None:
            context = value['owner'], value['remote'], value['generation']
            if context != self.context or value['closed']:
                raise JobPacketUnavailable('Job terminal/session changed while picking jobs')
        if value['error']:
            raise JobPacketUnavailable('Cannot decode job reply: ' + value['error'])
        return value

    def command(self, action: int, target: int = 0) -> CommandStamp:
        if self.controller.instance is None:
            raise JobPacketUnavailable('No owned game instance for job submission')
        started = self.controller.polling_time()
        for attempt in range(3):
            self.controller._check_remote_logoff()
            self.controller._check_pause()
            issued = time.time()
            try:
                self.sender(self.controller.instance, action, target)
                return issued, started
            except JobPacketUnavailable as error:
                # Re-read the live connection after a no-send preflight race.
                # Acceptance and uncertain submissions are never retried here.
                if action == 8 or attempt == 2:
                    raise
                log_event(f'Packet job command {action} not submitted: {error}; retrying read request')
                self.controller._yield(.2)
        raise AssertionError('Job command exhausted retries without returning or raising')

    def wait(self, predicate: Callable[[JobSnapshot, float], T | None],
             command: CommandStamp, timeout: float) -> T | None:
        issued, started = command
        deadline = min(self.deadline, started + timeout)
        while self.controller.polling_time() < deadline:
            value = self.state()
            result = predicate(value, issued)
            if result:
                return result
            self.controller._yield(.05)
        return None

    def pick(self) -> int:
        self.controller.start_packet_capture()
        self.monitor = self.controller.job_monitor
        if self.monitor is None:
            raise JobPacketUnavailable('No owned proxy capture; use job_pickup=ui')
        self.deadline = self.controller.polling_time() + 450
        # Establish port ownership before the terminal can send its first rows.
        ready_deadline = self.controller.polling_time() + 3
        while self.monitor.job_snapshot() is None and self.controller.polling_time() < ready_deadline:
            self.controller._yield(.1)
        self.state()
        count = self.controller.count_mission()
        log_event(f'Packet job pickup started: count={count}, target={self.target}, '
                  f'destinations={sorted(self.policy.destinations)}, levels={self.policy.minimum}..{self.policy.maximum or "any"}')
        rounds = 0
        finish_reason = 'time_budget_exhausted'
        while count < self.target and self.controller.polling_time() < self.deadline:
            rounds += 1
            self.context = None
            previous = self.state()
            opened = self.command(6)
            catalog = self.wait(lambda value, since: value if (
                value['owner'] == previous['owner']
                and value['generation'] > previous['generation'] and not value['closed']
                and value['list_seen'] > 0 and value['list_seen'] >= value['opened_at']) else None, opened, 20)
            if catalog is None:
                # No accept has been attempted in this round.
                snapshot = self.state()
                exhausted = self.controller.polling_time() >= self.deadline
                reason = 'pickup time budget exhausted' if exhausted else 'no fresh catalog within 20 seconds'
                log_event(f'Packet catalog wait ended: reason={reason}; generation={snapshot["generation"]}, '
                          f'opened_at={snapshot["opened_at"]}, requested_at={opened[0]}, '
                          f'last_list_at={snapshot["list_seen"]}, rows={len(snapshot["jobs"])}, '
                          f'closed={snapshot["closed"]}, owner={snapshot["owner"]}, remote={snapshot["remote"]}')
                self.command(9)
                if exhausted:
                    break
                log_event(f'Packet job round {rounds}: no acceptance attempted; retrying catalog within pickup time budget')
                self.controller._yield(min(2, max(0, self.deadline - self.controller.polling_time())))
                continue
            self.context = catalog['owner'], catalog['remote'], catalog['generation']
            candidates = [job for job in catalog['jobs'].values() if self.policy.candidate(job)]
            candidates.sort(key=lambda job: (job['level'], job['id']), reverse='uranus' not in self.policy.destinations)
            log_event(f'Packet job catalog: rows={len(catalog["jobs"])}, candidates={len(candidates)}, round={rounds}')
            confirmed = 0
            stop_reason = ''
            inspected: set[int] = set()
            stream_deadline = min(self.deadline, opened[1] + 60)
            while count < self.target and self.controller.polling_time() < stream_deadline:
                current = self.state()
                candidates = [job for job in current['jobs'].values()
                              if job['id'] not in inspected and self.policy.candidate(job)]
                candidates.sort(key=lambda job: (job['level'], job['id']), reverse='uranus' not in self.policy.destinations)
                if not candidates:
                    if time.time() - current['list_seen'] >= 1.5:
                        break
                    self.controller._yield(.05)
                    continue
                job = candidates[0]
                inspected.add(job['id'])
                requested = self.command(7, job['id'])
                def fresh_description(value: JobSnapshot, since: float) -> tuple[ObservedJobDescription | None, JobDescriptionError | None] | None:
                    error = value['description_errors'].get(job['id']) or value['description_errors'].get(0)
                    if error is not None and error['seen'] >= since:
                        return None, error
                    description = value['descriptions'].get(job['id'])
                    return (description, None) if description is not None and description['seen'] >= since else None
                description_reply = self.wait(fresh_description, requested, 5)
                if description_reply is None:
                    log_event(f'Packet job {job["id"]}: description timeout; skipped without accepting')
                    continue
                description, description_error = description_reply
                if description_error is not None:
                    log_event(f'Packet job {job["id"]}: invalid description skipped without accepting; '
                              f'reason={description_error["reason"]}; availability={description_error["availability"]}; '
                              f'body_prefix={description_error["body_prefix"]}')
                    continue
                if description is None:
                    continue
                if not self.policy.matches(description):
                    continue
                self.before_accept()
                if job['id'] not in self.state()['jobs']:
                    continue
                if self.deadline - self.controller.polling_time() < 5:
                    break  # Reserve a complete reply budget before an acceptance.
                accepted = self.command(8, job['id'])
                try:
                    reply = self.wait(lambda value, since:
                        'accepted' if value['accepted'].get(job['id'], 0) >= since else
                        'limit' if value['limit_at'] >= since else
                        value['rejection'] if value['rejected_at'] >= since else None, accepted, 5)
                except JobPacketUnavailable as error:
                    raise JobPacketUncertain(f'Job {job["id"]} acceptance unconfirmed after capture/session loss; stopped without retry') from error
                if reply is None:
                    raise JobPacketUncertain(f'Job {job["id"]} acceptance reply missing; stopped without retry or UI fallback')
                if reply != 'accepted':
                    stop_reason = reply
                    finish_reason = f'server: {reply}'
                    log_event(f'Packet job pickup stopped by server: {reply}; count={count}')
                    break
                confirmed += 1
                count += 1
                self.on_accept(count)
                log_event(f'Packet job accepted: id={job["id"]}, title={job["title"]}, '
                          f'description={description["description"]}, count={count}/{self.target}')
            self.context = None
            self.command(9)
            recounted = self.controller.count_mission()
            if recounted < count:
                log_event(f'Packet job recount below confirmed count: screen/packet={recounted}, confirmed={count}; preserving confirmed count')
            count = max(count, recounted)
            log_event(f'Packet job round {rounds}: confirmed={confirmed}, count={count}/{self.target}')
            if stop_reason:
                break
            if count < self.target:
                self.controller._yield(min(2, max(0, self.deadline - self.controller.polling_time())))
        if count >= self.target:
            finish_reason = 'target_met'
        log_event(f'Packet job pickup finished: count={count}, target={self.target}, '
                  f'target_met={count >= self.target}, rounds={rounds}, reason={finish_reason}')
        return count
