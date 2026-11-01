# safemode.py - pico-usbkeyboard 1.0.1. CircuitPython runs this INSTEAD of boot.py/code.py when it starts in
# safe mode. A reset of the PC the Pico is plugged into can dip the USB power: CircuitPython then starts in safe mode
# (brownout) with Wi-Fi up but no KVM program. Restart normally after a short pause (1 s, so the Pico shows up on USB
# before the BIOS has finished scanning). A user-requested safe mode is left alone.
import time
import microcontroller
import supervisor

reason = supervisor.runtime.safe_mode_reason
if reason not in (supervisor.SafeModeReason.USER, supervisor.SafeModeReason.PROGRAMMATIC):
    time.sleep(1)
    microcontroller.reset()
