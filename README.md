<h1 align="center">Hearth</h1>

<p align="center">
  <strong>The local AI that actually runs your computer.</strong>
</p>

<p align="center">
  It talks. It listens. It opens your apps, reads and writes your files, drives a real browser you can watch, and remembers you, all on your own machine.<br>
  No account. No cloud required. No telemetry.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-yellow" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Windows">
  <img src="https://img.shields.io/badge/local--first-yes-success" alt="Local-first">
  <img src="https://img.shields.io/badge/status-v0.7--preview-orange" alt="v0.7-preview">
</p>

<p align="center">
  <em>The framework is <strong>Hearth</strong>. The assistant it ships with is named <strong>JARVIS</strong>, rename it to anything. The resident, and the house.</em>
</p>

---

## Why Hearth exists

The smartest AI in the world lives in someone else's cloud. It's brilliant, and it can't touch your computer, it forgets you the moment the tab closes, and the meter never stops.

So you ran a model locally. Now what? Most "local AI" projects are one of three things:

1. **A chat UI** around a model (LibreChat, Open WebUI, big-AGI). Beautiful, but it's just chat. It can't open your files or *do* anything on your machine.
2. **A coding agent** (Aider, Cline, Continue, Open Interpreter). Powerful, but scoped to "write code in this folder," not "be the AI on my PC."
3. **A cloud-locked assistant** (ChatGPT, Claude, Pi). Great, until they change the rules, deprecate your model, or you go offline.

**Hearth is the fourth thing.** A local-first operator that runs on the model you already have, controls your actual Windows PC, files, shell, apps, browser, the desktop itself (clicks and types), screenshots, voice, talks back, listens, and remembers you across sessions. Nothing ever leaves your machine except a web search, and only when you ask.

And it keeps growing: **skills are shareable.** A skill is a folder that teaches Hearth a workflow ("clean up my Downloads", "turn this folder of photos into a contact sheet"). Installing one someone else wrote is a single line, `/skill install someone/their-repo`, and writing your own is one command.

Reach it however you work: a terminal **CLI**, a desktop/web **app**, a headless **bridge**, or an **MCP server**.

> **v0.7-preview**, the CLI and desktop app are the daily drivers. Voice and the bundled llama.cpp server work but are preview-quality (see notes below). Windows is the supported platform; macOS/Linux run from source with most tools working.

Where it's headed, grounded computer-use (it watches the screen, points, and acts), a guided "walk me through this" mode, and Mac/Linux, is in the **[Roadmap](ROADMAP.md)**.

---

## Install

You need **Windows 10/11** and **Python 3.11+**.

```powershell
git clone https://github.com/0pen-sourcer/hearth.git
cd hearth

# Install. Pick how you want to run the model:
.\install.ps1                      # bring your own server (LM Studio / Ollama / vLLM / llama.cpp / a cloud key)
.\install.ps1 -BuiltinLLM cuda     # NVIDIA GPU: Hearth installs + runs its own llama.cpp server
.\install.ps1 -BuiltinLLM cpu      # CPU-only: same, no GPU

# Launch
.\hearth.bat
```

The installer is idempotent (safe to re-run) and has switches to skip optional pieces, voice, STT, MCP SDK, file readers, desktop window, browser control. Run `Get-Help .\install.ps1 -Detailed` for the full list.

On first launch, a short onboarding flow asks which model brain to use, sets up voice, and personalizes the assistant.

### Pointing it at a model

Hearth auto-detects a running **LM Studio** (port 1234), **Ollama** (11434), or **llama.cpp** server (8080) at boot, no configuration needed. To use something else, set `LOCAL_API_BASE` to any OpenAI-compatible endpoint.

A cloud key is optional. In the desktop app's Settings (or via env vars), you can point the chat brain at Gemini, OpenAI, Grok, or OpenRouter and switch back to local at any time without restarting. Files, voice, and memory stay local regardless; only the prompt goes to the provider you choose.

