# pico-usbkeyboard

A Raspberry Pi Pico W running CircuitPython that acts as a **USB keyboard** for a target PC and is controlled over
Wi-Fi with a small token-authenticated TCP protocol. An AI agent such as Claude Code, a script or a person can type
into the target PC remotely, **including in BIOS setup and boot menus**, where software remote-desktop tools cannot
reach. A cheap USB HDMI/VGA capture stick on the controller PC provides the screen side, so together they form a
simple remote KVM.

```
controller PC (client/kvm.py) --TCP over the LAN--> Pico W (CircuitPython) --USB HID keyboard--> target PC
controller PC (client/grab.ps1) <--USB-- capture stick <--HDMI/VGA-- target PC's video output
```

Tested with an old Award BIOS PC and Linux, on CircuitPython 9.2.9.

## Contents

| Path | What | Goes where |
|---|---|---|
| `pico/code.py` | launcher: starts `kvm_main.py`, rolls back a failed network update | root of `CIRCUITPY` |
| `pico/kvm_main.py` | Wi-Fi, TCP server, USB HID glue | root of `CIRCUITPY` |
| `pico/kvm_core.py` | hardware-independent command interpreter and updater (unit-tested) | root of `CIRCUITPY` |
| `pico/boot.py` | USB setup (boot keyboard), hides the drive and serial console | root of `CIRCUITPY`, copied **last** |
| `pico/safemode.py` | restarts normally after a brownout safe mode | root of `CIRCUITPY` |
| `pico/settings.toml.example` | template for `CIRCUITPY/settings.toml` | edit the copy **on the Pico only** |
| `client/kvm.py` | command-line client, Python 3 standard library only | controller PC |
| `client/enter-bios.kvm.example` | example key script | controller PC |
| `client/grab.ps1`, `client/watch.ps1` | screenshots from a capture stick, and a live viewer (Windows, ffmpeg) | controller PC |
| `setup/setup-pico.ps1` | guided first install over USB (Windows, PowerShell 7) | controller PC |
| `setup/set-token.ps1` | makes a random token and stores it in `PICO_KVM_TOKEN` (Windows) | controller PC |
| `docs/wiring-buttons.md` | optional power/reset button wiring through optocouplers | |
| `tests/` | unit tests, no hardware needed | any PC with Python 3 |

## Hardware

- **Raspberry Pi Pico W** (RP2040 with Wi-Fi; the plain Pico has no Wi-Fi) and a good, short micro-USB cable to a USB
  port on the target PC. USB 1.1 is enough; the Pico is a full-speed device.
- Optional: a jumper wire or push button between **GP15 and GND** for maintenance mode (see below).
- Optional, for the screen: a **USB HDMI (or VGA-to-HDMI) capture stick** (UVC, e.g. MS2109-based) on the controller
  PC, and an HDMI/VGA adapter for the target's video output.
- A controller PC on the same LAN with Python 3 (any OS). The setup and capture scripts are PowerShell 7 for Windows;
  on Linux/macOS do the same steps by hand and use ffmpeg with `v4l2`/`avfoundation` for the capture.
- Wi-Fi: the Pico W is 2.4 GHz only.

## 1. Flash CircuitPython and add adafruit_hid

1. Download **CircuitPython 9.x for Raspberry Pi Pico W** from <https://circuitpython.org/board/raspberry_pi_pico_w/>
   (tested with 9.2.9; use the Pico **W** build).
2. Hold **BOOTSEL**, plug the Pico into the controller PC, release. A drive `RPI-RP2` appears; copy the `.uf2` onto
   it. The Pico restarts as a drive called `CIRCUITPY`.
3. Download the matching *Adafruit CircuitPython Library Bundle* (9.x, mpy) from
   <https://circuitpython.org/libraries> and copy its folder `lib/adafruit_hid` to `CIRCUITPY/lib/`. Nothing else is
   needed: `wifi`, `socketpool`, `usb_hid`, `microcontroller`, `watchdog` are built in.

On Windows, `setup/setup-pico.ps1` does steps 2-3 and section 3 for you (pinned downloads with SHA-256 checks, dry run
by default, `-Apply` to do it; run it again after each stage).

## 2. settings.toml

Create `CIRCUITPY/settings.toml` **on the Pico** from `pico/settings.toml.example`:

