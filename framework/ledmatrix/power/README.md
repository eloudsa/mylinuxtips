# measure-ledmatrix-power.sh

Measure what the **Framework Laptop 16 LED Matrix** modules actually cost in battery power.

Nobody had published a figure for this, so the script measures it rather than estimating it. The
official documentation only gives a design ceiling — each Input Module may draw up to 500 mA on
the 5 V rail — which says nothing about a real display at real brightness.

**Short answer, measured on a Framework Laptop 16 (Ryzen AI 9 HX 370, Fedora 44):**
around **0.7 W**, or roughly **7 to 9 minutes** of runtime over a full charge. Under 4 % of idle
consumption.

---

## How it works

A laptop at idle draws 10–20 W with constant fluctuation, so you cannot simply read the battery
gauge with the display on and call it a day: the effect being measured is smaller than the noise.

The script does an **A/B comparison** instead:

1. **Phase A** — stops the matrix services, waits 30 s for the system to settle, takes 20 samples
2. **Phase B** — starts the service under test, waits 30 s, takes 20 samples again
3. Compares the **medians** of the two phases, and computes the **standard deviation** of phase A
   as a noise floor

If the difference is smaller than twice that noise floor, the script says so explicitly and
presents the result as an upper bound rather than a measurement. Medians rather than means,
because a single background task spike would drag a mean around.

Total run time is about 3 minutes 20.

## Requirements

- A Framework Laptop 16 with at least one LED Matrix module (the script refuses to run otherwise)
- Linux with `/sys/class/power_supply/` — i.e. any modern kernel
- A systemd **user** unit that drives the matrices, such as the one described in the
  [LED Matrix system monitor how-to](https://noratek.dev/howto/framework16-led-matrix-system-monitor-fedora/)
- `bash`, `awk`, `systemctl`, `udevadm` — all present on a stock install

No root, no dependencies to install.

## Usage

```bash
chmod +x measure-ledmatrix-power.sh
./measure-ledmatrix-power.sh
```

Optional arguments:

```bash
./measure-ledmatrix-power.sh [unit] [samples] [period]
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `unit` | `ledmatrix-labels-sysmon.service` | systemd `--user` unit to test |
| `samples` | `20` | samples per phase |
| `period` | `3` | seconds between samples |

Comparing two display styles, for instance:

```bash
./measure-ledmatrix-power.sh ledmatrix-sysmon.service
./measure-ledmatrix-power.sh ledmatrix-labels-sysmon.service
```

The script walks you through the prerequisites before it starts: it detects the modules, reports
what it found out about your battery, makes you unplug the charger, and asks you to leave the
machine alone for the duration.

## Sample output

```
This script measures how much battery power the LED Matrix modules draw,
by sampling battery discharge with the display off, then on.

LED Matrix modules detected: 2
Battery
  device          : BAT1
  model           : FRANDBA
  power reading   : current_x_voltage
  usable capacity : 87.8 Wh  [charge_full x voltage_min_design]
  design capacity : 85.0 Wh
  health          : 103 % of design
  cycle count     : 12

Discharging at 21.30 W. Before starting, please:

  - close as many applications as you can, and let the machine settle
  - do NOT change screen brightness during the run
  - do NOT plug in an external display, or any USB device
  - do NOT touch the keyboard or trackpad once the run starts

─────────────────────────────────────────────
  Matrices dark     :  21.26 W
  Matrices lit      :  22.05 W
  Cost of the display:  0.79 W  (3.7 %)
  Baseline noise (SD):  0.11 W
─────────────────────────────────────────────

Conclusion

  The 0.79 W difference is well clear of the 0.11 W baseline noise,
  so the measurement is sound.

  On this 88 Wh battery, at the measured idle draw:
    matrices dark : 4 h 08 min of runtime
    matrices lit  : 3 h 59 min of runtime
    difference    : about 9 min over a full charge
```

## Nothing is assumed about your battery

Battery reporting varies a lot between vendors, so the script discovers rather than guesses, and
shows you what it found — including which sysfs field each number came from, so an odd result can
be diagnosed instead of trusted.

**Pack discovery.** Batteries are enumerated by `type=Battery`, not by a `BAT*` name pattern:
firmwares call the pack `BAT0`, `BAT1`, `BATT`, `BATC`, `CMB0` and worse. Peripheral batteries
(mice, headsets, USB-C sources) declare `scope=Device` and are excluded. Machines with **two
packs** are handled by summing both, and each pack may use a different reading method.

**Instantaneous power**, in order of preference:

1. `power_now` — microwatts, straight from the firmware
2. `current_now × voltage_now` — for gauges that report charge rather than energy, as on
   Framework AMD models. The sign of `current_now` varies between EC implementations, so it is
   taken in absolute value.

**Usable capacity**, in order of preference:

1. `energy_full` — Wh directly
2. `charge_full × voltage_min_design` — Ah converted at *nominal* voltage
3. `charge_full × voltage_now` — last resort, and flagged as such

Step 3 matters: `voltage_now` is the instantaneous terminal voltage, typically 8–12 % above
nominal on a charged pack. Using it inflates capacity and every runtime figure derived from it.
When the script has to fall back to it, it warns you rather than quietly reporting an optimistic
number.

Battery **health** and **cycle count** are shown when available, since runtime figures mean
something different on a pack at 70 % of its design capacity.

**Sanity checks** run before the three-minute measurement rather than after: a gauge reading 0 W
while discharging (some embedded controllers refresh only every 30 s) or an implausible figure
above 200 W both abort with an explanation.

## Interpreting the result

**What the figure covers.** The number aggregates three things: the LEDs themselves, both RP2040
microcontrollers held awake by the refresh, and the host-side service waking the CPU on every
interval. That last one is not negligible — a wakeup every second keeps the CPU out of its deeper
C-states. To separate them, re-run with a longer refresh interval; if the cost drops, polling was
the larger share.

**Repeat the measurement.** Three runs on the same machine gave 0.60 W, 0.65 W and 0.79 W, with a
within-run noise of 0.11–0.15 W. The spread *between* runs is larger than the noise *within* a
run, so the standard deviation the script reports understates the true uncertainty. Two or three
runs give a much more honest answer than one.

**Known limitations.**

- sysfs units are assumed to follow the `power_supply` standard (microwatts, microamperes). A few
  older ACPI drivers report in milliamperes; the 200 W guard catches gross cases, but a factor of
  1000 in the other direction would look like a very frugal machine.
- The method measures a **difference in idle draw**. It says nothing about consumption under
  load, where the fans and SoC dominate anyway.
- LED Matrix modules are exclusive to the Framework Laptop 16. The measurement approach itself is
  reusable on any laptop, but the hardware check is not.

## License

Same as the rest of this repository.