### Model recommendation

Any ~7B-or-larger model with OpenAI-style tool-calling works. On ~8GB VRAM, tool adherence is best on recent tool-trained models. Small local models handle everyday tasks well (open this, read that, remember this) but can fumble long multi-step chains; a built-in loop guard catches and breaks those spirals. For heavier reasoning (deep web research, multi-page browser sessions) a larger or cloud model helps.

Hearth ships a tool-call parser that recognizes the formats emitted by Gemma, Hermes, Qwen 2.5/3, Llama 3.x, Mistral, Phi, Granite, and Cohere Command-R, plus a generic `<function=NAME>` form, so models whose tool calls aren't natively parsed by the server still work.

### macOS / Linux

Hearth runs from source on macOS and Linux. The CLI and web UI work, and most tools (shell, files, screenshots, app launching, window focus, clipboard, web search, reminders, notifications) have native POSIX paths. That said, both get far less mileage than Windows, so treat them as lightly tested and expect rough edges. The desktop-control layer, the one-click installer, and the packaged build are Windows-only for now; everywhere else you clone and run with Python. See **[docs/INSTALL_LINUX_MAC.md](docs/INSTALL_LINUX_MAC.md)** for the exact steps, and [CONTRIBUTING.md](CONTRIBUTING.md) if you want to help port the rest.

---

## Interfaces

| Interface | Launch | Notes |
|---|---|---|
| **CLI** | `.\hearth.bat` | Terminal app with voice loop, command history, slash-command autocomplete, model control, and a context-usage footer. |
| **Desktop app / Web UI** | `python -m hearth.tray --open` (or `Hearth.exe` from a release build) | Native window with multi-chat sidebar, a Models tab for downloading and loading GGUFs, voice mode, file drop, inline permission prompts, and a settings panel. Same backend as the CLI. |
| **Bridge** | `python -m hearth.headless --prompt "..."` | Non-interactive. Emits JSONL events to stdout so you can drive Hearth from scripts, CI, or another agent. `--format text` for human-readable output. |
| **MCP server** | `python -m hearth.mcp_server` | Exposes Hearth's tools to any MCP-aware chat host (LM Studio, Cline, Claude Desktop, Cursor). |

---

## What it can do

**Files.** Read, write, edit, list, move, delete. `read_file` extracts clean text from PDF, DOCX, XLSX, PPTX, EPUB, IPYNB, CSV, JSON, HTML, RTF, and single-stream `.gz`/`.bz2`/`.xz`. It peeks into and pulls files out of zip/tar archives without unpacking them, and can summarize very large PDFs, hundreds of pages that don't fit in context, by working through them in chunks.

**Shell.** Real PowerShell or cmd commands, with output captured and sanitized.

**Apps and URLs.** Open any installed app, file, folder, Start Menu shortcut, or URL with one tool. Media opens in your default player, archives in your archive tool, folders in Explorer.

**Browser.** Drive a real Chrome / Brave / Edge session you can watch: `browse` opens a page and lists its clickable elements, `browse_click` scrolls the target into view and clicks it, `browse_type` fills fields. The session persists across calls for multi-step flows.

**Desktop control.** Beyond the browser, Hearth can operate the actual desktop. `desktop_snapshot` reads a window's real buttons, fields and menu items as a list (an accessibility snapshot, precise, not pixel-guessing), then `desktop_click` / `desktop_type` act on them by name. It can also move the mouse, click, type, and press key combos directly, you watch the real cursor move. Every action that changes something asks first. Windows for now (cross-platform in progress).

**Screen and vision.** Take a screenshot, or attach an image, and have a vision-capable model describe it.

**Web.** Search and fetch pages via DuckDuckGo. No API key.

**Memory.** Per-fact markdown files that persist across sessions. The index is always loaded and the facts most relevant to your message are folded into context automatically. Memory self-curates: each fact tracks how often it's recalled, cold facts archive automatically (never deleted) when a category grows too large, and an archived fact warms back to active storage once it's recalled enough. When you save something that overlaps an existing fact, Hearth notices and decides whether to update or add.

