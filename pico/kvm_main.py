# kvm_main.py - pico-usbkeyboard: TCP line server -> USB HID keyboard (+ optional mouse), started by code.py (the
# launcher). CircuitPython 9.x on Raspberry Pi Pico W. Needs kvm_core.py next to it and adafruit_hid in lib/.
# Protocol: see the README.
import os
import time
import wifi
import socketpool
import microcontroller
import usb_hid
import binascii
from watchdog import WatchDogMode
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

import kvm_core

SSID = os.getenv("CIRCUITPY_WIFI_SSID")
PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD")
# One token per controller PC (KVM_TOKENS = "tokA,tokB"); KVM_TOKEN alone still works.
TOKENS = kvm_core.parse_tokens(os.getenv("KVM_TOKENS") or os.getenv("KVM_TOKEN"))
PORT = int(os.getenv("KVM_PORT", 8099))
IDLE_S = int(os.getenv("KVM_IDLE_S", 300))
EAGAIN_ERRNOS = (11, 116)  # EAGAIN, ETIMEDOUT: "no data yet" (not verified on hardware)

if not TOKENS or min(len(t) for t in TOKENS) < 12 or not SSID:
    print("KVM_TOKENS (each >=12 chars) and CIRCUITPY_WIFI_SSID must be set in settings.toml")
    while True:   # refuse to run unauthenticated; no watchdog yet so this just idles
        time.sleep(1)

# Watchdog: RP2040 max is ~8 s. Everything below must feed it.
wdt = microcontroller.watchdog
wdt.timeout = 8
wdt.mode = WatchDogMode.RESET


def sleep_ms(ms):
    end = time.monotonic() + ms / 1000
    while True:
        wdt.feed()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.1))


def connect():
    while not wifi.radio.connected:
        wdt.feed()
        try:
            wifi.radio.connect(SSID, PASSWORD, timeout=5)
        except Exception as e:
            print("wifi:", e)
            sleep_ms(1000)


def send_line(conn, text):
    data = (text + "\n").encode()
    while data:
        n = conn.send(data)
        data = data[n:]
        wdt.feed()


def make_hid(tries=3):
    """adafruit_hid raises OSError when the host has not set up USB yet (PC in POST or rebooting).
    Wait briefly for it, feeding the watchdog."""
    # Try only briefly, then start Wi-Fi anyway: waiting here for ever would leave the Pico unreachable while the
    # PC is off or a BIOS never sets it up. The keyboard is then made at the first key command
    # (kvm_core.Kvm.hid_factory).
    kb = None
    for _ in range(tries):
        try:
            kb = Keyboard(usb_hid.devices)
            break
        except OSError as e:          # host not reading yet
            print("usb not ready:", e)
            sleep_ms(1000)
        except Exception as e:        # no keyboard device (boot.py problem): run without one, stay reachable
            print("no keyboard:", e)
            break
    try:
        ms = Mouse(usb_hid.devices)
    except Exception:   # KVM_MOUSE = 0 -> no mouse device enabled in boot.py; or host not ready for it
        ms = None
    return kb, ms


keyboard, mouse = make_hid()

kvm = kvm_core.Kvm(keyboard, mouse, KeyboardLayoutUS(keyboard), Keycode, Mouse,
                   TOKENS, sleep_ms, uptime_s=lambda: int(time.monotonic()))
VERSION = "0.4.3"
kvm.version = VERSION
kvm.hid_factory = lambda: make_hid(1)
kvm.layout_factory = KeyboardLayoutUS
import supervisor
kvm.usb_ready = lambda: supervisor.runtime.usb_connected


def usb_info():
    # Does the host treat us as a keyboard? boot=1 when the host asked for the boot protocol (SET_PROTOCOL),
    # leds= the last LED report the host sent (a BIOS typically sets NumLock at POST).
    s = "boot=%s" % usb_hid.get_boot_device()
    led = None
    if kvm.kbd is not None:
        led = kvm.kbd.led_status
    s += " leds=" + ("-" if not led else binascii.hexlify(bytes(led)).decode())
    return s


kvm.usb_info = usb_info