| Key | Meaning |
|---|---|
| `CIRCUITPY_WIFI_SSID`, `CIRCUITPY_WIFI_PASSWORD` | your 2.4 GHz Wi-Fi |
| `KVM_TOKENS` | one or more tokens, comma-separated, no spaces, **each at least 12 characters** (the firmware refuses to start otherwise). Use one token per controller so you can revoke one alone. `KVM_TOKEN` (single) also works. |
| `KVM_PORT` | TCP port, default `8099` |
| `KVM_MOUSE` | optional: `1` = keyboard + mouse (see limitations); default `0` = keyboard only |
| `KVM_IDLE_S` | optional: drop an idle client after this many seconds, default `300` |

Generate a token with `setup/set-token.ps1` (Windows) or
`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`. Leave `CIRCUITPY_WEB_API_PASSWORD` **unset**: that
keeps CircuitPython's Wi-Fi web workflow (file and REPL access over the network) switched off. The real
`settings.toml` never goes into git (`.gitignore` covers it).

## 3. First install over USB

1. Copy `kvm_core.py`, `kvm_main.py`, `safemode.py` and `code.py` to `CIRCUITPY`.
2. Write `settings.toml` (section 2).
3. Copy `boot.py` **last**. From the next plug-in, `boot.py` hides the `CIRCUITPY` drive and the serial console, so the
   target PC and its BIOS only see a keyboard (no mass-storage or serial-port driver prompts on old systems), and makes
   the file system writable for the firmware so it can take network updates.
4. Unplug, plug the Pico into the target PC (it is powered from there), find its address in your router's DHCP client
   list (a DHCP reservation keeps it fixed) and check with `kvm.py --host <pico-address> status`.

**Maintenance mode:** hold **GP15 to GND while plugging in** to get the `CIRCUITPY` drive and serial console back
(network updates are then off, because the host owns the drive). If you lock yourself out completely: BOOTSEL, flash
`flash_nuke.uf2`, then reflash CircuitPython.

## 4. Network updates with rollback

```
python client/kvm.py --host <pico-address> push pico/kvm_main.py pico/kvm_core.py
```

`push` sends each file in base64 chunks (`put-begin <name> <size> <crc32>`, `put-data ...`, `put-end`). The Pico writes
`<name>.new`, checks size and CRC-32, keeps the old file as `<name>.prev`, swaps the new one in and then reboots
(`--no-reboot` to skip). If the new `kvm_main.py`/`kvm_core.py` fails while starting, `code.py` (the launcher, never
updated over the network) restores the `.prev` files and restarts once. Only `kvm_main.py`, `kvm_core.py`,
`safemode.py` and `boot.py` can be sent; `settings.toml` and `code.py` never (change those in maintenance mode). An
error in `boot.py` only means CircuitPython falls back to its default USB setup: the program still runs and can be
fixed with another push.

## 5. Protocol

TCP, one line per command, `\n` terminated: `<TOKEN> <command> [args]`. The reply is one line, `OK ...` or `ERR ...`.
A plain line protocol rather than HTTP keeps the Pico's RAM use small, keeps one persistent low-latency connection for
key sequences, and lets any language (or `nc`) drive it. It has no TLS (see security).

| Command | Meaning |
|---|---|
| `type <text>` | types text with the **US layout**. Escapes: `\n` Enter, `\t` Tab, `\\` backslash; any other `\x` stays literal (`C:\WINDOWS` works, write `C:\\new`). All or nothing: an untypable character gives `ERR` and nothing is typed. |
| `key <name>` | taps one key: `esc enter tab backspace delete`/`del` `insert`/`ins` `home end pgup pgdn up down left right space + - f1`..`f12 a`..`z 0`..`9 capslock numlock scrolllock printscreen pause menu dash equals grave backslash hash nonusbackslash` |
| `combo <mod+mod+key>` | e.g. `combo ctrl+alt+del`; modifiers `ctrl alt shift gui` (`win`, `super`) |
| `hold <key>` / `release <key>` / `release-all` | keep a key down (e.g. `hold shift`) / let it go / let everything go |
| `delay [ms]` | key press and gap time, default 40, range 0-5000; alone it reads the current value |
| `mouse <dx> <dy>` | relative move, split into steps of at most 127 (max 32767 per axis per command); mouse mode only |
| `click left\|right\|middle` | press and release; mouse mode only |
| `status` | e.g. `OK status delay=40 held=- uptime=123 version=0.4.3 usb=yes kbd=ok mouse=no taps=12 boot=1 leds=01 reset=POWER_ON ...` |
| `mousemode on\|off` | keyboard + mouse or keyboard only, from the next reboot (writes a flag file that wins over `KVM_MOUSE`) |
| `put-begin`, `put-data`, `put-end` | network update (use `kvm.py push`) |
| `reboot` | restarts the Pico |
| `pcpower [press\|hold]`, `pcreset` | front-panel buttons, only if wired (`docs/wiring-buttons.md`) |

