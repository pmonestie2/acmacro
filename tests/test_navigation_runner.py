import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import navigate
from watchdog import StopMacro


class NavigationRunnerTests(unittest.TestCase):
    def test_pm_monitor_starts_before_route_and_stops_after_arrival(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'diagnostics').mkdir()
            controller=MagicMock()
            controller.instance.valid.return_value=True
            controller.get_current_position.return_value='carp'
            navigator=MagicMock(); navigator.route.return_value='orsini'
            event=MagicMock()
            with patch.object(navigate,'ROOT',root), patch.object(navigate,'PLAYER'), \
                 patch.object(navigate,'load_player_config',return_value=SimpleNamespace(name='pw')), \
                 patch.object(navigate,'AcController',return_value=controller) as constructor, \
                 patch.object(navigate,'CapturedNavigator',return_value=navigator), \
                 patch.object(navigate,'reset_chat') as reset, patch.object(navigate,'stop_chat') as stop, \
                 patch.object(navigate,'run_every',return_value=event) as monitor, \
                 patch('sys.argv',['navigate.py','config.txt','orsini']):
                def routed(*args):
                    controller.init_read_chat.assert_called_once()
                    monitor.assert_called_once_with(20,controller.check_chat_response)
                    return 'orsini'
                navigator.route.side_effect=routed
                reset.side_effect=lambda *args: self.assertTrue(constructor.called)
                navigate.main()
                self.assertFalse(constructor.call_args.kwargs['singleton'])
                reset.assert_called_once_with('pw')
                stop.assert_called_once()
                event.set.assert_called_once(); event.join.assert_called_once()
                self.assertEqual(json.loads((root/'diagnostics/navigate_pw.json').read_text())['state'],'arrived')

    def test_intentional_pm_stop_is_never_restarted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'diagnostics').mkdir()
            controller=MagicMock(); controller.instance.valid.return_value=True
            controller.init_read_chat.side_effect=StopMacro('GM requested logout')
            with patch.object(navigate,'ROOT',root), patch.object(navigate,'PLAYER'), \
                 patch.object(navigate,'load_player_config',return_value=SimpleNamespace(name='pw')), \
                 patch.object(navigate,'AcController',return_value=controller), \
                 patch.object(navigate,'reset_chat'), patch.object(navigate,'stop_chat'), \
                 patch.object(navigate,'run_every'), patch('sys.argv',['navigate.py','config.txt','orsini']):
                with self.assertRaises(StopMacro): navigate.main()
                controller.relogin.assert_not_called()
                controller.close.assert_called_once()
