# Optional: separate power for the Pico, and power/reset buttons through optocouplers

The firmware already has the commands `pcpower` and `pcreset` (GP16 and GP17 are driven low, "not pressed", from
start-up, which is harmless when nothing is connected); nothing happens on the PC until this is wired.
This is an optional add-on and is listed under "Future enhancements" in the README: it has been designed but not
yet proven on hardware.

## Why

- Cutting mains power to recover a hung PC is crude: many older BIOSes have no "power on after power loss" setting,
  Wake-on-LAN after a mains cut is unreliable, and a hard cut risks file-system damage.
- When the Pico draws its power from the target PC's USB port, every PC reset dips its supply: it restarts,
  reappears late on USB and can miss the BIOS's USB scan (and in the worst case confuse the BIOS's USB code).

With its own supply the Pico stays up through every reset and power-off, and with two optocouplers it can press the
case's power and reset buttons like a person: `kvm.py pcpower` (0.5 s press: power on, or ACPI shutdown),
`kvm.py pcpower hold` (6 s: force off), `kvm.py pcreset` (0.3 s).

## Parts

| Qty | Part | For |
|---|---|---|
| 1 | USB power supply (5 V, >= 0.5 A) + a cable you can cut, or a USB breakout | the Pico's own power |
| 1 | Schottky diode 1N5817 (or 1N5819) | feeds VSYS without back-feeding the supply or the PC |
| 2 | Optocoupler PC817 (DIP-4), or a ready-made 2-channel optocoupler board | power and reset "buttons" |
| 2 | Resistor 220-330 ohm | LED current of each PC817 (about 7-10 mA at 3.3 V) |
| - | Dupont jumper wires (female for the motherboard header pins) | |

## Pico W pins (USB socket at the top, looking at the components)

| Pico pin (physical) | Name | Connect to |
|---|---|---|
| 39 | VSYS | diode **cathode** (band); the diode **anode** to the supply's +5 V |
| 38 | GND | the supply's 0 V; also both PC817 pin 2 (cathode) |
| 21 | GP16 | 330 ohm -> PC817 #1 pin 1 (anode) = **power** button |
| 22 | GP17 | 330 ohm -> PC817 #2 pin 1 (anode) = **reset** button |
| 20 | GP15 | leave free: GP15 to GND while plugging in = maintenance mode |

The USB data cable to the target PC stays as it is. The Pico W has its own diode from VBUS to VSYS, so the two
supplies simply OR together: whichever is higher feeds the Pico, and neither feeds the other.

## Motherboard side (front-panel header)

Each PC817 output goes **in parallel with** the case button, which keeps working:

| PC817 | Pin 4 (collector) | Pin 3 (emitter) |
|---|---|---|
| #1 power | the power-switch pin that carries the voltage | the power-switch pin at ground |
| #2 reset | the reset pin that carries the voltage | the reset pin at ground |

**Find which pin is which with a multimeter, not from a diagram** (board manuals differ between revisions): with the PC
switched off but mains on, measure each power-switch pin to the chassis. The one at about 3-5 V is the "+" pin ->
PC817 pin 4. Do the same for the reset pins with the PC on. If the case cable is plugged onto the header, you can slip
the jumper wires onto the same pins from behind, or use a Y-split.

An optocoupler only conducts one way: if a "press" does nothing, swap pins 3 and 4 at the header.

## Test (no risk to the PC)

1. Pico on its own supply, no motherboard wires yet: `kvm.py status` should answer even while the PC is off.
2. Measure across PC817 #1 pins 3-4 (ohms): `kvm.py pcpower` -> it should read near 0 ohm for half a second.
3. Wire it, PC off: `kvm.py pcpower` -> the PC starts. `kvm.py pcreset` while it runs -> it resets.
