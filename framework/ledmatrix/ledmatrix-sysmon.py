#!/usr/bin/env python3
"""
ledmatrix-sysmon.py — Visualise les métriques système sur les deux LED Matrix
du Framework Laptop 16.

  Matrice GAUCHE : CPU (util ou PSI)  | mémoire (used ou PSI)
  Matrice DROITE : température CPU    | vitesse ventilateurs (%)

Chaque matrice = 9 colonnes x 34 lignes. Deux jauges de 4 colonnes séparées
par une colonne-séparateur. Pilotage LED par LED en niveaux de gris.

Dépendances : pyserial. Stdlib only sinon.

Exemples :
  python3 ledmatrix-sysmon.py                  # boucle temps réel
  python3 ledmatrix-sysmon.py --preview        # rendu ASCII dans le terminal (sans matériel)
  python3 ledmatrix-sysmon.py --once           # pousse une seule trame puis quitte
  python3 ledmatrix-sysmon.py --list-sensors   # liste les capteurs hwmon détectés
"""

import argparse
import glob
import os
import signal
import time

import serial

# ───────────────────────── Configuration ─────────────────────────

LEFT_DEV = "/dev/ledmatrix-left"
RIGHT_DEV = "/dev/ledmatrix-right"

WIDTH = 9
HEIGHT = 34
REFRESH_S = 1.0
BAUD = 115200

# Orientation : mets False si les barres apparaissent à l'envers
FILL_FROM_BOTTOM = True

# Luminosités (0-255)
BRIGHT_BASE = 18
BRIGHT_TIP = 130
BRIGHT_DANGER = 255
BRIGHT_BASELINE = 9
BRIGHT_DIVIDER = 4

# Mode jauge CPU : "util" (% occupation) ou "psi" (pression/contention)
CPU_MODE = "util"

# Mode jauge mémoire : "used" (% RAM utilisée) ou "psi"
MEM_MODE = "used"

# Échelle température (°C) -> 0..100 % de la jauge
TEMP_MIN = 35.0
TEMP_MAX = 95.0
TEMP_DANGER = 85.0

# Ventilateurs : RPM correspondant à 100 % (ajuste avec --list-sensors)
FAN_MAX_RPM = 5300.0
FAN_DANGER_PCT = 90.0

# Pression PSI (avg10) considérée comme "danger"
PSI_DANGER = 60.0

# Capteur température préféré (sinon : max de tous les temp*_input)
PREFERRED_TEMP_HWMON = "k10temp"


# ───────────────────────── Lecture des capteurs ─────────────────────────

def read_psi(resource):
    """Retourne 'some avg10' (%) depuis /proc/pressure/<resource>, ou 0.0."""
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
    """% d'occupation CPU global, calculé en delta entre deux appels (/proc/stat)."""
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
    """% de RAM utilisée via /proc/meminfo (MemTotal vs MemAvailable)."""
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


def read_temp_c():
    """Température CPU en °C. Préfère PREFERRED_TEMP_HWMON, sinon max global."""
    preferred = None
    fallback = 0.0
    for d in _hwmon_dirs():
        name = _hwmon_name(d)
        for inp in sorted(glob.glob(os.path.join(d, "temp*_input"))):
            milli = _read_int(inp)
            if milli is None:
                continue
            celsius = milli / 1000.0
            fallback = max(fallback, celsius)
            if name == PREFERRED_TEMP_HWMON:
                preferred = max(preferred or 0.0, celsius)
    return preferred if preferred is not None else fallback


def read_fan_rpm():
    """RPM ventilateur le plus rapide trouvé dans hwmon, ou 0."""
    best = 0
    for d in _hwmon_dirs():
        for inp in sorted(glob.glob(os.path.join(d, "fan*_input"))):
            rpm = _read_int(inp)
            if rpm:
                best = max(best, rpm)
    return best


def list_sensors():
    print("Capteurs hwmon détectés :\n")
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
                print(f"      {os.path.basename(inp):14s} {milli / 1000.0:6.1f} °C  {label}")
        for inp in sorted(glob.glob(os.path.join(d, "fan*_input"))):
            rpm = _read_int(inp)
            if rpm is not None:
                print(f"      {os.path.basename(inp):14s} {rpm:6d} RPM")
    print("\nAjuste FAN_MAX_RPM avec la valeur max observée ventilo à fond.")


# ───────────────────────── Construction de la trame ─────────────────────────

def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def build_bar(frac, danger):
    """Retourne 34 valeurs (0-255) pour une barre verticale."""
    frac = clamp01(frac)
    vals = [0] * HEIGHT
    lit = int(round(frac * HEIGHT))

    order = list(range(HEIGHT - 1, -1, -1)) if FILL_FROM_BOTTOM else list(range(HEIGHT))
    bottom = order[0]

    body = BRIGHT_DANGER if danger else BRIGHT_BASE
    for k in range(lit):
        vals[order[k]] = body

    if lit > 0:
        tip = order[lit - 1]
        vals[tip] = BRIGHT_DANGER if danger else BRIGHT_TIP

    if vals[bottom] < BRIGHT_BASELINE:
        vals[bottom] = BRIGHT_BASELINE

    return vals


