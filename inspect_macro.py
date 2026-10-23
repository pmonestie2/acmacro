"""Run in the interactive Windows session to capture or restart one macro.

Invoked by the agent's on-demand scheduled task, not a focus/communication server.
Captures use the same input ownership queue as the macros.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

from focus import SharedFocus
from gui import find_window_exact, focus_window, client_geometry
import pyautogui

ROOT = Path(__file__).resolve().parent
REQUEST = ROOT / 'diagnostics' / 'inspect_request.json'
RESULT = ROOT / 'diagnostics' / 'inspect_result.json'
RUN_ID = str(time.time_ns())
EVIDENCE = ROOT / 'navigation' / 'pw_hull_upgrade' / 'evidence'


def record_view(window):
    """Preserve the supervised PW view while shared focus is still owned."""
    if not window or 'macboypw' not in __import__('win32gui').GetWindowText(window).lower():
        return
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    _, size, origin = client_geometry(window)
    pyautogui.screenshot(region=(*origin, *size)).save(EVIDENCE / f'{RUN_ID}.png')


def record_action(phase, request, **extra):
    if request.get('toon') != 'macboypw' and request.get('title') != 'ENB_macboypw':
        return
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with (EVIDENCE.parent / 'actions.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'id':RUN_ID, 'time':time.time(), 'phase':phase,
                                 'request':request, **extra}) + '\n')


def await_gate(request):
    """Supervised arrival only; passive travel waits never retain focus."""
    import math
    import win32process
    from explore_sector import live_world
    from focus import process_identity
    from objects import GATE_OBJ, STATION_OBJ
    session = request['session']
    deadline = time.monotonic() + 300
    arrival_since = None
    focus = SharedFocus('inspection', 'Inspection', claim_game=False)
    try:
        while time.monotonic() < deadline:
            if process_identity(request['proxy_pid']) != request['proxy_born']:
                raise RuntimeError('Supervised proxy identity changed')
            world = live_world(session, request['sector'])
            target = next((o for o in world['catalog'] if o['id'] == request['target']), None)
            player = world['recent'].get('player')
            station = request.get('station', False)
            valid_name = target and (target.get('name') == request.get('station_name') if station else
                                    (target.get('name') or '').startswith(('Sector Gate', 'System Gate', 'Gate to', 'Accelerator')))
            if not valid_name or not player:
                raise RuntimeError('Expected gate/player missing from current sector')
            distance = math.dist(player['raw_xyz'], target['raw_xyz'])
            # Large station models can dock >20 km from their origin. The
            # active UI control remains the final authority for range.
            if distance < (30000 if station else 15000):
                arrival_since = arrival_since or time.monotonic()
                if time.monotonic() - arrival_since >= 6:
                    focus.acquire()
                    try:
                        window = find_window_exact(request['title'])
                        if not window or win32process.GetWindowThreadProcessId(window)[1] != request['pid']:
                            raise RuntimeError('Supervised window identity changed')
                        if not focus_window(window):
                            raise RuntimeError('Cannot focus supervised window')
                        pyautogui.press('w')
                        time.sleep(.4)
                        _, size, origin = client_geometry(window)
                        pyautogui.moveTo(origin[0] + size[0] // 2, origin[1] + size[1] // 4)
                        sc = pyautogui.screenshot(region=(*origin, *size))
                        sc.save(ROOT / 'diagnostics' / 'supervised_gate_arrival.png')
                        sc.save(EVIDENCE / f'{RUN_ID}.png')
                        control = STATION_OBJ if station else GATE_OBJ
                        if control.test(screenshot=sc.resize((1280, 768)), objvar=25, min_match_ratio=.75):
                            pyautogui.click(origin[0] + int(1237 * size[0]/1280),
                                            origin[1] + int(515 * size[1]/768))
                            RESULT.write_text(json.dumps({'state':'gate_clicked', 'gate':target['name'], 'time':time.time()}))
                            return
                    finally:
                        focus.release()
            time.sleep(3)
        raise RuntimeError('Supervised gate arrival timed out')
    finally:
        focus.close()


def main():
    request = json.loads(REQUEST.read_text())
    if request['action'] == 'manufacture_callipus':
        from manufacture_batch import run_je
        RESULT.write_text(json.dumps(run_je(parallel=int(request.get('parallel', 1)))))
        return
    if request['action'] == 'capture_named_packets':
        import win32process
        from arrange_windows import client_path
        from focus import process_identity
        from game_instance import parent_process_id
        window = find_window_exact(request['title'])
        if not window:
            raise RuntimeError('Requested game window is missing')
        client_pid = win32process.GetWindowThreadProcessId(window)[1]
        proxy_pid = parent_process_id(client_pid)
        if (client_path(client_pid).name.casefold() != 'client.exe' or proxy_pid is None or
                client_path(proxy_pid).name.casefold() != 'net7proxy.exe'):
            raise RuntimeError('Requested client/proxy identity is invalid')
        path = ROOT / 'diagnostics' / f"je_manufacture_{RUN_ID}.jsonl"
        with path.with_suffix('.log').open('w') as output:
            process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'protocol/capture_jobs.py'),
                '--proxy', str(proxy_pid), '--seconds', '300', '--output', str(path), '--all-packets'],
                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        result = {'state': 'capture_started', 'pid': process.pid, 'client_pid': client_pid,
                  'client_born': process_identity(client_pid), 'proxy_pid': proxy_pid,
                  'proxy_born': process_identity(proxy_pid), 'capture': str(path), 'started': time.time()}
        (ROOT / 'diagnostics/je_manufacture_current.json').write_text(json.dumps(result, indent=2))
        RESULT.write_text(json.dumps(result))
        return
    if request['action'] == 'buy_trade_samples':
        from diagnostics.trade_samples import buy_samples
        RESULT.write_text(json.dumps(buy_samples()))
        return
    if request['action'] == 'stop_enb_drag':
        import win32api
        import win32con
        import win32event
        import win32gui
        import win32process
        from arrange_windows import client_path
        window = win32gui.FindWindow('AcMacro.EnbAltDrag.v1', None)
        if not window:
            RESULT.write_text(json.dumps({'state': 'not_running', 'program': 'enb_drag.exe'}))
            return
        pid = win32process.GetWindowThreadProcessId(window)[1]
        if pid != request['pid'] or client_path(pid) != (ROOT / 'enb_drag.exe').resolve():
            raise RuntimeError('Alt-drag helper identity changed; not stopping it')
        handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
        try:
            win32gui.PostMessage(window, win32con.WM_CLOSE, 0, 0)
            if win32event.WaitForSingleObject(handle, 5000) != win32event.WAIT_OBJECT_0:
                raise RuntimeError('Alt-drag helper did not exit')
        finally:
            handle.Close()
        RESULT.write_text(json.dumps({'state': 'stopped', 'pid': pid, 'program': 'enb_drag.exe'}))
        return
    if request['action'] == 'start_enb_drag':
        process = subprocess.Popen([str(ROOT / 'enb_drag.exe')], cwd=ROOT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        RESULT.write_text(json.dumps({'state': 'started', 'pid': process.pid, 'program': 'enb_drag.exe'}))
        return
    if request['action'] == 'arrange_characters':
        from arrange_windows import arrange, named_games
        targets = named_games()
        arrange(targets)
        RESULT.write_text(json.dumps({'state': 'arranged', 'characters': [toon for toon, path, corner in targets]}))
        return
    if request['action'] == 'vault_unload':
        from vault import main as unload_vault
        arguments = [str(ROOT / request['config'])]
        if request.get('list_only', False):
            arguments.append('--list')
        else:
            arguments.append('--all')
        code = unload_vault(arguments)
        RESULT.write_text(json.dumps({'state': 'vault_unload_finished', 'exit_code': code}))
        return
    if request['action'] == 'packet_open_vendor':
        from ac import AcController, PLAYER
        from game_instance import GameInstance
        from player import load_player_config
        from protocol.vendor_trading import VendorRoute, VendorSession, VendorTrader
        settings = load_player_config(ROOT / request['config'])
        PLAYER.configure(settings)
        instance = GameInstance(settings, shared_install=True)
        if request.get('title'):
            import win32process
            from focus import process_identity
            window = find_window_exact(request['title'])
            if not window or win32process.GetWindowThreadProcessId(window)[1] != request['pid']:
                raise RuntimeError('Requested dialogue window identity changed')
            instance.pid = request['pid']
            instance.born = process_identity(instance.pid)
        instance.discover(apply_layout=False)
        if instance.pid != request['pid']:
            raise RuntimeError('Requested vendor client identity changed')
        session = VendorSession.from_instance(instance)
        controller = AcController(request.get('title', instance.title))
        route = VendorRoute(request['npc'], request['npc'], 0, b'', b'', 0)
        trader = VendorTrader(controller, session, route, 1, 1)
        try:
            trader.capture.open()
            if 'dialogue_reply' in request:
                import struct
                trader.capture.read()
                trader.send(0x55, struct.pack('<IB', session.game_id & 0xffffff, int(request['dialogue_reply'])))
                trader.wait_reply(0x54, request['dialog'].encode('cp1252'))
            elif request.get('dialogue_only', False):
                import struct
                trader.close_trade()
                trader.capture.read()
                if 'space_target' in request:
                    trader.send(0x2c, struct.pack('<4I', session.game_id, 2, int(request['space_target']), 0))
                else:
                    trader.send(0x4e, struct.pack('<IIB', session.game_id & 0xffffff, request['npc'], 4))
                trader.wait_reply(0x54, request['dialog'].encode('cp1252'))
                if 'reply_after_open' in request:
                    trader.capture.read()
                    trader.send(0x55, struct.pack('<IB', session.game_id & 0xffffff, int(request['reply_after_open'])))
                    trader.wait_reply(0x54, b'')
            else:
                trader.close_trade()
                trader.open_trade(request['npc'], request['dialog'].encode('cp1252'))
            time.sleep(.3)
            _, size, origin = client_geometry(controller.window)
            path = ROOT / 'diagnostics' / f"vendor_open_{settings.name}.png"
            pyautogui.screenshot(region=(*origin, *size)).save(path)
            state = 'dialogue_open_acknowledged' if request.get('dialogue_only', False) else 'trade_open_acknowledged'
            RESULT.write_text(json.dumps({'state': state, 'npc': request['npc'],
                                          'client_pid': session.client_pid, 'proxy_pid': session.proxy_pid,
                                          'path': str(path)}))
        finally:
            try:
                trader.capture.close()
            finally:
                controller.close()
        return
    if request['action'] == 'window_health':
        import win32process
        from window_health import check_window_responsive
        window = find_window_exact(request['title'])
        result = {'title': request['title'], 'window': window, 'pid': request['pid'],
                  'actual_pid': win32process.GetWindowThreadProcessId(window)[1] if window else None,
                  'time': time.time()}
        probe_started = time.perf_counter()
        try:
            check_window_responsive(window, request['pid'], request['born'])
            result['state'] = 'responsive'
        except RuntimeError as error:
            result.update(state='unavailable', error=str(error))
        result['probe_ms'] = round((time.perf_counter() - probe_started) * 1000, 2)
        RESULT.write_text(json.dumps(result))
        return
    if request['action'] == 'navigate':
        config = ROOT / f"config_player_{request['toon']}.txt"
        from player import load_player_config
        if load_player_config(config).name != request['toon']:
            raise ValueError('Config/toon mismatch')
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'navigate.py'),
                                    str(config), request['destination']], cwd=ROOT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        RESULT.write_text(json.dumps({'state': 'started', 'pid': process.pid}))
        return
    if request['action'] == 'explore_sector':
        output = (ROOT / 'diagnostics' / 'supervised_exploration.log').open('a')
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'exploration_plan.py'),
                                    '--proxy', str(request['proxy_pid']), '--run'],
                                   cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        RESULT.write_text(json.dumps({'state': 'started', 'pid': process.pid}))
        return
    if request['action'] == 'start_capture':
        output = (ROOT / 'diagnostics' / 'supervised_capture.log').open('a')
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'protocol' / 'capture_world.py')],
                                   cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        RESULT.write_text(json.dumps({'state': 'started', 'pid': process.pid}))
        return
    if request['action'] == 'supervised_gate_arrival':
        await_gate(request)
        return
    if request['action'] == 'desktop':
        path = ROOT / 'diagnostics' / 'inspect_desktop.png'
        pyautogui.screenshot().save(path)
        RESULT.write_text(json.dumps({'state': 'captured', 'path': str(path), 'time': time.time()}))
        return
    if request['action'] in ('supervised_click', 'supervised_drag', 'supervised_kraken', 'supervised_key', 'supervised_navigation', 'supervised_station_reset', 'supervised_map'):
        # Explicit one-off coordinates from a current screenshot, scoped to PID.
        import win32process
        window = find_window_exact(request['title'])
        if not window or win32process.GetWindowThreadProcessId(window)[1] != request['pid']:
            raise RuntimeError('Supervised window identity changed')
        focus = SharedFocus('inspection', 'Inspection', claim_game=False)
        try:
            focus.acquire()
            if not focus_window(window):
                raise RuntimeError('Cannot focus supervised window')
            time.sleep(.2)
            if request['action'] == 'supervised_map':
                from ac import AcController, PLAYER
                PLAYER.current_position = request['sector']
                controller = AcController(request['title'], singleton=True)
                controller.get_map()
                _, size, origin = client_geometry(window)
                path = ROOT / 'diagnostics' / f"inspect_{request['toon']}.png"
                pyautogui.screenshot(region=(*origin, *size)).save(path)
                RESULT.write_text(json.dumps({'state':'captured', 'path':str(path)}))
                return
            if request['action'] == 'supervised_station_reset':
                # Supervised use after a capture verifies a docked station room.
                # Use the same reset command and orientation fields as sbreset.
                from memory import setmemory_u32_window
                setmemory_u32_window(window, 0x00BDBB80, 0)
                setmemory_u32_window(window, 0x00BDB1C0, request.get('orientation', 0))
                pyautogui.write('/starbase reset', interval=.02)
                pyautogui.press('enter')
                time.sleep(1)
                _, size, origin = client_geometry(window)
                path = ROOT / 'diagnostics' / 'inspect_station_reset.png'
                pyautogui.screenshot(region=(*origin, *size)).save(path)
                RESULT.write_text(json.dumps({'state':'station_reset', 'path':str(path), 'time':time.time()}))
                return
            if request['action'] == 'supervised_key':
                from gui import key_press
                keys = request.get('keys') or [{'key':request['key'], 'duration':request.get('duration', .3)}]
                if sum(min(5, max(.1, k.get('duration', .3))) for k in keys) > 10:
                    raise ValueError('Supervised key sequence exceeds short focus budget')
                for key in keys:
                    key_press(key['key'], min(5, max(.1, key.get('duration', .3))))
                time.sleep(.3)
                RESULT.write_text(json.dumps({'state':'key_sent', 'time':time.time()}))
                return
            if request['action'] in ('supervised_kraken', 'supervised_navigation'):
                # Already equipped: reuse setup_kranken's self-target/slot keys
                # and the macro's scan-code input, without inventory dragging.
                from gui import key_press
                key_press('`', .5)
                key_press('4', 1.5)
                if request['action'] == 'supervised_navigation':
                    # Caller verifies idle ship, live target, and Kraken slot.
                    # Keep activation and departure under one shared ownership.
                    from types import SimpleNamespace
                    from focus import process_identity
                    from gate_navigation import submit
                    proxy = request['proxy_pid']
                    from datetime import datetime
                    born = str(datetime.fromisoformat(request['proxy_born']))
                    if process_identity(proxy) != born:
                        raise RuntimeError('Supervised proxy identity changed')
                    instance = SimpleNamespace(proxy_pid=proxy, proxy_born=born,
                        valid=lambda: process_identity(proxy) == born)
                    outcome = submit(instance, int(request['target'], 16),
                        via=tuple(int(v, 16) for v in request.get('via', ())))
                    RESULT.write_text(json.dumps({'state':outcome['state'], 'time':time.time()}))
                    return
                RESULT.write_text(json.dumps({'state':'kraken_activated', 'time':time.time()}))
                return
            _, size, origin = client_geometry(window)
            if request['action'] == 'supervised_drag':
                start, end = request['start'], request['end']
                if any(not 0 <= v < 1 for point in (start, end) for v in point):
                    raise ValueError('Invalid supervised drag coordinates')
                pyautogui.moveTo(origin[0]+int(start[0]*size[0]), origin[1]+int(start[1]*size[1]))
                pyautogui.dragTo(origin[0]+int(end[0]*size[0]), origin[1]+int(end[1]*size[1]), duration=.8)
                time.sleep(.3)
                RESULT.write_text(json.dumps({'state':'dragged', 'time':time.time()}))
                return
            x, y = request['fraction']
            if not (0 <= x < 1 and 0 <= y < 1):
                raise ValueError('Invalid supervised coordinates')
            button = request.get('button', 'left')
            if button not in ('left', 'right'):
                raise ValueError('Invalid supervised mouse button')
            pyautogui.click(origin[0] + int(x*size[0]), origin[1] + int(y*size[1]), button=button)
            RESULT.write_text(json.dumps({'state':'clicked', 'size':size, 'origin':origin, 'time':time.time()}))
        finally:
            record_view(window)
            focus.close()
        return
    toon = request['toon']
    config = ROOT / f'config_player_{toon}.txt'
    from player import load_player_config
    if load_player_config(config).name != toon:
        raise ValueError('Config/toon mismatch')
    if request['action'] == 'packet_pick_jobs':
        from ac import AcController, PLAYER
        from job_policy import JobPolicy
        from packet_jobs import PacketJobPicker
        from macro_control import active_statuses
        from util import configure_event_log
        if any(item['toon'].casefold() == toon.casefold() for item in active_statuses()):
            raise RuntimeError('Stop the existing macro before one-shot pickup')
        settings = load_player_config(config)
        if request.get('client_pid'):
            from dataclasses import replace
            from arrange_windows import client_path
            path = client_path(int(request['client_pid']))
            if path.name.casefold() != 'client.exe':
                raise RuntimeError('Requested process is not a game client')
            settings = replace(settings, client_exe=str(path))
        if 'target' in request:
            from dataclasses import replace
            target = int(request['target'])
            if not 1 <= target <= 9:
                raise ValueError('One-shot mission target must be between 1 and 9')
            settings = replace(settings, mission_count=target)
        PLAYER.configure(settings)
        PLAYER.set_pos('earthJOB')
        configure_event_log(ROOT / f'macro_{toon}.log')
        controller = AcController(f'ENB_{toon}', settings=settings)
        try:
            if not controller.window and request.get('restart'):
                from pluto import PlutoRunner
                controller.relogin(initial=True)
                controller._terminated = False
                position = controller.get_current_position()
                if position != 'earthS':
                    raise RuntimeError(f'Packet pickup requires Earth station; found {position}')
                PLAYER.set_pos(position)
                controller.earth1_2()
                controller.earth2_j()
                runner = PlutoRunner(settings)
                runner.controller = controller
                runner.walk_job()
            controller.require_window()
            policy = JobPolicy.from_settings(settings, settings.low_job, settings.avoid_pit)
            count = PacketJobPicker(controller, policy, settings.mission_count).pick()
            RESULT.write_text(json.dumps({'state':'picked', 'toon':toon, 'count':count,
                                         'target':settings.mission_count, 'time':time.time()}))
        finally:
            controller.close()
        return
    if request['action'] == 'restart':
        # Caller must stop/verify the previous macro first, leaving its game up.
        output = (ROOT / 'diagnostics' / f'supervised_{toon}.log').open('a')
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'pluto.py'), str(config)],
                                   cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        RESULT.write_text(json.dumps({'state': 'started', 'pid': process.pid}))
        return
    focus = SharedFocus('inspection', 'Inspection', claim_game=False)
    window = None
    try:
        focus.acquire()
        window = find_window_exact(request.get('title', f'ENB_{toon}'))
        if not window or not focus_window(window):
            raise RuntimeError('Cannot focus the requested game')
        time.sleep(.2)
        _, size, origin = client_geometry(window)
        if request['action'] == 'inventory_first_page':
            from ac import AcController
            controller = AcController(request.get('title', f'ENB_{toon}'), singleton=True)
            for _ in range(9):
                controller.lclick(37, 558)
        if request['action'] == 'sell_one_inventory_item':
            from ac import AcController
            controller = AcController(request.get('title', f'ENB_{toon}'), singleton=True)
            controller.lclick(int(request['x']), int(request['y']), shift=True)
            controller.dismiss_trade()
        if request['action'] == 'earth_vendor_sell_all':
            from ac import AcController
            import win32process
            if win32process.GetWindowThreadProcessId(window)[1] != request['pid']:
                raise RuntimeError('Supervised vendor client identity changed')
            controller = AcController(request.get('title', f'ENB_{toon}'), singleton=True)
            controller.earth3_vendor(reset_position=bool(request.get('reset_position', True)))
        if request['action'] == 'leave_earth_vendor_room':
            from ac import AcController
            controller = AcController(request.get('title', f'ENB_{toon}'), singleton=True)
            controller.earth3_2()
        if request['action'] == 'count_mission':
            # A supervised one-shot count; never launches or resumes the macro.
            from ac import AcController, PLAYER
            from util import configure_event_log
            PLAYER.configure(load_player_config(config))
            configure_event_log(ROOT / f'macro_{toon}.log')
            controller = AcController(request.get('title', f'ENB_{toon}'), singleton=True)
            before = ROOT / 'diagnostics' / f'count_{toon}_before.png'
            after = ROOT / 'diagnostics' / f'count_{toon}_after.png'
            controller.screenshot().save(before)
            count = controller.count_mission()
            controller.screenshot().save(after)
            RESULT.write_text(json.dumps({'state':'counted', 'toon':toon, 'count':count,
                'source':'screen estimate (one-shot inspection has no packet baseline)',
                'before':str(before), 'after':str(after), 'time':time.time()}))
            return
        if request['action'] == 'stop_warp':
            # Supervised use only after a capture proves the ship is warping.
            pyautogui.press('q')
            time.sleep(.3)
        if request['action'] == 'select_nearest':
            pyautogui.press('tab')
            time.sleep(.3)
        if request['action'] == 'select_point':
            x, y = request['x'], request['y']
            if not (0 <= x < size[0] and 0 <= y < size[1]):
                raise ValueError('Selection coordinates outside the client')
            pyautogui.click(origin[0]+x,origin[1]+y)
            time.sleep(.3)
        if request['action'] == 'hover_point':
            x, y = request['x'], request['y']
            if not (0 <= x < size[0] and 0 <= y < size[1]):
                raise ValueError('Hover coordinates outside the client')
            pyautogui.moveTo(origin[0]+x, origin[1]+y)
            time.sleep(2)
        if request['action'] == 'dismiss_scan':
            # One-off supervised dismissal at coordinates verified in a capture.
            # Never used as an unattended generic popup click.
            pyautogui.click(origin[0] + 394, origin[1] + 462)
            time.sleep(.3)
        path = ROOT / 'diagnostics' / f'inspect_{toon}.png'
        pyautogui.screenshot(region=(*origin, *size)).save(path)
        import win32process
        RESULT.write_text(json.dumps({'state': 'captured', 'path': str(path),
            'pid': win32process.GetWindowThreadProcessId(window)[1], 'time': time.time()}))
    finally:
        record_view(window)
        focus.close()


if __name__ == '__main__':
    request = json.loads(REQUEST.read_text())
    record_action('started', request)
    try:
        main()
        record_action('completed', request, result=json.loads(RESULT.read_text()))
    except Exception as error:
        RESULT.write_text(json.dumps({'state': 'error', 'error': str(error)}))
        record_action('failed', request, error=str(error))
        raise
