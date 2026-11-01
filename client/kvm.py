#!/usr/bin/env python3
"""pico-usbkeyboard client (standard library only).

  PICO_KVM_TOKEN=... python kvm.py --host <pico-address> key del
  python kvm.py --host <pico-address> type "dir\\n"
  python kvm.py --host <pico-address> script enter-bios.kvm

The token is read ONLY from the environment variable PICO_KVM_TOKEN and is
never printed. Host/port/layout may also come from PICO_KVM_HOST /
PICO_KVM_PORT / PICO_KVM_LAYOUT.

Script files: one protocol command per line (without the token); '#' comments
and blank lines are ignored. Client-side directives (not sent):
  sleep <ms>
  repeat <count> <interval_ms> <command...>
"""
import argparse
import os
import socket
import sys
import time

DEFAULT_PORT = 8099
SIMPLE = ("key", "combo", "hold", "release", "click", "delay", "type", "mouse")

# The Pico types with the US layout. A target OS set to the UK layout reads the same keys differently
# ('#'->'£', '"'->'@', '@'->'"', '\'->'#', '|'->'~'), so in "uk" mode the client sends, for each character, what
# lands on the right UK key. BIOS setup screens read keys as US: use --layout us (the default) there.
UK_SWAP = {'"': "@", "@": '"', "£": "#", "¬": "~"}       # same key, other US character
UK_KEYS = {"#": "key hash", "~": "combo shift+hash",
           "\\": "key nonusbackslash", "|": "combo shift+nonusbackslash"}
LAYOUTS = ("us", "uk")


def type_commands(text, layout="us"):
    """Protocol commands that make `text` appear on a target using `layout`.
    `text` uses the protocol escapes \\n, \\t, \\\\ (as for `type`); in "us" mode it is sent unchanged."""
    if layout == "us":
        return ["type " + text]
    if layout != "uk":
        raise KvmError("unknown layout %s (use us or uk)" % layout)
    cmds, run, i = [], [], 0

    def flush():
        if run:
            cmds.append("type " + "".join(run))
            run.clear()
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in "nt\\":
            nxt = text[i + 1]
            i += 2
            if nxt == "\\":
                flush(); cmds.append(UK_KEYS["\\"])
            else:
                run.append("\\" + nxt)          # \n, \t: the Pico's own escapes, layout-independent
            continue
        if ch in UK_KEYS:
            flush(); cmds.append(UK_KEYS[ch])
        else:
            run.append(UK_SWAP.get(ch, ch))
        i += 1
    flush()
    return cmds


class KvmError(Exception):
    pass


class Session:
    def __init__(self, host, port, token, timeout=120.0):
        self.token = token
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(timeout)
        self.rfile = self.sock.makefile("rb")

    def send(self, command):
        """Send one command (no token) and return the reply line."""
        if "\n" in command or "\r" in command:
            raise KvmError("command contains a newline")
        self.sock.sendall((self.token + " " + command + "\n").encode("utf-8"))
        line = self.rfile.readline()
        if not line:
            raise KvmError("connection closed by Pico")
        return line.decode("utf-8", "replace").rstrip("\r\n")

    def close(self):
        try:
            self.send("release-all")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def run_script(session, path, keep_going=False, out=print, layout="us"):
    """Run a script file. Returns number of ERR replies.
    A line `layout us|uk` (client-side) switches how later `type` lines are sent."""
    errors = 0
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for n, raw in enumerate(lines, 1):
        line = raw.strip() if not raw.lstrip().lower().startswith("type ") else raw.lstrip()
        if not line or line.startswith("#"):
            continue
        word = line.split(" ", 1)[0].lower()
        if word == "sleep":
            time.sleep(int(line.split()[1]) / 1000.0)
            continue
        if word == "layout":
            layout = line.split()[1].lower()
            if layout not in LAYOUTS:
                raise KvmError("line %d: layout us|uk" % n)
            out("%d: layout %s (client)" % (n, layout))
            continue
        count, interval, cmd = 1, 0, line
        if word == "repeat":
            parts = line.split(" ", 3)
            if len(parts) < 4:
                raise KvmError("line %d: usage repeat <count> <interval_ms> <command>" % n)
            count, interval, cmd = int(parts[1]), int(parts[2]), parts[3]
        for i in range(count):
            parts = type_commands(cmd[5:], layout) if cmd.lower().startswith("type ") else [cmd]
            for c in parts:
                reply = session.send(c)
                out("%d: %s -> %s" % (n, c, reply))
                if reply.startswith("ERR"):
                    errors += 1
                    if not keep_going:
                        return errors
            if interval and i < count - 1:
                time.sleep(interval / 1000.0)
    return errors


