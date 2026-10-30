# Security Policy

Inglen runs on your machine with broad permissions: files, shell commands, app launching, browser control and desktop input. Security matters both in what Inglen lets a model or agent do and in how Inglen itself is built.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

Instead, use one of these:

1. **GitHub Private Vulnerability Reporting** (preferred). Go to the repo's Security tab and choose "Report a vulnerability". This lets us coordinate a fix privately before disclosure.
2. **Email** the address in the maintainer's GitHub profile.

Include:
- A short description of what's wrong
- Steps to reproduce, such as a script or terminal transcript
- What an attacker could do with it
- Any suggested fix, if you have one

We aim to acknowledge within 72 hours and ship a fix within 14 days for critical issues. Researchers who report responsibly get credited in the release notes unless they would rather stay anonymous.

## Threat model

Inglen assumes:

- **The local user is trusted.** Inglen runs for you, not against you. The permission prompts are not meant to defend the machine from its owner.
- **The model or agent is semi-trusted.** It can hallucinate, misunderstand a request, or be prompt-injected. A switch in the chat bar decides what may run without asking. **Ask** (the default) checks before anything acts on the machine. **Auto-edit** lets file edits inside the workspace through. **Auto** runs routine work and still asks before destructive or clearly external actions such as deleting, moving, sending, uploading, pushing, publishing or installing. `HEARTH_AUTO_APPROVE=1` removes the prompts on purpose.
- **Writes outside the workspace are blocked by default.** New installs use `~/Inglen` unless the person picks another workspace. Older installs that already use `~/Jarvis` keep it so an update does not move their files. Writes outside the active workspace need `/allow <path>` or `HEARTH_EXTRA_WORKSPACES`, and the check follows links to where a write really lands. `HEARTH_LOCKDOWN=1` keeps reads inside the workspace too.
- **Network use is explicit or tied to a feature the person turned on.** Web search and fetch run when the model calls those tools. A cloud model or agent brain receives the messages and context sent to it. Plus keeps a connection to the relay only when Plus is on. Telegram, Discord, WhatsApp and email only connect after the person sets them up.
- **There is no automatic crash upload or conversation telemetry.** The optional install count is off unless the person says yes. When enabled, once a day it sends a random install id, version, OS and kind of brain to the relay's `/count`; the count service does not store the internet address.
- **Some background requests still happen for product data and downloads.** Inglen checks GitHub for its own updates, can download model/image engines and models from their published hosts, checks npm for installed agent updates, reads the public models.dev catalog for model details, can fetch the announcement feed, and fetches small site/service icons when the UI needs them. Those hosts can see ordinary request metadata such as the internet address and timing. Inglen does not attach chats, memory or files to those requests.
- **Chat apps let in only the people you approve.** Someone new who messages your Telegram, Discord or WhatsApp bot gets a pairing code, and nothing runs for them until you approve it on the PC. Changing who has access is refused from inside a chat app, and a risky step asked from a phone goes through the same approval system.
- **The Plus phone app uses a relay that cannot read message contents.** The PC connects only when Plus is enabled and the licence and activation are valid. A phone pairs by scanning a QR code and comparing six digits on both screens. Messages between the paired PC and phone are encrypted under keys held by those devices. The relay can see the room identifier, timing and size of frames; it stores the phone notification subscription and can hold up to 200 sealed messages for an away phone for up to 7 days. It cannot decrypt the message contents.
- **Agent programs are as trusted as you make them.** Claude Code, Codex, Cursor and protocol agents used as the brain send their Inglen tool actions through the same permission system. Helpers may also have their own tools and sandboxes. Coding helpers working in a git repository can be isolated in their own worktree so their changes wait to be reviewed and brought back.

Out of scope:

- A malicious user with shell access. They already have shell access.
- A setup where `HEARTH_AUTO_APPROVE=1` intentionally removes approval prompts.
- Side-channel attacks on a model provider's own service.
- A guaranteed sandbox against every possible tool or agent escape. Inglen sanitizes arguments and avoids elevated execution, but it should not be run as administrator or root.

