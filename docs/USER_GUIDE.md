# Hearth — user guide

The "you just downloaded this and have no idea where to start" guide.

---

## TL;DR — five minutes to a working Jarvis

```powershell
# 1. clone
git clone https://github.com/0pen-sourcer/hearth.git
cd hearth

# 2. install (venv, deps, voice models)
.\install.ps1

# 3. install LM Studio (https://lmstudio.ai), open it, click "Discover",
#    grab Harmonic-Hermes-9B (Q4_K_M), then click "Local Server → Start"

# 4. create the Desktop + Startup-folder shortcuts (one-shot, optional)
.\.venv\Scripts\python.exe -m hearth.install_shortcuts

# 5. pick your launcher
.\hearth.bat                                   # CLI (voice + keyboard)
.\.venv\Scripts\python.exe -m hearth.tray --open   # tray + native window
.\Hearth-cli.bat                               # CLI in Windows Terminal
```

That's it. Hearth is now a tray icon that launches on every boot, opens a chat window on click, and remembers you across sessions.

---

## Four ways to use it

| Interface | Launch | When |
|---|---|---|
| **CLI** | `.\hearth.bat` | Daily driver. Voice loop, slash commands, fast iteration. |
| **Desktop app** | `.\.venv\Scripts\python.exe -m hearth.tray --open` OR `dist\Hearth\Hearth.exe` | Visual chat, multi-conversation sidebar, file drop, voice mode takeover, GPU stats. |
| **Browser** | `python -m hearth.web` | Same UI in your default browser. Useful if you want LAN access. |
| **MCP** | `python -m hearth.mcp_server` | Plugs Hearth's 50+ tools into **LM Studio's native chat UI** — gets tool cards rendered there. |

The desktop app and browser share one backend (`hearth/web.py`) and one HTML file (`hearth/ui.html`). Native window = same UI in a PyWebView wrapper.

---

## The first 30 seconds in the GUI

1. **Top bar** — model picker on the left (the dropdown auto-loads when you pick), GPU chip in the middle, voice-mode button on the right (mic-with-circle icon)
2. **Sidebar** — "New chat" at top, conversation list below, then Memory / Files / Logs / Settings tabs
3. **Status bar** — model · context · tools · memory count · live state
4. **Type something** in the chat input, hit Enter. Multi-line with Shift+Enter.
5. **First time?** An onboarding modal asks for your name + tone + browser. Saved to settings + memory.

---

## Try these in chat to see what Hearth can do

| Prompt | What happens |
|---|---|
| `find my [movie/game/folder name]` | Walks all drives + common dirs, returns paths sorted by relevance |
| `play that` (after the find) | Opens the top match in your default player |
| `what's eating up my disk` | Calls `disk_usage` — top folders + files by size |
| `summarize ~/Jarvis/whatever.pdf` | Reads + 3-5 bullet summary |
| `what's the latest news on [topic]` | DuckDuckGo search + fetches top URL + summary |
| `what's my GPU temp` | Live `nvidia-smi` call |
| `remind me to take a break in 30 minutes` | Toast notification fires in 30 |
| `remember that my favorite framework is FastAPI` | Saves to `~/Jarvis/memory/` — persists across sessions |
| `what did we talk about last week with that deployment bug` | Searches across ALL past chats via SQLite FTS5 |
| `open chrome with my work profile` | Opens with the right profile (configurable in settings) |

---

## Slash commands (CLI)

```
/help                          list every command
/clear                         wipe THIS chat (history preserved server-side)
/compact                       ask Jarvis to summarize old turns + extract facts
/tools                         show all tools
/models                        list models the running server can see
/model <id>                    load a specific model
/voice on|off                  toggle TTS
/voice speed <n>               TTS playback rate (0.5x - 2.5x)
/listen [on|off]               continuous voice-in with TTS interrupt
/think on|off                  toggle inline reasoning display
/brain [local|grok|...]        switch chat brain mid-session
/name [NewName]                show / set agent name (Cortana, Friday, etc.)
/perms                         show saved tool permissions
/perms forget <tool>           drop one saved decision
/perms reset                   forget ALL saved decisions
/allow <path>                  extend write access to a folder this session
/mem                           list memories (CLI)
/mem tree                      ASCII tree by category
/mem map                       open the GUI memory graph in browser
/log                           tail activity log
/agent                         list available sub-agent personas
/agent <slug> "<prompt>"       spawn a sub-agent synchronously
/jobs [all|<id>|kill <id>]     background jobs (disk_usage / shell / etc.)
/mcp                           MCP server status
/mcp edit                      open ~/Jarvis/mcp.json in $EDITOR
/mcp config                    print the snippet for LM Studio / Claude Desktop
/mcp run                       run Hearth as an MCP server in this terminal
/migrate <hermes|openclaw>     import memory from another agent (dry-run)
/migrate <src> apply           write the import
/exit  /quit                   close
```