`+` and `-` are the **keypad** plus and minus (what Award/AMI BIOS setup uses to change values, independent of
NumLock); `dash` and `equals` are the main-row keys.

In `status`, `boot=1` means the host asked for the boot protocol, `leds=` is the last LED report the host sent (a BIOS
usually sets NumLock during POST): both are a quick way to see whether the BIOS has picked up the keyboard.

Safety behaviour: a wrong or missing token gives `ERR auth`, sends nothing to USB and closes the connection. All keys
are released on disconnect, idle timeout, any error and any bad command. One client at a time: a second gets
`ERR busy`. A hardware watchdog (8 s) resets the board if the main loop hangs. HID commands while the host is not
reading USB (PC off, in POST) answer `ERR usb not ready` at once.

## 6. Client

```powershell
# Windows: setup\set-token.ps1 stores the token in the user variable PICO_KVM_TOKEN
$env:PICO_KVM_HOST = "<pico-address>"
python client\kvm.py status
python client\kvm.py combo ctrl+alt+del
python client\kvm.py type "dir\n"
python client\kvm.py key f2
python client\kvm.py script client\enter-bios.kvm.example
```

```sh
# Linux/macOS
export PICO_KVM_TOKEN=...      # from a secrets manager or your shell profile, not the command line
python3 client/kvm.py --host <pico-address> status
```

- The token is read **only** from the environment variable `PICO_KVM_TOKEN` (there is no `--token` argument, so it
  never lands in shell history or process lists) and is never printed.
- `--host`, `--port` and `--layout` can also come from `PICO_KVM_HOST`, `PICO_KVM_PORT`, `PICO_KVM_LAYOUT`.
- Exit codes: 0 OK, 1 the Pico answered `ERR`, 2 usage/configuration, 3 connection error.
- **Script files:** one command per line without the token, `#` comments and blank lines ignored, plus client-side
  directives `sleep <ms>`, `repeat <count> <interval_ms> <command>` and `layout us|uk`. A script stops at the first
  `ERR` unless `--keep-going`. To enter BIOS setup, start the script, *then* power on or reset the PC.

## 7. Keyboard layouts (US and UK)

The Pico always sends **US key positions**. BIOS setup screens and boot menus read them as US, so the default
`--layout us` is right there. If the target **OS** uses a UK layout, characters on the keys that differ come out wrong
(`"` and `@` swapped, `#` gives `£`, `\` gives `#`, `|` and `~` differ). With `--layout uk` (or `layout uk` in a script,
or `PICO_KVM_LAYOUT=uk`) the client translates each character to the key that produces it on a UK layout, using the
raw keys `hash` (HID 0x32) and `nonusbackslash` (HID 0x64). Other layouts are not supported by `type`; use `key` and
`combo` for single keys.

## 8. Screen capture

`client/grab.ps1` (Windows, PowerShell 7, ffmpeg on PATH) reads a UVC capture stick through DirectShow:

```powershell
pwsh -File client\grab.ps1 -List                     # device names and video modes
pwsh -File client\grab.ps1                           # one PNG into .\shots, prints its path
pwsh -File client\grab.ps1 -Device "<name>" -Size 1280x720 -OutDir .\shots
pwsh -File client\grab.ps1 -Every 1 -View            # a PNG every second plus a live window
pwsh -File client\watch.ps1 -Dir .\shots             # viewer that follows the newest PNG
```

Only one program can open the stick at a time (close OBS first); `-Every -View` feeds both a live window and the
screenshots from one ffmpeg. The newest `-Keep` screenshots are kept (default 20, at least 90 s worth with `-Every`).
On Linux, the equivalent is e.g. `ffmpeg -f v4l2 -i /dev/video0 -frames:v 1 shot.png`.

## 9. Security

A network-controlled keyboard is remote code execution on the PC it is plugged into. Treat it that way.

- **Token:** long and random (12 characters minimum, 32 recommended), one per controller, kept out of git, chats and
  shell history (use the environment variable). Wrong token = disconnect; there is no rate limit, so length is the
  defence.
- **No TLS:** the token travels in clear text and can be sniffed on the LAN. **LAN only: never port-forward** the
  port. Put the Pico on a trusted network or VLAN.
