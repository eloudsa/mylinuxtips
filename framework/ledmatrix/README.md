# ledmatrix-sysmon

Turn both **Framework Laptop 16 LED Matrix** input modules into a live system monitor on Linux,
driven by a configuration file.

Two display modes, switchable at any time without restarting anything:

| `mode = "gauge"` | `mode = "value"` |
| --- | --- |
| Vertical bars, 34 levels | Metric name above its current figure |
| Two bars side by side per matrix | Two blocks stacked per matrix |
| Good for *is it moving?* | Good for *what is the number?* |

Four metrics — **cpu**, **mem**, **temp**, **fan** — are assigned freely to four positions, and
the display can turn itself off on battery or below a charge threshold.

Single file, no framework, `pyserial` as the only third-party dependency. It speaks the raw matrix
protocol (`StageGreyCol` + `DrawGreyColBuffer`) directly — `ledmatrixctl` is not needed at runtime.

> **Full walkthrough:** [Drive both Framework Laptop 16 LED Matrix modules as a live system monitor on Fedora](https://noratek.dev/howto/framework16-led-matrix-system-monitor-fedora/)
> — udev rules for stable left/right symlinks, `cros_ec_hwmon` setup, systemd user service,
> troubleshooting. Read it first; this README covers the script and its configuration.

---

## Requirements

- Framework Laptop 16 with **two** LED Matrix input modules installed
- Linux **6.11+** for `cros_ec_hwmon` (mainline fan and temperature readings from the EC)
- Python **3.11+** for `tomllib` — configuration falls back to defaults on older versions
- `pyserial`
- Two stable device symlinks, `/dev/ledmatrix-left` and `/dev/ledmatrix-right`

That last point is the non-obvious one: both modules report the same USB serial number and the
`/dev/ttyACM*` ordering is not stable across reboots, so the sides must be pinned by physical USB
path (`ID_PATH`) in a udev rule. See the how-to for the exact rule.

## Install

```bash
mkdir -p ~/.local/bin ~/.config/ledmatrix-sysmon
curl -fsSL -o ~/.local/bin/ledmatrix-sysmon.py https://raw.githubusercontent.com/eloudsa/mylinuxtips/main/framework/ledmatrix/ledmatrix-sysmon.py
curl -fsSL -o ~/.local/bin/ledmatrix-config-check https://raw.githubusercontent.com/eloudsa/mylinuxtips/main/framework/ledmatrix/ledmatrix-config-check
chmod +x ~/.local/bin/ledmatrix-sysmon.py ~/.local/bin/ledmatrix-config-check
```

A dedicated virtualenv keeps the dependency out of your shell-managed Python (`mise`, `pyenv`, …),
which a systemd user service would not inherit anyway:

```bash
/usr/bin/python3 -m venv ~/.local/share/ledmatrix-venv
~/.local/share/ledmatrix-venv/bin/pip install pyserial
```

Then drop `config.toml.example` at `~/.config/ledmatrix-sysmon/config.toml` and edit to taste.
Running with no configuration file at all is perfectly valid — every setting has a default.

## Usage

```bash
ledmatrix-sysmon.py                 # run
ledmatrix-sysmon.py --check-config  # validate the configuration and report it
ledmatrix-sysmon.py --preview       # ASCII rendering in the terminal, no hardware needed
ledmatrix-sysmon.py --once          # push a single frame and exit
ledmatrix-sysmon.py --once --no-clear # push a single frame and leave it lit
ledmatrix-sysmon.py --list-sensors  # dump every hwmon temperature and fan input
ledmatrix-sysmon.py --config PATH   # use a configuration file elsewhere
```

`--list-sensors` is the calibration entry point: it tells you whether `cros_ec` exposes
`fan*_input`, whether `k10temp` is present, and what RPM your fans actually reach.

## Configuration

Lives at `$XDG_CONFIG_HOME/ledmatrix-sysmon/config.toml`, falling back to
`~/.config/ledmatrix-sysmon/config.toml`. See `config.toml.example` for a fully commented
template.

**It is re-read whenever its modification time changes.** Edit the file and the display follows
within one refresh interval — no restart, no `systemctl` call. Switching between gauge and value
mode is a one-line edit.

### Sections at a glance

```toml
[display]
enabled = true          # master switch; false blanks both matrices
mode = "gauge"          # "gauge" or "value"
interval = 1.0          # seconds between refreshes
gauge_labels = false    # vertical metric names above the bars
fill_from_bottom = true # flip if your bars grow the wrong way

[power]
policy = "always"       # "always" or "ac_only"
battery_off_below = 0   # blank below this charge percentage; 0 disables
resume_on_charge = true # false latches the display off until restart
lid_closed_off = true   # stop driving the matrices while the lid is shut

[layout]
slot1 = "cpu"           # cpu, mem, temp, fan, or none
slot2 = "mem"
slot3 = "temp"
slot4 = "fan"

[scale]                 # sensor ranges and danger thresholds
[brightness]            # 0-255 per display element
```

### Layout

Slot geometry depends on the mode, because the two modes arrange a matrix differently:

| | `gauge` mode | `value` mode |
| --- | --- | --- |
| `slot1` | left matrix, left bar | left matrix, top block |
| `slot2` | left matrix, right bar | left matrix, bottom block |
| `slot3` | right matrix, left bar | right matrix, top block |
| `slot4` | right matrix, right bar | right matrix, bottom block |

Gauges sit side by side rather than stacked so each bar keeps all 34 rows. Stacking them would
halve the resolution to 15 levels, which is a poor trade for a metric you read at a glance.

Set a slot to `"none"` to leave that position dark. No metric may occupy two slots — if one does,
the whole arrangement reverts to the default rather than silently dropping one of them.

### Power policy

`policy = "ac_only"` blanks the matrices whenever the machine runs on battery.

`battery_off_below = 20` blanks them below 20 % charge. `resume_on_charge = true` lights them back
up once the threshold is cleared or the charger is plugged in; `false` latches the display off
until the service restarts, which is what "stay off" actually has to mean — otherwise a 1 % blip
would turn everything back on.

When the display goes dark, one blank frame is sent and the script stops writing to the serial
ports. The modules then fall asleep on their own after 60 s, which is exactly the behaviour you
want for saving power.

If no mains supply can be found at all, `ac_only` errs on the side of keeping the display lit.

### Closed lid

`lid_closed_off = true` (the default) stops driving the matrices while the lid is shut, based on
`/proc/acpi/button/lid/*/state`.

This is not cosmetic. The Framework 16 pulls the modules' `SLEEP#` pin low whenever the lid is
closed, and the firmware treats that as a standing instruction: while the pin is asserted the
module sleeps and the LED controller is powered down, whatever the host sends. But any command
still wakes the device — so a monitor pushing a frame every second wakes the LED controller
continuously, only for it to sleep again, and you never see any of it because the display is
behind a closed lid.

In clamshell mode with an external display, this is pure waste. Set it to `false` only if you have
a reason to keep writing regardless.

When the lid state cannot be determined, the display stays lit rather than blanking on a guess.

### Value mode has no decimals

The matrix is 9 pixels wide. A 3×5 font fits exactly three characters per line, and `100` already
uses all nine columns. A decimal point plus one digit would need roughly 13 pixels. Values are
therefore rounded to integers — a physical limit, not a design choice.

### Nothing is fatal

A missing file, an unparseable file, an unknown section, a misspelled key, a value of the wrong
type, a number out of range, or an inconsistent combination — each falls back to its default and
is reported on stderr. **The monitor never refuses to start because of its configuration.**

Cross-field consistency is checked too: `temp_min` below `temp_max`, `temp_danger` inside that
range, no duplicate metric across slots.

## Validating the configuration

```bash
ledmatrix-config-check                 # the default configuration file
ledmatrix-config-check /path/to/config.toml
```

It prints every problem found, then the *effective* settings with overridden values marked, then
the resolved layout with its physical positions, then the current power state and whether the
display would be lit right now.

Exit status is `0` when clean, `1` when problems were found, `2` when the monitor script could not
be located. The wrapper is thin on purpose — the validation logic lives in the monitor itself,
since two implementations would drift apart the first time a setting is added.

Sample output:

```
Configuration file: /home/user/.config/ledmatrix-sysmon/config.toml

Problems found (2), each falling back to the default:
  - display.mode: expected one of gauge, value, got 'gauges'
  - layout: cpu assigned to more than one slot — using the default arrangement

Layout in gauge mode:
  slot1 -> cpu   (left matrix, left bar)
  slot2 -> mem   (left matrix, right bar)
  slot3 -> temp  (right matrix, left bar)
  slot4 -> fan   (right matrix, right bar)

Current power state: on AC, battery at 90 %
Display would currently be: lit
```

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
TimeoutStopSec=5

[Install]
WantedBy=default.target
```

`ConditionPathExists` matters: expansion modules can be pulled out at any time, and without it an
enabled service fails and retries in a loop whenever a matrix is absent. With it, systemd skips
the start cleanly.

`TimeoutStopSec` is a safety net rather than a necessity. Serial writes are bounded by
`write_timeout`, so the process always notices SIGTERM — but 5 s is a saner ceiling than the 45 s
the user manager applies by default, should anything ever block unexpectedly.

No command-line options belong in the unit — everything is in the configuration file, which can be
changed while the service runs.

## Calibration

Set `scale.fan_max_rpm` to your unit's real maximum. Force the fans to full duty, read the RPM,
then return to automatic control:

```bash
sudo framework_tool --fansetduty 100
sleep 5
sudo framework_tool --thermal
sudo framework_tool --autofanctrl
```

On the Ryzen AI 9 HX 370 model both fans top out around 5285 RPM, so `5300.0` is the right value.

Use `--list-sensors` to confirm which hwmon device holds your CPU temperature and set
`scale.preferred_temp_hwmon` accordingly (`k10temp` on AMD, `coretemp` on Intel).

## Power consumption

Measured on a Framework Laptop 16 (Ryzen AI 9 HX 370, Fedora 44): the display costs about
**0.7 W**, roughly **7 to 9 minutes** of runtime over a full charge, under 4 % of idle
consumption. Three runs gave 0.60 W, 0.65 W and 0.79 W.

The figure covers the LEDs, both RP2040 microcontrollers held awake by the refresh, and the host
service waking the CPU on every interval. Raising `display.interval` reduces the last of those —
5 s is plenty in value mode.

Measure it on your own machine with
[`measure-ledmatrix-power.sh`](../power/measure-ledmatrix-power.sh).

## Notes

- Written and tested on Fedora 44, Framework Laptop 16 (Ryzen AI 9 HX 370 / Radeon 890M). Nothing
  is Fedora-specific beyond the package names.
- Frames are staged column by column and committed atomically, so the display never tears.
- Serial writes are bounded by `write_timeout` and their failures absorbed: a sleeping module
  whose USB buffer has stopped draining would otherwise block the process indefinitely, leaving it
  unable to notice SIGTERM. A dropped frame is harmless — the next one is one interval away.
- The script resolves `/dev/ledmatrix-*` with `os.path.realpath()` before opening: pyserial only
  matches canonical device paths, and a symlink passed straight through is rejected.
- `pyserial` is imported lazily, so `--check-config`, `--list-sensors` and `--preview` work on a
  machine without it.
- Reading fans and temperatures needs no root and no out-of-tree module — `cros_ec_hwmon` has been
  mainline since Linux 6.11, superseding the older `framework-laptop-kmod` DKMS route.

## References

- [inputmodule-rs](https://github.com/FrameworkComputer/inputmodule-rs) — LED Matrix firmware and protocol
- [InputModules](https://github.com/FrameworkComputer/inputmodules) — hardware reference designs and power budget
- [framework16-inputmodule](https://pypi.org/project/framework16-inputmodule) — host-side `ledmatrixctl`
- [framework-system](https://github.com/FrameworkComputer/framework-system) — `framework_tool`
- [cros_ec_hwmon](https://www.kernel.org/doc/html/latest/hwmon/cros_ec_hwmon.html) — kernel driver documentation
