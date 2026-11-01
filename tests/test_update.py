import binascii
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pico"))
sys.path.insert(0, os.path.join(HERE, "..", "client"))
import kvm_core as core  # noqa: E402
import kvm  # noqa: E402


class MemFile(io.BytesIO):
    def __init__(self, fs, path):
        super().__init__()
        self.fs, self.path = fs, path

    def close(self):
        self.fs.files[self.path] = self.getvalue()
        super().close()


class MemFs:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def open(self, path, mode):
        assert mode == "wb"
        return MemFile(self, path)

    def rename(self, a, b):
        self.files[b] = self.files.pop(a)

    def remove(self, p):
        del self.files[p]

    def exists(self, p):
        return p in self.files


def make(files=None):
    fs = MemFs(files)
    k = core.Kvm(None, None, None, None, None, "token-123456789", lambda ms: None)
    k.updater = core.Updater(fs, binascii.crc32, binascii.a2b_base64)
    return k, fs


def run(k, cmd):
    return k.handle_line("token-123456789 " + cmd)


class UpdateTests(unittest.TestCase):
    def test_round_trip_keeps_prev(self):
        k, fs = make({"/kvm_core.py": b"old"})
        data = bytes(range(256)) * 5            # 1280 bytes: several put-data lines
        for c in kvm.push_commands("kvm_core.py", data):
            self.assertLessEqual(len("token-123456789 " + c), core.MAX_LINE)
            self.assertTrue(run(k, c).startswith("OK"), c)
        self.assertEqual(fs.files["/kvm_core.py"], data)
        self.assertEqual(fs.files["/kvm_core.py.prev"], b"old")
        self.assertNotIn("/kvm_core.py.new", fs.files)

    def test_bad_crc_keeps_old(self):
        k, fs = make({"/kvm_main.py": b"old"})
        cmds = kvm.push_commands("kvm_main.py", b"new code")
        cmds[0] = cmds[0].rsplit(" ", 1)[0] + " deadbeef"
        for c in cmds[:-1]:
            run(k, c)
        self.assertTrue(run(k, cmds[-1]).startswith("ERR check failed"))
        self.assertEqual(fs.files, {"/kvm_main.py": b"old"})

    def test_only_updatable_names(self):
        k, fs = make()
        for name in ("settings.toml", "code.py", "../x.py", "lib/x.py"):
            self.assertTrue(run(k, "put-begin %s 3 0" % name).startswith("ERR not updatable"), name)
        with self.assertRaises(kvm.KvmError):
            kvm.push_commands("settings.toml", b"x")
        self.assertEqual(fs.files, {})

    def test_too_much_and_order(self):
        k, fs = make()
        self.assertTrue(run(k, "put-data QUJD").startswith("ERR no put-begin"))
        self.assertTrue(run(k, "put-end").startswith("ERR no put-begin"))
        run(k, "put-begin kvm_core.py 2 0")
        self.assertTrue(run(k, "put-data QUJD").startswith("ERR too much"))
        self.assertEqual(fs.files, {})
        self.assertTrue(run(k, "put-begin kvm_core.py 0 0").startswith("ERR bad size"))

    def test_abort_on_disconnect(self):
        k, fs = make()
        run(k, "put-begin kvm_core.py 3 0")
        k.updater.abort()
        self.assertEqual(fs.files, {})

    def test_disabled_and_reboot(self):
        k = core.Kvm(None, None, None, None, None, "token-123456789", lambda ms: None)
        self.assertEqual(run(k, "put-begin kvm_core.py 3 0"), "ERR update disabled")
        self.assertEqual(run(k, "reboot"), "OK reboot")
        self.assertTrue(k.reboot_pending)

    def test_wrong_token_cannot_update(self):
        k, fs = make()
        self.assertEqual(k.handle_line("nope kvm put-begin kvm_core.py 3 0"), "ERR auth")
        self.assertEqual(fs.files, {})


class ModeTests(unittest.TestCase):
    def test_mousemode_flag(self):
        k, fs = make()
        self.assertIn("now off", run(k, "mousemode off"))
        self.assertIn("/kvm_kbd_only", fs.files)
        self.assertNotIn("/kvm_mouse_on", fs.files)
        self.assertIn("now on", run(k, "mousemode on"))
        self.assertNotIn("/kvm_kbd_only", fs.files)
        self.assertIn("/kvm_mouse_on", fs.files)
        self.assertTrue(run(k, "mousemode maybe").startswith("ERR"))

    def test_usb_not_ready_refuses_hid_only(self):
        k, fs = make()
        k.usb_ready = lambda: False
        self.assertTrue(run(k, "key up").startswith("ERR usb not ready"))
        self.assertTrue(run(k, "status").startswith("OK"))


class Pin:
    def __init__(self):
        self.value, self.log = False, []

    def __setattr__(self, k, v):
        if k == "value" and hasattr(self, "log"):
            self.log.append(v)
        object.__setattr__(self, k, v)


class ButtonTests(unittest.TestCase):
    def test_not_wired(self):
        k, fs = make()
        self.assertEqual(run(k, "pcpower"), "ERR power button not wired")

    def test_press_hold_reset(self):
        k, fs = make()
        slept = []
        k.sleep_ms = slept.append
        k.buttons = {"power": Pin(), "reset": Pin()}
        self.assertEqual(run(k, "pcpower press"), "OK power 500 ms")
        self.assertEqual(run(k, "pcpower hold"), "OK power 6000 ms")
        self.assertEqual(run(k, "pcreset"), "OK reset 300 ms")
        self.assertEqual(slept, [500, 6000, 300])
        self.assertEqual(k.buttons["power"].log, [True, False, True, False])
        self.assertFalse(k.buttons["reset"].value)
        self.assertTrue(run(k, "pcpower long").startswith("ERR"))

    def test_released_on_error(self):
        k, fs = make()
        def boom(ms):
            raise OSError("x")
        k.sleep_ms = boom
        k.buttons = {"power": Pin(), "reset": Pin()}
        self.assertTrue(run(k, "pcpower").startswith("ERR"))
        self.assertFalse(k.buttons["power"].value)


class LazyHidTests(unittest.TestCase):
    def test_keyboard_made_at_first_key(self):
        k, fs = make()
        k.kbd = None
        made = []
        class Kb:
            def press(self, *c): made.append(c)
            def release_all(self): pass
        k.hid_factory = lambda: (None, None)
        self.assertEqual(run(k, "key a"), "ERR usb not ready (keyboard pending)")
        self.assertIn("kbd=pending", run(k, "status"))
        k.hid_factory = lambda: (Kb(), None)
        k.layout_factory = lambda kb: "layout-for-" + type(kb).__name__
        k.kc = type("KC", (), {"A": 4})
        self.assertEqual(run(k, "key a"), "OK key a")
        self.assertEqual(k.layout, "layout-for-Kb")
        self.assertIn("kbd=ok", run(k, "status"))

    def test_status_usb_info_and_taps(self):
        k, fs = make()
        class Kb:
            def press(self, *c): pass
            def release_all(self): pass
        k.kbd = Kb()
        k.kc = type("KC", (), {"A": 4})
        k.usb_info = lambda: "boot=1 leds=01"
        self.assertEqual(run(k, "key a"), "OK key a")
        st = run(k, "status")
        self.assertIn("taps=1 boot=1 leds=01", st)
        def boom():
            raise RuntimeError("x")
        k.usb_info = boom
        self.assertIn("usbinfo=RuntimeError", run(k, "status"))


if __name__ == "__main__":
    unittest.main()
