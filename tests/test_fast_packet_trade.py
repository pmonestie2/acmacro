import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from diagnostics import run_fast_packet_trade_cycles as batch


class FastPacketTradeTests(unittest.TestCase):
    def test_replaced_client_prevents_native_submission(self) -> None:
        probe = batch.PinnedVendorProbe(126, 123, 'original client birth')
        with patch.object(batch, 'process_identity', return_value='new client birth'), \
                patch.object(batch.vendor.VendorProbe, 'send') as send:
            with self.assertRaisesRegex(RuntimeError, 'client exited or changed'):
                probe.send(0x27, b'purchase')
        send.assert_not_called()

    def test_stop_signal_prevents_new_submissions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            stop = Path(folder) / 'stop'
            stop.write_text('stop')
            probe = Mock(spec=batch.vendor.VendorProbe)
            with patch.object(batch, 'STOP', stop):
                with self.assertRaisesRegex(RuntimeError, 'User stop requested'):
                    batch.submit(probe, 0x27, b'purchase')
            probe.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
