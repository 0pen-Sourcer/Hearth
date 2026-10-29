# Security Policy

Inglen runs on your machine with broad permissions (file system, shell, app launching). Security matters, both in what Inglen lets the model do and in how Inglen itself is built.

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Instead, use one of these:

1. **GitHub Private Vulnerability Reporting** (preferred). Go to the repo's Security tab and choose "Report a vulnerability". This lets us coordinate a fix privately before disclosure.
2. **Email** the address in the maintainer's GitHub profile.

Include:
- A short description of what's wrong
- Steps to reproduce (concrete is best, such as a script or a terminal transcript)
- What an attacker could do with it
- Any suggested fix (optional)

We aim to acknowledge within 72 hours and ship a fix within 14 days for critical issues. Researchers who report responsibly get credited in the release notes (unless you'd rather stay anonymous).

## Threat model

Inglen assumes:

- **The local user is trusted.** Inglen runs *for* you, not against you. The permission prompts are speed bumps, not defenses against a malicious user.
- **The model is semi-trusted.** A model can hallucinate or be jailbroken into running bad shell commands. A switch in the chat bar decides what runs without asking. **Ask** (the default) checks before anything acts on the machine; **Auto-edit** lets file edits inside the workspace through; **Auto** runs routine work and still asks before anything that deletes, moves, sends, uploads, pushes, publishes or installs. `HEARTH_AUTO_APPROVE=1` removes the prompts on purpose.
- **The filesystem outside `~/Jarvis/` is read-only by default.** Writes need `/allow <path>` or `HEARTH_EXTRA_WORKSPACES`, and the check follows links to where a write really lands. `HEARTH_LOCKDOWN=1` keeps reads inside the workspace too.
- **Network traffic is opt-in.** Web search and fetch only happen when the model calls those tools. A cloud model or an agent brain gets what you send it. Nothing else phones home: no analytics, no crash reporting, and no telemetry except the opt-in install count (off by default; once a day, a random install id, the version, the OS and the kind of brain, sent to the relay's /count, which keeps no IP addresses). The only other requests are plain downloads that carry nothing about you: Inglen's update check on GitHub, engine downloads from llama.cpp's and stable-diffusion.cpp's GitHub releases when you install one, the npm registry for installed agents' updates, and the public models.dev catalog (once a day) for the model menu's details.
- **Chat apps let in only the people you approve.** Someone new who messages your Telegram, Discord or WhatsApp bot gets a pairing code, and nothing runs for them until you approve it on the PC. Changing who has access is refused from inside a chat app, and a risky step asked from a phone is asked in that chat under the same approval switch.
- **The Plus phone app goes through a relay that can't read it.** Only with Plus turned on does the PC keep a connection to the relay server, and it checks the licence key with the payment provider. A phone pairs by scanning a QR code and comparing six digits on both screens. Everything between them is AES-256-GCM under keys only the two paired devices hold (X25519 and HKDF): a live connection gets a fresh key and a counter that refuses replayed or reordered frames, and messages left for a phone that is away, notifications and their answers are sealed with the pairing key. The relay can't read any of it. It does see which room a frame belongs to, when frames pass and how big they are, keeps the phone's notification subscription, and holds up to 200 sealed messages for an away phone for up to 7 days. A phone asks for risky steps under the same approval switch.
- **Agent programs are as trusted as you make them.** Claude Code, Codex, Cursor and protocol agents used as the brain send their commands and file changes to Inglen's approval switch. As helpers they run under their own rules, inside a workspace-write sandbox where they have one; one working in a git repository works on its own copy, and its changes wait for you to bring them in.

Out of scope (we don't claim to defend against these):

- A malicious LLM with `HEARTH_AUTO_APPROVE=1` set. The user explicitly disabled the safety bumper.
- A malicious user with shell access. They already have shell access.
- Side-channel attacks on the LLM's responses. Use a model you trust.
- Sandbox escapes via tool-call argument injection. We sanitize and pass argument lists rather than shell strings where we can, but a determined model and a careless user can probably escape eventually. Inglen never runs elevated; don't run it as an administrator or root yourself.

## Known sharp edges

- **`run_command`** runs arbitrary shell commands. It refuses destructive patterns and redirects to disk before running and always asks about them; an approved destructive command does run. Don't blanket-allow it unless you trust your model.
- **`edit_file` / `write_file`** can damage files if the model writes the wrong thing. Confined to the workspace by default; permission-prompted on writes.
- **`open_app`** can launch any executable findable on the system. Permission-prompted. Don't allow if you're running an untrusted prompt.
- **`web_fetch`** can be steered to internal IPs (`http://192.168.x.x/admin`) by a model that's been told to. We don't block private ranges. If you don't want this, run Inglen on a host with no LAN access to admin panels.
- **`browse`, `browse_click`, `browse_type` and `browse_key`** drive a real Chromium with your existing session cookies. A jailbroken model can in theory navigate to your bank tab. Opening a page doesn't ask; clicking, typing and key presses ask like every other input tool. Don't paste API keys into a chat right before allowing browser input without asking.
- **Tool-call parser** post-processes model output to extract Gemma, Hermes, Llama 3, Mistral, Phi, Granite and Command-R tool-call patterns. It is regex-based and can't tell whether an extracted call is one the user wanted; the same permission prompt that gates native tool calls gates these too.
- **The local API** that the window talks to refuses requests from other sites, so a page in a browser tab can't read stored keys or call tools.
- **Shared memory** (Memory tab, Share with agents) gives the coding agents you switch on Inglen's memory and past chats through an MCP server, and nothing else of Inglen's. Anything those agents send to their own service can include it.
- **Built-in llama.cpp server** binds to `127.0.0.1:1234` by default, and the image engine to `127.0.0.1:8189`. A Windows Job Object kills the child process when Inglen exits, but a force-kill of the parent before the child registers with the job (rare race) can leave it orphan. Restart of Inglen always frees the port.
- **`~/Jarvis/model_configs.json`** is plain JSON. If a malicious tool could write to it, it could pin a different model path. The file lives in your home dir and is only readable/writable by you on a default Windows install. Treat it as user data.

## Supported versions

We're pre-1.0 and ship rapidly. Only the latest release gets security fixes. If you're running an old build, the answer to "is X patched here?" is probably "upgrade".

## A note on AI safety alignment

Inglen's persona explicitly disables generic AI safety disclaimers in conversations with the local user. That's a UX choice, not a security choice. The permission prompts and sandbox boundaries are the real defenses; the persona's tone is just tone.
