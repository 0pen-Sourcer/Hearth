<h1 align="center">Hearth 🔥</h1>

<p align="center">
  <strong>A local-first AI for your machine. It talks. It listens. It actually does things.</strong>
</p>

<p align="center">
  <strong>Local-first by default</strong> — point Hearth at LM Studio / Ollama / any OpenAI-compatible local server and it just works. No accounts, no subscriptions, no API keys, no cloud. <strong>Cloud-ready when you want it</strong> — paste a Gemini / OpenAI / Grok / OpenRouter key in Settings → Chat brain and flip it in one click. Same tools, same voice loop, same persona. Your call, your machine.
</p>

<p align="center">
  <a href="#-install-in-60-seconds"><img src="https://img.shields.io/badge/install-60s-orange?style=for-the-badge" alt="Install in 60s"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/local--first-yes-success?style=for-the-badge" alt="Local-first">
  <img src="https://img.shields.io/badge/python-3.11%2B-yellow?style=for-the-badge" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Windows%20%C2%B7%20Mac%20%C2%B7%20Linux-blue?style=for-the-badge" alt="Windows, Mac, Linux">
</p>

<p align="center">
  <em>Codename: JARVIS. The agent still introduces itself that way — it's the resident, Hearth is the house.</em>
</p>

---

<!-- demo.gif lives in docs/demo.gif when present; until the screencap is
     recorded for v0.7-preview, the README leads with the concrete claims below
     instead of a broken-image placeholder. -->

<p align="center">
  <em>Hearth runs entirely on your PC: talks, listens, opens apps, finds files,
  drives a real browser, reads PDFs / DOCX / XLSX / EPUB / ZIP — and writes its
  own tools when it hits a gap. Local-first by default, cloud-ready when you want it.</em>
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
| Works with any OpenAI-compatible local server | ✅ | ✅ | ✅ | partial | partial |
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
| **Self-curating memory (recall-count, archive, warm-back)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Learns your machine (hardware · models · drives)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP **server** (Hearth's tools in any MCP chat) | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP **client** (use OTHER MCP servers' tools)  | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Sub-agents** (fork focused workers, sync or background) | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Auto-background long ops** (no 30-min spinner) | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Migrate from Hermes / OpenClaw** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Generates PDFs / decks / spreadsheets on request**  | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Background workers (results auto-arrive in chat)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Action reminders** (fire a tool, not just a toast) | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Renameable agent** (chat avatar + persona + folder) | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Writes its own tools (local, self-improving)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| Personality / "Jarvis" vibe    |     ✅     |     ❌     |        ❌        |     ❌     |   ish  |

The unique combo nobody else has: **voice loop + computer control + writes its own tools + sub-agent fork + self-curating memory + MCP both directions + local-only**.

---

## ⚡ Install in 60 seconds

> **You need:** Windows 10/11, Python 3.11+.

```powershell
# 1. Clone
git clone https://github.com/0pen-sourcer/hearth.git
cd hearth

# 2. Install. Pick your LLM path:
.\install.ps1                           # bring your own server (LM Studio, Ollama, vLLM, llama.cpp, or a cloud key)
.\install.ps1 -BuiltinLLM cuda          # NVIDIA GPU: Hearth bundles + runs its own llama.cpp server, no external app
.\install.ps1 -BuiltinLLM cpu           # CPU-only: same, no GPU required

# 3. Launch
.\hearth.bat
```

**Mac / Linux:** Hearth is Python — it runs on macOS and Linux too. The `.ps1` installer is Windows-only for now (it bootstraps a venv + installs deps); on Mac/Linux you can install the deps by hand:

```bash
git clone https://github.com/0pen-sourcer/hearth.git && cd hearth
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
chmod +x hearth.sh && ./hearth.sh
```

Tray icon + voice loop are wired for all 3 OSes. The bundled installer + .exe distribution are still Windows-first; PRs welcome to formalize a `.sh` installer and the Mac/Linux build path.

**First-run onboarding** is a 7-step card overlay — pick a brain (local server or cloud key), tune voice, personalize, optionally import memory from a previous agent (Hermes / OpenClaw — auto-skipped if neither is installed), you're in.

Hearth auto-detects **LM Studio** (port 1234), **Ollama** (11434), or **llama.cpp** (8080) at boot — no env config needed. Or open **Settings → Chat brain** to plug in **Gemini / Grok / OpenAI / OpenRouter** with your API key. Cloud and local are first-class; switch any time without restarting.

