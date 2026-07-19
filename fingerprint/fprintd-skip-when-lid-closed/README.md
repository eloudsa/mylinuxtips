# fprintd-skip-when-lid-closed

When you use a laptop **docked with the lid closed** (external monitor, keyboard,
mouse), any `sudo`, `su`, `chroot`-into-`su` or polkit prompt still tries the
**fingerprint reader first** — but the reader is physically unreachable. You sit
there while `pam_fprintd` waits; sometimes `Ctrl-C` drops you to the password
prompt, sometimes it just kills the command. There is no built-in fprintd option
to handle this ([libfprint#403](https://gitlab.freedesktop.org/libfprint/libfprint/-/issues/403)
has been open for years).

This tip adds a tiny **lid check in front of `pam_fprintd`**: when the lid is
closed, PAM skips fingerprint and goes straight to the password prompt — instantly,
no waiting, no `Ctrl-C`. When the lid is open, fingerprint works exactly as before.
It reuses your existing fprintd-enrolled fingerprints and installs no daemon.

> **Full walkthrough:** a step-by-step write-up is published on
> [noratek.dev](https://noratek.dev/howto/fprintd-skip-when-lid-closed-fedora).
> This README is the condensed, copy-paste version.

---

## How it works

A one-line script, `fprint-lid-check.sh`, exits **failure when the lid is closed**:

```bash
grep -qw closed /proc/acpi/button/lid/*/state 2>/dev/null && exit 1
exit 0
```

It is placed in the PAM auth stack immediately **before** `pam_fprintd.so`, guarded
by the control `[success=ignore default=1]`:

| Lid state | `fprint-lid-check.sh` | PAM control does | Result |
| --- | --- | --- | --- |
| **open** | exit 0 (success) | `success=ignore` → continue | `pam_fprintd` runs → fingerprint offered |
| **closed** | exit 1 (failure) | `default=1` → skip next module | `pam_fprintd` skipped → password prompt |

The decision is made **at the moment you authenticate**, so there is zero lag and
nothing running in the background. (A daemon that polls the lid and stops the
`fprintd` service is another approach — see
[andypiper/fw-lid-fprint-daemon](https://github.com/andypiper/fw-lid-fprint-daemon)
— but polling adds latency and heavier service masking; the PAM approach here
avoids both.)

## Requirements

- A laptop that exposes lid state at `/proc/acpi/button/lid/*/state` (most do).
  Check: `cat /proc/acpi/button/lid/*/state` should print `open` or `closed`.
- Fingerprint auth already set up with **fprintd** (Fedora:
  `authselect enable-feature with-fingerprint`, prints enrolled with
  `fprintd-enroll`).
- `pam_exec.so` (ships with `pam`, present by default) at
  `/usr/lib64/security/pam_exec.so`.
- Fedora with **authselect-managed** PAM — the files live under `/etc/authselect/`
  and `/etc/pam.d/system-auth` is a symlink to them. See *Notes* for non-authselect
  distros.

## Install

```bash
sudo bash install-lid-fprint.sh
```

The installer:

1. Writes `/usr/local/bin/fprint-lid-check.sh` (root-owned, `0755`).
2. Backs up `/etc/authselect/system-auth` and `/etc/authselect/fingerprint-auth`
   to timestamped `.bak-<stamp>` files.
3. Inserts the `pam_exec` line before `pam_fprintd` in each — **idempotently**
   (re-running is a no-op if already patched).
4. Sanity-checks that the `pam_exec` line landed **and** password auth
   (`pam_unix`) is still present; if not, it auto-restores the backup and aborts.

`system-auth` covers **sudo, su, chroot's `su`, polkit and console login**;
`fingerprint-auth` covers the **GNOME (GDM) fingerprint prompt** so the lock
screen also goes straight to password when docked.

> **Safety:** editing PAM auth can lock you out if something goes wrong. Before
> running the installer, open a second terminal and keep a root shell alive
> (`sudo -i`). If `sudo` misbehaves afterwards, restore the `.bak-<stamp>` files
> from that shell.

## Check status

Because authselect can silently regenerate the PAM files (see *Notes*), a quick
check tells you whether the fix is still in place — no root needed:

```bash
bash status-lid-fprint.sh
```

It verifies the helper script and that the lid-check sits before `pam_fprintd` in
both PAM files, and prints the current lid state:

```
fprintd lid-skip status
  [ok] helper           /usr/local/bin/fprint-lid-check.sh (executable)
  [ok] system-auth      lid-check before pam_fprintd (lines 8 < 9)
  [ok] fingerprint-auth lid-check before pam_fprintd (lines 7 < 8)
  lid state: closed

Result: INSTALLED — nothing to do.
```

It exits `0` when fully installed and `1` when anything is missing (so you can wire
it into a login check or just eyeball it), pointing you back at the installer if a
regeneration wiped it.

## Test

`sudo -k` clears the cached credential so you actually hit the auth stack:

```bash
# Lid CLOSED  -> jumps straight to a password prompt, no fingerprint wait
sudo -k; sudo true

# Lid OPEN    -> offers fingerprint as before
sudo -k; sudo true
```

## Uninstall

Restore the backups the installer reported and remove the helper:

```bash
sudo cp /etc/authselect/system-auth.bak-<stamp>      /etc/authselect/system-auth
sudo cp /etc/authselect/fingerprint-auth.bak-<stamp> /etc/authselect/fingerprint-auth
sudo rm /usr/local/bin/fprint-lid-check.sh
```

## Notes and gotchas

- **authselect can wipe the edit.** Those PAM files are marked *"Generated by
  authselect — user changes will be overwritten."* Toggling the fingerprint switch
  in GNOME Settings, or a `pam`/authselect update that re-renders them, removes the
  line. Run `bash status-lid-fprint.sh` any time to check; if it reports missing,
  just re-run `sudo bash install-lid-fprint.sh` — it's idempotent. For
  a permanent setup you can `authselect opt-out` (you then manage PAM by hand) or
  bake the change into a custom authselect profile.
- **Lid node name varies.** Some machines use `LID`, others `LID0`; the glob
  `/proc/acpi/button/lid/*/state` handles both. If your laptop has no such node,
  the script exits 0 (fingerprint stays enabled) — swap the check for an
  external-display probe (e.g. `/sys/class/drm/*/status` reading `connected`).
- **The reader still exists when docked** — it's just unreachable. This tip only
  changes *when PAM offers it*; it does not touch fprintd, libfprint or your
  enrolled prints.
- **Not TPM-related.** If you also use a TPM for other things, note the prompt you
  were fighting is `pam_fprintd`, not the TPM.
- **Non-authselect distros** (Debian/Ubuntu/Arch): the same idea works — add the
  `auth [success=ignore default=1] pam_exec.so quiet /usr/local/bin/fprint-lid-check.sh`
  line just before `pam_fprintd.so` in `/etc/pam.d/common-auth` (Debian) or the
  relevant `/etc/pam.d/*` file. Only the file location differs; adjust the
  `pam_exec.so` path to your arch (e.g. `/usr/lib/x86_64-linux-gnu/security/`).
- Developed and tested on **Fedora 44** (Framework Laptop). The mechanism
  (`pam_exec` + a lid probe) is distro-neutral; only the authselect file paths are
  Fedora specifics.

## References

- [libfprint#403](https://gitlab.freedesktop.org/libfprint/libfprint/-/issues/403)
  — upstream request for "disable fingerprint when the reader isn't usable"
- [andypiper/fw-lid-fprint-daemon](https://github.com/andypiper/fw-lid-fprint-daemon)
  — the alternative systemd-daemon approach
- `man pam_exec`, `man pam.conf` — the `pam_exec` module and PAM control syntax
- `man authselect` — how Fedora generates and manages the PAM stack
