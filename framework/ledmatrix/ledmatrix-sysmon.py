#!/usr/bin/env python3
"""
ledmatrix-sysmon.py — System monitor for the two Framework Laptop 16 LED Matrix
input modules, driven by a configuration file.

Two display modes:

  gauge   vertical bars, 34 levels, two side by side per matrix
  value   metric name and current figure in 3x5 digits, two stacked per matrix

Four metrics — cpu, mem, temp, fan — are assigned to four slots:

           gauge mode              value mode
  slot 1   left matrix, left bar   left matrix, top block
  slot 2   left matrix, right bar  left matrix, bottom block
  slot 3   right matrix, left bar  right matrix, top block
  slot 4   right matrix, right bar right matrix, bottom block

Configuration lives at $XDG_CONFIG_HOME/ledmatrix-sysmon/config.toml, falling
back to ~/.config/ledmatrix-sysmon/config.toml. It is re-read whenever its
modification time changes, so edits take effect without restarting the service.

A missing file, an unparseable file, or any individual setting that is missing,
misspelled, out of range or inconsistent falls back to its default. The script
never refuses to run because of its configuration.

Dependencies: pyserial. Standard library otherwise.

  ledmatrix-sysmon.py                 run
  ledmatrix-sysmon.py --check-config  validate the configuration and exit
  ledmatrix-sysmon.py --preview       ASCII rendering, no hardware needed
  ledmatrix-sysmon.py --once          push a single frame and exit
  ledmatrix-sysmon.py --list-sensors  dump detected hwmon inputs
"""

import argparse
import glob
import os
import signal
import sys
import time

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

# pyserial is only needed to talk to the hardware. Importing it lazily keeps
# --check-config, --list-sensors and --preview usable without it.
serial = None


def require_serial():
    global serial
    if serial is None:
        import serial as _serial
        serial = _serial
    return serial

# ───────────────────────── Hardware constants ─────────────────────────

LEFT_DEV = "/dev/ledmatrix-left"
RIGHT_DEV = "/dev/ledmatrix-right"

WIDTH = 9
HEIGHT = 34
GAUGE_W = 4
BAUD = 115200

METRICS = ("cpu", "mem", "temp", "fan", "none")

# ───────────────────────── Defaults ─────────────────────────

DEFAULTS = {
    "display": {
        "enabled": True,
        "mode": "gauge",
        "interval": 1.0,
        "gauge_labels": False,
        "fill_from_bottom": True,
    },
    "power": {
        "policy": "always",
        "battery_off_below": 0,
        "resume_on_charge": True,
        "lid_closed_off": True,
    },
    "layout": {
        "slot1": "cpu",
        "slot2": "mem",
        "slot3": "temp",
        "slot4": "fan",
    },
    "scale": {
        "temp_min": 35.0,
        "temp_max": 95.0,
        "temp_danger": 85.0,
        "fan_max_rpm": 5300.0,
        "cpu_danger": 90.0,
        "mem_danger": 90.0,
        "psi_danger": 60.0,
        "cpu_source": "util",
        "mem_source": "used",
        "preferred_temp_hwmon": "k10temp",
    },
    "brightness": {
        "base": 18,
        "tip": 130,
        "danger": 255,
        "baseline": 9,
        "divider": 4,
        "label": 32,
        "value": 110,
    },
}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ledmatrix-sysmon", "config.toml")


# ───────────────────────── Configuration loading ─────────────────────────

class Config:
    """Validated settings, plus the list of problems found while validating."""

    def __init__(self, values, problems, path, mtime):
        self.values = values
        self.problems = problems
        self.path = path
        self.mtime = mtime

    def __getitem__(self, section):
        return self.values[section]


def _deep_defaults():
    return {s: dict(v) for s, v in DEFAULTS.items()}


def _check_bool(raw, section, key, out, problems):
    if isinstance(raw, bool):
        out[key] = raw
    else:
        problems.append(f"{section}.{key}: expected true or false, got {raw!r}")


def _check_choice(raw, section, key, choices, out, problems):
    if isinstance(raw, str) and raw.lower() in choices:
        out[key] = raw.lower()
    else:
        problems.append(
            f"{section}.{key}: expected one of {', '.join(sorted(choices))}, got {raw!r}")