---

## Voice mode — the Dexter-style takeover

Click the mic-with-circle icon top-right. The whole window goes black with a 15×15 dot grid in the center.

1. **Tap mic** → "listening…" (the dot pattern stays calm at rest)
2. **Speak** → mic captures audio
3. **Stay silent 1.5s** → auto-transcribes via whisper
4. **Jarvis replies** → text appears in the transcript area + voice speaks through your speakers, **dot grid does a smooth violet wave outward from center**
5. **Mic auto-reopens** after TTS finishes — conversational loop
6. **Press Esc** to exit

**Why mic stays muted during TTS:** the speakers→mic feedback loop is worse than no interruption. Type any time though — keyboard always works in voice mode.

---

## Wake word — "Jarvis"

Right-click tray → toggle **Wake word: on**.

A background thread listens with whisper (energy-gated — only transcribes when audio crosses a voice-activity threshold, so it sleeps at ~0% CPU). When it hears "jarvis", "hey jarvis", "wake up jarvis", "yo jarvis" → tray fires `_open_desktop_window()` which brings the chat to focus.

**Customize phrases** via env var (settings.json soon):
```powershell
$env:JARVIS_WAKE_PHRASES = "jarvis,hearth,computer"
```

Tune detection sensitivity + the idle-CPU budget via the `JARVIS_WAKE_*` env vars.

---

## Reminders

Natural-language parser. All of these work:

| Say | Stored as |
|---|---|
| `in 25 minutes` | now + 25min |
| `in 2 hours` | now + 2h |
| `tomorrow at 7am` | tomorrow 07:00 |
| `tonight at 9pm` | today 21:00 |
| `next monday at 10am` | nearest future Monday 10:00 |
| `2026-05-30 14:30` | ISO direct |
| `9pm` | today 21:00 (rolls to tomorrow if past) |

When the time hits: Windows toast notification (via plyer / win10toast), **plus voice readout** if TTS is enabled.

Storage: `~/Jarvis/reminders.json`. Edit by hand if you want.

Tools: `set_reminder`, `list_reminders`, `cancel_reminder`.

---

## Memory across sessions

Two layers:

1. **Per-fact memory** (`~/Jarvis/memory/*.md`) — Jarvis saves your preferences here. Always loaded into context as an index. Categories: `user` / `feedback` / `project` / `reference`. View/edit/delete in the Memory tab. Tools: `memory_save`, `memory_recall`, `memory_forget`, `memory_list`.

2. **Conversation FTS5 search** (`~/Jarvis/session_index.db`) — every message you've ever sent is indexed in SQLite FTS5. When you say "what did we talk about last week", Jarvis calls `search_chats` and gets back ranked snippets across all past chats. Auto-rebuilds when conversations change.

Together: short-term you'd want a fact in mind → `memory_save`. Long-term recall of "we talked about that thing once" → `search_chats`.

---

## File drop

Drag any of these onto the window. Drop overlay appears. Hearth uploads → asks Jarvis to read it.

- **PDF** → pypdf text extraction (per-page if needed)
- **DOCX** → python-docx (paragraphs + tables)
- **XLSX / XLSM** → openpyxl (sheets + sample rows)
- **PPTX** → python-pptx (slide text)
- **EPUB / IPYNB / RTF** → stdlib
- **CSV / TSV** → stdlib csv
- **JSON / JSONL** → structure dump
- **HTML / XML** → stripped text
- **`.gz / .bz2 / .xz`** → decompress + head
- **ZIP / TAR.*** → `list_archive` (use `extract_archive_file` to pull one file out without unpacking)
- **Images** → `view_image` (vision pipeline if the loaded model supports it)

---

## Permission prompts

Risky tools (delete, move, write, run_command, open_app) pop a modal in the GUI:

- **Allow** — just this once
- **Deny** — refuses, model sees the denial
- **Always** — allow this tool for the rest of the session
- **Never** — block this tool for the rest of the session

CLI uses `[y/n/a/N]` keyboard prompts instead. `/perms` shows the current state. `/perms clear` resets.

---

## Settings panel

