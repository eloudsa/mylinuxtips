# CLAUDE.md

Guidance for Claude Code (and any AI assistant) when working in this repository.

## What this project is

`mylinuxtips` is a **public** collection of practical Linux tips, scripts and
how-tos. Each tip is self-contained: a working script plus a `README.md` that
explains it. Some tips are written up on the owner's blog,
[noratek.dev](https://noratek.dev/), which links back to the scripts here.

The audience is other Linux users who will read and run this material. Clarity,
correctness and safety matter more than cleverness.

## Non-negotiable rules

- **This repository is public — no secrets, ever.** A push is effectively a
  publish: scripts here are linked from noratek.dev, so treat every committed
  change as immediately public. Before writing or staging anything, scan the
  content and the diff for values that must not ship:
  - passwords, passphrases, API keys, tokens, bearer/auth headers, private keys
  - private IP addresses, internal hostnames, real serial numbers, MAC addresses
  - personal paths or anything identifying beyond the author's public identity

  Replace them with placeholders (`<token>`, `<hostname>`, `xxxx`). If you are
  unsure whether a value is sensitive, treat it as sensitive and ask.
- **No AI attribution in git history.** Do **not** add `Co-Authored-By: Claude`
  or any "Generated with Claude" line to commits or pull requests.
- **Communicate in English.** Code, comments in new material, commit messages,
  READMEs and issues are written in English. (Some existing scripts have French
  comments; leave them unless asked, but write new content in English.)
- **Commit and push only when explicitly asked.** Don't push on your own
  initiative.

## Repository layout

```
<topic>/<tip-name>/
    <script>            # the tip itself (a single script where possible)
    README.md           # requirements, install, usage, gotchas
README.md               # index of all tips + licence
CLAUDE.md               # this file
```

Example: `framework/ledmatrix/` contains `ledmatrix-sysmon.py` and its README.

## Conventions for a tip

When adding or editing a tip, match the style of the existing ones (see
`framework/ledmatrix/README.md` as the reference):

- **Self-contained.** Prefer a single script with a minimal, clearly stated set
  of dependencies. Stdlib-only is ideal; call out any third-party requirement.
- **A dedicated `README.md`** with, roughly: a one-line summary, requirements,
  install, usage examples, configuration, and a **Notes/gotchas** section for
  the non-obvious details (the stuff that actually cost time to figure out).
- **Link the blog post** when one exists, and say the how-to is the full
  walkthrough while the README covers the script alone.
- **Distro-neutral where possible.** Scripts are developed on Fedora; note
  anything Fedora-specific (package names, versions) rather than assuming it.
- **Tested before documented.** Don't describe behaviour you haven't verified on
  real hardware/system when the tip depends on it.

## When adding a new tip

1. Create `<topic>/<tip-name>/` with the script and a `README.md`.
2. Add a row to the **Contents** table in the root `README.md`.
3. Keep the licence stance intact: everything here is free to use and reuse.

## Licence stance

All material is shared for anyone to use, copy, modify and redistribute freely,
with no warranty. Keep any new content consistent with that (don't introduce
restrictive headers).

## Claude memory

Claude Code's per-project memories live in
`~/.claude/projects/-home-eloudsa-Projects-mylinuxtips/memory/` (indexed by
`MEMORY.md`), and are mirrored to **`~/Dropbox/AI/Memories/mylinuxtips/`** so they
can move between laptops (convention: one folder per project under
`~/Dropbox/AI/Memories/<project-name>/`).

This is a manual snapshot, not live sync. To restore on another machine, copy the
files back into that machine's project memory dir; re-copy to Dropbox after
memories change.