# Hearth — architecture deep-dive

A local-only personal AI agent (codename: **JARVIS**). Runs against any
OpenAI-compatible local server (LM Studio, Ollama with the OpenAI compat
layer, vLLM, llama.cpp). No paid APIs. No cloud. Your machine, your model,
your data.

> Looking for the 60-second setup? See [README.md](../README.md). This
> doc is the deeper architectural tour.

---

## Layout

```
hearth/                         # repo root
├── hearth.bat                  # Windows launcher (drop a shortcut on Desktop)
├── hearth_cli.py               # CLI app
├── README.md                   # public-facing readme
├── docs/ARCHITECTURE.md        # this file
└── hearth/                     # the package
    ├── __init__.py
    ├── tools.py                # 38 tools, dispatch, provider format converters
    ├── listen.py               # faster-whisper STT (continuous + one-shot)
    ├── persona.py              # the system prompt
    ├── memory.py               # per-fact files + always-loaded index
    ├── voice.py                # Kokoro / Piper TTS, sentence-streaming
    ├── mcp_server.py           # MCP bridge for LM Studio's chat UI
    └── dev_tools.py            # interactive tool tester / REPL
```

Workspace: `~/Jarvis/` (override with `$env:JARVIS_WORKSPACE`).

```
~/Jarvis/
├── memory/         # MEMORY.md (index) + per-fact .md files
├── logs/           # activity.jsonl + jarvis_history.json + input_history.txt
├── screenshots/    # screenshot tool dumps here
├── voices/         # drop Kokoro / Piper model files here
└── rules.md        # plain-text behavior rules — edit any time
```

---

## Two ways to talk to him

### A. The CLI (`python hearth_cli.py` or `hearth.bat`)
Streaming text, framed tool-call cards, footer status bar, voice (Kokoro),
permission prompts, `@file` attachments, image-vision support, `/compact`,
proper history with arrow keys + reverse search.

### B. Inside LM Studio's chat (via MCP)
LM Studio's native MCP integration shows every tool call as a live card in
its chat UI — title, arguments, result. No CLI needed. Same tools, same
memory, same workspace.

LM Studio's chat UI does **not** support TTS — voice is CLI-only.

---

## Setup

```powershell
# 1. Deps (once)
.venv\Scripts\python.exe -m pip install openai prompt_toolkit psutil

# 2. Start LM Studio. Load any chat model. "Start Server".

# 3. Run
.\hearth.bat
```

For the LM Studio MCP bridge, edit LM Studio's `mcp.json`:
```json
{
  "mcpServers": {
    "Jarvis": {
      "command": "<absolute path to repo>/.venv/Scripts/python.exe",
      "args": ["<absolute path to repo>/hearth/mcp_server.py"]
    }
  }
}
```
Install the MCP SDK first: `pip install mcp`. Restart LM Studio.

---

## The toolbelt (38 tools, auto-filtered if deps missing)

| Group | Tools |
|---|---|
| **Files** | `read_file`, `write_file`, `edit_file` (string replace + fuzzy fallback), `list_directory`, `create_directory`, `delete_path`, `move_path` |
| **Search** | `grep_search` (rg if installed), `glob_files` |
| **Web** | `web_search` (DuckDuckGo, free), `web_fetch` (HTML→text, auto-`https://`) |
| **Shell** | `run_command` (PowerShell on Windows by default; `shell:"cmd"` to force cmd.exe) |
| **System** | `system_info`, `list_processes` (psutil), `network_info`, `get_battery` (auto-hidden if no battery), `list_installed_apps` (Windows registry) |
| **Apps** | `open_app` (PATH → UWP URI → file path → Start Menu shortcut), `open_url`, `screenshot`, `clipboard_read`, `clipboard_write` |
| **Memory** | `memory_save`, `memory_recall`, `memory_list`, `memory_forget` |
| **Time** | `get_time` |
| **Session** | `end_session` (model calls this when you wrap up; CLI exits cleanly) |

**Permission model:**
- Reads = unrestricted (set `JARVIS_LOCKDOWN=1` to confine to workspace).
- Writes / deletes / moves = confined to `~/Jarvis/`.
- Risky tools (writes, run_command, open_app, open_url, memory_forget)
  prompt for confirmation per session: `[y]es / [n]o / [a]lways / [N]ever`.
- `JARVIS_AUTO_APPROVE=1` to skip prompts (power-user mode).

---

## Memory system

Pattern lifted from Claude Code's auto-memory.

```
~/Jarvis/memory/
├── MEMORY.md         # always-loaded index, one line per fact
├── user_role.md      # per-fact files, YAML frontmatter
├── feedback_pip.md
├── project_portfolio.md
└── reference_router.md
```

Every per-fact file has frontmatter:
```markdown
---
name: My router
type: reference
description: admin URL & where the password is
updated: 2026-05-07T12:34:56
---
192.168.1.1 — password is on the sticker on the back of the box.
```

