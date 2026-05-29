# Security Policy

Hearth runs on your machine with broad permissions (file system, shell, app launching). Security matters — both in what Hearth lets the model do, and in how Hearth itself is built.

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Instead, use one of these:

1. **GitHub Private Vulnerability Reporting** — preferred. Go to the repo's Security tab → "Report a vulnerability." This lets us coordinate a fix privately before disclosure.
2. **Email** — send to the address in the maintainer's GitHub profile.

Include:
- A short description of what's wrong
- Steps to reproduce (concrete is best — a script or a CLI session transcript)
- What an attacker could do with it
- Any suggested fix (optional)

We aim to acknowledge within 72 hours and ship a fix within 14 days for critical issues. Researchers who report responsibly get credited in the release notes (unless you'd rather stay anonymous).

## Threat model

Hearth assumes:

- **The local user is trusted.** Hearth runs *for* you, not against you. The permission prompts are speed bumps, not defenses against a malicious user.
- **The local LLM is semi-trusted.** A model can hallucinate or be jailbroken into running bad shell commands. That's why risky tools (writes, run_command, app launching) ask for confirmation by default — `JARVIS_AUTO_APPROVE=1` removes that safety on purpose.
- **The filesystem outside `~/Jarvis/` is read-only by default.** Writes need `/allow <path>` or `JARVIS_EXTRA_WORKSPACES`.
- **Network traffic is opt-in.** Web search / fetch only happen when the model calls those tools. Nothing else phones home — no telemetry, no analytics, no crash reporting.

Out of scope (we don't claim to defend against these):

- A malicious LLM with `JARVIS_AUTO_APPROVE=1` set. The user explicitly disabled the safety bumper.
- A malicious user with shell access. They already have shell access.
- Side-channel attacks on the LLM's responses. Use a model you trust.
- Sandbox escapes via tool-call argument injection — we string-sanitize and use parameterized argument lists where possible, but a determined model + a careless user can probably escape eventually. Don't run Hearth as root.

## Known sharp edges

- **`run_command`** runs arbitrary shell commands. Permission-prompted by default. Don't blanket-allow it unless you trust your model.
- **`edit_file` / `write_file`** can damage files if the model writes the wrong thing. Confined to the workspace by default; permission-prompted on writes.
- **`open_app`** can launch any executable findable on the system. Permission-prompted. Don't allow if you're running an untrusted prompt.
- **`web_fetch`** can be steered to internal IPs (`http://192.168.x.x/admin`) by a model that's been told to. We don't block private ranges. If you don't want this, run Hearth on a host with no LAN access to admin panels.

## Supported versions

We're pre-1.0 and ship rapidly. Only the latest release on `main` gets security fixes. If you're running an old build, the answer to "is X patched here?" is probably "upgrade."

## A note on AI safety alignment

Hearth's persona explicitly disables generic AI safety disclaimers in conversations with the local user. That's a UX choice, not a security choice. The permission prompts and sandbox boundaries are the real defenses; the persona's tone is just tone.
