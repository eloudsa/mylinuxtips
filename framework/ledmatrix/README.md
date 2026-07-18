# ledmatrix-sysmon

Drive both **Framework Laptop 16 LED Matrix** input modules as a live system monitor on Linux.

```
  LEFT (cpu|mem)      RIGHT (temp|fan)
```

- **Left matrix** — CPU utilisation (left gauge), memory used (right gauge) — *is it working?*
- **Right matrix** — CPU temperature (left gauge), fan speed (right gauge) — *is it getting hot?*

Each matrix is 9 columns × 34 rows: two 4-column vertical gauges separated by a dim divider
column, drawn LED by LED in greyscale. A baseline row stays lit so the module never looks dead,
and a gauge switches to full brightness when it crosses its danger threshold.

Single file, no framework, `pyserial` as the only third-party dependency. It speaks the raw
matrix protocol (`StageGreyCol` + `DrawGreyColBuffer`) directly — `ledmatrixctl` is not needed at
runtime.

> **Full walkthrough:** [Drive both Framework Laptop 16 LED Matrix modules as a live system monitor on Fedora](https://noratek.dev/howto/framework16-led-matrix-system-monitor-fedora/)
> — udev rules for stable left/right symlinks, `cros_ec_hwmon` setup, systemd user service,
> troubleshooting and verification. Read it first; this README covers the script alone.

---

## Requirements

- Framework Laptop 16 with **two** LED Matrix input modules installed.
- Linux **6.11+** for `cros_ec_hwmon` (mainline fan/temperature readings from the EC).
- Python 3.9+ and `pyserial`.
- Two stable device symlinks, `/dev/ledmatrix-left` and `/dev/ledmatrix-right`.

That last point is the non-obvious one: both modules report the same USB serial number and the
`/dev/ttyACM*` ordering is not stable across reboots, so the sides must be pinned by physical USB
path (`ID_PATH`) in a udev rule. See the how-to for the exact rule.

The script will run with a single matrix or none at all only in `--preview` and `--list-sensors`
modes; the live loop opens both devices.

## Install

```bash
mkdir -p ~/.local/bin
curl -fsSL -o ~/.local/bin/ledmatrix-sysmon.py https://raw.githubusercontent.com/eloudsa/mylinuxtips/main/framework/ledmatrix/ledmatrix-sysmon.py
chmod +x ~/.local/bin/ledmatrix-sysmon.py
```

A dedicated virtualenv keeps the dependency out of your shell-managed Python (`mise`, `pyenv`,
…), which a systemd user service would not inherit anyway:

```bash
/usr/bin/python3 -m venv ~/.local/share/ledmatrix-venv
~/.local/share/ledmatrix-venv/bin/pip install pyserial
```

## Usage

```bash
ledmatrix-sysmon.py                  # live loop, Ctrl-C blanks both matrices
ledmatrix-sysmon.py --once           # push a single frame, then blank and exit
ledmatrix-sysmon.py --once --no-clear # push a single frame and leave it on screen
ledmatrix-sysmon.py --preview        # ASCII rendering in the terminal, no hardware needed
ledmatrix-sysmon.py --list-sensors   # dump every hwmon temperature and fan input
ledmatrix-sysmon.py --interval 0.5   # refresh period in seconds (default: 1.0)
```

`--list-sensors` is the calibration and diagnosis entry point: it tells you whether `cros_ec`
exposes `fan*_input`, whether `k10temp` is present, and what RPM your fans actually reach.

## Configuration

Everything is tunable at the top of the file — no config format, no CLI flag sprawl.

| Constant | Default | What it does |
| --- | --- | --- |
| `CPU_MODE` | `"util"` | `"util"` = % busy from `/proc/stat`; `"psi"` = contention from `/proc/pressure/cpu` |
| `MEM_MODE` | `"used"` | `"used"` = % RAM used; `"psi"` = memory pressure |
| `TEMP_MIN` / `TEMP_MAX` | `35.0` / `95.0` | °C range mapped onto the temperature gauge |
| `TEMP_DANGER` | `85.0` | °C above which the bar goes full brightness |
| `FAN_MAX_RPM` | `5300.0` | RPM treated as 100 % — **calibrate this per unit** |
| `PREFERRED_TEMP_HWMON` | `"k10temp"` | hwmon device to read CPU temperature from; falls back to the global max |
| `FILL_FROM_BOTTOM` | `True` | flip if your bars grow the wrong way |
| `BRIGHT_*` | 4–255 | baseline, divider, bar body, bar tip and danger brightness |
| `REFRESH_S` | `1.0` | default refresh period |

**PSI vs utilisation.** The defaults show *activity*, which keeps the gauges visibly alive.
Switching either left gauge to `"psi"` shows Linux pressure-stall information instead — a truer
saturation signal, but flat near zero most of the time. Pick based on whether you want a dashboard
or an alarm.

**Calibrating `FAN_MAX_RPM`** with `framework_tool`:

```bash
sudo framework_tool --fansetduty 100
sleep 5
sudo framework_tool --thermal
sudo framework_tool --autofanctrl
```

On the Ryzen AI 9 HX 370 model both fans top out around 5285 RPM.

## Running as a service

A systemd **user** unit, with the venv interpreter pinned explicitly:

```ini
[Unit]
Description=LED Matrix system monitor (Framework 16)
After=graphical-session.target
ConditionPathExists=/dev/ledmatrix-left

[Service]
ExecStart=%h/.local/share/ledmatrix-venv/bin/python %h/.local/bin/ledmatrix-sysmon.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

`ConditionPathExists` matters: expansion modules can be pulled out at any time, and without it an
enabled service fails and retries in a loop whenever a matrix is absent. With it, systemd skips
the start cleanly.

Installation, preset policy (`enable` vs `disable`) and the `ModuleNotFoundError: No module named
'serial'` trap are covered in the [how-to](https://noratek.dev/howto/framework16-led-matrix-system-monitor-fedora/).

## Notes

- Written and tested on Fedora 44, Framework Laptop 16 (Ryzen AI 9 HX 370 / Radeon 890M).
  Nothing is Fedora-specific beyond the package names.
- Frames are staged column by column and committed atomically, so the display never tears.
- The script resolves `/dev/ledmatrix-*` with `os.path.realpath()` before opening: pyserial only
  matches canonical device paths, and a symlink passed straight through is rejected.
- Reading fans and temperatures needs no root and no out-of-tree module — `cros_ec_hwmon` has
  been mainline since Linux 6.11, superseding the older `framework-laptop-kmod` DKMS route.

## References

- [inputmodule-rs](https://github.com/FrameworkComputer/inputmodule-rs) — LED Matrix firmware and protocol
- [framework16-inputmodule](https://pypi.org/project/framework16-inputmodule) — host-side `ledmatrixctl`
- [framework-system](https://github.com/FrameworkComputer/framework-system) — `framework_tool`
- [cros_ec_hwmon](https://www.kernel.org/doc/html/latest/hwmon/cros_ec_hwmon.html) — kernel driver documentation
