import gc
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chatproc
import watchdog


class ChatCleanupTests(unittest.TestCase):
    def test_wmi_cycles_are_released_on_owner_thread_before_com_uninitializes(self):
        for mode in ("success", "read_error", "create_error", "delete_error"):
            with self.subTest(mode=mode):
                events, errors = [], []
                def record(label):
                    events.append((label, threading.get_ident()))

                class Snapshot:
                    DeviceObject = r"\\?\GLOBALROOT\Device\Snapshot"
                    def __init__(self, cls):
                        self._instance_of = cls
                    def Delete_(self):
                        record("delete")
                        if mode == "delete_error":
                            raise OSError("delete failed")

                class ShadowClass:
                    def __init__(self, namespace):
                        self._namespace = namespace
                    def Create(self, **kwargs):
                        return (1, None) if mode == "create_error" else (0, "snapshot-id")
                    def __call__(self, **kwargs):
                        return [Snapshot(self)]
                    def __del__(self):
                        record("release")

                class Namespace:
                    def __init__(self, **kwargs):
                        self._classes_map = {"Win32_ShadowCopy": ShadowClass(self)}
                    @property
                    def Win32_ShadowCopy(self):
                        return self._classes_map["Win32_ShadowCopy"]

                def read_text(*args, **kwargs):
                    if mode == "read_error":
                        raise OSError("read failed")
                    return "chat"

                def read():
                    try:
                        chatproc.read_chat_log(Path(r"C:\game\chat.log"))
                    except OSError:
                        errors.append(mode)

                with patch("chatproc.require_admin"), patch("chatproc.wmi.WMI", side_effect=Namespace), \
                     patch.object(Path, "read_text", side_effect=read_text), \
                     patch("chatproc.pythoncom.CoInitialize", side_effect=lambda: record("initialize")), \
                     patch("chatproc.pythoncom.CoUninitialize", side_effect=lambda: record("uninitialize")):
                    worker = threading.Thread(target=read)
                    worker.start()
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    gc.collect()
                labels = [label for label, _ in events]
                self.assertEqual(labels[0], "initialize")
                self.assertEqual(labels[-1], "uninitialize")
                self.assertIn("release", labels)
                self.assertTrue(all(ident == worker.ident for _, ident in events))
                self.assertEqual(errors, [] if mode == "success" else [mode])

    def test_stopped_timer_drains_inflight_cleanup_and_never_polls_again(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def poll():
            calls.append(1)
            entered.set()
            try:
                release.wait(2)
            finally:
                finished.set()
        task = watchdog.run_every(0.01, poll)
        try:
            self.assertTrue(entered.wait(1))
            task.set()
            task.join(0.01)
            self.assertFalse(finished.is_set())
            release.set()
            task.join(2)
            self.assertTrue(finished.is_set())
            self.assertFalse(task.thread.is_alive())
            self.assertEqual(calls, [1])
        finally:
            task.set()
            release.set()
            task.join(2)


if __name__ == "__main__":
    unittest.main()