**Reminders.** One-shot or recurring, set in natural language ("in 25 minutes", "every weekday at 9am"). A reminder can also fire a tool when it pops (for example, run a web search and include the result in the toast). Reminders that came due while Hearth was closed surface on next launch, in the CLI they print into the chat, since Windows often suppresses the toast. Set an [ntfy](https://ntfy.sh) topic and reminders also push to your phone, so they reach you even when the PC is asleep.

**Phone.** Reach Hearth from your phone through a **Telegram** or **Discord** bot, bot token only, no OAuth, no public server to host. Message the bot and it runs the same agent loop as the CLI, replies (long answers chunked), and sends back any file it produced. An allowlist (your chat/user id) is the only gate. Configure it in the desktop app under **Settings → Reach from phone** (or hand-edit the config); reminders can also push to your phone via [ntfy](https://ntfy.sh). An experimental WhatsApp bridge exists too (unofficial, see the caveats). Opt-in; full setup in [docs/PHONE.md](docs/PHONE.md).

**Skills, and you can share them.** A skill is a folder with a `SKILL.md` that teaches Hearth a repeatable workflow; the model sees a one-line summary of each and loads the full steps only when it uses one, so dozens can be installed without bloating context. Built-in skills cover PDFs, slide decks (PPTX), spreadsheets (XLSX), diagrams (SVG/HTML), and ASCII art (plus PDF split/merge). The part that compounds: **install a skill someone else wrote with one line**, `/skill install owner/repo` (or paste a GitHub link in chat), and publish your own by pushing a folder to GitHub. Install discloses what a skill can do and asks before it lands; its scripts only ever run through the same permission prompt as any command. Write once, share with a link, see [docs/SKILLS.md](docs/SKILLS.md) and the community index, [awesome-hearth-skills](https://github.com/0pen-sourcer/awesome-hearth-skills).

**Sub-agents.** Fork focused workers that run with a tightened system prompt and a restricted tool allowlist. Six personas ship: researcher, coder, archivist, librarian, summarizer, and a PDF coordinator that fans out summarizer workers over a document and reduces their results. Workers run synchronously or in the background; background results arrive in your next chat turn rather than blocking. Fork depth is bounded to prevent runaway spawning.

**Watch a team build.** Ask for a team, "one frontend, one backend, one architect", and Hearth spawns each as a sub-agent and opens a live terminal pane per agent (Windows Terminal split-panes, or tmux on Linux/macOS), so you watch them think, call tools, and finish side by side. Opt-in; the agents act on their own once you ask, so it only runs when you do.

**Background jobs.** Long-running operations (whole-drive scans, big searches) return a job ID immediately and run in a background thread while you keep working. List jobs and collect results when they finish.

**MCP, both directions.** Hearth exposes its own tools as an MCP server, and it also consumes other MCP servers: drop an `mcp.json` in the workspace and their tools appear alongside the built-ins.

**Voice.** Text-to-speech (Kokoro) streams sentence by sentence; speech-to-text (faster-whisper) supports a continuous-listen mode with mid-sentence barge-in, start talking and the current reply stops. Speech-to-text auto-detects CUDA and runs on the GPU when one is available, falling back to CPU. Preview-quality at v0.7.

**Self-extending tools.** When Hearth hits a capability gap, it can write a new tool for itself with `create_plugin`, validated, saved to the workspace, and usable the same turn. You can also hand-write plugins (a `TOOL` dict plus a `run(args)` function); any `*.py` in the plugins folder auto-loads at startup.

**Identity.** A `soul.md` file rides at the top of every system prompt. The assistant can write its own durable identity instructions there, and you can rename the whole agent, the chat avatar, persona, and workspace folder all follow.

**Image and video generation.** Optional tools for cloud image/video generation, plus integration with a local Stable Diffusion (Forge) install for fully local image generation.

**A long tool list isn't context bloat.** Hearth has ~100 tools, but the model never sees all of them at once. A core set loads by default; the rest stay behind a `load_tools` meta-tool the model calls on demand and then uses inline, so the per-turn prompt stays small no matter how many tools exist. The model only ever loads what it needs. Set `HEARTH_ALL_TOOLS=1` to load everything up front.

---

## How it works

```
   you (mic) ──▶ faster-whisper ──┐
   you (kbd) ───────── text ──────┤
                                   ▼
                            Hearth core (CLI / app / bridge)
                                   │  messages + tools (OpenAI format)
                                   ▼
                  local server (LM Studio / Ollama / built-in llama.cpp)
                  or an OpenAI-compatible cloud endpoint
                                   │  reply + tool calls
                                   ▼
                            tool executor
                  files · shell · web · apps · browser · memory
                  + your plugins + remote MCP servers + sub-agents
                                   │
                                   ▼  (loops until done; a loop guard stops spirals)
   you (ears) ◀── Kokoro TTS ◀── streamed reply
```

The same tool executor is exposed through `hearth/mcp_server.py`, so any MCP-aware chat host sees the same tools, memory, and workspace as the Hearth CLI or desktop app.

---

## Workspace layout

```
~/Jarvis/                  ← the agent's home (override with $env:JARVIS_WORKSPACE)
├── soul.md                ← self-written identity, loaded into every prompt
├── rules.md               ← plain-text house rules, re-read every turn
├── memory/
│   ├── MEMORY.md          ← always-loaded index
│   ├── <fact>.md          ← per-fact files
│   └── _archive/          ← cold facts (recalled back automatically)
├── logs/                  ← activity log (JSONL) + history
├── voices/                ← Kokoro / Whisper model files
├── screenshots/
├── plugins/               ← auto-loaded custom tools
├── PDFs/ · PPTX/ · XLSX/  ← generated documents, by type
└── subagents/             ← per-worker transcripts
```

Reads default to your whole disk (the assistant needs to know your machine). Writes, deletes, and moves are confined to the workspace unless you grant access to a folder. Set `JARVIS_LOCKDOWN=1` to confine reads to the workspace too.

---

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `LOCAL_API_BASE` | auto-detected | OpenAI-compatible endpoint |
| `LOCAL_API_KEY` | (none) | API key for the endpoint, if it needs one |
| `LOCAL_MODEL` | auto-detected | Override the served model id |
| `JARVIS_WORKSPACE` | `~/Jarvis` | Where memory/logs/voices live |
| `JARVIS_LOCKDOWN` | `0` | `1` = confine reads to the workspace too |
| `JARVIS_AUTO_APPROVE` | `0` | `1` = skip risky-tool permission prompts |
| `JARVIS_EXTRA_WORKSPACES` | (none) | Extra paths writes are allowed in |
| `JARVIS_VOICE` | `am_michael` | Kokoro voice id |
| `JARVIS_VOICE_SPEED` | `1.0` | TTS playback rate |
| `JARVIS_STT_DEVICE` | auto | force `cpu` or `cuda` for speech-to-text (auto-detects GPU when unset) |
| `JARVIS_WAKE_WORD` | (none) | If set, continuous listen only triggers on this prefix |
| `HEARTH_PERSONA_NAME` | `JARVIS` | Assistant name |
| `HEARTH_PERSONA` | (none) | Tone overlay: `bro` / `chill` / `professional` / `formal` |
| `HEARTH_ALL_TOOLS` | `0` | `1` = load every tool up front instead of on demand |
| `HEARTH_NTFY_TOPIC` | (none) | [ntfy](https://ntfy.sh) topic to push reminders to your phone |

`HEARTH_*` and `JARVIS_*` prefixes are interchangeable.

---

## CLI commands

```
/help                 full list
/models, /model <n>   list / switch model
/tools                list available tools
/voice [on|off]       text-to-speech toggle
/listen [on|off]      continuous voice input
/listen               one-shot voice input
/mem                  show memory index
/log [n]              tail recent activity
/compact              summarize old turns + extract facts to memory
/context <n>          set context window
/think [on|off]       show/hide model reasoning
/agent <slug> "..."   dispatch a sub-agent
/jobs [id]            list background jobs / show one's result
/mcp                  MCP status / config
/migrate              import memory from another agent
/name <new>           rename the assistant
/allow <path>         grant write access to a folder this session
/perms                show / reset cached tool permissions
/clear                wipe history (keep system prompt)
/exit                 quit
```

Plus `@<path>` to attach a file (text spliced inline, images sent to vision), arrow-key history, reverse search, and a multi-line input mode.

---

## Voice setup

**Text-to-speech (Kokoro):** ~80 MB ONNX model, runs on CPU in real time. The installer offers to download the model; pick a voice with `JARVIS_VOICE`.

**Speech-to-text (faster-whisper):** the `base.en` model (~150 MB) auto-downloads on first `/listen on`. It auto-detects CUDA and uses the GPU when available, otherwise CPU. Use `/listen` for one-shot or `/listen on` for continuous mode with barge-in.

---

## FAQ

**Does it need LM Studio?** No. Anything OpenAI-compatible works, Ollama, vLLM, llama.cpp, LocalAI, or the bundled llama.cpp server (`-BuiltinLLM`).

**Can it use a cloud model?** Optionally. Set a cloud endpoint in Settings or via env vars. Local is the default; your files, voice, and memory stay local either way.

**Is anything sent to the cloud?** Only web search/fetch (DuckDuckGo), and only when the model invokes them. No telemetry.

**Will it touch my files unexpectedly?** Writes are confined to the workspace by default and risky tools prompt for permission the first time per session. `JARVIS_AUTO_APPROVE=1` removes the prompts.

**Does it work offline?** Yes, except web search/fetch.

**Why "Hearth"?** It's the framework, the warm center of the machine. The default character is JARVIS, but personas, voices, and models are all swappable.

---

## Contributing

Good first PRs: POSIX ports of the Windows-specific tools in [`hearth/tools.py`](hearth/tools.py), more voice presets in [`hearth/voice.py`](hearth/voice.py), and new tools (one function plus a definition in `TOOL_DEFINITIONS`). See [CONTRIBUTING.md](CONTRIBUTING.md).

## Privacy

Hearth runs on your machine and **collects nothing**, no account, no telemetry, no analytics, no server the author operates. Your files, prompts, and memory stay local.

The only things that ever leave your computer are ones **you** turn on, and they go to the service *you* chose, not to us:

- a **cloud model**, if you point the brain at one (Gemini/OpenAI/Grok/OpenRouter), then your prompt goes to that provider;
- **web search / fetch** (DuckDuckGo), when the model looks something up;
- **ntfy** push and the **Telegram** bridge, if you configure them;
- **email**, if you set up the optional IMAP/SMTP tool.

All of those are off by default. Run against a local model with none of them configured and Hearth is fully offline. Because it's a local, no-data-collection tool, there's no privacy policy or terms to agree to, and the [MIT license](LICENSE) is the warranty/liability disclaimer.

## Support

Hearth is free and MIT-licensed, built by one developer. If it's useful to you, a ⭐ is the biggest help, it's how other people find the project. If you'd like to go further, sponsorship options are in [docs/SUPPORT.md](docs/SUPPORT.md).

## License

[MIT](LICENSE) © [@0pen-sourcer](https://github.com/0pen-sourcer).

## Acknowledgements

- [Kokoro TTS](https://github.com/thewh1teagle/kokoro-onnx), text-to-speech
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper), speech-to-text
- [LM Studio](https://lmstudio.ai), local LLM runner
- [Model Context Protocol](https://modelcontextprotocol.io), tool interop
