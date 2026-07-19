#!/usr/bin/env bash
# Exit 1 (failure) when the laptop lid is closed, so PAM skips pam_fprintd and
# falls through to password auth. Exit 0 (lid open / sensor missing) otherwise.
#
# Used from the PAM auth stack via pam_exec, e.g.:
#   auth [success=ignore default=1] pam_exec.so quiet /usr/local/bin/fprint-lid-check.sh
grep -qw closed /proc/acpi/button/lid/*/state 2>/dev/null && exit 1
exit 0