> **Built-in llama.cpp.** `-BuiltinLLM cuda` / `cpu` at install makes Hearth ship + run its own llama-cpp-python server with a tool-calling model auto-picked for your VRAM. If you'd rather use LM Studio or Ollama, skip the flag — Hearth detects them either way.

Type, or `/voice on` to speak, or `/listen on` to listen. Say "bye" when done.

### Interfaces

| Interface | Launch | Status |
|---|---|---|
| **CLI** (voice + keyboard) | `.\hearth.bat` | **v0.7-preview — daily driver.** Violet HEARTH banner on boot. Voice loop with mid-sentence interrupt, prompt_toolkit history, slash autocomplete. `/models disk \| picks \| hf \| get \| use \| stop` for full model control without leaving the terminal — and `/models use <n>` now live-retargets the running session, no restart. Persistent `[a]lways`/`[N]ever` decisions, context-usage footer. |
| **Desktop app + Web UI** | `python -m hearth.tray --open` (or `Hearth.exe` from the release zip) | **v0.7-preview — polished.** Single-instance lock (click the exe 5× → still one tray icon). Models tab with My Models / Discover / Quick picks sub-nav, inline load-config (GPU offload, ctx, KV cache, threads, flash attn) saved per-model. Settings sidebar with Chat brain / Voice / Behavior / About. File drop + paperclip attach. Realtime voice mode (silero VAD + faster-whisper, 0.3s endpoint, instant barge-in). Inline permission prompts. Cloud endpoint switcher in onboarding + Settings. Same backend as the CLI. |
| **Bridge** (programmatic) | `python -m hearth.headless --prompt "..."` | Stable. JSONL events to stdout — drive Hearth from CI, scripts, other agents. |
| **MCP server** (other LLM chat UIs) | `python -m hearth.mcp_server` | Stable. Exposes every built-in tool as a live tool-card inside any MCP-aware chat UI. |

**Chat brain — pick anything OpenAI-compatible. Local-first, cloud-ready.**
- **Local (recommended):** LM Studio, Ollama, vLLM, llama.cpp, LocalAI — anything OpenAI-compatible. Hearth auto-detects.
- **Cloud (optional):** Gemini, Grok, OpenAI, OpenRouter via Settings → Chat brain. Paste a key, hit "Use this brain" — done. No restart, no re-onboarding.
- **Experimental:** bundled llama-cpp-python server (`-BuiltinLLM cuda` / `cpu`) — works, but tool-call reliability lags LM Studio's. Experimental this release.
- **Mix and match:** switch live mid-session. Voice mode, tools, memory, persona — all unchanged.

**Tool calls work on every modern open model.** Hearth ships a multi-family parser ([hearth/tool_call_parser.py](hearth/tool_call_parser.py)) that recognizes Gemma 3/4's `<|toolcall>` syntax, Hermes / Qwen 2.5 / Qwen 3 ChatML, Llama 3.x `<|python_tag|>`, Mistral `[TOOL_CALLS]`, Phi 3/4, Granite, Cohere Command-R, and any model that emits generic `<function=NAME>` blocks. When the model speaks tool-call syntax that llama-cpp-python's server doesn't natively parse, Hearth catches it, normalizes the tool name (Gemma's `viewimage` → real `view_image`), strips the gibberish from the chat surface, and routes it through the regular tool executor. The same parser feeds the CLI, GUI, and bridge.

**Mac / Linux:** Not officially supported yet. Most of the codebase is cross-platform; what isn't is shell defaults, app-launching, and screenshot. PRs welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## What Jarvis can actually do

**Fork himself to do focused work in parallel — without freezing the chat.** Type *"summarize this PDF using the pdf_coordinator subagent"* and Hearth spawns N background workers, each chewing on a chunk, and a coordinator reduces them into a final summary. You keep chatting while they run; each one's completion arrives as a `<task-notification>` block in your next turn. Sub-agents come with personas (researcher, coder, archivist, librarian, summarizer, pdf_coordinator), tool-allowlist isolation (so an archivist can't write files), cost-class routing (cheap personas force local even when you're on Grok — fan-out of 50 chunks doesn't cost $5), and depth-3 fork-bomb protection. `/agent <slug> "<prompt>"` to dispatch from the CLI. Each subagent leaves a JSONL transcript at `~/Jarvis/subagents/<id>.jsonl` you can `read_file` for live progress.

