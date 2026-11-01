# code.py - pico-usbkeyboard launcher 1.0.0. Never changed over the network: it starts kvm_main.py and,
# if that fails while starting (e.g. a bad network update), puts back the .prev files the update left and
# restarts the Pico once. Errors after start-up are handled inside kvm_main.py.
import os
import time
import microcontroller

FILES = ("kvm_main.py", "kvm_core.py")


def exists(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False


try:
    import kvm_main   # runs the server; returns only on an error during start-up
except Exception as e:
    print("kvm_main failed:", type(e).__name__, e)
    restored = []
    for f in FILES:
        if exists(f + ".prev"):
            try:
                if exists(f):
                    os.rename(f, f + ".bad")
                os.rename(f + ".prev", f)
                restored.append(f)
            except OSError as e2:
                print("restore", f, e2)
    if restored:
        print("restored", restored, "- restarting")
        time.sleep(1)
        microcontroller.reset()
    raise
