from contextlib import contextmanager
import unittest
from unittest.mock import Mock, patch
import ac


class VendorFocusTests(unittest.TestCase):
    def controller(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        depth = [0]
        @contextmanager
        def action():
            depth[0] += 1
            try:
                yield
            finally:
                depth[0] -= 1
        controller.input_action = Mock(side_effect=action)
        controller.lclick = Mock(side_effect=lambda *args, **kwargs: self.assertGreater(depth[0], 0))
        controller._yield = Mock()
        return controller

    def test_sales_keep_one_complete_pass_together(self):
        controller = self.controller()
        controller.sell_all()
        self.assertEqual(controller.input_action.call_count, 1)
        self.assertEqual(controller.lclick.call_count, 84)
        self.assertEqual(sum(c.kwargs.get("shift", False) for c in controller.lclick.call_args_list), 60)

    def test_purchase_burst_keeps_focus_and_preserves_click_delays(self):
        controller = self.controller()
        controller.repeat_vendor_click(924, 720, 40, 0.1)
        controller.input_action.assert_called_once()
        self.assertEqual(controller.lclick.call_count, 40)
        self.assertTrue(all(c.args == (924, 720) and c.kwargs == {"yield_time": 0.1}
                            for c in controller.lclick.call_args_list))

    def test_earth_vendor_can_reopen_without_reset_or_walking(self):
        controller = self.controller()
        controller.sbreset = Mock()
        controller.key_press = Mock()
        controller.wait_trade = Mock()
        controller.sell_all = Mock()
        controller.dismiss_trade = Mock()
        controller.earth3_vendor(reset_position=False)
        controller.sbreset.assert_not_called()
        controller.key_press.assert_not_called()
        controller.lclick.assert_called_once()
        controller.wait_trade.assert_called_once()
        controller.sell_all.assert_called_once()
        controller.dismiss_trade.assert_called_once()

    def test_inventory_groups_four_completed_drags_and_preserves_slot_order(self):
        controller = self.controller()
        active = [False]
        groups = []
        @contextmanager
        def action():
            self.assertFalse(active[0])
            active[0] = True
            groups.append([])
            try:
                yield
            finally:
                active[0] = False
        controller.input_action = Mock(side_effect=action)
        controller.press = Mock(side_effect=lambda *args: self.assertTrue(active[0]))
        controller.lclick = Mock(side_effect=lambda *args, **kwargs: self.assertTrue(active[0]))
        def drag(x, y):
            self.assertTrue(active[0])
            groups[-1].append((x, y))
        controller.empty_one = Mock(side_effect=drag)
        controller.empty_inventory()
        self.assertEqual([len(group) for group in groups], [0, 4, 4, 4])
        self.assertEqual([slot for group in groups for slot in group],
                         [(x, y) for y in (324, 388, 450, 509) for x in (69, 132, 221)])
        self.assertEqual(controller.lclick.call_count, 10)
