import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pico"))
import kvm_core as core  # noqa: E402


class FakeKeycode:
    """Every attribute name used by the tables gets a distinct int."""
    def __init__(self):
        names = set(core.KEY_TABLE.values()) | set(core.MODIFIER_TABLE.values())
        for i, n in enumerate(sorted(names), 1):
            setattr(self, n, i)


class FakeMouseCls:
    LEFT_BUTTON, RIGHT_BUTTON, MIDDLE_BUTTON = 1, 2, 4


class FakeLayout:
    def keycodes(self, ch):
        if ord(ch) > 127:
            raise ValueError(ch)
        if ch.isupper():
            return (900, ord(ch))      # shift + base
        return (ord(ch),)


class FakeKbd:
    def __init__(self, fail=False):
        self.log, self.fail = [], fail

    def press(self, *c):
        if self.fail:
            raise OSError("usb gone")
        self.log.append(("press", c))

    def release(self, *c):
        self.log.append(("release", c))

    def release_all(self):
        self.log.append(("release_all",))


class FakeMouse:
    def __init__(self):
        self.log = []

    def move(self, x=0, y=0, wheel=0):
        self.log.append(("move", x, y))

    def press(self, b):
        self.log.append(("press", b))

    def release_all(self):
        self.log.append(("release_all",))


TOKEN = str("test-token-xyz123")   # dummy test value


def make(fail=False):
    kb, ms, sl = FakeKbd(fail), FakeMouse(), []
    kvm = core.Kvm(kb, ms, FakeLayout(), FakeKeycode(), FakeMouseCls, TOKEN, sl.append)
    return kvm, kb, ms, sl


def run(kvm, cmd, tok=TOKEN):
    return kvm.handle_line("%s %s\n" % (tok, cmd))


class ParserTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(core.parse_line("T key Enter\r\n"), ("T", "key", "Enter"))
        self.assertEqual(core.parse_line("T type  a  b "), ("T", "type", " a  b "))
        self.assertEqual(core.parse_line("T"), ("T", "", ""))
        self.assertEqual(core.parse_line(""), ("", "", ""))

    def test_unescape(self):
        self.assertEqual(core.unescape(r"a\nb\tc\\d"), "a\nb\tc\\d")
        self.assertEqual(core.unescape(r"C:\WINDOWS"), "C:\\WINDOWS")
        self.assertEqual(core.unescape("end\\"), "end\\")

    def test_unknown_and_empty(self):
        kvm, kb, *_ = make()
        self.assertTrue(run(kvm, "bogus").startswith("ERR unknown command"))
        self.assertTrue(run(kvm, "").startswith("ERR"))
        self.assertTrue(run(kvm, "_run").startswith("ERR"))
        self.assertEqual([e for e in kb.log if e[0] == "press"], [])