UPDATABLE = ("kvm_main.py", "kvm_core.py", "safemode.py", "boot.py")
CHUNK = 300   # bytes per put-data line (400 base64 characters, under the Pico's 512-byte line limit)


def push_commands(name, data):
    """Protocol lines that send one file to the Pico."""
    import base64
    import zlib
    if name not in UPDATABLE:
        raise KvmError("%s cannot be updated over the network (only %s)" % (name, ", ".join(UPDATABLE)))
    cmds = ["put-begin %s %d %08x" % (name, len(data), zlib.crc32(data) & 0xFFFFFFFF)]
    for i in range(0, len(data), CHUNK):
        cmds.append("put-data " + base64.b64encode(data[i:i + CHUNK]).decode("ascii"))
    cmds.append("put-end")
    return cmds


def build_parser():
    p = argparse.ArgumentParser(description="Control a pico-usbkeyboard Pico W over the LAN.")
    p.add_argument("--host", default=os.environ.get("PICO_KVM_HOST"))
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("PICO_KVM_PORT", DEFAULT_PORT)))
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--layout", choices=LAYOUTS, default=os.environ.get("PICO_KVM_LAYOUT", "us"),
                   help="keyboard layout the target OS uses, for `type` (us: default, also BIOS setup; uk: OS set to UK)")
    sub = p.add_subparsers(dest="sub", required=True)
    sub.add_parser("type").add_argument("text", nargs="+")
    for name in ("key", "combo", "hold", "release", "delay"):
        s = sub.add_parser(name)
        s.add_argument("arg", nargs="?" if name == "delay" else None)
    sub.add_parser("release-all")
    m = sub.add_parser("mouse")
    m.add_argument("dx", type=int)
    m.add_argument("dy", type=int)
    sub.add_parser("click").add_argument("button", choices=("left", "right", "middle"))
    sub.add_parser("status")
    pu = sub.add_parser("push", help="send firmware files to the Pico over the network (then reboot)")
    pu.add_argument("files", nargs="+", help="kvm_main.py and/or kvm_core.py")
    pu.add_argument("--no-reboot", action="store_true")
    sub.add_parser("reboot")
    sub.add_parser("pcpower", help="press the PC's power button: press (0.5 s) or hold (6 s, force off)").add_argument(
        "how", nargs="?", default="press", choices=("press", "hold"))
    sub.add_parser("pcreset", help="press the PC's reset button (0.3 s)")
    sub.add_parser("mousemode", help="on|off: keyboard+mouse or keyboard only (after reboot)").add_argument(
        "state", choices=("on", "off"))
    s = sub.add_parser("script")
    s.add_argument("file")
    s.add_argument("--keep-going", action="store_true")
    return p


def command_for(args):
    if args.sub == "type":
        return "type " + " ".join(args.text)
    if args.sub in ("key", "combo", "hold", "release"):
        return "%s %s" % (args.sub, args.arg)
    if args.sub == "delay":
        return "delay" + (" " + args.arg if args.arg else "")
    if args.sub == "mouse":
        return "mouse %d %d" % (args.dx, args.dy)
    if args.sub == "click":
        return "click " + args.button
    if args.sub == "pcpower":
        return "pcpower " + args.how
    if args.sub == "mousemode":
        return "mousemode " + args.state
    return args.sub  # release-all, status


def main(argv=None):
    args = build_parser().parse_args(argv)
    token = os.environ.get("PICO_KVM_TOKEN")
    if not token:
        print("error: set the PICO_KVM_TOKEN environment variable", file=sys.stderr)
        return 2
    if not args.host:
        print("error: --host (or PICO_KVM_HOST) required", file=sys.stderr)
        return 2
    try:
        session = Session(args.host, args.port, token, args.timeout)
    except OSError as e:
        print("error: cannot connect: %s" % e, file=sys.stderr)
        return 3
    try:
        if args.sub == "push":
            for f in args.files:
                with open(f, "rb") as fh:
                    data = fh.read()
                name = os.path.basename(f)
                for c in push_commands(name, data):
                    reply = session.send(c)
                    if not reply.startswith("OK put-data"):
                        print(reply)
                    if reply.startswith("ERR"):
                        return 1
            if not args.no_reboot:
                print(session.send("reboot"))
                print("the Pico restarts (about 15 s); check with: status")
            return 0
        if args.sub == "script":
            errs = run_script(session, args.file, args.keep_going, layout=args.layout)
            return 1 if errs else 0
        cmd = command_for(args)
        cmds = type_commands(cmd[5:], args.layout) if args.sub == "type" else [cmd]
        rc = 0
        for c in cmds:
            reply = session.send(c)
            print(reply)
            if reply.startswith("ERR"):
                return 1
        return rc
    except (KvmError, OSError, KeyboardInterrupt) as e:
        print("error: %s" % (e or "interrupted"), file=sys.stderr)
        return 3
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