**Long ops don't freeze the agent.** Drive-root scans (`disk_usage('C:\')`), whole-tree walks, big find_file queries — they can take many minutes. Hearth auto-backgrounds them: the tool returns a `job_id` in milliseconds, the scan runs in a daemon thread, you keep chatting. `/jobs` lists what's running; `/jobs <id>` shows the result when done. Same pattern (via `hearth/jobs.py`) is available for any in-process Python task and any shell command you want to fire and forget.

**Plug into other MCP servers, expose Hearth as one.** Drop a standard `mcp.json` in your workspace, point at any MCP server (filesystem, git, postgres, whatever), and Hearth surfaces those tools alongside its own. Going the other way, Hearth's own toolset is exposed to any MCP-aware client. Same protocol both directions.

**Skills — say what you want, get a real document.** Ask for a deck, a brief, a spreadsheet, a one-pager — Hearth picks the right format, picks a style that fits the topic, writes a fresh build script tailored to YOUR request (no rigid template), runs it, and opens the result. PDFs land in `~/Jarvis/PDFs/`, decks in `~/Jarvis/PPTX/`, sheets in `~/Jarvis/XLSX/` — separated, not dumped in one pile. You can drop your own skill folders into the workspace and they're invokable by name on next launch.

**Background workers that come back on their own.** Tell Hearth to research three things at once, or summarize a 500-page PDF while you keep chatting. It spawns the work in the background, names each worker, and surfaces the results in your chat the moment they're done — no "are you finished yet?" needed. Each worker has its own scoped toolset so a researcher can't write files and a summarizer can't run shell commands.

**Memory that doesn't pile up the same fact three different ways.** When you change something you've told it before, Hearth notices the overlap and asks itself whether to update the existing fact or save as a new one. Your saved profile stays sharp instead of accumulating five contradictory entries.

**Memory that curates itself.** Per-fact markdown files with regex-classified sub-categories. Each fact tracks `recall_count` + `last_recalled_at`. When a sub-category bucket exceeds a soft cap (~6000 chars), the coldest facts auto-archive (move to `_archive/`, never deleted). When the chat surfaces a topic, sibling facts in the same cluster get pulled in too — **including from archive, marked `(cold)`**. After 3 hits, an archived fact auto-promotes back to hot. Hot + cold + warm-back in one system. No other local-AI ships this combo.

**Migrate from Hermes or OpenClaw in one command.** Coming from another agent? `python -m hearth.migrate --from hermes --apply` reads your `~/.hermes/memories/USER.md` + `MEMORY.md` (splits on `\n§\n`), maps each entry to a Hearth memory file, classifies it into the right sub-category. OpenClaw equivalent reads `~/.openclaw/workspace/MEMORY.md` H2 sections + `memory/YYYY-MM-DD*.md` daily notes. Optional flags: `--include-skills` parks SKILL.md dirs under `~/Jarvis/imported_skills/`; `--include-config` pulls the source agent's model/provider into `settings.json`. **API keys are never copied** (security boundary). `/migrate` slash + Settings → Behavior → Migrate panel + onboarding step 6 all surface it.

**Action reminders that actually act.** `set_reminder` now takes an optional `action_tool` + `action_args`. *"At 9am tomorrow, run `web_search` on 'new gpu drivers' and remind me"* → at 9am the tool actually runs, its result is appended to the toast body. Plus: missed-reminder catchup on next launch (reminders set while Hearth was off fire as "while you were away"), snooze tool that resurrects fired one-shots, auto-prune of one-shots older than 30 days. Reminders panel in Settings → Behavior with per-reminder snooze + cancel buttons.

**Rename the whole agent end-to-end.** Default is JARVIS. Hate it? Settings → Behavior → Agent name → "Cortana" → Rename. Triggers: chat avatar letter changes (J → C), persona signature updates, AND `~/Jarvis/` folder atomically renames to `~/Cortana/` (helper subprocess waits for the tray to exit, moves the dir, respawns with the new `JARVIS_WORKSPACE` env). Every memory, conversation, generated file comes along — nothing lost. CLI has `/name Cortana` for the persona swap (folder rename is GUI-only since it needs a tray restart). Onboarding step 5 asks during first-run.