- **One client at a time**, idle clients are dropped, keys are released on every error.
- `settings.toml` (Wi-Fi password and tokens) lives only on the Pico and cannot be read or written over the network,
  but anyone with physical access (GP15 jumper) can read it.
- Leave `CIRCUITPY_WEB_API_PASSWORD` unset. Unplug the Pico when it is not needed.

## 10. Using it from Claude Code

Claude Code can drive the target PC with the client and look at the result with the capture script:

1. Store the token in `PICO_KVM_TOKEN` and the address in `PICO_KVM_HOST` in the environment Claude Code starts from,
   so neither appears in commands or transcripts.
2. Allow the client without a prompt for each key, e.g. in the project's `.claude/settings.json`:

   ```json
   {
     "permissions": {
       "allow": [
         "Bash(python3 client/kvm.py *)",
         "Bash(pwsh -File client/grab.ps1 *)"
       ]
     }
   }
   ```

   (On Windows with the PowerShell tool, the equivalent is `PowerShell(python client\kvm.py *)`. Keep the rule as
   narrow as this: it lets Claude type anything on the target PC, so only allow it for a target you are happy for it
   to control.)
3. Give Claude a loop: send keys, grab a screenshot, read it, decide the next keys. Prefer key scripts for fixed
   sequences, for example `bios-boot-order.kvm`:

   ```
   # Start this, then reset the PC. Award BIOS: enter setup, open the boot features page.
   layout us
   delay 80
   repeat 60 150 key del
   sleep 3000
   key down
   sleep 1000
   key enter
   ```

   `python3 client/kvm.py script bios-boot-order.kvm`, then `grab.ps1` to check where it landed.
4. Tell Claude in your project instructions which PC the keyboard is plugged into, which layout the OS uses, and which
   actions need your confirmation (for example anything in BIOS setup that saves changes).

## 11. Tests

```
python -m unittest discover -s tests -v
```

No hardware needed: the tests cover the parser, key table, mouse splitting, token checks, release-all on errors (mocked
HID), network update and rollback files, button commands, the UK layout translation and the client against a loopback
fake server. One test checks every key name against the real `adafruit_hid` if it is installed
(`pip install adafruit-circuitpython-hid`). `boot.py`, `code.py`, `kvm_main.py` and `safemode.py` need the board and are
only compile-checked (`python -m py_compile pico/*.py`).

## 12. Known limitations and design notes

- **Keyboard-only is the default** (the standard boot keyboard descriptor, no report ID), because that is what an old
  BIOS's USB legacy keyboard support handles. CircuitPython's own keyboard device puts a report ID in front of each
  report, which such a BIOS enumerates but ignores.
- **Mouse mode** (`KVM_MOUSE = 1` or `mousemode on`) uses CircuitPython's composite keyboard + mouse: one HID interface
  with report IDs. On an old Award BIOS it caused the Pico to be reset at each host boot. CircuitPython's `usb_hid`
  cannot build two separate boot interfaces, so a reliable keyboard + mouse for BIOSes would need TinyUSB firmware in C.
  The mouse is relative only (no "move to x,y"; pointer acceleration makes distances unpredictable), with no wheel or
  drag.
- Some BIOSes ship with **"USB Keyboard Support"** (or "USB Legacy Support") **disabled by "Load Defaults"**: enable it,
  or the keyboard works only once an OS has loaded. You may need a physical keyboard once to change it.
- **Key gaps of about 1 s are safest in BIOS setup**: some setup screens drop keys that arrive faster (use `sleep 1000`
  between keys in scripts, or `delay 100`-`300`).
- When powered from the target's USB port, a PC reset can brown out the Pico; `safemode.py` restarts it, but it can
  reappear on USB late. A separate supply (`docs/wiring-buttons.md`) avoids this.
- `type` is US positions, ASCII only, 2 x `delay` ms per character; at most 6 keys at once; no media keys.
- One client, plain TCP, 2.4 GHz Wi-Fi only.

## 13. Future enhancements

- **PS/2 keyboard and mouse output** from the Pico (emulating the PS/2 protocol on GPIOs with level shifting), for
  PCs whose BIOS has no usable USB keyboard support. Note that a passive USB-to-PS/2 adapter does **not** work with the
  Pico: those only work with keyboards that speak PS/2 themselves over the USB connector.
- **Power and reset button control** through optocouplers, with the Pico on its own supply: the commands exist, the
  wiring is described in `docs/wiring-buttons.md`.
- **TinyUSB firmware in C** with separate keyboard and mouse boot interfaces, so mouse support works in BIOSes too.

## License

MIT, see `LICENSE`.
