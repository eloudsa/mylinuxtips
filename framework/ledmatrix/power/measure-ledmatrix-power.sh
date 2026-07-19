#!/usr/bin/env bash
#
# measure-ledmatrix-power.sh — Measure what the Framework Laptop 16 LED Matrix
# modules actually cost in battery power, by A/B comparison.
#
#   Phase A: services stopped, matrices dark
#   Phase B: service running, matrices lit
#
# The difference between the two medians is the cost of the display. Because a
# laptop at idle already draws 10-20 W with constant fluctuation, the script
# also measures the noise floor and refuses to over-interpret a small delta.
#
# Usage:
#   ./measure-ledmatrix-power.sh [unit] [samples] [period]
#
#   unit     systemd --user unit to test  (default: ledmatrix-labels-sysmon.service)
#   samples  samples per phase            (default: 20)
#   period   seconds between samples      (default: 3)

set -u

export LC_ALL=C

UNIT="${1:-ledmatrix-labels-sysmon.service}"
SAMPLES="${2:-20}"
PERIOD="${3:-3}"
SETTLE=30

ALL_UNITS="ledmatrix-sysmon.service ledmatrix-labels-sysmon.service"

# ───────────────────────── Introduction ─────────────────────────

echo
echo "This script measures how much battery power the LED Matrix modules draw,"
echo "by sampling battery discharge with the display off, then on."
echo

# ───────────────────────── LED Matrix detection ─────────────────────────

found=0
for link in /dev/ledmatrix-left /dev/ledmatrix-right; do
    [ -e "$link" ] && found=$((found + 1))
done

if [ "$found" -eq 0 ]; then
    for dev in /dev/ttyACM*; do
        [ -e "$dev" ] || continue
        vid=$(udevadm info -q property -n "$dev" 2>/dev/null |
              sed -n 's/^ID_VENDOR_ID=//p')
        [ "$vid" = "32ac" ] && found=$((found + 1))
    done
fi

if [ "$found" -eq 0 ]; then
    echo "No LED Matrix module detected." >&2
    echo "Expected /dev/ledmatrix-left and /dev/ledmatrix-right, or a tty device" >&2
    echo "with USB vendor ID 32ac. Check the udev rules and that the modules are" >&2
    echo "seated in the input deck." >&2
    exit 1
fi

echo "LED Matrix modules detected: $found"

# ───────────────────────── Battery discovery ─────────────────────────
#
# Enumerated by type, not by name: firmwares call the pack BAT0, BAT1, BATT,
# BATC, CMB0 and worse. Peripheral batteries (mice, headsets, USB-C sources)
# declare scope=Device and are excluded. Machines with two packs are handled
# by summing both — a single-pack reading would understate the draw.

BATS=()
METHODS=()