def _check_name(raw, section, key, out, problems):
    if isinstance(raw, str) and raw.strip():
        out[key] = raw.strip()
    else:
        problems.append(f"{section}.{key}: expected a non-empty name, got {raw!r}")


def _check_number(raw, section, key, lo, hi, out, problems, integer=False):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        problems.append(f"{section}.{key}: expected a number, got {raw!r}")
        return
    if not (lo <= raw <= hi):
        problems.append(f"{section}.{key}: {raw} is outside the range {lo} to {hi}")
        return
    out[key] = int(raw) if integer else float(raw)


VALIDATORS = {
    ("display", "enabled"): lambda r, o, p: _check_bool(r, "display", "enabled", o, p),
    ("display", "mode"): lambda r, o, p: _check_choice(r, "display", "mode", {"gauge", "value"}, o, p),
    ("display", "interval"): lambda r, o, p: _check_number(r, "display", "interval", 0.1, 3600, o, p),
    ("display", "gauge_labels"): lambda r, o, p: _check_bool(r, "display", "gauge_labels", o, p),
    ("display", "fill_from_bottom"): lambda r, o, p: _check_bool(r, "display", "fill_from_bottom", o, p),

    ("power", "policy"): lambda r, o, p: _check_choice(r, "power", "policy", {"always", "ac_only"}, o, p),
    ("power", "battery_off_below"): lambda r, o, p: _check_number(r, "power", "battery_off_below", 0, 100, o, p, integer=True),
    ("power", "resume_on_charge"): lambda r, o, p: _check_bool(r, "power", "resume_on_charge", o, p),
    ("power", "lid_closed_off"): lambda r, o, p: _check_bool(r, "power", "lid_closed_off", o, p),

    ("scale", "temp_min"): lambda r, o, p: _check_number(r, "scale", "temp_min", -50, 150, o, p),
    ("scale", "temp_max"): lambda r, o, p: _check_number(r, "scale", "temp_max", -50, 150, o, p),
    ("scale", "temp_danger"): lambda r, o, p: _check_number(r, "scale", "temp_danger", -50, 150, o, p),
    ("scale", "fan_max_rpm"): lambda r, o, p: _check_number(r, "scale", "fan_max_rpm", 100, 20000, o, p),
    ("scale", "cpu_danger"): lambda r, o, p: _check_number(r, "scale", "cpu_danger", 0, 100, o, p),
    ("scale", "mem_danger"): lambda r, o, p: _check_number(r, "scale", "mem_danger", 0, 100, o, p),
    ("scale", "psi_danger"): lambda r, o, p: _check_number(r, "scale", "psi_danger", 0, 100, o, p),
    ("scale", "cpu_source"): lambda r, o, p: _check_choice(r, "scale", "cpu_source", {"util", "psi"}, o, p),
    ("scale", "mem_source"): lambda r, o, p: _check_choice(r, "scale", "mem_source", {"used", "psi"}, o, p),
    ("scale", "preferred_temp_hwmon"): lambda r, o, p: _check_name(r, "scale", "preferred_temp_hwmon", o, p),
}

for _k in ("base", "tip", "danger", "baseline", "divider", "label", "value"):
    VALIDATORS[("brightness", _k)] = (
        lambda r, o, p, k=_k: _check_number(r, "brightness", k, 0, 255, o, p, integer=True))


def load_config(path=None):
    """Read and validate the configuration, falling back per-setting."""
    path = path or config_path()
    values = _deep_defaults()
    problems = []
    mtime = None

    if not os.path.exists(path):
        return Config(values, problems, path, None)

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    if tomllib is None:
        problems.append(
            "tomllib is unavailable (Python 3.11+ required) — using defaults throughout")
        return Config(values, problems, path, mtime)

    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        problems.append(f"cannot parse {path}: {exc}")
        return Config(values, problems, path, mtime)

    for section, entries in raw.items():
        if section not in DEFAULTS:
            problems.append(f"unknown section [{section}] — ignored")
            continue
        if not isinstance(entries, dict):
            problems.append(f"[{section}] is not a table — ignored")
            continue

        for key, value in entries.items():
            if key not in DEFAULTS[section]:
                problems.append(f"{section}.{key}: unknown setting — ignored")
                continue
            if section == "layout":
                continue
            check = VALIDATORS.get((section, key))
            if check is None:
                problems.append(f"{section}.{key}: no validator — ignored")
                continue
            check(value, values[section], problems)

    _validate_layout(raw.get("layout", {}), values["layout"], problems)
    _validate_scale_consistency(values["scale"], problems)

    return Config(values, problems, path, mtime)


