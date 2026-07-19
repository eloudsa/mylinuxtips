#!/usr/bin/env bash
#
# Skip fingerprint auth (pam_fprintd) when the laptop lid is closed, so sudo/su/
# chroot/polkit/login fall straight through to the password prompt instead of
# waiting on a fingerprint reader that is physically unreachable.
#
# Mechanism: a lid-check script exits FAILURE when the lid is closed. It is
# inserted into the PAM auth stack right before pam_fprintd with the control
# [success=ignore default=1]:
#   lid open   -> script succeeds -> success=ignore -> continue to pam_fprintd (fingerprint offered)
#   lid closed -> script fails    -> default=1      -> skip pam_fprintd        -> password prompt
#
# Target: Fedora with authselect-managed PAM (files under /etc/authselect/).
# Idempotent: safe to re-run, e.g. if authselect ever regenerates the files.
#
# Usage:  sudo bash install-lid-fprint.sh
set -uo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run as root:  sudo bash $0" >&2; exit 1; fi

LID_SCRIPT=/usr/local/bin/fprint-lid-check.sh
SYS=/etc/authselect/system-auth        # used by sudo, su, chroot's su, polkit, login
FPR=/etc/authselect/fingerprint-auth   # used by the GNOME (GDM) fingerprint prompt
STAMP=$(date +%Y%m%d-%H%M%S)
PAM_LINE='auth        [success=ignore default=1]                   pam_exec.so quiet /usr/local/bin/fprint-lid-check.sh'

# --- 1. install the lid-check script ---------------------------------------
cat > "$LID_SCRIPT" <<'EOF'
#!/usr/bin/env bash
# Exit 1 (failure) when the laptop lid is closed, so PAM skips pam_fprintd and
# falls through to password auth. Exit 0 (lid open / sensor missing) otherwise.
grep -qw closed /proc/acpi/button/lid/*/state 2>/dev/null && exit 1
exit 0
EOF
chown root:root "$LID_SCRIPT"
chmod 755 "$LID_SCRIPT"
echo "[ok] installed $LID_SCRIPT"

# --- helper: insert PAM_LINE before the first pam_fprintd line -------------
patch_file() {
  local f=$1
  if [[ ! -f $f ]]; then echo "[skip] $f not found"; return 0; fi
  if grep -q 'fprint-lid-check.sh' "$f"; then
    echo "[ok] $f already patched"; return 0
  fi
  if ! grep -q 'pam_fprintd.so' "$f"; then
    echo "[skip] $f has no pam_fprintd line"; return 0
  fi
  cp -a "$f" "$f.bak-$STAMP"
  awk -v line="$PAM_LINE" '
    /pam_fprintd\.so/ && !done { print line; done=1 }
    { print }
  ' "$f" > "$f.new" && cat "$f.new" > "$f" && rm -f "$f.new"

  # sanity: our line present AND password auth (pam_unix) still there
  if grep -q 'fprint-lid-check.sh' "$f" && grep -q 'pam_unix.so' "$f"; then
    echo "[ok] patched $f  (backup: $f.bak-$STAMP)"
  else
    echo "[FAIL] sanity check on $f -> restoring backup" >&2
    cat "$f.bak-$STAMP" > "$f"
    return 1
  fi
}

patch_file "$SYS" || exit 1
patch_file "$FPR" || exit 1

echo
echo "Done. Current auth section of $SYS:"
grep -n '^auth' "$SYS"
echo
echo "To undo: restore the .bak-$STAMP files and 'rm $LID_SCRIPT'"
