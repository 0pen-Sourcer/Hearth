<h1 align="center">Hearth 🔥</h1>

<p align="center">
  <strong>A local AI for your machine. It talks. It listens. It actually does things.</strong>
</p>

<p align="center">
  No cloud. No API keys. No subscription. <strong>Ships with its own built-in LLM server</strong> - first run picks a model that fits your GPU and downloads it from Hugging Face. Or point it at LM Studio / Ollama / vLLM / a cloud key. Your call.
</p>

<p align="center">
  <a href="#-install-in-60-seconds"><img src="https://img.shields.io/badge/install-60s-orange?style=for-the-badge" alt="Install in 60s"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/local--first-yes-success?style=for-the-badge" alt="Local-first">
  <img src="https://img.shields.io/badge/python-3.11%2B-yellow?style=for-the-badge" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Windows-blue?style=for-the-badge" alt="Windows">
</p>

<p align="center">
  <em>Codename: JARVIS. The agent still introduces itself that way — it's the resident, Hearth is the house.</em>
</p>

---

<p align="center">
  <!-- TODO: record 20s screen-cap, drop as docs/demo.gif (or .webp/mp4), update src -->
  <img src="docs/demo.gif" alt="Hearth — talking, listening, opening apps, reading files, all locally" width="720">
</p>

<p align="center">
  <em>Above: you say "Jarvis, find the biggest folders on D, open the top one." It speaks back, runs the scan, opens Explorer. All on your machine. <strong>No screenshot is staged — that's the actual CLI.</strong></em>
</p>

---

## Why Hearth exists

Every "personal AI" project on GitHub is one of three things:

1. **A chat UI** that wraps a cloud API (LibreChat, Open WebUI, big-AGI). Beautiful, but it's just chat — it can't open your files, can't speak to you, can't *do* anything on your machine.
2. **A coding agent** (Aider, Cline, Continue, OpenInterpreter). Powerful, but scoped to "write code in this folder" — not "be the AI on my PC."
3. **A cloud-locked assistant** (Pi, Claude.ai, ChatGPT). Great until they change the rules, lose your data, deprecate the model you liked, or you go offline.

**Hearth is the fourth thing.** A local-first operator that runs on the model you already have, controls your actual computer, talks back, and listens — and **none of it ever leaves your machine** (except DuckDuckGo searches, opt-in).

It's the project I'd want if I just got an RTX card and downloaded LM Studio. "Cool, now what?" → this.

---

## Feature comparison

| Feature                       | **Hearth** | Open WebUI | OpenInterpreter | LibreChat | Aider |
| ----------------------------- | :--------: | :--------: | :-------------: | :-------: | :---: |
| Runs 100% locally             |     ✅     |     ✅     |        ✅        |     ⚠️     |    ⚠️   |
| Voice in (mic)                |     ✅     |     ❌     |        ❌        |     ❌     |    ❌   |
| Voice out (TTS)               |     ✅     |     ❌     |        ❌        |     ❌     |    ❌   |
| Voice **interrupt** (talk over it) | ✅    |     ❌     |        ❌        |     ❌     |    ❌   |
| File read / write / edit       |     ✅     |     ❌     |        ✅        |     ❌     |    ✅   |
| **Read PDF / DOCX / XLSX / EPUB / ZIP**  | ✅ | text-only | text-only | text-only | text-only |
| Launch any app on your PC      |     ✅     |     ❌     |     partial      |     ❌     |    ❌   |
| Screenshot + vision            |     ✅     |     ❌     |        ❌        |     ❌     |    ❌   |
| Web search + fetch             |     ✅     |     ✅     |        ❌        |     ✅     |    ❌   |
| **Drives a real browser (watch it click)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| Persistent fact memory         |     ✅     |   limited  |        ❌        |     ✅     |    ❌   |
| **Learns your machine (hardware · models · drives)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP bridge for LM Studio chat  |     ✅     |     ❌     |        ❌        |     ❌     |    ❌   |
| **Writes its own tools (local, self-improving)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| Personality / "Jarvis" vibe    |     ✅     |     ❌     |        ❌        |     ❌     |   ish  |

