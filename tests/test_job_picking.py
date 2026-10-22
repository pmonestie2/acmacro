from contextlib import contextmanager
import unittest
from unittest.mock import Mock

from pluto import PlutoRunner


class JobPickingTests(unittest.TestCase):
    def test_batched_focus_preserves_selection_loading_and_acceptance(self):
        runner = PlutoRunner()
        controller = runner.controller = Mock()
        groups = []
        active = []
        @contextmanager
        def focus(name):
            self.assertFalse(active)
            group = []
            groups.append((name, group))
            active.append(group)
            try:
                yield
            finally:
                active.pop()
        controller.with_focus.side_effect = focus
        controller.lclick.side_effect = lambda x, y: active[0].append(('click', x, y))
        controller._yield.side_effect = lambda seconds: active[0].append(('wait', seconds))
        def recognize():
            self.assertEqual(active[0][-1], ('wait', 2))
            active[0].append(('read',))
            return True
        runner.pickBatchJob(recognize)
        rows = [group for name, group in groups if name == 'job rows']
        self.assertEqual(len(rows), 6)
        expected = []
        for _ in range(2):
            for row in range(10):
                expected.extend([('click', 290, 327 + row * 20), ('wait', 2),
                                 ('read',), ('click', 1089, 706), ('wait', 1)])
        self.assertEqual([action for group in rows for action in group], expected)
        self.assertTrue(all(sum(action[1] for action in group if action[0] == 'wait') <= 12
                            for group in rows))

    def test_pit_jobs_are_still_rejected_within_a_batch(self):
        runner = PlutoRunner(avoid_pit=True)
        controller = runner.controller = Mock()
        from contextlib import nullcontext
        controller.with_focus.side_effect = lambda name: nullcontext()
        runner.is_pit_job = Mock(side_effect=[True, False] * 10)
        runner.pickBatchJob(lambda: True)
        accepted = [call for call in controller.lclick.call_args_list if call.args == (1089, 706)]
        self.assertEqual(len(accepted), 10)
