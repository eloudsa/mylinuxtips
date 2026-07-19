# mylinuxtips

A collection of practical Linux tips, scripts and how-tos that have helped me in
my day-to-day work. Each entry is self-contained: a working script plus a README
that explains what it does, how to run it, and the non-obvious details that
tripped me up.

Some of these tips are written up in more depth on my blog,
[noratek.dev](https://noratek.dev/), with links back to the scripts here.

## Contents

| Tip | What it does |
| --- | --- |
| [framework/ledmatrix](framework/ledmatrix/) | Drive both Framework Laptop 16 LED Matrix modules as a live system monitor (CPU, memory, temperature, fan) on Linux. |
| [ssh/github-ssh-hangs-fedora-gnome](ssh/github-ssh-hangs-fedora-gnome/) | Fix `git push`/`ssh` hanging on Fedora/GNOME — replace the buggy `gcr-ssh-agent` with a plain OpenSSH agent, with optional zero-prompt passphrase from the keyring. |

*More tips will be added over time.*

## How this repo is organised

Tips are grouped by topic into top-level directories (e.g. `framework/` for
Framework Laptop tips). Each tip lives in its own subdirectory containing the
script(s) and a dedicated `README.md`. Start with the tip's README — it covers
requirements, installation and usage. The blog post, when there is one, has the
full walkthrough.

## Usage and licence

Licensed under the [MIT License](LICENSE) — you are free to use, copy, modify and
redistribute everything in this repository, for any purpose, provided the
copyright and permission notice is kept. It is shared in the hope that it saves
someone else the time it took me to work these things out. No warranty of any
kind — read what a script does before running it, especially anything that
touches hardware or system services.

If a tip helps you, a link back to [noratek.dev](https://noratek.dev/) or this
repository is always appreciated, but never required.

## Contributing

This is primarily my personal notebook, but corrections and improvements are
welcome — open an issue or a pull request.