def _validate_layout(raw, out, problems):
    """Slots must name known metrics, and no metric may occupy two slots."""
    if not isinstance(raw, dict):
        problems.append("[layout] is not a table — using the default arrangement")
        return

    staged = {}
    for slot in ("slot1", "slot2", "slot3", "slot4"):
        if slot not in raw:
            continue
        value = raw[slot]
        if not isinstance(value, str) or value.lower() not in METRICS:
            problems.append(
                f"layout.{slot}: expected one of {', '.join(METRICS)}, got {value!r}")
            continue
        staged[slot] = value.lower()

    assigned = [v for v in staged.values() if v != "none"]
    duplicates = {m for m in assigned if assigned.count(m) > 1}
    if duplicates:
        problems.append(
            "layout: " + ", ".join(sorted(duplicates)) +
            " assigned to more than one slot — using the default arrangement")
        return

    out.update(staged)


def _validate_scale_consistency(scale, problems):
    """Cross-field checks that no single-value validator can catch."""
    if scale["temp_min"] >= scale["temp_max"]:
        problems.append(
            f"scale: temp_min ({scale['temp_min']}) must be below temp_max "
            f"({scale['temp_max']}) — reverting both to defaults")
        scale["temp_min"] = DEFAULTS["scale"]["temp_min"]
        scale["temp_max"] = DEFAULTS["scale"]["temp_max"]

    if not (scale["temp_min"] <= scale["temp_danger"] <= scale["temp_max"]):
        problems.append(
            f"scale: temp_danger ({scale['temp_danger']}) lies outside the "
            f"temp_min..temp_max range — reverting to default")
        scale["temp_danger"] = DEFAULTS["scale"]["temp_danger"]


# ───────────────────────── Fonts ─────────────────────────

CHAR_H = 5