# Front-panel buttons through optocouplers (docs/wiring-buttons.md): GP16 = power switch, GP17 = reset switch.
# Driven low (= not pressed) from the start; harmless when nothing is wired.
try:
    import board
    import digitalio
    _btn = {}
    for _name, _gp in (("power", board.GP16), ("reset", board.GP17)):
        _p = digitalio.DigitalInOut(_gp)
        _p.switch_to_output(value=False)
        _btn[_name] = _p
    kvm.buttons = _btn
except Exception as e:
    print("buttons:", e)
try:   # why the Pico last started: shows a safe-mode/brownout or watchdog restart in `status`
    kvm.boot_info = "reset=%s" % str(microcontroller.cpu.reset_reason).split(".")[-1]
    # which HID devices boot.py enabled (usage page/usage, report ids) and whether the keyboard-only flag exists
    try:
        _devs = "%d:" % len(usb_hid.devices) + ",".join("%x/%x" % (getattr(d, "usage_page", -1), getattr(d, "usage", -1)) for d in usb_hid.devices)
    except Exception as _e:
        _devs = "?" + type(_e).__name__
    try:
        os.stat("/kvm_kbd_only")
        _flag = "yes"
    except OSError:
        _flag = "no"
    kvm.boot_info += " hid=%s kbdonlyflag=%s KVM_MOUSE=%s" % (_devs, _flag, os.getenv("KVM_MOUSE"))
    # SOFTWARE after a PC reboot = safemode.py restarted us (or a `reboot`); POWER_ON = plugged in
except Exception:
    kvm.boot_info = ""


class PicoFs:
    open = staticmethod(open)
    rename = staticmethod(os.rename)
    remove = staticmethod(os.remove)

    @staticmethod
    def exists(path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False


# The filesystem is writable for code only when boot.py remounted it (normal mode, USB drive hidden).
try:
    with open("/.kvm_write_test", "w") as _f:
        _f.write("x")
    os.remove("/.kvm_write_test")
    kvm.updater = kvm_core.Updater(PicoFs, binascii.crc32, binascii.a2b_base64)
except OSError:
    print("filesystem read-only (maintenance mode?): network update off")

connect()
pool = socketpool.SocketPool(wifi.radio)
server = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
server.setsockopt(pool.SOL_SOCKET, pool.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", PORT))
server.listen(1)
server.setblocking(False)
print("listening on", wifi.radio.ipv4_address, PORT)

client = None
buf = bytearray()
last_rx = 0
chunk = bytearray(128)


def drop():
    global client, buf
    kvm.release_all()
    if kvm.updater is not None:
        kvm.updater.abort()      # a half-sent file is thrown away
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
    client = None
    buf = bytearray()


while True:
    wdt.feed()
    try:
        if not wifi.radio.connected:
            drop()
            connect()
            continue

        # New connection? Only one client at a time.
        try:
            conn, _addr = server.accept()
        except OSError as e:
            conn = None
            if e.errno not in EAGAIN_ERRNOS:
                raise
        if conn is not None:
            if client is None:
                client = conn
                client.setblocking(False)
                buf = bytearray()
                last_rx = time.monotonic()
            else:
                try:
                    send_line(conn, "ERR busy")
                    conn.close()
                except Exception:
                    pass

        if client is not None:
            try:
                n = client.recv_into(chunk)
            except OSError as e:
                if e.errno in EAGAIN_ERRNOS:
                    n = None
                else:
                    drop()
                    continue
            if n == 0:           # peer closed
                drop()
                continue
            if n:
                last_rx = time.monotonic()
                buf += chunk[:n]
                while b"\n" in buf:
                    i = buf.index(b"\n")
                    raw = bytes(buf[:i])
                    buf = buf[i + 1:]
                    try:
                        line = raw.decode("utf-8")
                    except UnicodeError:
                        send_line(client, "ERR encoding")
                        continue
                    reply = kvm.handle_line(line)
                    send_line(client, reply)
                    if kvm.reboot_pending:
                        drop()
                        sleep_ms(300)
                        microcontroller.reset()
                    if reply == "ERR auth":   # wrong/missing token: hang up
                        drop()
                        break
                if client is not None and len(buf) > kvm_core.MAX_LINE:
                    send_line(client, "ERR line too long")
                    drop()
            elif time.monotonic() - last_rx > IDLE_S:
                drop()
        time.sleep(0.005)
    except Exception as e:      # any failure: let go of every key and button
        print("error:", type(e).__name__, e)
        drop()
        time.sleep(0.2)
