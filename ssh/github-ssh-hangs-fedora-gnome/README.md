# github-ssh-hangs-fedora-gnome

`git push`, `git fetch` or a bare `ssh -T git@github.com` **hangs for 30–60 s (or
forever)** on a Fedora/GNOME machine — but the repo is small and the network is
fine. This is not a data-size or bandwidth problem. It is a bug in
`gcr-ssh-agent` (the SSH agent shipped by gnome-keyring ≥ 46): when it has to
sign the authentication challenge for a **passphrase-protected key**, it freezes,
and it keeps hijacking `SSH_AUTH_SOCK` to point at `/run/user/$UID/gcr/ssh`.

The fix is to stop using `gcr-ssh-agent` for SSH and run a plain OpenSSH agent
instead. An optional second step restores *zero-prompt* passphrase persistence
using libsecret, so you never type the passphrase again.

> **Full walkthrough:** [SSH to GitHub hangs on Fedora / GNOME](https://noratek.dev/howto/ssh-git-github-hangs-fedora-gnome/)
> — background on the bug and the reasoning. This README is the condensed,
> copy-paste version.

---

## Diagnose

Confirm the agent is the culprit before changing anything. Check where
`SSH_AUTH_SOCK` points:

```bash
echo "$SSH_AUTH_SOCK"
# /run/user/1000/gcr/ssh   -> gcr-ssh-agent is in charge (the problem)
# /run/user/1000/ssh-agent.socket -> already on a plain agent
```

Then time a connection. A verbose run shows it stalling right after offering the
key, at the signing step:

```bash
time ssh -T git@github.com
ssh -vT git@github.com 2>&1 | grep -i 'offer\|sign\|auth'
```

If it hangs on a passphrase-protected key while `SSH_AUTH_SOCK` points into
`/gcr/`, this is your bug.

## Fix — run a dedicated OpenSSH agent

**1. Add a user service for `ssh-agent`** at
`~/.config/systemd/user/ssh-agent.service`:

```ini
[Unit]
Description=OpenSSH agent (user)

[Service]
Type=simple
Environment=SSH_AUTH_SOCK=%t/ssh-agent.socket
ExecStart=/usr/bin/ssh-agent -D -a $SSH_AUTH_SOCK

[Install]
WantedBy=default.target
```

**2. Point `SSH_AUTH_SOCK` at it for every session** via
`~/.config/environment.d/ssh-agent.conf`:

```ini
SSH_AUTH_SOCK=${XDG_RUNTIME_DIR}/ssh-agent.socket
```

`environment.d` is read at login by the systemd user manager, so the variable is
set for graphical sessions and the services they start.

**3. Mask the gcr agent so it can never take over again:**

```bash
systemctl --user mask gcr-ssh-agent.socket gcr-ssh-agent.service
```

**4. Enable the agent and log back in** (a full logout/login is the clean way to
pick up the new `environment.d` value):

```bash
systemctl --user daemon-reload
systemctl --user enable --now ssh-agent.service
# log out and back in, then verify:
echo "$SSH_AUTH_SOCK"     # -> .../ssh-agent.socket, not .../gcr/ssh
ssh-add ~/.ssh/id_github  # load your key (prompts for the passphrase once)
ssh -T git@github.com     # authenticates immediately now
```

At this point pushes are fast again. You will be prompted for the key passphrase
once per session (per boot). If that is fine, you are done — the next section is
optional.

## Optional — zero-prompt passphrase from the keyring

The nice property of the old gcr agent was that it remembered the passphrase in
the login keyring. We can rebuild that on the plain agent with libsecret, so the
key is loaded automatically at login and **no prompt ever appears** — without
re-enabling gcr (never do that; it brings the bug back).

**1. Store the passphrase in the GNOME login keyring** (interactive — it asks for
the passphrase and writes it to the keyring, not to any file):

```bash
secret-tool store --label="SSH passphrase id_github" ssh-key id_github
```

**2. Add an askpass helper** at `~/.local/bin/ssh-askpass-keyring` that prints the
passphrase by reading it back out of the keyring:

```bash
#!/usr/bin/env bash
exec secret-tool lookup ssh-key id_github
```

```bash
chmod +x ~/.local/bin/ssh-askpass-keyring
```

**3. Add a oneshot service** at `~/.config/systemd/user/ssh-add-keyring.service`
that loads the key at login, feeding the passphrase from the keyring:

```ini
[Unit]
Description=Load SSH key into the agent from the GNOME keyring
After=ssh-agent.service
Requires=ssh-agent.service

[Service]
Type=oneshot
Environment=SSH_AUTH_SOCK=%t/ssh-agent.socket
Environment=SSH_ASKPASS=%h/.local/bin/ssh-askpass-keyring
Environment=SSH_ASKPASS_REQUIRE=force
ExecStart=/usr/bin/ssh-add %h/.ssh/id_github

[Install]
WantedBy=graphical-session.target
```

`SSH_ASKPASS_REQUIRE=force` makes `ssh-add` use the helper even though a terminal
is available, so it reads the passphrase non-interactively from the keyring.

**4. Enable it:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now ssh-add-keyring.service
systemctl --user status ssh-add-keyring.service   # -> active (exited), success
ssh-add -l                                          # -> your key is listed
ssh -T git@github.com                               # -> silent auth, no prompt
```

The login keyring is unlocked by PAM when you log in, so the passphrase is
available to the helper from the very first push of the session.

## Notes and gotchas

- **Keep the key name consistent.** These files use `id_github`; change it in all
  four places — the `secret-tool` attribute, the `lookup` in the helper, the
  `ExecStart` path, and the `store` label — to match your key.
- **If the passphrase ever changes**, re-run the `secret-tool store` command; the
  helper picks up the new value automatically.
- **Fedora has no `/usr/libexec/openssh/ssh-askpass`** by default — only
  `gcr-ssh-askpass`. That is why this recipe ships its own askpass helper rather
  than relying on a system one.
- **Do not un-mask gcr-ssh-agent.** If you later see `SSH_AUTH_SOCK` back in
  `/gcr/ssh`, something re-enabled it; the hang will return.
- Developed on Fedora 44 / GNOME. The mechanism (systemd user services,
  `environment.d`, libsecret) is distro-neutral; only package names and the gcr
  version window are Fedora/GNOME specifics.

## References

- [SSH to GitHub hangs on Fedora / GNOME](https://noratek.dev/howto/ssh-git-github-hangs-fedora-gnome/) — the full write-up
- `man ssh-agent`, `man ssh-add` — `SSH_ASKPASS` and `SSH_ASKPASS_REQUIRE`
- `man secret-tool` — storing and looking up secrets in the login keyring
- `man environment.d` — per-user environment for the systemd user manager