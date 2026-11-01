"""Hardware-independent core of the pico-usbkeyboard firmware.

Everything here runs on CircuitPython *and* CPython so it can be unit-tested
without hardware. Hardware objects (keyboard, mouse, layout, Keycode class,
sleep function) are injected by kvm_main.py (or by the tests).

Protocol: one command per line, "<TOKEN> <command> [args]". One-line reply:
"OK ..." or "ERR ...".
"""

MAX_LINE = 512            # bytes per command line
MAX_MOUSE_STEP = 127      # HID relative report limit per axis (signed 8 bit)
MAX_MOUSE_TOTAL = 32767   # per axis, per command
MAX_DELAY_MS = 5000
DEFAULT_DELAY_MS = 40
MOUSE_STEP_DELAY_MS = 8   # pause between split mouse reports

# key name (lower case) -> attribute name on adafruit_hid.keycode.Keycode
KEY_TABLE = {
    "esc": "ESCAPE", "escape": "ESCAPE",
    "enter": "ENTER", "return": "ENTER",
    "tab": "TAB",
    "backspace": "BACKSPACE", "bs": "BACKSPACE",
    "delete": "DELETE", "del": "DELETE",
    "insert": "INSERT", "ins": "INSERT",
    "home": "HOME", "end": "END",
    "pgup": "PAGE_UP", "pageup": "PAGE_UP",
    "pgdn": "PAGE_DOWN", "pagedown": "PAGE_DOWN",
    "up": "UP_ARROW", "down": "DOWN_ARROW",
    "left": "LEFT_ARROW", "right": "RIGHT_ARROW",
    "space": "SPACE",
    # +/- : the KEYPAD keys (work regardless of NumLock; Award/AMI "+/-")
    "+": "KEYPAD_PLUS", "plus": "KEYPAD_PLUS",
    "-": "KEYPAD_MINUS", "minus": "KEYPAD_MINUS",
    # main-row minus / equals
    "dash": "MINUS", "equals": "EQUALS", "=": "EQUALS",
    "capslock": "CAPS_LOCK", "numlock": "KEYPAD_NUMLOCK",
    "scrolllock": "SCROLL_LOCK", "printscreen": "PRINT_SCREEN",
    "pause": "PAUSE", "menu": "APPLICATION",
    # raw usages for non-US layouts (UK: # key, \ | key)
    "hash": "POUND",              # HID 0x32 (UK '#' key)
    "nonusbackslash": "KEYPAD_BACKSLASH",   # HID 0x64 (UK '\' key)
    "backslash": "BACKSLASH",     # HID 0x31 (US '\' key)
    "grave": "GRAVE_ACCENT",
}
for _i in range(1, 13):
    KEY_TABLE["f%d" % _i] = "F%d" % _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEY_TABLE[_c] = _c.upper()
for _d in "0123456789":
    KEY_TABLE[_d] = ("ZERO", "ONE", "TWO", "THREE", "FOUR",
                     "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")[int(_d)]

MODIFIER_TABLE = {
    "ctrl": "LEFT_CONTROL", "control": "LEFT_CONTROL",
    "alt": "LEFT_ALT",
    "shift": "LEFT_SHIFT",
    "gui": "LEFT_GUI", "win": "LEFT_GUI", "super": "LEFT_GUI",
}

BUTTONS = {"left": "LEFT_BUTTON", "right": "RIGHT_BUTTON", "middle": "MIDDLE_BUTTON"}


class CommandError(Exception):
    """Raised for a bad command; message becomes 'ERR <message>'."""


def token_ok(supplied, expected):
    """Constant-time (length-leaking only) token comparison."""
    if not expected or not supplied:
        return False
    a = supplied.encode() if isinstance(supplied, str) else bytes(supplied)
    b = expected.encode() if isinstance(expected, str) else bytes(expected)
    diff = len(a) ^ len(b)
    for i in range(len(b)):
        diff |= (a[i] if i < len(a) else 0) ^ b[i]
    return diff == 0


def parse_tokens(value):
    """KVM_TOKENS / KVM_TOKEN value -> list of tokens (comma-separated, one per
    controller PC, so each can be revoked alone). Blank entries are dropped."""
    if not value:
        return []
    return [t.strip() for t in str(value).split(",") if t.strip()]


