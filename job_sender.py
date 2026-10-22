"""One guarded native job-terminal command through the macro's owned proxy."""
import json
from pathlib import Path
import time

from focus import process_identity
from job_types import OwnedJobProxy
from protocol import proxy_commands
from protocol.job_packets import build_request
from watchdog import StopMacro


class JobPacketUnavailable(RuntimeError):
    """No job command was submitted; UI selection remains an alternative."""


class JobPacketUncertain(StopMacro):
    """A submission may have happened; stop without retrying an acceptance."""


def submit(instance: OwnedJobProxy, action: int, target: int = 0) -> dict[str, object]:
    report: dict[str, object] = dict(action=action, target=target, proxy_pid=getattr(instance, 'proxy_pid', None))
    try:
        if (not instance or not isinstance(instance.proxy_pid, int) or not instance.valid()
                or process_identity(instance.proxy_pid) != instance.proxy_born):
            raise RuntimeError('Owned job proxy is missing or its PID was reused')
        process = proxy_commands.Process(instance.proxy_pid)
        try:
            root = process.u32(0x44e620)
            sender = process.u32(root + 0x22e)
            player = process.u32(sender + 0x10) & 0xffffff
        finally:
            process.k.CloseHandle(process.handle)
        frame = build_request(player, target, action)
        proxy_commands.submit(instance.proxy_pid, report=report, frame=frame)
    except Exception as error:
        report['error'] = str(error)
        if report.get('thread_id') and report.get('native_handler_attempted') is not False:
            raise JobPacketUncertain(f'Job command {action} for {target} uncertain; no retry: {error}') from error
        raise JobPacketUnavailable(str(error)) from error
    finally:
        folder = Path(__file__).resolve().parent / 'diagnostics'
        folder.mkdir(exist_ok=True)
        with (folder / 'job_submissions.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(report, time=time.time())) + '\n')
    if report.get('state') == 'not_sent' and report.get('native_handler_attempted') is False:
        raise JobPacketUnavailable('Native preflight rejected job command before submission')
    if report.get('state') != 'handler_returned':
        raise JobPacketUncertain(f'Job command {action} for {target} has uncertain native result; no retry')
    return report
