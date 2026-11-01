import io
import os
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "client"))
sys.path.insert(0, os.path.join(HERE, "..", "pico"))
import kvm  # noqa: E402
import kvm_core  # noqa: E402


class FakeServer:
    """Loopback server that records lines and answers OK/ERR auth."""
    def __init__(self, token):
        self.token, self.lines = token, []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.t = threading.Thread(target=self.serve, daemon=True)
        self.t.start()

    def serve(self):
        conn, _ = self.srv.accept()
        f = conn.makefile("rb")
        for raw in f:
            line = raw.decode().rstrip("\n")
            self.lines.append(line)
            tok, cmd, rest = kvm_core.parse_line(line)
            conn.sendall(b"OK\n" if tok == self.token else b"ERR auth\n")
        conn.close()


class ClientTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = kvm.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_missing_token_env(self):
        os.environ.pop("PICO_KVM_TOKEN", None)
        rc, out, err = self.run_main(["--host", "127.0.0.1", "status"])
        self.assertEqual(rc, 2)
        self.assertIn("PICO_KVM_TOKEN", err)

    def test_no_token_argument_exists(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            kvm.build_parser().parse_args(["--token", "x", "status"])

    def test_command_and_token_never_printed(self):
        os.environ["PICO_KVM_TOKEN"] = "tok-123456789012"
        s = FakeServer("tok-123456789012")
        rc, out, err = self.run_main(["--host", "127.0.0.1", "--port", str(s.port),
                                      "combo", "ctrl+alt+del"])
        self.assertEqual(rc, 0)
        self.assertNotIn("tok-123456789012", out + err)
        self.assertEqual(s.lines[0], "tok-123456789012 combo ctrl+alt+del")
        self.assertEqual(s.lines[-1], "tok-123456789012 release-all")

    def test_bad_token_gives_nonzero(self):
        os.environ["PICO_KVM_TOKEN"] = "wrong-token-xx"
        s = FakeServer("tok-123456789012")
        rc, out, _ = self.run_main(["--host", "127.0.0.1", "--port", str(s.port), "status"])
        self.assertEqual(rc, 1)
        self.assertIn("ERR auth", out)

    def test_script_repeat_and_comments(self):
        os.environ["PICO_KVM_TOKEN"] = "t0k"
        s = FakeServer("t0k")
        with tempfile.NamedTemporaryFile("w", suffix=".kvm", delete=False) as f:
            f.write("# comment\n\ndelay 10\nrepeat 3 0 key del\ntype  hi there\n")
        try:
            rc, out, _ = self.run_main(["--host", "127.0.0.1", "--port", str(s.port),
                                        "script", f.name])
        finally:
            os.unlink(f.name)
        self.assertEqual(rc, 0)
        self.assertEqual(s.lines[:5], ["t0k delay 10"] + ["t0k key del"] * 3 + ["t0k type  hi there"])

    def test_command_for(self):
        p = kvm.build_parser()
        self.assertEqual(kvm.command_for(p.parse_args(["mouse", "5", "-3"])), "mouse 5 -3")
        self.assertEqual(kvm.command_for(p.parse_args(["type", "a", "b"])), "type a b")
        self.assertEqual(kvm.command_for(p.parse_args(["delay"])), "delay")
        self.assertEqual(kvm.command_for(p.parse_args(["release-all"])), "release-all")

class UkLayoutTests(unittest.TestCase):
    def test_us_mode_unchanged(self):
        self.assertEqual(kvm.type_commands('a"@#\\n', "us"), ['type a"@#\\n'])

    def test_uk_swaps_and_keys(self):
        self.assertEqual(kvm.type_commands('say "hi" to a@b', "uk"), ['type say @hi@ to a"b'])
        self.assertEqual(kvm.type_commands("\u00a3 \u00ac", "uk"), ["type # ~"])
        self.assertEqual(kvm.type_commands("1#2~3", "uk"),
                         ["type 1", "key hash", "type 2", "combo shift+hash", "type 3"])
        self.assertEqual(kvm.type_commands("a|b", "uk"),
                         ["type a", "combo shift+nonusbackslash", "type b"])

    def test_uk_backslash_and_escapes(self):
        # C:\WINDOWS typed as C: then the UK backslash key; \n stays a Pico escape
        self.assertEqual(kvm.type_commands("C:\\WINDOWS\\n", "uk"),
                         ["type C:", "key nonusbackslash", "type WINDOWS\\n"])
        self.assertEqual(kvm.type_commands("a\\\\b", "uk"), ["type a", "key nonusbackslash", "type b"])

    def test_default_layout_is_us(self):
        self.assertEqual(kvm.type_commands('a"#'), ['type a"#'])
        os.environ.pop("PICO_KVM_LAYOUT", None)
        self.assertEqual(kvm.build_parser().parse_args(["status"]).layout, "us")

    def test_bad_layout(self):
        with self.assertRaises(kvm.KvmError):
            kvm.type_commands("x", "de")


if __name__ == "__main__":
    unittest.main()