class KeyTableTests(unittest.TestCase):
    def test_required_names(self):
        need = ("esc enter tab backspace delete insert home end pgup pgdn up down "
                "left right space + -").split()
        need += ["f%d" % i for i in range(1, 13)]
        need += list("abcdefghijklmnopqrstuvwxyz0123456789")
        for n in need:
            self.assertIn(n, core.KEY_TABLE, n)
        for m in ("ctrl", "alt", "shift", "gui"):
            self.assertIn(m, core.MODIFIER_TABLE)

    def test_all_attrs_exist_on_real_keycode_if_available(self):
        try:
            from adafruit_hid.keycode import Keycode
        except ImportError:
            self.skipTest("adafruit_hid not installed")
        for name, attr in list(core.KEY_TABLE.items()) + list(core.MODIFIER_TABLE.items()):
            self.assertTrue(hasattr(Keycode, attr), (name, attr))

    def test_key_press_release(self):
        kvm, kb, *_ = make()
        self.assertEqual(run(kvm, "key Del"), "OK key del")
        fk = FakeKeycode()
        self.assertEqual(kb.log[0], ("press", (fk.DELETE,)))
        self.assertEqual(kb.log[1], ("release_all",))

    def test_unknown_key(self):
        kvm, *_ = make()
        self.assertTrue(run(kvm, "key bogus").startswith("ERR unknown key"))

    def test_combo(self):
        kvm, kb, *_ = make()
        fk = FakeKeycode()
        self.assertEqual(run(kvm, "combo ctrl+alt+del"), "OK combo ctrl+alt+del")
        self.assertEqual(kb.log[0], ("press", (fk.LEFT_CONTROL, fk.LEFT_ALT, fk.DELETE)))
        self.assertEqual(kb.log[1], ("release_all",))

    def test_combo_errors(self):
        kvm, *_ = make()
        for bad in ("combo ctrl", "combo a+b", "combo ctrl++", "combo"):
            self.assertTrue(run(kvm, bad).startswith("ERR"), bad)

    def test_hold_release_status(self):
        kvm, kb, *_ = make()
        run(kvm, "hold shift")
        self.assertIn("held=shift", run(kvm, "status"))
        run(kvm, "release shift")
        self.assertIn("held=-", run(kvm, "status"))
        run(kvm, "hold a")
        self.assertEqual(run(kvm, "release-all"), "OK released")
        self.assertIn("held=-", run(kvm, "status"))

    def test_type(self):
        kvm, kb, *_ = make()
        self.assertEqual(run(kvm, "type Hi\\n"), "OK typed 3")
        presses = [e[1] for e in kb.log if e[0] == "press"]
        self.assertEqual(presses, [(900, ord("H")), (ord("i"),), (10,)])

    def test_type_unsupported_is_all_or_nothing(self):
        kvm, kb, *_ = make()
        self.assertTrue(run(kvm, "type ab\u00a3").startswith("ERR cannot type"))
        self.assertEqual([e for e in kb.log if e[0] == "press"], [])

    def test_delay(self):
        kvm, kb, ms, sl = make()
        self.assertEqual(run(kvm, "delay"), "OK delay 40")
        self.assertEqual(run(kvm, "delay 250"), "OK delay 250")
        run(kvm, "key a")
        self.assertEqual(sl, [250, 250])
        self.assertTrue(run(kvm, "delay -1").startswith("ERR"))
        self.assertTrue(run(kvm, "delay 99999").startswith("ERR"))
        self.assertTrue(run(kvm, "delay x").startswith("ERR"))


class MouseTests(unittest.TestCase):
    def test_split_sums_and_limits(self):
        rnd = random.Random(1)
        cases = [(0, 0), (1, 0), (127, -127), (128, 0), (-300, 1000), (32767, -32767)]
        cases += [(rnd.randint(-5000, 5000), rnd.randint(-5000, 5000)) for _ in range(500)]
        for dx, dy in cases:
            steps = core.split_move(dx, dy)
            self.assertEqual(sum(s[0] for s in steps), dx)
            self.assertEqual(sum(s[1] for s in steps), dy)
            for sx, sy in steps:
                self.assertLessEqual(abs(sx), 127)
                self.assertLessEqual(abs(sy), 127)
        self.assertEqual(core.split_move(0, 0), [])
        self.assertEqual(len(core.split_move(128, 0)), 2)
        self.assertEqual(len(core.split_move(127, 0)), 1)

    def test_mouse_command(self):
        kvm, kb, ms, sl = make()
        self.assertEqual(run(kvm, "mouse 300 -10"), "OK mouse 300 -10 steps=3")
        moves = [e for e in ms.log if e[0] == "move"]
        self.assertEqual(sum(m[1] for m in moves), 300)
        self.assertEqual(sum(m[2] for m in moves), -10)
        self.assertEqual(len(sl), 2)   # pauses between steps only

    def test_mouse_errors(self):
        kvm, *_ = make()
        for bad in ("mouse 1", "mouse a b", "mouse 40000 0", "mouse 1 2 3"):
            self.assertTrue(run(kvm, bad).startswith("ERR"), bad)

    def test_click(self):
        kvm, kb, ms, sl = make()
        self.assertEqual(run(kvm, "click right"), "OK click right")
        self.assertEqual(ms.log[0], ("press", 2))
        self.assertEqual(ms.log[1], ("release_all",))
        self.assertTrue(run(kvm, "click side").startswith("ERR"))

    def test_no_mouse(self):
        kvm, *_ = make()
        kvm.mouse = None
        self.assertEqual(run(kvm, "mouse 1 1"), "ERR mouse disabled")
        self.assertEqual(run(kvm, "click left"), "ERR mouse disabled")