**Talk to him with real barge-in.** Voice in (faster-whisper), voice out (Kokoro), with parallel-mic barge-in: you start talking mid-response, his TTS dies instantly, the in-flight LLM call aborts, and recording starts. No "wait for him to finish" awkwardness. `/sleep` to silence him until you say the wake word, `/wake` to bring him back.

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

**Drives a real web browser — and you watch it.** Not static scraping: `browse` opens YOUR installed Chrome (real one, with codecs, not bot-flagged), reads the rendered page, and lists every clickable link/button; `browse_click` smooth-scrolls the target into view then glides a visible violet cursor and clicks; `browse_type` fills fields. The session persists across calls (multi-step: search → click result → read → click again). 404s are caught so the model stops URL-guessing. **The packaged exe ships Playwright pre-bundled** — zero setup. (Best on a capable model — point it at Grok or Gemini via Settings → LLM endpoint for the slick demo.)

**Grows its own tools.** This is the part nobody else does locally: when Hearth hits a capability it doesn't have, it can **write a new tool for itself** with `create_plugin` — validated, saved to `~/Jarvis/plugins/`, and usable the same turn. They auto-load forever after. You can hand-write plugins too (a `TOOL` dict + a `run(args)` function). `list_plugins` / `delete_plugin` to manage them. It's a self-improving agent — except 100% local, private, no account, no telemetry.

```
❯ you don't have a base64 tool — make one and encode "hi jarvis"
🔧 create_plugin(name="base64_tool", code="...")
   → Plugin 'base64_tool' created + loaded. Available NOW.
🔧 base64_tool(text="hi jarvis")
hi jarvis → aGkgamFydmlz
```

**Schedule reminders, recurring or one-shot.** "Remind me to take a break in 25 minutes" → desktop toast at 25. "Remind me to stretch every 30 minutes" → re-arms after each fire. Natural-language times (`tomorrow at 7am`, `next monday 10am`, `in 2 hours`). Stored in `~/Jarvis/reminders.json`, fired by a background watcher.

**Edit a `rules.md` to tweak behavior.** Re-read every turn. No code edits, no restarts. Or set `HEARTH_PERSONA=bro|chill|professional|formal` to overlay a tone onto the base persona.

**Cloud key for the slick demos.** Open **Settings → LLM endpoint**, pick Gemini / Grok / OpenAI / OpenRouter / Custom, paste your key, click Apply. The next message uses it — no restart. Saved to `~/Jarvis/settings.json` so the CLI picks it up too. Local stays the default; cloud is a transparent flex when you want bigger reasoning.

**Web search + fetch** via DuckDuckGo. Free. No API key.

**Persistent permission prompts** for risky stuff (writes, shell commands, app launches). In the CLI: inline `[y]es / [n]o / [a]lways / [N]ever / or type what to do instead`. In the GUI: an inline keyboard strip above the input (1=Yes, 2=No, 3=Always, 4=Never, Enter=Yes, Esc=No, type any text = decline + tell Jarvis what to do instead). **`Always` and `Never` save to `~/Jarvis/permissions.json` — survive restarts AND are shared between CLI and GUI.** `/perms forget <tool>` drops one; `/perms reset` clears all. Workspace-internal writes (`~/Jarvis/**`) auto-approve.

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
                            │   Built-in llama.cpp server (bundled), │
                            │   or LM Studio / Ollama / vLLM,        │
                            │   or Gemini / Grok / OpenAI (cloud)    │
                            └─────────────────────┬───────────────┘
                                                  │ stream + tool_calls
                                                  ▼
                                    ┌───────────────────────┐
                                    │  hearth/tools.py      │
                                    │  70+ tools, sandboxed │
                                    │  + your own plugins   │
                                    │  + remote MCP servers │
                                    │  + spawn_subagent fork│
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

Same `execute_tool` is also exposed via [`hearth/mcp_server.py`](hearth/mcp_server.py) as an MCP server, so any MCP-aware chat UI (LM Studio, Cline, Claude Desktop) sees every tool as a live card. Same tools, same memory, same workspace — whether you're in the Hearth CLI, the Hearth desktop app, or a third-party chat host.

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
| `JARVIS_VOICE_SPEED`   | `1.0`                     | TTS playback rate (`/voice speed <n>` to change live) |
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
Not yet. The codebase is mostly portable — `tools.py` has Windows branches for app-launching and registry access that need POSIX equivalents. [Help wanted →](https://github.com/0pen-sourcer/hearth/labels/help%20wanted)

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
