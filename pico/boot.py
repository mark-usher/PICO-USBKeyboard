# boot.py - runs once at power-up, before USB starts. CircuitPython 9.x.
# Makes the Pico W look to the target PC like a plain USB HID boot keyboard (optionally keyboard + mouse),
# with the boot-protocol flag set so a BIOS can use it.
import os
import board
import digitalio
import storage
import usb_cdc
import usb_hid
import usb_midi

# Maintenance jumper: hold GP15 to GND while plugging in to get the CIRCUITPY
# drive and serial console back (they are hidden in normal use so the target PC
# and its BIOS only see a keyboard).
maintenance = False
try:
    pin = digitalio.DigitalInOut(board.GP15)
    pin.switch_to_input(pull=digitalio.Pull.UP)
    maintenance = not pin.value
    pin.deinit()
except Exception:
    pass

usb_midi.disable()

def _exists(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False


# Keyboard-only mode is the DEFAULT. Mouse mode (keyboard + mouse) only with KVM_MOUSE = 1 in settings.toml, or when
# /kvm_mouse_on exists (set over the network with `mousemode on`); `mousemode off` writes /kvm_kbd_only.
#
# Keyboard-only uses the standard boot keyboard descriptor WITHOUT a report ID. CircuitPython's own KEYBOARD device
# puts report ID 1 in front of every report; an old Award BIOS enumerated it but ignored the keys. A BIOS reads the
# plain 8-byte boot report format, so that is what we send.
#
# Mouse mode uses CircuitPython's composite (KEYBOARD + MOUSE in one HID interface with report IDs). On an old Award
# BIOS this caused the Pico to be reset at each host boot. usb_hid cannot build two separate boot interfaces; a
# reliable keyboard + mouse for BIOSes would need TinyUSB firmware in C.
BOOT_KEYBOARD = bytes((
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,             # Generic Desktop, Keyboard, Collection (Application)
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,  # modifiers
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,             # reserved byte
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,  # 5 LEDs
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,             # LED padding
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x05, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x00, 0x81, 0x00,  # 6 keys
    0xC0,
))

# /kvm_mouse_on wins over everything (settings.toml cannot be changed over the network: it holds the Wi-Fi password
# and tokens); /kvm_kbd_only wins over KVM_MOUSE = 1.
_kbd_only = os.getenv("KVM_MOUSE", 0) not in (1, "1") or _exists("/kvm_kbd_only")
if _exists("/kvm_mouse_on"):
    _kbd_only = False
if _kbd_only:
    devices = (usb_hid.Device(report_descriptor=BOOT_KEYBOARD, usage_page=0x01, usage=0x06,
                              report_ids=(0,), in_report_lengths=(8,), out_report_lengths=(1,)),)
else:
    devices = (usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE)

usb_hid.enable(devices, boot_device=1)

if not maintenance:
    storage.disable_usb_drive()   # no mass storage on the target side
    usb_cdc.disable()             # no serial console / CDC (old hosts may ask for a driver)
    storage.remount("/", readonly=False)   # lets kvm_main.py take network updates (no host owns the drive)