def build_frame(frac_a, danger_a, frac_b, danger_b):
    """9 colonnes : 0-3 jauge A, 4 séparateur, 5-8 jauge B."""
    bar_a = build_bar(frac_a, danger_a)
    bar_b = build_bar(frac_b, danger_b)
    divider = [BRIGHT_DIVIDER] * HEIGHT

    cols = []
    for x in range(WIDTH):
        if x <= 3:
            cols.append(list(bar_a))
        elif x == 4:
            cols.append(list(divider))
        else:
            cols.append(list(bar_b))
    return cols


# ───────────────────────── Sortie matrice ─────────────────────────

def push_frame(ser, cols):
    """Stage 9 colonnes en gris puis commit (mise à jour atomique)."""
    for x in range(WIDTH):
        ser.write(bytes([0x32, 0xAC, 0x07, x] + cols[x]))
    ser.write(bytes([0x32, 0xAC, 0x08, 0x00]))


def blank(ser):
    push_frame(ser, [[0] * HEIGHT for _ in range(WIDTH)])


def open_matrix(path):
    real = os.path.realpath(path)
    return serial.Serial(real, BAUD, timeout=1)


def preview(cols_left, cols_right):
    """Rendu ASCII des deux matrices côte à côte."""
    ramp = " .:-=+*#@"

    def cell(v):
        if v <= 0:
            return " "
        return ramp[1 + min(len(ramp) - 2, v * (len(ramp) - 1) // 256)]

    rows = range(HEIGHT) if FILL_FROM_BOTTOM else range(HEIGHT - 1, -1, -1)

    print("\x1b[H\x1b[2J", end="")
    print("   LEFT (cpu|mem)      RIGHT (temp|fan)")
    for y in rows:
        l = "".join(cell(cols_left[x][y]) for x in range(WIDTH))
        r = "".join(cell(cols_right[x][y]) for x in range(WIDTH))
        print(f"   {l}        {r}")


# ───────────────────────── Boucle principale ─────────────────────────

def sample():
    """Retourne (gauche_a, gauche_b, droite_a, droite_b) en (frac, danger)."""
    if CPU_MODE == "psi":
        cpu = read_psi("cpu")
        cpu_danger = cpu > PSI_DANGER
    else:
        cpu = read_cpu_util()
        cpu_danger = cpu > 90.0

    if MEM_MODE == "used":
        mem = read_mem_used_pct()
        mem_danger = mem > 90.0
    else:
        mem = read_psi("memory")
        mem_danger = mem > PSI_DANGER

    temp = read_temp_c()
    fan_rpm = read_fan_rpm()
    fan_pct = min(100.0, fan_rpm / FAN_MAX_RPM * 100.0) if FAN_MAX_RPM else 0.0

    left_a = (cpu / 100.0, cpu_danger)
    left_b = (mem / 100.0, mem_danger)

    temp_frac = (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)
    right_a = (temp_frac, temp >= TEMP_DANGER)
    right_b = (fan_pct / 100.0, fan_pct >= FAN_DANGER_PCT)

    return left_a, left_b, right_a, right_b


def main():
    ap = argparse.ArgumentParser(description="LED Matrix system monitor (Framework 16)")
    ap.add_argument("--preview", action="store_true", help="rendu ASCII terminal, sans matériel")
    ap.add_argument("--once", action="store_true", help="une seule trame puis quitte")
    ap.add_argument("--list-sensors", action="store_true", help="liste les capteurs hwmon")
    ap.add_argument("--interval", type=float, default=REFRESH_S, help="période en secondes")
    ap.add_argument("--no-clear", action="store_true", help="ne pas éteindre les matrices en sortie")
    args = ap.parse_args()

    if args.list_sensors:
        list_sensors()
        return

    if args.preview:
        try:
            while True:
                la, lb, ra, rb = sample()
                cl = build_frame(la[0], la[1], lb[0], lb[1])
                cr = build_frame(ra[0], ra[1], rb[0], rb[1])
                preview(cl, cr)
                if args.once:
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
        return

    left = open_matrix(LEFT_DEV)
    right = open_matrix(RIGHT_DEV)

    stop = {"flag": False}

    def handle(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    try:
        while not stop["flag"]:
            la, lb, ra, rb = sample()
            push_frame(left, build_frame(la[0], la[1], lb[0], lb[1]))
            push_frame(right, build_frame(ra[0], ra[1], rb[0], rb[1]))
            if args.once:
                break
            t = 0.0
            while t < args.interval and not stop["flag"]:
                time.sleep(0.05)
                t += 0.05
    finally:
        if not args.no_clear:
            try:
                blank(left)
                blank(right)
            except (OSError, serial.SerialException):
                pass
        left.close()
        right.close()


if __name__ == "__main__":
    main()
