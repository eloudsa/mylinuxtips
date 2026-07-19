#!/usr/bin/env bash
#
# Report whether the "skip fingerprint when lid closed" fix is currently active.
# Useful because authselect can regenerate the PAM files (e.g. after toggling the
# fingerprint switch in GNOME Settings) and silently drop the lid-check line.
#
# Runs as a normal user (all inspected files are world-readable).
# Exit 0 = fully installed; exit 1 = missing/incomplete -> re-run the installer.
#
# Usage:  bash status-lid-fprint.sh
set -uo pipefail

LID_SCRIPT=/usr/local/bin/fprint-lid-check.sh
SYS=/etc/pam.d/system-auth        # resolves through the authselect symlink
FPR=/etc/pam.d/fingerprint-auth
ok=0

say()  { printf '  [%s] %-16s %s\n' "$1" "$2" "$3"; }

echo "fprintd lid-skip status"

# --- 1. helper script -------------------------------------------------------
if [[ -x $LID_SCRIPT ]]; then
  say ok "helper" "$LID_SCRIPT (executable)"
else
  say MISSING "helper" "$LID_SCRIPT not found or not executable"; ok=1
fi

# --- 2. PAM files: lid-check must sit before pam_fprintd ---------------------
check_pam() {
  local name=$1 f=$2
  if [[ ! -r $f ]]; then say MISSING "$name" "$f not readable"; ok=1; return; fi
  local fp lc
  fp=$(grep -n 'pam_fprintd.so'   "$f" | head -n1 | cut -d: -f1)
  lc=$(grep -n 'fprint-lid-check.sh' "$f" | head -n1 | cut -d: -f1)
  if [[ -z $fp ]]; then
    say na "$name" "no pam_fprintd line — nothing to guard"
  elif [[ -n $lc && $lc -lt $fp ]]; then
    say ok "$name" "lid-check before pam_fprintd (lines $lc < $fp)"
  else
    say MISSING "$name" "pam_fprintd present but lid-check missing/misplaced"; ok=1
  fi
}
check_pam "system-auth"      "$SYS"
check_pam "fingerprint-auth" "$FPR"

# --- 3. bonus: current lid state -------------------------------------------
lid=$(cat /proc/acpi/button/lid/*/state 2>/dev/null | awk '{print $2}' | head -n1)
echo "  lid state: ${lid:-unknown}"

echo
if [[ $ok -eq 0 ]]; then
  echo "Result: INSTALLED — nothing to do."
else
  echo "Result: NOT fully installed — run:  sudo bash install-lid-fprint.sh"
fi
exit $ok