The unique combo nobody else has: **voice loop + computer control + writes its own tools + local-only**.

---

## ⚡ Install in 60 seconds

> **You need:** Windows 10/11, Python 3.11+, an LLM server (we recommend [LM Studio](https://lmstudio.ai) — free, GUI, one-click model load).

```powershell
# 1. Clone
git clone https://github.com/0pen-sourcer/hearth.git
cd hearth

# 2. Install (creates venv, installs deps, auto-downloads voice models)
.\install.ps1

# 3. Start LM Studio, load a chat model, click "Start Server".

# 4. Launch
.\hearth.bat
```

That's it. Type, or `/voice on` to speak, or `/listen on` to listen. Say "bye" when done.

### Interfaces

| Interface | Launch | Status |
|---|---|---|
| **CLI** (voice + keyboard) | `.\hearth.bat` | **v0.5 daily driver.** Voice loop with TTS interrupt, prompt_toolkit history, full slash commands, inline `[y/n/a/N]` permission prompts, context-usage footer. |
| **Bridge** (programmatic) | `python -m hearth.headless --prompt "..."` | Stable. JSONL events to stdout — drive Hearth from CI, scripts, other agents. |
| **MCP** (LM Studio chat) | `python -m hearth.mcp_server` | Stable. Exposes every built-in tool as a live tool-card inside LM Studio's native chat UI. |
| **Desktop app + Web UI** | `python -m hearth.tray --open` | **Preview — v0.6 work-in-progress.** Multi-chat sidebar, file drop, native window, system tray, wake word, conversations that persist across restart. Working but a few rough edges vs the CLI. See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md). |

**No LM Studio?** Anything OpenAI-compatible works — Ollama (with its compat layer), vLLM, llama.cpp, LocalAI. Set `LOCAL_API_BASE` to point at it.

**Mac / Linux:** Not officially supported in v0.5. Most of the codebase is cross-platform; what isn't is shell defaults, app-launching, and screenshot. PRs welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## What Jarvis can actually do

**Talk to him.** Voice in (faster-whisper), voice out (Kokoro), with mid-sentence interrupt: you start talking, he shuts up and listens. No "wait for him to finish" awkwardness.

```
❯ /listen on
listening: ON — talk any time. type to interrupt.

🎙 "Jarvis, what's eating up space on my D drive?"
> running disk scan...
🔧 disk_usage(path="D:", min_size_mb=500)
Top hits on D:: Games\Steam (340GB), Backups\2024 (89GB), ML\models (52GB)...
```

**Open anything on your PC.** Apps, files (videos in your default player, archives in your archive tool, folders in Explorer), URLs, Start Menu shortcuts. One tool: `open_app`. Just say "open Brave" or "play that movie I downloaded last night."

**See your screen.** Attach any image with `@C:/path/screenshot.png` and he reads it via the LLM's vision (works with Gemma 3, Qwen-VL, Llava, etc). Or call `screenshot` and have him describe what's on screen.

**Read any file.** Drop a PDF, DOCX, XLSX, PPTX, EPUB, IPYNB, CSV, JSON, HTML, RTF, or `.gz`/`.bz2`/`.xz` stream — `read_file` extracts clean text automatically (pypdf, python-docx, openpyxl, python-pptx, stdlib). For archives, `list_archive` peeks inside and `extract_archive_file` pulls one file out without unpacking. For a quick TL;DR, `summarize_file` returns content pre-framed for a 3-5 bullet summary. No "this is binary, paste the text" excuses.

**Run commands.** Real PowerShell or cmd, sandboxed. He picks the right tool for "find the biggest 10 folders" instead of writing a bad `Get-ChildItem` pipeline.

**Remember you across sessions.** Per-fact markdown files in `~/Jarvis/memory/`. The index is always loaded; the facts most relevant to your message are folded into context automatically (fenced as authoritative reference), so he *uses* what he knows instead of re-asking. Type `user`, `feedback`, `project`, or `reference` — he files things correctly, and saves new facts on his own.

**Knows your machine.** On first run (and via `/learn` anytime) Hearth detects your GPU/VRAM, RAM, the models on your server, and a top-level map of your drives, and remembers them. So he answers "what models do I have?" instantly and goes straight to the right folder — instead of burning two minutes recursively scanning a drive and finding nothing.

**Drives a real web browser — and you watch it.** Not static scraping: `browse` opens a controlled Chromium (you see the window), reads the rendered page, and lists every clickable link/button; `browse_click` glides a visible cursor to an element and clicks it; `browse_type` fills fields. The session persists, so it browses multi-step (search → click result → read → click again). `pip install playwright && python -m playwright install chromium` to enable. (Best on a capable model — point it at Grok/Gemini for the slick demo.)

**Grows its own tools.** This is the part nobody else does locally: when Hearth hits a capability it doesn't have, it can **write a new tool for itself** with `create_plugin` — validated, saved to `~/Jarvis/plugins/`, and usable the same turn. They auto-load forever after. You can hand-write plugins too (a `TOOL` dict + a `run(args)` function). `list_plugins` / `delete_plugin` to manage them. It's a self-improving agent — except 100% local, private, no account, no telemetry.

```
❯ you don't have a base64 tool — make one and encode "hi jarvis"
🔧 create_plugin(name="base64_tool", code="...")
   → Plugin 'base64_tool' created + loaded. Available NOW.
🔧 base64_tool(text="hi jarvis")
hi jarvis → aGkgamFydmlz
```

**Edit a `rules.md` to tweak behavior.** Re-read every turn. No code edits, no restarts.

**Web search + fetch** via DuckDuckGo. Free. No API key.

**Permission prompts** for risky stuff (writes, shell commands, app launches). `[y]es / [n]o / [a]lways / [N]ever this session`. Skip with `JARVIS_AUTO_APPROVE=1` once you trust him.

**Headless / scriptable mode.** Drive Jarvis from another script, another agent, a CI job — no typing required:

```powershell
python -m hearth.headless --prompt "find my biggest folders on D and open the top one" --format text
```

Emits JSONL events (user / thinking / tool_call / tool_result / assistant / done) to stdout — pipe it anywhere. `--think` to see model reasoning, `--model qwen/qwen3.5-9b` to pin the model, `--format text` for human eyeballs.

[Full tool list →](docs/TOOLS.md) · [Voice setup →](#voice-setup) · [User guide →](docs/USER_GUIDE.md)

---

## How it works

```
                   ┌────────────────────────┐
   YOU (mic) ─────▶│  faster-whisper (CPU)  │─── text ───┐
                   └────────────────────────┘            │
                                                          ▼
   YOU (kbd) ──────────────────── text ─────────▶ ┌──────────────┐
                                                   │hearth_cli.py │
                                                   │   (the CLI)  │
                                                   └──────┬───────┘
                                                          │
                                            messages + tools (OpenAI format)
                                                          ▼
                            ┌─────────────────────────────────────┐
                            │   LM Studio / Ollama / vLLM (local) │
                            │   any chat model — Gemma, Qwen, …   │
                            └─────────────────────┬───────────────┘
                                                  │ stream + tool_calls
                                                  ▼
                                    ┌───────────────────────┐
                                    │  hearth/tools.py      │
                                    │  48 tools, sandboxed  │
                                    │  + your own plugins   │
                                    └────────┬──────────────┘
                                             │
                              files · shell · web · apps · memory
                                             │
                                             ▼ result text
                       (loops until done — a loop-guard stops spirals)
                                             │
                                             ▼
   YOU (ears) ◀── Kokoro TTS ◀── sentence chunks ◀── streamed reply
```

Same `execute_tool` is also exposed via [`hearth/mcp_server.py`](hearth/mcp_server.py) so **LM Studio's native chat UI** sees every tool as a live card. Same tools, same memory, same workspace — whether you're in the CLI or LM Studio's chat.

---

## Workspace layout

```
~/Jarvis/                  ← the agent's home (override with $env:JARVIS_WORKSPACE)
├── memory/
│   ├── MEMORY.md          ← always-loaded index
│   └── <fact>.md          ← per-fact files with frontmatter
├── logs/
│   ├── activity.jsonl     ← every tool call, JSONL
│   └── jarvis_history.json
├── voices/                ← drop Kokoro + Whisper model files here
├── screenshots/
└── rules.md               ← plain-text rules, re-read every turn
```

Reads default to **unrestricted** (your whole disk — he needs to *know* your machine). Writes/deletes/moves are **confined** to the workspace. Override with `JARVIS_LOCKDOWN=1` to also confine reads.

---

## Voice setup

**Voice OUT (Kokoro TTS).** ~80 MB ONNX, runs on CPU in real-time, zero VRAM contention with your LLM.

```powershell
pip install kokoro-onnx sounddevice numpy
# the installer drops these into ~/Jarvis/voices/ automatically:
#   kokoro-v1.0.onnx       (or .fp16.onnx)
#   voices-v1.0.bin
```

In the CLI: `/voice on`. Pick a voice with `$env:JARVIS_VOICE=bm_george` (British male Jarvis), `am_michael` (default US male), or [any of the others](https://github.com/thewh1teagle/kokoro-onnx#voices).

**Voice IN (faster-whisper).** ~150 MB `base.en` model, also CPU, also real-time.

```powershell
pip install faster-whisper
# model auto-downloads on first /listen on
```

`/listen` for one-shot, `/listen on` for continuous-with-interrupt mode.

[More in the user guide →](docs/USER_GUIDE.md)

---

## Configuration (env vars)

| Variable               | Default                  | Purpose |
| ---------------------- | ------------------------ | ------- |
| `LOCAL_API_BASE`       | `http://localhost:1234/v1` | OpenAI-compatible endpoint |
| `LOCAL_MODEL`          | auto-detected             | Override LM Studio's loaded model |
| `JARVIS_WORKSPACE`     | `~/Jarvis`                | Where memory/logs/voices live |
| `JARVIS_LOCKDOWN`      | `0`                       | `1` = confine reads to workspace too |
| `JARVIS_CONTEXT`       | auto-detected from LM Studio | Tokens of context to use |
| `JARVIS_VOICE`         | `am_michael`              | Kokoro voice ID |
| `JARVIS_VOICE_SPEED`   | `1.5`                     | TTS playback rate (`/voice speed <n>` to change live) |
| `JARVIS_AUTO_APPROVE`  | `0`                       | `1` = skip risky-tool prompts |
| `JARVIS_EXTRA_WORKSPACES` | (none)                | Extra paths writes are allowed in |
| `JARVIS_WAKE_WORD`     | (none — accept all)       | If set (e.g. `"jarvis"` or `"hey jarvis"`), `/listen on` only triggers a turn when the utterance starts with it |

`HEARTH_*` env vars are also accepted everywhere `JARVIS_*` is — pick whichever feels right.

The table above is the full env-var reference.

---

## CLI reference (the slash commands)

```
/help                 full list
/models, /model <n>   list / switch LM Studio model
/tools                list available tools
/voice [on|off]       TTS toggle
/listen [on|off]      continuous voice input
/listen               one-shot voice input
/mem                  show memory index
/log [n]              tail last n activity entries
/compact              summarize old turns + extract facts to memory
/context <n>          set context window (overrides auto-detect)
/think [on|off]       show/hide model reasoning
/allow <path>         add write access to an extra folder this session
/perms                show cached tool permissions
/clear                wipe history (keep system prompt)
/exit                 quit
```

Plus: `@<path>` to attach a file (text → spliced inline; images → vision), arrow-key history, Ctrl-R reverse search, `/multi` for multi-line mode.

---

## FAQ

**Q: Does it work without LM Studio?**
Yes — anything OpenAI-compatible. Point `LOCAL_API_BASE` at Ollama (`http://localhost:11434/v1`), vLLM, llama.cpp's server, LocalAI, etc.

**Q: Can I point it at a cloud model instead?**
Yes, optionally. Local is the default and the whole point — but if you want to feel the ceiling, set `LOCAL_API_BASE` + `LOCAL_API_KEY` + `LOCAL_MODEL` to any OpenAI-compatible cloud (Gemini, xAI/Grok, OpenAI, OpenRouter) and Hearth runs against it. A frontier model nails the multi-step chains a 9B sometimes fumbles. Your files/voice/memory stay local either way; only the prompt goes to the provider you chose.

**Q: Which model should I use?**
Anything ~7B+ that supports OpenAI-style tool-calling. Tested on ~8GB VRAM: **Harmonic-Hermes-9B (Q4_K_M)** has the best tool adherence and is the recommended default; **Qwen 3.5 9B** is a solid fallback. Avoid Gemma-3-family models for now — they emit tool calls as raw text instead of the structured format, so Hearth can't parse them.

**Honest expectation:** small local models are great for everyday tasks (open this, read that, remember this) but they can fumble *long* multi-step chains — over-fetching, re-running a search, occasionally not knowing when to stop. Hearth has a loop-guard that catches and breaks these, but for genuinely complex work (deep web research, driving the browser through several pages) point it at a capable model: set `LOCAL_API_BASE`+`LOCAL_API_KEY`+`LOCAL_MODEL` for Grok/Gemini/etc. Local stays the default and the point; cloud is there when you want the ceiling.

**Q: Is anything sent to the cloud?**
Only DuckDuckGo HTML scraping for `web_search`/`web_fetch`, and only when the model invokes them. Everything else — files, shell, voice, memory — is on your machine. No telemetry. No analytics. No phone-home.

**Q: Can it actually use my GPU?**
The LLM does (via LM Studio / your inference server). Whisper is CPU-by-default (override with `JARVIS_STT_DEVICE=cuda`). Kokoro is CPU.

**Q: Will it nuke my files?**
Writes are sandboxed to `~/Jarvis/` by default. Risky tools prompt you the first time per session. `JARVIS_AUTO_APPROVE=1` removes the prompts once you trust it.

**Q: Mac / Linux?**
Not yet (v0.5). The codebase is mostly portable — `tools.py` has Windows branches for app-launching and registry access that need POSIX equivalents. [Help wanted →](https://github.com/0pen-sourcer/hearth/labels/help%20wanted)

**Q: Does it work offline?**
Everything except `web_search` / `web_fetch` works fully offline.

**Q: Why "Hearth"?**
A hearth is the warm center of a home. That's what this is — the AI that lives on your PC and becomes the center of how you work. The character we ship by default is **Jarvis**, but Hearth is the framework — you can swap personas, voices, models, anything.

---

## Contributing

Hearth started as one person's tool. The faster it generalizes, the better it gets for everyone.

**Easy wins for first PRs:**
- Mac / Linux ports of Windows-specific tools ([`hearth/tools.py`](hearth/tools.py), look for `if platform.system() == "Windows"`)
- More personas in [`hearth/personas/`](hearth/personas/) — JARVIS is just one
- More voice presets in [`hearth/voice.py`](hearth/voice.py)
- New tools (the pattern is one function in `tools.py` + a definition in `TOOL_DEFINITIONS`)

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide. Discussion happens in [GitHub Discussions](https://github.com/0pen-sourcer/hearth/discussions).

---

## Support

Hearth is free and built by one high-schooler in spare time. The best thing you can do is **⭐ star the repo** — it's the #1 way other people discover it. Want to go further? See [docs/SUPPORT.md](docs/SUPPORT.md). No pressure, ever — a star or a bug report helps just as much as a coffee.

---

## Author

Built by **[@0pen-sourcer](https://github.com/0pen-sourcer)** — a high-schooler who wanted a real local Jarvis and decided to share it. Reach me via [Issues](https://github.com/0pen-sourcer/hearth/issues) or [Discussions](https://github.com/0pen-sourcer/hearth/discussions).

---

## License

[MIT](LICENSE) © [@0pen-sourcer](https://github.com/0pen-sourcer). Do anything you want with it. Attribution appreciated but not required.

---

## Acknowledgements

- [Kokoro TTS](https://github.com/thewh1teagle/kokoro-onnx) — the voice that makes this feel real
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — the ears
- [LM Studio](https://lmstudio.ai) — the easiest local LLM runner that exists
- The [MCP spec](https://modelcontextprotocol.io) for letting Hearth slot into chat UIs natively
- Every "local AI" repo that came before — we learned what NOT to do from a lot of them

---

<p align="center">
  <strong>If Hearth helps you, ⭐ the repo — that's how other people find it.</strong>
</p>