class AuthTests(unittest.TestCase):
    def test_token_ok(self):
        self.assertTrue(core.token_ok("abc", "abc"))
        self.assertFalse(core.token_ok("abd", "abc"))
        self.assertFalse(core.token_ok("abcd", "abc"))
        self.assertFalse(core.token_ok("ab", "abc"))
        self.assertFalse(core.token_ok("", "abc"))
        self.assertFalse(core.token_ok("x", ""))      # empty configured token never matches
        self.assertFalse(core.token_ok("", ""))

    def test_wrong_or_missing_token_sends_nothing(self):
        kvm, kb, ms, sl = make()
        self.assertEqual(run(kvm, "key a", tok="nope"), "ERR auth")
        self.assertEqual(kvm.handle_line("key a"), "ERR auth")
        self.assertEqual(kvm.handle_line(""), "ERR auth")
        self.assertEqual(kb.log, [])
        self.assertEqual(ms.log, [])
        self.assertEqual(sl, [])
        self.assertEqual(kvm.handle_line(TOKEN), "ERR empty command")  # valid token, no cmd

    def test_auth_does_not_touch_held_state(self):
        kvm, kb, *_ = make()
        run(kvm, "hold shift")
        n = len(kb.log)
        run(kvm, "release-all", tok="bad")
        self.assertEqual(len(kb.log), n)       # attacker could not release
        self.assertEqual(kvm.held, ["shift"])

    def test_parse_tokens(self):
        self.assertEqual(core.parse_tokens("aaa, bbb ,,ccc"), ["aaa", "bbb", "ccc"])
        self.assertEqual(core.parse_tokens(""), [])
        self.assertEqual(core.parse_tokens(None), [])
        self.assertEqual(core.parse_tokens("single"), ["single"])

    def test_one_token_per_pc(self):
        tok_a, tok_b = "pc-a-token-000001", "pc-b-token-000002"
        kb, ms, sl = FakeKbd(), FakeMouse(), []
        kvm = core.Kvm(kb, ms, FakeLayout(), FakeKeycode(), FakeMouseCls, [tok_a, tok_b], sl.append)
        self.assertTrue(run(kvm, "key a", tok=tok_a).startswith("OK"))
        self.assertTrue(run(kvm, "key a", tok=tok_b).startswith("OK"))
        n = len(kb.log)
        self.assertEqual(run(kvm, "key a", tok="someone-else-123"), "ERR auth")
        self.assertEqual(len(kb.log), n)
        self.assertFalse(core.any_token_ok(tok_a, []))


class SafetyTests(unittest.TestCase):
    def test_release_all_on_error(self):
        kvm, kb, ms, _ = make(fail=True)
        self.assertEqual(run(kvm, "key a"), "ERR internal OSError")
        self.assertIn(("release_all",), kb.log)
        self.assertIn(("release_all",), ms.log)

    def test_release_all_on_command_error(self):
        kvm, kb, ms, _ = make()
        run(kvm, "hold shift")
        run(kvm, "key bogus")
        self.assertEqual(kvm.held, [])
        self.assertIn(("release_all",), kb.log)

    def test_release_all_never_raises(self):
        kvm, kb, ms, _ = make()
        def boom():
            raise OSError
        kb.release_all = boom
        ms.release_all = boom
        kvm.release_all()   # must not raise


if __name__ == "__main__":
    unittest.main()