**Four types** (hard-coded, helps the LLM file things correctly):
- `user` — who you are, role, expertise, preferences
- `feedback` — corrections / confirmations about how Jarvis should behave
- `project` — ongoing work context
- `reference` — pointers (links, paths, credentials, dashboards)

**The index is always injected into the system prompt** so the model knows
what facts exist. Per-fact bodies load on demand via `memory_recall`. Hard
caps: 200 lines / 25KB on the index.

**Compaction extracts facts:** when conversation passes 75% of context, the
compactor first asks the LLM to extract durable facts as JSON, saves them
to memory, *then* summarizes the rest. Important details survive past
compaction.

**`~/Jarvis/rules.md`** — Void's pattern: a plain-text rules file re-read
every turn. Edit freely without touching code.

---

## Vision (image attachments)

`@<path>` to an image (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`)
encodes it as a base64 data URL and sends as an OpenAI multimodal
`image_url` content block. The LLM must support vision — Gemma 4, Qwen-VL,
Llava, etc.

```
❯ what's in @C:/Users/me/Jarvis/screenshots/shot_20260507_001234.png
```

Plain text files attached the same way get spliced inline as fenced code.

---

## Context window — compaction & truncation

Local LLMs hate long contexts. Three layers of defense:

### 1. Fix LM Studio's overflow policy
LM Studio model load panel → **Context Overflow Policy → "Rolling Window"**.
The default is "Truncate Middle" which keeps your first message and the
tail, dropping the actual tool results in between → causes the famous
"Yo" regurgitation bug.

### 2. Auto-compaction at 75%
`hearth_cli.py` automatically extracts facts to memory THEN summarizes old
turns when context hits 75% of the window. Manual: `/compact`.

### 3. Boot-time prune
On startup, if loaded history exceeds 50% of the context window, oldest
non-system messages are dropped until under 30%. Keeps the footer accurate
and the next turn from auto-compacting wastefully.

Plus Void-style weight-based **trimming** as a final safety net before
each LLM call (chars trimmed in-place — never deletes a turn entirely
so tool-call IDs stay valid).

### 4. KV-cache savings (LM Studio settings)
The 15k slowdown is KV cache spilling out of VRAM:
- **K Cache Quantization → Q8_0** (halves KV memory, biggest win)
- **V Cache Quantization → Q8_0**
- **Flash Attention → ON**

### 5. AIRLLM (don't bother)
AIRLLM streams *layers* across VRAM/CPU/disk so 70B fits in 8GB — but
it's slower per token and **does not help context-length speed**.
Different problem. Skip.

---

## Voice (Kokoro TTS)

Local. Free. Sounds genuinely human. ~80MB ONNX, runs real-time on CPU,
zero VRAM contention with your LLM.

```powershell
.venv\Scripts\python.exe -m pip install kokoro-onnx sounddevice numpy
```

Drop two files into `~/Jarvis/voices/`:
- `kokoro-v1.0.onnx` (or `kokoro-v1.0.fp16.onnx`)
- `voices-v1.0.bin`

From: <https://github.com/thewh1teagle/kokoro-onnx/releases>

In CLI:
```
/voice              show status (errors surface here if files unrecognized)
/voice on           enable TTS
/voice off          mute
/voice reload       re-detect after dropping new files (no restart)
```

**Streaming**: speech splits at sentence boundaries so audio starts ~1
sentence after text appears, not after the entire reply finishes.

**Interrupts**: when you send a new message, in-flight TTS stops cleanly.

**Speed**: default `1.15x` (set `$env:JARVIS_VOICE_SPEED=1.0` for natural
pace, `1.3` for fast).

**Voice picks**: set `$env:JARVIS_VOICE=am_michael` (default), or:
- `am_adam` — deeper US male
- `bm_george` — British male (proper Jarvis vibe)
- `bf_emma` — British female
- `am_eric` — younger US male

Full list: <https://github.com/thewh1teagle/kokoro-onnx#voices>

**Piper fallback** if Kokoro doesn't fit: `pip install piper-tts`, drop
any Piper voice `.onnx` + `.json` into the same folder.

---

## CLI reference

### Slash commands
```
/help               full list
/models             list local models
/model <id|n>       switch (alias: /load)
/tools              list available tools
/clear              wipe context (keep system)
/chats              list LM Studio threads
/chat <n|name>      load thread (read-only copy into local history)
/history            last 5 messages
/workspace          show workspace path
/log [n]            tail activity log (default 15)
/tokens             context usage estimate
/compact            summarize old turns + extract facts
/mem                show memory index
/rules              show rules.md path
/voice [on|off|reload]   TTS toggle (alias: /voices)
/context <n>        set context window in tokens (runtime)
/multi              toggle multi-line input mode
/perms              show cached tool permissions
/exit               quit (or just say bye — model calls end_session)
```

### Input
```
Enter               submit (single-line mode)
Esc + Enter         insert newline (or submit, in multi-line mode)
↑ / ↓               history
Ctrl-R              reverse search history
Ctrl-C × 2          exit (single Ctrl-C abandons current line)
@<path>             attach a file (text spliced inline; images as vision)
```

### Multi-line mode
`/multi` toggles. Indicator appears in the footer (`multi`).
- ON: Enter = newline, Esc+Enter = submit, AND single-line slash commands
  still submit on Enter (so you can `/multi` to toggle off).
- OFF: Enter = submit, Esc+Enter = newline, paste = single block.

---

## Optional: mirror CLI chat into LM Studio's UI

```powershell
$env:JARVIS_LMSTUDIO_SYNC = "1"
.\hearth.bat
```

Mirrors to a dedicated `~/.lmstudio/conversations/jarvis_cli.conversation.json`.
Restart LM Studio → "Jarvis CLI" thread appears in its chat list.

**Read-only from LM Studio's side** — keep typing in the CLI, refresh
LM Studio to see updates. Your other LM Studio threads are never touched.

---

## Environment variables

```
LOCAL_API_BASE              http://localhost:1234/v1
LOCAL_MODEL                 (auto-detected from /v1/models if unset)
JARVIS_WORKSPACE            ~/Jarvis
JARVIS_LOCKDOWN             0     1 = also confine reads to workspace
JARVIS_CONTEXT              8192  match LM Studio's loaded context
JARVIS_RESERVED_OUTPUT      2048  budget for the reply
JARVIS_COMPACT_AT           0.75  compact-trigger fraction
JARVIS_VOICE_ON             0     1 = autostart TTS
JARVIS_VOICE                am_michael
JARVIS_VOICE_SPEED          1.15
JARVIS_LMSTUDIO_SYNC        0     1 = mirror to LM Studio chat list
JARVIS_AUTO_APPROVE         0     1 = skip risky-tool permission prompts
```

---

## Activity log

Every tool call (CLI, MCP, dev_tools) writes a JSONL line to
`~/Jarvis/logs/activity.jsonl`. Tail it to watch live:

```powershell
Get-Content -Path "$HOME\Jarvis\logs\activity.jsonl" -Wait -Tail 10
```

Or `/log 30` from inside the CLI.

---

## Common problems

### "Voice says no engine but the files are there"
`/voice` now surfaces the actual load error. Most common:
- Wrong file format / API mismatch (e.g. an old `kokoro-onnx` lib vs
  a newer model file). Update with `pip install -U kokoro-onnx`.
- File names don't contain "kokoro" / "voices" substrings (the loader
  scans `~/Jarvis/voices/` for those keywords).

### "Model claims it can't run PowerShell"
Fixed. `run_command` defaults to PowerShell on Windows. Both classic
commands AND cmdlets work. The persona prompt now tells the model so.

### "Model carries old facts as if they're current"
Persona has a freshness rule — it should re-call `system_info` etc. before
quoting numbers. Smaller models still drift; switch to Qwen 3.5 9B for
better adherence.

### "Reasoning / thinking visible in LM Studio's chat but not in CLI"
Fixed. The CLI now handles BOTH inline `<think>...</think>` AND the
OpenAI `reasoning_content` field that LM Studio uses for reasoning models.
You'll see a `┌─ thinking ─` frame.

### "Multi-line paste breaks into per-line submits"
Install `prompt_toolkit`. Without it, plain `input()` submits each line
individually. With it, paste is a single block, and `Esc+Enter` adds
newlines.

### "set JARVIS_VOICE=am_michael got sent to the LLM"
Env vars must be set in PowerShell BEFORE launching, not inside the CLI.
Use:
```powershell
$env:JARVIS_VOICE = "am_michael"
.\hearth.bat
```

---

## Roadmap

**v0.5 (preparing for public launch):**
- Memory v1 (per-fact + index, 4 types, fact-extracting compactor)
- Voice OUT (Kokoro streaming, sentence chunks, interrupts, Piper fallback)
- Voice IN (faster-whisper, one-shot + continuous-with-interrupt)
- MCP server, dev_tools REPL
- Permission prompts for risky tools
- Vision via `@image.png`
- Context: auto-detect + trim + compact + KV-cache guidance
- LM Studio sync (opt-in)
- Persona v3 (proactive, evidence-based, anti-chatbot rules)
- Workspace allow-list (`/allow <path>` + `JARVIS_EXTRA_WORKSPACES`)
- Memory enum normalization (lenient — accepts "User Preference" etc.)
- 38 tools

**v0.5 launch blockers (active):**
- Smart `find` tool — walks common locations before asking for paths
- One-line PowerShell installer with model auto-download
- Repo hygiene (LICENSE, CONTRIBUTING, SECURITY, CI, issue templates)
- Demo video for the README

**v0.6:**
- Mac / Linux ports of Windows-specific tool branches
- Wake-word filter on `/listen` ambient mode
- Anti-yield re-prompt loop + tool-fail auto-retry
- Optional onboarding wizard (`hearth onboard`) — pattern lifted from clawdbot

**v1.0:**
- Semantic memory recall via local embeddings (nomic-embed-text)
- Plugin / skill system (drop a `.py` into `~/Jarvis/plugins/` → becomes a tool)
- Per-tool result formatters that the model can ask for "more"