| Setting | Default | What it does |
|---|---|---|
| Auto-load preferred model | on | Tells LM Studio to load your pick at startup |
| Preferred model | (none) | Which model to auto-load |
| Default `/think` mode on | off | Show model reasoning inline |
| TTS enabled | off | Speak every assistant reply |
| TTS device | CPU | CPU / GPU CUDA / GPU DirectML |
| STT enabled | off | Mic transcribes speech |
| STT device | CPU | CPU / GPU CUDA |
| STT model | base.en | tiny.en / base.en / small.en / medium.en |

Persisted to `~/Jarvis/settings.json`. Some require app reload (voice device changes).

---

## Picking the right model

Tested on 8GB VRAM:

| Model | Pros | Cons |
|---|---|---|
| **Harmonic-Hermes-9B Q4_K_M** | Best tool adherence, fast, decisive on multi-step chains | Slightly more shell drift than Qwen |
| Qwen 2.5 7B Instruct Q4_K_M | Cleaner memory tool use | ~30% slower overall |
| Qwen 3.5 9B Q4_K_M | Vision capable | Slightly less reliable on chains |
| ❌ RNJ-1 8B / any Gemma 3 | — | Emits tool calls as raw text inside the reasoning channel, NOT via OpenAI structured `tool_calls` field. Broken for Hearth until we ship an alt-format parser. |

**For the launch demo:** Hermes 9B. Period.

**Wanting to try cloud?** Set `LOCAL_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai` + your Gemini API key — Gemini has an OpenAI-compat endpoint that works with Hearth. But this defeats the "local-first" pitch; do it just to see the ceiling.

---

## Workspace layout

```
~/Jarvis/                  ← agent's home (override with $env:JARVIS_WORKSPACE)
├── memory/                ← per-fact .md files
│   ├── MEMORY.md          ← always-loaded index
│   └── <fact>.md
├── conversations/         ← every chat as JSON, persists across restart
│   └── c_<id>.json
├── session_index.db       ← FTS5 index over all conversations
├── reminders.json         ← scheduled reminders
├── settings.json          ← UI + voice device settings
├── rules.md               ← plain-text rules, re-read every turn
├── logs/
│   ├── activity.jsonl     ← every tool call
│   ├── jarvis_history.json ← CLI conversation history
│   ├── hearth_tray.log    ← bundled app stderr (when running from exe)
│   └── hearth_cli.log     ← bundled CLI stderr
├── voices/                ← Kokoro + Whisper model files
├── screenshots/           ← screenshot tool output
└── uploads/               ← files you dragged into the GUI
```

Reads default to **unrestricted** (whole disk — Jarvis needs to know your machine). Writes/deletes/moves **confined** to workspace. Override with `JARVIS_LOCKDOWN=1` to confine reads too.

---

## Star-farming the launch (for the maintainer)

If you're reading this as the contributor: the demo prompts that land best for a 30-second README GIF:

1. **"Find my [movie] and play it"** — find_file → open_app chain. Universal "wow, it knows my disk."
2. **"Summarize this"** + drop a real PDF. Instant value.
3. **"Remind me to take a break in 20 minutes"** + show the toast firing later. Real-Jarvis vibe.
4. **Voice mode** + GPU stats question. Local LLM + voice without API keys = the differentiator.
5. **"What did we discuss about [topic] last week"** — `search_chats`. Nobody else does this locally.

Post on **r/LocalLLaMA** (Tuesday 9-11 AM EST is the data-backed sweet spot). Lead with "I built a local Jarvis that has voice + cross-session memory + can actually open my apps." Pin the .exe in the GitHub Releases.

---

## When things break

- **GUI exe shows traceback dialog** → check `~/Jarvis/logs/hearth_tray.log` for the actual error
- **Wake word doesn't trigger** → check tray output (CLI mode) for "wake:" lines. Lower `ENERGY_THRESHOLD` in `hearth/wake.py` if mic is quiet
- **Model says "I don't have memory of past chats"** → it didn't call `search_chats`. Persona is supposed to push it — if it keeps missing, paste the prompt
- **TTS reads asterisks** → `_clean_for_tts` should strip them. If a specific pattern slips through, paste the text
- **Voice mode loops on its own speech** → mic is supposed to be muted during TTS. If it's looping, the `voiceSpeaking` flag isn't being set — paste a console log
- **`find_file` slow** → it's scanning drives. Pass a `path` arg in your prompt to scope it: `find X on G drive`

---

## Help / contribute / file bugs

- Issues: github.com/0pen-sourcer/hearth/issues
- Source: github.com/0pen-sourcer/hearth
- Docs: `docs/` folder in the repo
- Full tool list: [`docs/TOOLS.md`](TOOLS.md)
