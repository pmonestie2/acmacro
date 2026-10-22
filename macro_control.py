"""Pause/resume macros through files shared by desktop and SSH sessions."""
import argparse
from contextlib import contextmanager
import json
import msvcrt
import os
from pathlib import Path
import tempfile
import time

ROOT = Path(__file__).resolve().parent / 'diagnostics' / 'macro_control'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=path.name, suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream)
    try:
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.02)
    finally:
        temporary.unlink(missing_ok=True)


def read_request(root=ROOT):
    try:
        value = json.loads((root / 'request.json').read_text(encoding='utf-8'))
        if not isinstance(value.get('all'), bool) or not isinstance(value.get('toons'), dict):
            raise ValueError('Invalid pause control file')
        if any(not isinstance(key, str) or not isinstance(item, bool)
               for key, item in value['toons'].items()):
            raise ValueError('Invalid per-character pause state')
        return value
    except FileNotFoundError:
        return {'all': False, 'toons': {}}


@contextmanager
def request_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'request.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def set_request(paused, toons=(), root=ROOT):
    with request_lock(root):
        value = read_request(root)
        if toons:
            value['toons'].update({toon.casefold(): paused for toon in toons})
        else:
            value = {'all': paused, 'toons': {}}
        value['updated'] = time.time()
        atomic_json(root / 'request.json', value)
    return value


class PauseControl:
    def __init__(self, toon, root=ROOT):
        from focus import process_identity
        self.toon, self.root = toon, root
        self.pid, self.born = os.getpid(), process_identity(os.getpid())
        self.path = root / f'status_{self.pid}.json'
        self.acknowledge(False)

    def requested(self):
        try:
            value = read_request(self.root)
        except (OSError, ValueError):
            # A corrupted signal cannot resume a macro that was already paused.
            return getattr(self, 'paused', False)
        return value['toons'].get(self.toon.casefold(), value['all'])

    def acknowledge(self, paused):
        self.paused = paused
        atomic_json(self.path, {'toon': self.toon, 'pid': self.pid, 'born': self.born,
                               'paused': paused, 'updated': time.time()})

    def close(self):
        self.path.unlink(missing_ok=True)


def active_statuses(root=ROOT):
    from focus import process_identity
    result = []
    for path in root.glob('status_*.json'):
        try:
            item = json.loads(path.read_text(encoding='utf-8'))
            if process_identity(item['pid']) == item['born']:
                result.append(item)
        except (OSError, ValueError, KeyError):
            continue
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pause', 'resume', 'status'))
    parser.add_argument('toons', nargs='*', help='Character names; omit to control all macros')
    parser.add_argument('--all', action='store_true', help='Control all macros (default)')
    parser.add_argument('--wait', type=float, default=30, help='Seconds to wait for acknowledgments (default 30)')
    args = parser.parse_args(argv)
    if args.all and args.toons:
        parser.error('Use character names or --all, not both')
    if args.wait < 0:
        parser.error('--wait must be nonnegative')
    selected = {toon.casefold() for toon in args.toons}
    if args.action != 'status':
        set_request(args.action == 'pause', args.toons, ROOT)
        print(f"{args.action.title()} requested for {', '.join(args.toons) or 'all macros'}.", flush=True)
    deadline = time.monotonic() + args.wait
    while True:
        active = [item for item in active_statuses(ROOT) if not selected or item['toon'].casefold() in selected]
        desired = read_request(ROOT)
        pending = [item for item in active if item['paused'] != desired['toons'].get(item['toon'].casefold(), desired['all'])]
        missing = selected - {item['toon'].casefold() for item in active}
        if args.action == 'status' or not pending or time.monotonic() >= deadline:
            break
        time.sleep(.1)
    for item in active:
        target = desired['toons'].get(item['toon'].casefold(), desired['all'])
        print(f"{item['toon']}: {'PAUSED' if item['paused'] else 'RUNNING'}"
              f"{' (signal pending)' if item['paused'] != target else ''}; pid={item['pid']}")
    if not active or missing:
        print('No registered macro for ' + (', '.join(sorted(missing)) if missing else 'this selection') +
              '. Restart existing macros once to load pause support. The signal remains saved.')
    return 1 if pending or missing or not active else 0


if __name__ == '__main__':
    raise SystemExit(main())