## Known sharp edges

- **Prompt injection in Auto mode.** Content from a page, file, email or message can contain instructions aimed at the model rather than the person. Inglen's approval/reviewer system blocks many risky actions, but a plain outbound web request can still be used to carry data if a compromised model constructs one after reading private content. This is an open issue. Use **Ask** or **Auto-edit** when working with untrusted external content until the outbound-request guard is shipped.
- **`run_command`** runs arbitrary shell commands. Destructive commands and redirects to disk ask before running. An approved destructive command does run. Do not blanket-allow it unless you trust the model and the task.
- **`edit_file` / `write_file`** can damage files if the model writes the wrong thing. Writes are confined to the active workspace by default; file snapshots make ordinary edits reversible, but that is not a substitute for backups.
- **`open_app`** can launch executables found on the system. It is permission-gated.
- **`web_fetch`** can be steered toward internal/private network addresses by the model. Inglen does not currently block private ranges. Do not give the agent unrestricted network access on a network where sensitive admin pages rely only on being local.
- **`browse`, `browse_click`, `browse_type` and `browse_key`** drive a real Chromium session. Opening/reading can happen without an input approval; clicks, typing and key presses use the input permission path. A prompt-injected page may try to steer the model, so keep the approval mode appropriate to the site and task.
- **Tool-call parsing** supports several model-specific text formats. A model can produce a tool-shaped string the user did not intend. Parsed calls still go through the same permission checks as native tool calls.
- **The local API** binds to localhost and rejects requests from other browser origins/sites. This is important because the UI can call powerful local routes. Non-browser local programs are trusted by design so the terminal, bridges and MCP clients can connect.
- **Shared memory** can expose Inglen memory and past chats to coding agents you explicitly enable through the MCP memory server. Those agents may send the retrieved content to their own provider under that provider's terms.
- **The built-in llama.cpp server** uses Inglen's own local port (8790 by default; configurable) and the local image engine commonly uses 8189. Inglen tries to stop child processes on exit, but a hard process kill can still leave a child alive briefly. A restart cleans up the managed server state.
- **`model_configs.json`** is plain JSON in the active Inglen workspace. It stores per-model local load settings. Treat it as local user data.
- **Installer trust is not fully solved yet.** Code-only update patches are signed and file hashes are checked. Downloaded Windows installers are checked against the digest published with the GitHub release, but the installer is not Authenticode-signed and its release digest is not yet separately signed by Inglen's release key. The bundled/downloaded third-party runtimes also need the same level of release verification before we describe the supply chain as fully signed.
- **Relay room knowledge is still a low-probability denial-of-service edge.** Room ids are 128-bit values derived from pairing material and are not enumerable. If one were somehow disclosed, the relay currently does not require the phone to prove the pairing secret before occupying that room. Frames remain encrypted, so this is about disruption rather than reading their contents. A phone proof is planned.

## What is already hardened

The current development line includes fixes for issues found in the October 2026 security review:

- untrusted text can no longer turn a modal into executable HTML on the local app origin;
- browser requests from other sites are refused by the local API;
- write confinement resolves real paths so links cannot carry a write out of the workspace;
- model output cannot forge the internal approval fields used by tool calls;
- Plus relay access validates both the licence and its activation instance rather than accepting a copied key alone;
- licence credentials no longer need to travel in the relay URL;
- relay sockets have frame-rate limits;
- downloaded installers are hash-checked before Inglen will launch the file it downloaded;
- code update patches are signed, hash-checked and recover from an interrupted apply;
- MCP child processes do not inherit password/token/key-like environment variables by default;
- chat-app bridges gate messages by the approved sender identity.

## Supported versions

Inglen is pre-1.0 and ships rapidly. Only the latest public release receives security fixes. If you are running an older build, upgrade before reporting a problem that may already have been fixed.

## Conversation style is not a security boundary

The assistant's name, tone and personality are user-facing preferences. They do not turn permissions, sandboxing or approval checks on or off. Security decisions live in the tool and runtime boundaries above, not in a persona prompt.