FONT_3X5 = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "A": ("010", "101", "111", "101", "101"),
    "C": ("111", "100", "100", "100", "111"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("110", "101", "101", "101", "101"),
    "P": ("111", "101", "111", "100", "100"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "\xb0": ("010", "101", "010", "000", "000"),
    "-": ("000", "000", "111", "000", "000"),
}

FONT_4X5 = {
    "A": ("0110", "1001", "1111", "1001", "1001"),
    "C": ("1111", "1000", "1000", "1000", "1111"),
    "E": ("1111", "1000", "1110", "1000", "1111"),
    "F": ("1111", "1000", "1110", "1000", "1000"),
    "M": ("1001", "1111", "1111", "1001", "1001"),
    "N": ("1001", "1101", "1011", "1001", "1001"),
    "P": ("1110", "1001", "1110", "1000", "1000"),
    "T": ("1111", "0110", "0110", "0110", "0110"),
    "U": ("1001", "1001", "1001", "1001", "0110"),
}

VALUE_LABELS = {"cpu": "CPU", "mem": "MEM", "temp": "\xb0C", "fan": "FAN"}
GAUGE_LABELS = {"cpu": "CPU", "mem": "MEM", "temp": "TMP", "fan": "FAN"}

BLOCK_TOPS = (3, 18)
VALUE_OFFSET = 7
LABEL_GAP = 1
LABEL_MARGIN = 1


def draw_text(cols, text, y_top, bright, width=WIDTH, x_base=0):
    """Draw one horizontal line of 3x5 text, centred in the given width."""
    text = text.upper()
    span = len(text) * 3 + (len(text) - 1)
    gap = 1 if span <= width else 0
    span = len(text) * 3 + (len(text) - 1) * gap
    x = x_base + max(0, (width - span) // 2)

    for ch in text:
        glyph = FONT_3X5.get(ch)
        if glyph is None:
            x += 3 + gap
            continue
        for r, row in enumerate(glyph):
            y = y_top + r
            if not (0 <= y < HEIGHT):
                continue
            for dx, bit in enumerate(row):
                if bit == "1" and 0 <= x + dx < WIDTH:
                    cols[x + dx][y] = bright
        x += 3 + gap


def vertical_label_height(text):
    glyphs = [c for c in text.upper() if c in FONT_4X5]
    if not glyphs:
        return 0
    return len(glyphs) * CHAR_H + (len(glyphs) - 1) * LABEL_GAP


def draw_vertical_label(cols, text, bright):
    """Draw a 4x5 label top to bottom inside a 4-column gauge."""
    y = 0
    for ch in text.upper():
        glyph = FONT_4X5.get(ch)
        if glyph is None:
            continue
        for r, row in enumerate(glyph):
            if y + r >= HEIGHT:
                return
            for dx, bit in enumerate(row):
                if bit == "1":
                    cols[dx][y + r] = bright
        y += CHAR_H + LABEL_GAP


# ───────────────────────── Sensors ─────────────────────────

def read_psi(resource):
    try:
        with open(f"/proc/pressure/{resource}") as f:
            for line in f:
                if line.startswith("some"):
                    for tok in line.split():
                        if tok.startswith("avg10="):
                            return float(tok.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return 0.0


_prev_cpu = {"total": 0.0, "idle": 0.0}


def read_cpu_util():
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
    except OSError:
        return 0.0

    if len(parts) < 5 or parts[0] != "cpu":
        return 0.0

    try:
        nums = [float(x) for x in parts[1:]]
    except ValueError:
        return 0.0

    idle = nums[3] + (nums[4] if len(nums) > 4 else 0.0)
    total = sum(nums)

    d_total = total - _prev_cpu["total"]
    d_idle = idle - _prev_cpu["idle"]
    _prev_cpu["total"] = total
    _prev_cpu["idle"] = idle

    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))


def read_mem_used_pct():
    total = avail = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = float(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = float(line.split()[1])
                if total is not None and avail is not None:
                    break
    except (OSError, ValueError):
        return 0.0

    if not total:
        return 0.0
    return max(0.0, min(100.0, (1.0 - avail / total) * 100.0))


def _hwmon_dirs():
    return sorted(glob.glob("/sys/class/hwmon/hwmon*"))


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _hwmon_name(d):
    try:
        with open(os.path.join(d, "name")) as f:
            return f.read().strip()
    except OSError:
        return ""


def read_temp_c(preferred):
    best = None
    fallback = 0.0
    for d in _hwmon_dirs():
        name = _hwmon_name(d)
        for inp in sorted(glob.glob(os.path.join(d, "temp*_input"))):
            milli = _read_int(inp)
            if milli is None:
                continue
            celsius = milli / 1000.0
            fallback = max(fallback, celsius)
            if name == preferred:
                best = max(best or 0.0, celsius)
    return best if best is not None else fallback


def read_fan_rpm():
    best = 0
    for d in _hwmon_dirs():
        for inp in sorted(glob.glob(os.path.join(d, "fan*_input"))):
            rpm = _read_int(inp)
            if rpm:
                best = max(best, rpm)
    return best


def list_sensors():
    print("hwmon inputs detected:\n")
    for d in _hwmon_dirs():
        name = _hwmon_name(d)
        print(f"  {d}  (name={name!r})")
        for inp in sorted(glob.glob(os.path.join(d, "temp*_input"))):
            milli = _read_int(inp)
            label_path = inp.replace("_input", "_label")
            label = ""
            if os.path.exists(label_path):
                with open(label_path) as f:
                    label = f.read().strip()
            if milli is not None:
                print(f"      {os.path.basename(inp):14s} {milli / 1000.0:6.1f} C  {label}")
        for inp in sorted(glob.glob(os.path.join(d, "fan*_input"))):
            rpm = _read_int(inp)
            if rpm is not None:
                print(f"      {os.path.basename(inp):14s} {rpm:6d} RPM")
    print("\nSet scale.fan_max_rpm to the highest figure seen with fans at full duty.")


# ───────────────────────── Power state ─────────────────────────

def _supplies():
    return sorted(glob.glob("/sys/class/power_supply/*"))


def _read_str(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def ac_online():
    """True when a mains supply reports online. None when undetermined."""
    seen = False
    for s in _supplies():
        if _read_str(os.path.join(s, "type")) != "Mains":
            continue
        seen = True
        if _read_str(os.path.join(s, "online")) == "1":
            return True
    return False if seen else None


def battery_percent():
    """Charge percentage of the first system battery, or None."""
    for s in _supplies():
        if _read_str(os.path.join(s, "type")) != "Battery":
            continue
        if _read_str(os.path.join(s, "scope")) == "Device":
            continue
        cap = _read_int(os.path.join(s, "capacity"))
        if cap is not None:
            return cap
    return None


def lid_closed():
    """True when the lid is shut, False when open, None when undetermined.

    Worth acting on: the Framework 16 pulls the modules' SLEEP# pin low while
    the lid is closed, so the firmware blanks the LEDs regardless of what the
    host sends. Frames pushed in that state only wake the LED controller for
    nothing, and the display is behind a closed lid anyway.
    """
    for state in glob.glob("/proc/acpi/button/lid/*/state"):
        text = _read_str(state).lower()
        if "closed" in text:
            return True
        if "open" in text:
            return False
    return None


class PowerGate:
    """Decides whether the display should be lit, given the power policy.

    The battery threshold latches: once it fires with resume_on_charge off, the
    display stays dark until the service restarts or the setting changes.
    """

    def __init__(self):
        self.latched = False

    def allows(self, cfg):
        power = cfg["power"]

        if not cfg["display"]["enabled"]:
            return False

        if power["lid_closed_off"] and lid_closed() is True:
            return False

        on_ac = ac_online()
        pct = battery_percent()

        if power["policy"] == "ac_only":
            if on_ac is False:
                return False
            if on_ac is None:
                pass

        threshold = power["battery_off_below"]
        if threshold > 0 and pct is not None:
            below = pct < threshold
            charging = on_ac is True

            if power["resume_on_charge"]:
                self.latched = False
                if below and not charging:
                    return False
            else:
                if below and not charging:
                    self.latched = True
                if self.latched:
                    return False

        return True


# ───────────────────────── Sampling ─────────────────────────

def sample(cfg):
    """Return {metric: (fraction, value, danger)} for every metric."""
    scale = cfg["scale"]

    if scale["cpu_source"] == "psi":
        cpu = read_psi("cpu")
        cpu_danger = cpu > scale["psi_danger"]
    else:
        cpu = read_cpu_util()
        cpu_danger = cpu > scale["cpu_danger"]

    if scale["mem_source"] == "psi":
        mem = read_psi("memory")
        mem_danger = mem > scale["psi_danger"]
    else:
        mem = read_mem_used_pct()
        mem_danger = mem > scale["mem_danger"]

    temp = read_temp_c(scale["preferred_temp_hwmon"])
    rpm = read_fan_rpm()
    fan = min(100.0, rpm / scale["fan_max_rpm"] * 100.0) if scale["fan_max_rpm"] else 0.0

    span = scale["temp_max"] - scale["temp_min"]
    temp_frac = (temp - scale["temp_min"]) / span if span else 0.0

    return {
        "cpu": (cpu / 100.0, cpu, cpu_danger),
        "mem": (mem / 100.0, mem, mem_danger),
        "temp": (temp_frac, temp, temp >= scale["temp_danger"]),
        "fan": (fan / 100.0, fan, fan >= 100.0),
        "none": (0.0, 0.0, False),
    }


def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def format_value(value):
    v = int(round(value))
    if v > 999:
        v = 999
    if v < -99:
        v = -99
    return str(v)


# ───────────────────────── Rendering ─────────────────────────

def render_gauge_matrix(cfg, slot_a, slot_b, readings):
    """Two vertical bars side by side, separated by a dim divider column."""
    bright = cfg["brightness"]
    labels = cfg["display"]["gauge_labels"]

    texts = (GAUGE_LABELS.get(slot_a, "") if labels and slot_a != "none" else "",
             GAUGE_LABELS.get(slot_b, "") if labels and slot_b != "none" else "")
    bar_top = max(vertical_label_height(texts[0]), vertical_label_height(texts[1]))
    if bar_top:
        bar_top += LABEL_MARGIN

    halves = []
    for metric, text in zip((slot_a, slot_b), texts):
        cols = [[0] * HEIGHT for _ in range(GAUGE_W)]
        if text:
            draw_vertical_label(cols, text, bright["label"])

        if metric != "none":
            frac, _value, danger = readings[metric]
            span = HEIGHT - bar_top
            lit = int(round(clamp01(frac) * span)) if span > 0 else 0

            if cfg["display"]["fill_from_bottom"]:
                order = list(range(HEIGHT - 1, bar_top - 1, -1))
            else:
                order = list(range(bar_top, HEIGHT))

            body = bright["danger"] if danger else bright["base"]
            for k in range(lit):
                for x in range(GAUGE_W):
                    cols[x][order[k]] = body

            if lit > 0:
                tip = bright["danger"] if danger else bright["tip"]
                for x in range(GAUGE_W):
                    cols[x][order[lit - 1]] = tip

            for x in range(GAUGE_W):
                if cols[x][order[0]] < bright["baseline"]:
                    cols[x][order[0]] = bright["baseline"]

        halves.append(cols)

    divider = [bright["divider"]] * HEIGHT
    out = []
    for x in range(WIDTH):
        if x <= 3:
            out.append(halves[0][x])
        elif x == 4:
            out.append(list(divider))
        else:
            out.append(halves[1][x - 5])
    return out


def render_value_matrix(cfg, slot_a, slot_b, readings):
    """Two stacked blocks, each a label above its current figure."""
    bright = cfg["brightness"]
    cols = [[0] * HEIGHT for _ in range(WIDTH)]

    for top, metric in zip(BLOCK_TOPS, (slot_a, slot_b)):
        if metric == "none":
            continue
        _frac, value, danger = readings[metric]
        draw_text(cols, VALUE_LABELS[metric], top, bright["label"])
        draw_text(cols, format_value(value), top + VALUE_OFFSET,
                  bright["danger"] if danger else bright["value"])

    return cols


def render(cfg, readings):
    layout = cfg["layout"]
    renderer = render_gauge_matrix if cfg["display"]["mode"] == "gauge" else render_value_matrix
    left = renderer(cfg, layout["slot1"], layout["slot2"], readings)
    right = renderer(cfg, layout["slot3"], layout["slot4"], readings)
    return left, right


def blank_frame():
    return [[0] * HEIGHT for _ in range(WIDTH)]


# ───────────────────────── Matrix output ─────────────────────────

def push_frame(ser, cols):
    for x in range(WIDTH):
        ser.write(bytes([0x32, 0xAC, 0x07, x] + cols[x]))
    ser.write(bytes([0x32, 0xAC, 0x08, 0x00]))


def open_matrix(path):
    return require_serial().Serial(os.path.realpath(path), BAUD,
                                   timeout=1, write_timeout=1)


def preview(left, right):
    ramp = " .:-=+*#@"

    def cell(v):
        return " " if v <= 0 else ramp[1 + min(len(ramp) - 2, v * (len(ramp) - 1) // 256)]

    print("\x1b[H\x1b[2J", end="")
    print("   LEFT       RIGHT")
    for y in range(HEIGHT):
        l = "".join(cell(left[x][y]) for x in range(WIDTH))
        r = "".join(cell(right[x][y]) for x in range(WIDTH))
        print(f"   {l}  {r}")


# ───────────────────────── Configuration report ─────────────────────────

def report_config(cfg):
    """Print the effective configuration and any problems. Return an exit code."""
    print(f"Configuration file: {cfg.path}")
    if cfg.mtime is None and not os.path.exists(cfg.path):
        print("  not present — every setting falls back to its default")
    print()

    if cfg.problems:
        print(f"Problems found ({len(cfg.problems)}), each falling back to the default:")
        for p in cfg.problems:
            print(f"  - {p}")
    else:
        print("No problems found.")
    print()

    print("Effective settings:")
    for section in DEFAULTS:
        print(f"  [{section}]")
        for key, default in DEFAULTS[section].items():
            value = cfg[section][key]
            mark = "" if value == default else "   (overridden)"
            print(f"    {key:22s} = {value!r}{mark}")
    print()

    layout = cfg["layout"]
    mode = cfg["display"]["mode"]
    if mode == "gauge":
        geometry = ("left matrix, left bar", "left matrix, right bar",
                    "right matrix, left bar", "right matrix, right bar")
    else:
        geometry = ("left matrix, top block", "left matrix, bottom block",
                    "right matrix, top block", "right matrix, bottom block")

    print(f"Layout in {mode} mode:")
    for slot, where in zip(("slot1", "slot2", "slot3", "slot4"), geometry):
        print(f"  {slot} -> {layout[slot]:5s} ({where})")
    print()

    gate = PowerGate()
    on_ac = ac_online()
    pct = battery_percent()
    lid = lid_closed()
    ac_text = {True: "on AC", False: "on battery", None: "undetermined"}[on_ac]
    lid_text = {True: "closed", False: "open", None: "undetermined"}[lid]
    print(f"Current power state: {ac_text}" +
          (f", battery at {pct} %" if pct is not None else "") +
          f", lid {lid_text}")
    print("Display would currently be: " +
          ("lit" if gate.allows(cfg) else "dark"))

    return 1 if cfg.problems else 0


# ───────────────────────── Main ─────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="LED Matrix system monitor for the Framework Laptop 16")
    ap.add_argument("--config", default=None, help="path to the configuration file")
    ap.add_argument("--check-config", action="store_true",
                    help="validate the configuration, report it, and exit")
    ap.add_argument("--preview", action="store_true", help="ASCII rendering, no hardware")
    ap.add_argument("--once", action="store_true", help="push a single frame and exit")
    ap.add_argument("--list-sensors", action="store_true", help="dump hwmon inputs")
    ap.add_argument("--no-clear", action="store_true",
                    help="leave the matrices lit on exit")
    args = ap.parse_args()

    if args.list_sensors:
        list_sensors()
        return 0

    cfg = load_config(args.config)

    if args.check_config:
        return report_config(cfg)

    for p in cfg.problems:
        print(f"config: {p}", file=sys.stderr)

    gate = PowerGate()

    if args.preview:
        try:
            while True:
                cfg = maybe_reload(cfg, args.config)
                frames = render(cfg, sample(cfg)) if gate.allows(cfg) \
                    else (blank_frame(), blank_frame())
                preview(*frames)
                if args.once:
                    break
                time.sleep(cfg["display"]["interval"])
        except KeyboardInterrupt:
            pass
        return 0

    left = open_matrix(LEFT_DEV)
    right = open_matrix(RIGHT_DEV)

    stop = {"flag": False}

    def handle(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    was_lit = None

    try:
        while not stop["flag"]:
            cfg = maybe_reload(cfg, args.config)
            lit = gate.allows(cfg)

            if lit:
                fl, fr = render(cfg, sample(cfg))
            elif was_lit is not False:
                fl, fr = blank_frame(), blank_frame()
            else:
                fl = fr = None

            if fl is not None:
                try:
                    push_frame(left, fl)
                    push_frame(right, fr)
                except (OSError, require_serial().SerialException):
                    pass

            was_lit = lit

            if args.once:
                break

            waited = 0.0
            interval = cfg["display"]["interval"]
            while waited < interval and not stop["flag"]:
                time.sleep(0.05)
                waited += 0.05
    finally:
        if not args.no_clear:
            try:
                push_frame(left, blank_frame())
                push_frame(right, blank_frame())
            except (OSError, require_serial().SerialException):
                pass
        left.close()
        right.close()

    return 0


def maybe_reload(cfg, path):
    """Re-read the configuration when its modification time has changed."""
    target = path or config_path()
    try:
        mtime = os.path.getmtime(target) if os.path.exists(target) else None
    except OSError:
        return cfg

    if mtime == cfg.mtime:
        return cfg

    fresh = load_config(path)
    for p in fresh.problems:
        print(f"config: {p}", file=sys.stderr)
    return fresh


if __name__ == "__main__":
    # Restore default SIGPIPE handling so piping into head or less exits
    # quietly instead of raising BrokenPipeError.
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