for c in /sys/class/power_supply/*; do
    [ -d "$c" ] || continue
    [ "$(cat "$c/type" 2>/dev/null)" = "Battery" ] || continue
    [ "$(cat "$c/scope" 2>/dev/null)" = "Device" ] && continue

    if [ -r "$c/power_now" ]; then
        BATS+=("$c")
        METHODS+=("power_now")
    elif [ -r "$c/current_now" ] && [ -r "$c/voltage_now" ]; then
        BATS+=("$c")
        METHODS+=("current_x_voltage")
    fi
done

if [ "${#BATS[@]}" -eq 0 ]; then
    echo "No usable system battery found under /sys/class/power_supply/." >&2
    echo "A battery exposing either power_now, or current_now plus voltage_now," >&2
    echo "is required. Peripheral batteries (scope=Device) do not count." >&2
    exit 1
fi

# Instantaneous draw, summed across every pack.
read_power_uw() {
    local i total=0 v
    for i in "${!BATS[@]}"; do
        if [ "${METHODS[$i]}" = "power_now" ]; then
            v=$(cat "${BATS[$i]}/power_now" 2>/dev/null || echo 0)
        else
            v=$(awk -v a="$(cat "${BATS[$i]}/current_now" 2>/dev/null || echo 0)" \
                    -v b="$(cat "${BATS[$i]}/voltage_now" 2>/dev/null || echo 0)" \
                'BEGIN { if (a < 0) a = -a; printf "%.0f", a * b / 1000000 }')
        fi
        total=$((total + v))
    done
    echo "$total"
}

read_field() {
    if [ -r "$1/$2" ]; then
        cat "$1/$2"
    else
        echo ""
    fi
}

# Usable capacity is derived, never assumed. Preference order per pack:
#   1. energy_full                      — Wh straight from the firmware
#   2. charge_full x voltage_min_design — Ah converted at nominal voltage
#   3. charge_full x voltage_now        — last resort, overestimates
#
# Step 3 is flagged because voltage_now is the instantaneous terminal voltage,
# typically 8-12 % above nominal on a charged pack, which inflates capacity and
# every runtime figure derived from it.

CAP=0
CAP_DESIGN=0
CAP_KNOWN=0
CAP_APPROX=0

pack_capacity() {
    local bat="$1" volt="" volt_src="" ef efd cf cfd

    PACK_CAP=""
    PACK_DESIGN=""
    PACK_SOURCE=""

    ef=$(read_field "$bat" energy_full)
    if [ -n "$ef" ]; then
        PACK_CAP=$(awk -v e="$ef" 'BEGIN { printf "%.2f", e / 1000000 }')
        PACK_SOURCE="energy_full"
        efd=$(read_field "$bat" energy_full_design)
        [ -n "$efd" ] && PACK_DESIGN=$(awk -v e="$efd" 'BEGIN { printf "%.2f", e / 1000000 }')
        return
    fi

    cf=$(read_field "$bat" charge_full)
    [ -z "$cf" ] && return

    for f in voltage_min_design voltage_now; do
        volt=$(read_field "$bat" "$f")
        if [ -n "$volt" ] && [ "$volt" != "0" ]; then
            volt_src="$f"
            break
        fi
    done
    [ -z "$volt_src" ] && return

    [ "$volt_src" = "voltage_now" ] && CAP_APPROX=1

    PACK_CAP=$(awk -v c="$cf" -v v="$volt" 'BEGIN { printf "%.2f", c * v / 1000000000000 }')
    PACK_SOURCE="charge_full x $volt_src"

    cfd=$(read_field "$bat" charge_full_design)
    [ -n "$cfd" ] && PACK_DESIGN=$(awk -v c="$cfd" -v v="$volt" \
        'BEGIN { printf "%.2f", c * v / 1000000000000 }')
}

if [ "${#BATS[@]}" -gt 1 ]; then
    echo "Batteries (${#BATS[@]} packs, readings summed)"
else
    echo "Battery"
fi

for i in "${!BATS[@]}"; do
    bat="${BATS[$i]}"
    pack_capacity "$bat"

    [ "${#BATS[@]}" -gt 1 ] && echo
    printf "  device          : %s\n" "$(basename "$bat")"

    model=$(read_field "$bat" model_name)
    [ -n "$model" ] && printf "  model           : %s\n" "$model"

    printf "  power reading   : %s\n" "${METHODS[$i]}"

    if [ -n "$PACK_CAP" ]; then
        printf "  usable capacity : %s Wh  [%s]\n" "$PACK_CAP" "$PACK_SOURCE"
        CAP=$(awk -v a="$CAP" -v b="$PACK_CAP" 'BEGIN { printf "%.2f", a + b }')
        CAP_KNOWN=1
    else
        echo "  usable capacity : not exposed"
    fi

    if [ -n "$PACK_DESIGN" ]; then
        printf "  design capacity : %s Wh\n" "$PACK_DESIGN"
        CAP_DESIGN=$(awk -v a="$CAP_DESIGN" -v b="$PACK_DESIGN" 'BEGIN { printf "%.2f", a + b }')
        [ -n "$PACK_CAP" ] && awk -v c="$PACK_CAP" -v d="$PACK_DESIGN" \
            'BEGIN { if (d > 0) printf "  health          : %.0f %% of design\n", c * 100 / d }'
    fi

    cycles=$(read_field "$bat" cycle_count)
    [ -n "$cycles" ] && [ "$cycles" != "0" ] && printf "  cycle count     : %s\n" "$cycles"
done

if [ "$CAP_KNOWN" -eq 0 ]; then
    CAP=""
    echo
    echo "  No pack exposes its capacity — runtime estimates will be skipped."
elif [ "${#BATS[@]}" -gt 1 ]; then
    echo
    printf "  combined usable : %s Wh\n" "$CAP"
fi

if [ "$CAP_APPROX" -eq 1 ]; then
    echo
    echo "  Note: voltage_min_design is not exposed, so capacity was computed from"
    echo "  the instantaneous terminal voltage. Expect it to read roughly 10 % high,"
    echo "  and the runtime figures along with it."
fi
echo

# ───────────────────────── Pre-flight ─────────────────────────

while true; do
    discharging=0
    states=""
    for bat in "${BATS[@]}"; do
        st=$(cat "$bat/status" 2>/dev/null || echo "Unknown")
        [ "$st" = "Discharging" ] && discharging=1
        states="$states $(basename "$bat")=$st"
    done

    if [ "$discharging" -eq 1 ]; then
        break
    fi

    echo "Battery state:$states"
    echo "No pack is discharging, so the machine is still on AC power."
    echo "On AC, the battery gauge does not measure system consumption."
    echo
    read -r -p "Unplug the charger, then press Enter (Ctrl-C to abort): " _
    echo
done

# A gauge that reads zero, or an implausible figure, would silently produce a
# meaningless result three minutes later. Catch it now.
probe=$(read_power_uw)
if [ "$probe" -le 0 ]; then
    echo "The battery gauge reports 0 W while discharging." >&2
    echo "Some embedded controllers refresh the reading only every 30 s or so." >&2
    echo "Wait a moment and re-run; if it persists, this method cannot measure" >&2
    echo "power on this machine." >&2
    exit 1
fi
if [ "$probe" -gt 200000000 ]; then
    awk -v p="$probe" 'BEGIN {
        printf "The battery gauge reports %.0f W, which is not plausible for a laptop.\n", p / 1000000
    }' >&2
    echo "The sysfs units on this machine are probably not the expected microwatts." >&2
    exit 1
fi

awk -v p="$probe" 'BEGIN { printf "Discharging at %.2f W. Before starting, please:\n", p / 1000000 }'
echo
echo "  - close as many applications as you can, and let the machine settle"
echo "  - do NOT change screen brightness during the run"
echo "  - do NOT plug in an external display, or any USB device"
echo "  - do NOT touch the keyboard or trackpad once the run starts"
echo
echo "Any of these will move the baseline by more than the effect being measured."
echo

read -r -p "Ready? Press Enter to start (Ctrl-C to abort): " _
echo

# ───────────────────────── Sampling ─────────────────────────

median() {
    sort -n | awk '{ v[NR] = $1 }
        END {
            if (NR == 0) { print "0"; exit }
            if (NR % 2) { printf "%.0f\n", v[(NR + 1) / 2] }
            else        { printf "%.0f\n", (v[NR / 2] + v[NR / 2 + 1]) / 2 }
        }'
}

stddev() {
    awk '{ s += $1; q += $1 * $1; n++ }
        END {
            if (n < 2) { print "0"; exit }
            m = s / n
            printf "%.0f\n", sqrt((q / n) - (m * m))
        }'
}

collect() {
    local label="$1" out="$2" i val watts
    : > "$out"
    for ((i = 1; i <= SAMPLES; i++)); do
        val=$(read_power_uw)
        echo "$val" >> "$out"
        watts=$(awk -v v="$val" 'BEGIN { printf "%.2f", v / 1000000 }')
        printf "\r  %s: %2d/%d  %s W" "$label" "$i" "$SAMPLES" "$watts"
        sleep "$PERIOD"
    done
    printf "\r  %s: %d/%d samples collected%20s\n" "$label" "$SAMPLES" "$SAMPLES" ""
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

DURATION=$(( (SETTLE + SAMPLES * PERIOD) * 2 ))
echo "Unit under test: $UNIT"
echo "Run time: about $((DURATION / 60)) min"
echo

echo "Phase A — matrices dark"
# shellcheck disable=SC2086
systemctl --user stop $ALL_UNITS 2>/dev/null
echo "  settling for ${SETTLE}s..."
sleep "$SETTLE"
collect "phase A" "$TMP/a"

echo
echo "Phase B — matrices lit"
systemctl --user start "$UNIT" || { echo "Failed to start $UNIT" >&2; exit 1; }
echo "  settling for ${SETTLE}s..."
sleep "$SETTLE"
collect "phase B" "$TMP/b"

# ───────────────────────── Results ─────────────────────────

MA=$(median < "$TMP/a")
MB=$(median < "$TMP/b")
SD=$(stddev < "$TMP/a")
DELTA=$((MB - MA))

echo
echo "─────────────────────────────────────────────"
awk -v a="$MA" -v b="$MB" -v d="$DELTA" -v s="$SD" 'BEGIN {
    printf "  Matrices dark     : %6.2f W\n", a / 1000000
    printf "  Matrices lit      : %6.2f W\n", b / 1000000
    printf "  Cost of the display: %5.2f W", d / 1000000
    if (a > 0) printf "  (%.1f %%)", d * 100 / a
    printf "\n"
    printf "  Baseline noise (SD): %5.2f W\n", s / 1000000
}'
echo "─────────────────────────────────────────────"
echo
echo "Conclusion"
echo

awk -v a="$MA" -v b="$MB" -v d="$DELTA" -v s="$SD" -v cap="$CAP" 'BEGIN {
    dw = d / 1000000
    sw = s / 1000000

    if (dw <= 0) {
        print "  The lit run measured no higher than the dark run. Either the cost is"
        print "  below what this method can resolve, or the baseline drifted. Re-run"
        print "  with a quieter system before concluding anything."
        exit
    }

    if (dw < 2 * sw) {
        printf "  The %.2f W difference is smaller than twice the baseline noise\n", dw
        printf "  (%.2f W), so it is not statistically meaningful. What this does\n", sw
        print  "  establish is an upper bound: the display costs less than the"
        print  "  idle fluctuation of the machine itself. Treat it as negligible."
    } else {
        printf "  The %.2f W difference is well clear of the %.2f W baseline noise,\n", dw, sw
        print  "  so the measurement is sound."
    }

    print ""

    if (cap != "") {
        ha = cap / (a / 1000000)
        hb = cap / (b / 1000000)
        lost = (ha - hb) * 60
        printf "  On this %.0f Wh battery, at the measured idle draw:\n", cap
        printf "    matrices dark : %.0f h %02.0f min of runtime\n", int(ha), (ha - int(ha)) * 60
        printf "    matrices lit  : %.0f h %02.0f min of runtime\n", int(hb), (hb - int(hb)) * 60
        printf "    difference    : about %.0f min over a full charge\n", lost
        print ""
    }

    print "  Note what this figure covers: the LEDs themselves, both RP2040"
    print "  microcontrollers held awake by the refresh, and the Python service"
    print "  waking the CPU on every interval. To separate them, re-run with a"
    print "  longer --interval; if the cost drops, polling was the larger share."
}'