def any_token_ok(supplied, tokens):
    """True if `supplied` matches one of `tokens`. Every token is compared
    (no early exit), so the time does not tell which one matched."""
    ok = False
    for t in tokens or []:
        if token_ok(supplied, t):
            ok = True
    return ok


def parse_line(line):
    """Split 'TOKEN cmd rest' -> (token, cmd, rest). Missing parts are ''.

    'rest' keeps inner whitespace (needed for `type`); only the single
    separating space is removed. Trailing CR/LF is stripped.
    """
    line = line.rstrip("\r\n")
    parts = line.split(" ", 1)
    token = parts[0]
    remainder = parts[1] if len(parts) > 1 else ""
    parts = remainder.split(" ", 1)
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    return token, cmd, rest


def unescape(text):
    r"""Protocol escapes for `type`: \n -> LF, \t -> TAB, \\ -> \ .
    Any other backslash sequence is kept literally (so C:\WINDOWS works,
    but C:\new needs C:\\new)."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "n":
                out.append("\n"); i += 2; continue
            if nxt == "t":
                out.append("\t"); i += 2; continue
            if nxt == "\\":
                out.append("\\"); i += 2; continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_move(dx, dy, limit=MAX_MOUSE_STEP):
    """Split a relative move into (sx, sy) steps, each axis within +-limit.
    The sum of the steps is exactly (dx, dy); diagonals stay proportional."""
    biggest = max(abs(dx), abs(dy))
    if biggest == 0:
        return []
    n = (biggest + limit - 1) // limit
    steps = []
    px = py = 0
    for i in range(1, n + 1):
        cx = _rdiv(dx * i, n)
        cy = _rdiv(dy * i, n)
        steps.append((cx - px, cy - py))
        px, py = cx, cy
    return steps


def _rdiv(a, b):
    """Round-half-away-from-zero integer division (b > 0)."""
    if a >= 0:
        return (2 * a + b) // (2 * b)
    return -((2 * -a + b) // (2 * b))


def parse_int(text, what):
    try:
        return int(text)
    except ValueError:
        raise CommandError("bad %s" % what)


HID_COMMANDS = ("type", "key", "combo", "hold", "release", "mouse", "click")


class Kvm:
    """Command interpreter. Inject hardware so tests can mock it.

    keyboard: adafruit_hid.keyboard.Keyboard-like (press/release/release_all)
    mouse:    adafruit_hid.mouse.Mouse-like (move/press/release/release_all)
    layout:   object with .keycodes(char) -> tuple of keycodes (ValueError if unknown)
    keycode:  class/namespace holding keycode ints (adafruit_hid.keycode.Keycode)
    mouse_cls: class/namespace with LEFT_BUTTON/RIGHT_BUTTON/MIDDLE_BUTTON
    sleep_ms: callable(ms) - must feed the watchdog on the Pico
    """

    def __init__(self, keyboard, mouse, layout, keycode, mouse_cls,
                 token, sleep_ms, uptime_s=None):
        self.kbd = keyboard
        self.mouse = mouse
        self.layout = layout
        self.kc = keycode
        self.mc = mouse_cls
        self.token = token
        self.sleep_ms = sleep_ms
        self.uptime_s = uptime_s
        self.delay = DEFAULT_DELAY_MS
        self.held = []          # key names currently held
        self.held_buttons = []  # mouse buttons held (reserved)
        self.updater = None     # Updater (network update), set by kvm_main.py
        self.reboot_pending = False
        self.usb_ready = None   # callable -> bool, set by kvm_main.py
        self.buttons = None     # {"power": pin, "reset": pin}, set by kvm_main.py when wired
        self.hid_factory = None     # callable -> (keyboard, mouse), for a keyboard not made at start
        self.layout_factory = None  # callable(keyboard) -> layout
        self.version = ""
        self.usb_info = None    # callable -> str (boot protocol, LEDs), set by kvm_main.py
        self.taps = 0           # key taps sent without error since start

    # ---- helpers -------------------------------------------------------
    def resolve_key(self, name):
        n = name.lower()
        attr = KEY_TABLE.get(n) or MODIFIER_TABLE.get(n)
        if attr is None:
            raise CommandError("unknown key %s" % name)
        try:
            return getattr(self.kc, attr)
        except AttributeError:
            raise CommandError("key not supported %s" % name)

    def release_all(self):
        """Never raises: used on disconnect and in error paths."""
        for obj in (self.kbd, self.mouse):
            try:
                obj.release_all()
            except Exception:
                pass
        self.held = []

    # ---- command dispatcher -------------------------------------------
    def handle_line(self, line):
        """Authenticate and run one line. Returns the one-line reply.
        With a wrong/missing token NOTHING is sent to the HID devices."""
        token, cmd, rest = parse_line(line)
        tokens = self.token if isinstance(self.token, (list, tuple)) else [self.token]
        if not any_token_ok(token, tokens):
            return "ERR auth"
        try:
            return self._run(cmd, rest)
        except CommandError as e:
            self.release_all()
            return "ERR %s" % e
        except Exception as e:  # any hardware/other failure: fail safe
            self.release_all()
            return "ERR internal %s" % type(e).__name__

    def _run(self, cmd, rest):
        if cmd == "":
            raise CommandError("empty command")
        fn = getattr(self, "cmd_" + cmd.replace("-", "_"), None)
        if fn is None or cmd.startswith("_"):
            raise CommandError("unknown command %s" % cmd)
        if cmd in HID_COMMANDS and self.usb_ready is not None and not self.usb_ready():
            raise CommandError("usb not ready (host not reading the keyboard)")
        if cmd in HID_COMMANDS and self.kbd is None:
            self._make_hid()
            if self.kbd is None:
                raise CommandError("usb not ready (keyboard pending)")
        return fn(rest.strip() if cmd != "type" else rest)

    def _tap(self, codes):
        self.kbd.press(*codes)
        self.sleep_ms(self.delay)
        self.kbd.release_all()
        self.sleep_ms(self.delay)
        self.taps += 1

    # ---- commands ------------------------------------------------------
    def cmd_type(self, text):
        text = unescape(text)
        if text == "":
            raise CommandError("no text")
        seqs = []
        for ch in text:  # validate everything first: all-or-nothing
            try:
                seqs.append(self.layout.keycodes(ch))
            except (ValueError, KeyError):
                raise CommandError("cannot type %r" % ch)
        for codes in seqs:
            self._tap(codes)
        return "OK typed %d" % len(seqs)

    def cmd_key(self, arg):
        if not arg or " " in arg:
            raise CommandError("usage: key <name>")
        self._tap((self.resolve_key(arg),))
        return "OK key %s" % arg.lower()

    def cmd_combo(self, arg):
        if not arg or " " in arg:
            raise CommandError("usage: combo mod+mod+key")
        names = arg.split("+")
        if len(names) < 2 or "" in names:
            raise CommandError("usage: combo mod+mod+key")
        # the last element is the key; earlier ones must be modifiers
        for m in names[:-1]:
            if m.lower() not in MODIFIER_TABLE:
                raise CommandError("not a modifier %s" % m)
        codes = [self.resolve_key(n) for n in names]
        if len(codes) > 6:
            raise CommandError("too many keys")
        self._tap(tuple(codes))
        return "OK combo %s" % arg.lower()

    def cmd_hold(self, arg):
        if not arg or " " in arg:
            raise CommandError("usage: hold <key>")
        self.kbd.press(self.resolve_key(arg))
        if arg.lower() not in self.held:
            self.held.append(arg.lower())
        return "OK hold %s" % arg.lower()

    def cmd_release(self, arg):
        if not arg or " " in arg:
            raise CommandError("usage: release <key>")
        self.kbd.release(self.resolve_key(arg))
        if arg.lower() in self.held:
            self.held.remove(arg.lower())
        return "OK release %s" % arg.lower()

    def cmd_release_all(self, arg):
        self.release_all()
        return "OK released"

    def cmd_mouse(self, arg):
        parts = arg.split()
        if self.mouse is None:
            raise CommandError("mouse disabled")
        if len(parts) != 2:
            raise CommandError("usage: mouse <dx> <dy>")
        dx = parse_int(parts[0], "dx")
        dy = parse_int(parts[1], "dy")
        if abs(dx) > MAX_MOUSE_TOTAL or abs(dy) > MAX_MOUSE_TOTAL:
            raise CommandError("move too large")
        steps = split_move(dx, dy)
        for i, (sx, sy) in enumerate(steps):
            self.mouse.move(sx, sy)
            if i < len(steps) - 1:
                self.sleep_ms(MOUSE_STEP_DELAY_MS)
        return "OK mouse %d %d steps=%d" % (dx, dy, len(steps))

    def cmd_click(self, arg):
        name = arg.lower()
        if self.mouse is None:
            raise CommandError("mouse disabled")
        if name not in BUTTONS:
            raise CommandError("usage: click left|right|middle")
        button = getattr(self.mc, BUTTONS[name])
        self.mouse.press(button)
        self.sleep_ms(self.delay)
        self.mouse.release_all()
        self.sleep_ms(self.delay)
        return "OK click %s" % name

    def cmd_delay(self, arg):
        if arg == "":
            return "OK delay %d" % self.delay
        ms = parse_int(arg, "ms")
        if ms < 0 or ms > MAX_DELAY_MS:
            raise CommandError("delay 0..%d" % MAX_DELAY_MS)
        self.delay = ms
        return "OK delay %d" % ms

    def cmd_status(self, arg):
        up = ""
        if self.uptime_s is not None:
            up = " uptime=%d" % self.uptime_s()
        ver = (" version=" + self.version) if self.version else ""
        if self.usb_ready is not None:
            ver += " usb=" + ("yes" if self.usb_ready() else "no")
        ver += " kbd=" + ("ok" if self.kbd is not None else "pending")
        ver += " mouse=" + ("yes" if self.mouse is not None else "no")
        ver += " taps=%d" % self.taps
        if self.usb_info is not None:   # boot protocol asked by the host, LED report last received
            try:
                ver += " " + self.usb_info()
            except Exception as e:
                ver += " usbinfo=%s" % type(e).__name__
        if getattr(self, "boot_info", ""):
            ver += " " + self.boot_info
        return "OK status delay=%d held=%s%s%s" % (
            self.delay, ",".join(self.held) or "-", up, ver)

    def _make_hid(self):
        if self.hid_factory is None:
            return
        kb, ms = self.hid_factory()
        if kb is not None:
            self.kbd = kb
            if self.layout_factory is not None:
                self.layout = self.layout_factory(kb)
        if self.mouse is None and ms is not None:
            self.mouse = ms

    def _upd(self):
        if self.updater is None:
            raise CommandError("update disabled")
        return self.updater

    def cmd_put_begin(self, arg):
        return self._upd().begin(arg)

    def cmd_put_data(self, arg):
        return self._upd().data(arg)

    def cmd_put_end(self, arg):
        return self._upd().end(arg)

    def cmd_mousemode(self, arg):
        """mousemode on|off (after the next reboot). on = /kvm_mouse_on, which boot.py lets win over settings.toml;
        off = /kvm_kbd_only (keyboard only, the default when no flag and no KVM_MOUSE = 1)."""
        upd = self._upd()
        fs = upd.fs
        on_flag, off_flag = upd.path("kvm_mouse_on"), upd.path("kvm_kbd_only")

        def put(p):
            f = fs.open(p, "wb")
            f.write(b"1")
            f.close()

        def rm(p):
            if fs.exists(p):
                fs.remove(p)
        if arg == "on":
            rm(off_flag)
            put(on_flag)
        elif arg == "off":
            rm(on_flag)
            put(off_flag)
        elif arg != "":
            raise CommandError("mousemode on|off")
        state = "on" if fs.exists(on_flag) else ("off" if fs.exists(off_flag) else "default")
        return "OK mousemode %s (now %s; reboot to apply)" % (arg or "?", state)

    # ---- front-panel buttons (wiring sheet docs/wiring-buttons.md) -------------------------------------
    # self.buttons: {"power": obj, "reset": obj} with .value = True (pressed) / False, set by kvm_main.py.
    def _press(self, which, ms):
        pin = (self.buttons or {}).get(which)
        if pin is None:
            raise CommandError("%s button not wired" % which)
        try:
            pin.value = True
            self.sleep_ms(ms)
        finally:
            pin.value = False
        return "OK %s %d ms" % (which, ms)

    def cmd_pcpower(self, arg):
        """pcpower press (0.5 s: on, or ACPI shutdown) | hold (6 s: force off)."""
        if arg in ("", "press"):
            return self._press("power", 500)
        if arg == "hold":
            return self._press("power", 6000)
        raise CommandError("pcpower press|hold")

    def cmd_pcreset(self, arg):
        if arg not in ("",):
            raise CommandError("pcreset takes no argument")
        return self._press("reset", 300)

    def cmd_reboot(self, arg):
        self.release_all()
        self.reboot_pending = True     # kvm_main.py resets after sending this reply
        return "OK reboot"


# ---- network update ------------------------------------------------------
# put-begin <name> <size> <crc32hex>, put-data <base64> (repeat), put-end, then reboot.
# Only UPDATABLE files; written to <name>.new, checked (size + CRC-32), then the old file becomes
# <name>.prev and <name>.new becomes <name>. code.py (the launcher) puts the .prev files back if the
# new program fails to start. boot.py may be sent (an error in it only means CircuitPython's default USB setup;
# the program still runs); settings.toml and code.py never.
UPDATABLE = ("kvm_main.py", "kvm_core.py", "safemode.py", "boot.py")
MAX_UPDATE = 64 * 1024


class Updater:
    """fs: object with open(path, mode), rename(a, b), remove(path), exists(path).
    crc32(data, crc) and a2b_base64(text) as in binascii."""

    def __init__(self, fs, crc32, a2b_base64, root="/"):
        self.fs, self.crc32, self.b64 = fs, crc32, a2b_base64
        self.root = root
        self.cur = None   # [name, size, crc, file, written, running_crc]

    def path(self, name):
        return self.root + name

    def abort(self):
        if self.cur:
            try:
                self.cur[3].close()
                self.fs.remove(self.path(self.cur[0] + ".new"))
            except Exception:
                pass
        self.cur = None

    def begin(self, arg):
        parts = arg.split()
        if len(parts) != 3:
            raise CommandError("put-begin <name> <size> <crc32hex>")
        name, size, crc = parts[0], parse_int(parts[1], "size"), parts[2].lower()
        if name not in UPDATABLE:
            raise CommandError("not updatable %s" % name)
        if not 0 < size <= MAX_UPDATE:
            raise CommandError("bad size")
        try:
            crc = int(crc, 16)
        except ValueError:
            raise CommandError("bad crc")
        self.abort()
        f = self.fs.open(self.path(name + ".new"), "wb")
        self.cur = [name, size, crc, f, 0, 0]
        return "OK put-begin %s %d" % (name, size)

    def data(self, arg):
        if not self.cur:
            raise CommandError("no put-begin")
        try:
            chunk = self.b64(arg.strip())
        except Exception:
            self.abort()
            raise CommandError("bad base64")
        c = self.cur
        if c[4] + len(chunk) > c[1]:
            self.abort()
            raise CommandError("too much data")
        c[3].write(chunk)
        c[4] += len(chunk)
        c[5] = self.crc32(chunk, c[5])
        return "OK put-data %d" % c[4]

    def end(self, arg):
        if not self.cur:
            raise CommandError("no put-begin")
        name, size, crc, f, written, got = self.cur
        f.close()
        new = self.path(name + ".new")
        if written != size or (got & 0xFFFFFFFF) != crc:
            self.cur = None
            self.fs.remove(new)
            raise CommandError("check failed %d/%d crc %08x" % (written, size, got & 0xFFFFFFFF))
        old, prev = self.path(name), self.path(name + ".prev")
        if self.fs.exists(prev):
            self.fs.remove(prev)
        if self.fs.exists(old):
            self.fs.rename(old, prev)
        self.fs.rename(new, old)
        self.cur = None
        return "OK put-end %s %d crc %08x (reboot to use it)" % (name, size, crc)
