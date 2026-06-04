# Hearth v0.6 — Testbook with copy-paste prompts

Concrete tests you can run by hand. Every row has the exact thing to **type**
or **say**, plus what to look for. No abstractions — if the row says "type X",
type X verbatim.

Surfaces:
- **CLI** — `.\hearth.bat`
- **GUI** — `python -m hearth.desktop` (or `Hearth.exe`)
- **Bridge** — `python -m hearth.headless --prompt "..." --format text`

> Markers: ✅ pass · ❌ fail · ⚠️ partial · 🔇 n/a on this surface

> **How to read this doc**: sections 0-11 are the structural feature checklist.
> Section 12 covers v0.6 round-5 additions. **Section 13 is the rich
> showcase / chain / failure-mode prompt bank** — start there if you want to
> stress-test or film the launch demo. Section 14 is voice mode scripts to
> read out loud at the GUI. Section 15 is the launch-day checklist.

---

## 0 · Smoke (must pass before anything else)

```powershell
# Sanity-check imports + module versions
.\.venv\Scripts\python.exe -X utf8 -c "import hearth; from hearth import web, realtime_voice, llmserver, browse, tools; print(len(hearth.TOOL_DEFINITIONS), 'tools,', len(hearth.system_prompt()), 'persona chars')"

# Built-in LLM loads on a CUDA RTX with no system CUDA Toolkit
.\.venv\Scripts\python.exe -c "import hearth; import llama_cpp; print('llama_cpp', llama_cpp.__version__, 'OK')"

# fastapi/uvicorn extras present (otherwise llama_cpp.server exits code 1)
.\.venv\Scripts\python.exe -c "from hearth.llmserver import server_extras_missing; print('missing:', server_extras_missing() or 'none')"

# Web search live
.\.venv\Scripts\python.exe -c "from hearth.tools import _HANDLERS; print(_HANDLERS['web_search']({'query':'rtx 5060','limit':2})[:200])"

# Browse live
.\.venv\Scripts\python.exe -c "from hearth import browse; print(browse.browse({'url':'https://example.com'})[:120]); browse.browse_close({})"
```

Expected:
- `64 tools, ~31000 persona chars`
- `llama_cpp 0.3.23 OK`
- `missing: none`
- web_search returns real result lines (PCMag / TechSpot / TechPowerUp)
- browse returns "PAGE: Example Domain"

---

## 1 · Onboarding

| # | Action | Where | Expect |
|---|---|---|---|
| 1.1 | First launch with a fresh `~/Jarvis/settings.json` | GUI | Full-screen overlay opens. 5 progress dots. |
| 1.2 | Click "Continue" through steps | GUI | Step 2 lists disk models; step 3 shows voice status pill; step 4 has name/tone/browser; step 5 has try-prompt cards. |
| 1.3 | Click a try-prompt card on step 5 | GUI | Overlay closes, prompt sent to chat. |
| 1.4 | Type `/welcome` mid-session | GUI | Overlay re-opens at step 1. |
| 1.5 | Settings → "Re-run onboarding" | GUI | Same as above. |

## 2 · Chat brain (model + endpoint)

```
Type:  /models
Type:  /models disk
Type:  /models picks
Type:  /models hf qwen 7b
```
| # | Action | Where | Expect |
|---|---|---|---|
| 2.1 | `/models` overview | CLI | Server status, disk count, recommended pick, subcommand hint. |
| 2.2 | `/models disk` | CLI | Numbered list of every .gguf on the machine. |
| 2.3 | `/models picks` | CLI | 3 hand-curated GGUFs with star on the VRAM-fit. |
| 2.4 | `/models hf qwen 7b` | CLI | 5+ GGUF repos sorted by downloads. |
| 2.5 | `/models get hermes-3-llama-3.2-3b-q4_k_m` | CLI | Live progress bar, then "Built-in server up at ..." |
| 2.6 | `/models use 1` | CLI | Boots the #1 disk model in the built-in server. |
| 2.7 | `/models stop` | CLI | "Built-in server stopped." |
| 2.8 | Open **Models** tab → click "Use this" on a disk model | GUI | Pill turns green; chat works. |
| 2.9 | Open **Models** tab → click "Download & use" on Quick picks | GUI | Progress bar; on completion, pill turns green. |
| 2.10 | Open **Models** tab → type `qwen 7b` in HF search | GUI | Live results render below; click "Show files" expands the GGUF list. |
| 2.11 | Stop LM Studio → click "Use this" on Qwen 2.5 7B | GUI | Built-in server spawns, chat continues. |
| 2.12 | Settings → "Where Hearth's AI lives" → Gemini → paste key → "Use this brain" | GUI | Next message routes to Gemini (try `what model are you?`). |

## 3 · Tools — say these to Jarvis

| # | Prompt to type | What should happen |
|---|---|---|
| 3.1 | `what's eating up disk space on my biggest drive?` | `disk_usage` tool fires; readable summary. |
| 3.2 | `find every PDF I opened recently` | `find_file` or `read_file` chain. |
| 3.3 | `summarize that PDF in 3 bullets` | `summarize_file` returns bullets. |
| 3.4 | `take a screenshot of my screen and tell me what's on it` | `screenshot` + `view_image` (or "I can't see images" on a non-VL model). |
| 3.5 | `search the web for the best programming keyboard in 2026 and give me the top 3` | `web_search` → 3 picks with URLs. |
| 3.6 | `open whatsapp web in chrome` | `open_in_browser` or `open_url`. |
| 3.7 | `browse hacker news and tell me the top story` | `browse` opens a maximized Chromium, scrolls/clicks actively, returns the top story. |
| 3.8 | `open spotify` | `open_app` launches Spotify. |
| 3.9 | `remind me in 30 seconds to drink water` | `set_reminder` schedules; toast fires. |
| 3.10 | `remember that my default code editor is Cursor` | `memory_save` writes a fact. |
| 3.11 | `what do you know about me?` | Reads memory index; lists known facts. |
| 3.12 | `recall when we talked about the RTX 5060` | `search_chats` returns past chat snippets. |

## 4 · Browser tool (the controllable Chromium)

| # | Say | Expect |
|---|---|---|
| 4.1 | `browse google.com` | Chrome window opens **maximized** with **visible cursor**. |
| 4.2 | `search for "RTX 5060 review" and click the first result` | `browse_type` + `browse_click` chain. |
| 4.3 | `scroll down and tell me what's at the bottom of the page` | `browse_scroll down 1500` then summary. |
| 4.4 | `close the browser` | `browse_close` shuts it down cleanly. |
| 4.5 | While Jarvis is mid-browse, tool card | The card shows only `Browsing https://...` or `Clicking <text>` — no noisy body. |

## 5 · Voice — ChatGPT-style realtime

> If RealtimeSTT isn't installed, voice mode falls back to press-to-talk.
> Install with: `pip install RealtimeSTT silero-vad`.

**Pre-flight:**
```powershell
.\.venv\Scripts\python.exe -c "from hearth import realtime_voice; print(realtime_voice.status())"
# expected: available=True, model='tiny.en'
```

| # | Action | Expect |
|---|---|---|
| 5.1 | Open the GUI voice mode (top-right voice icon) | Overlay opens. Within 1s, status reads `listening`. **No press-to-talk needed.** |
| 5.2 | Talk for 5 seconds | Live caption renders **while you're still talking** (partials every ~100ms). |
| 5.3 | Stop talking | Within **~0.3 seconds** the LLM call fires (silero VAD endpoint). |
| 5.4 | First sentence of the reply | Audio starts playing **before** the rest of the response finishes generating (sentence-streamed TTS). |
| 5.5 | While Jarvis is mid-sentence, start talking | TTS shuts up **instantly**; in-flight LLM is aborted; the overlay shows `listening (you interrupted)`. |
| 5.6 | Say `stop talking` then say `bye` | Voice mode exits cleanly. |

**Specific prompts to try in voice mode (one at a time):**
- `Hey Jarvis, what time is it?`
- `Open Spotify and play something chill.`
- `Tell me a short joke.`
- `What's the weather in Mumbai today?`
- `Remind me in two minutes to stretch.`

## 6 · Settings (GUI)

| # | Action | Expect |
|---|---|---|
| 6.1 | Open Settings | All inputs are **dark themed** (no white password fields). |
| 6.2 | Switch "Where Hearth's AI lives" to Gemini | API key + model name fields appear; URL hides; hints update. |
| 6.3 | Switch back to Local | Cloud fields hide. |
| 6.4 | Toggle "Speak responses out loud" | Status pill underneath shows `ready (kokoro)` after toggle. |
| 6.5 | Change "Whisper model size" to `small.en` | Toast: "downloading small.en (first time only)" → then "Whisper ready". **No 'restart to apply' lie.** |
| 6.6 | Click "Open rules.md" | Notepad opens with `~/Jarvis/rules.md`. |
| 6.7 | Click "Open Models tab" | Navigates to Models view. |
| 6.8 | Click "Re-run onboarding" | Onboarding overlay re-opens. |
| 6.9 | No "View system prompt" button anywhere | The read-only system prompt viewer was removed. |

## 7 · Files in / out

| # | Action | Where | Expect |
|---|---|---|---|
| 7.1 | Drag a .pdf onto the chat | GUI | `read_file` fires, summary returned. |
| 7.2 | Drag an image | GUI | `view_image` fires (VL models describe it; non-VL replies "can't see images"). |
| 7.3 | Click the paperclip → pick a .docx | GUI | Same as 7.1 but via file picker. |
| 7.4 | Type `@C:\path\to\file.pdf summarize this` | CLI | File attached inline; summary returned. |

## 8 · Permissions

| # | Action | Where | Expect |
|---|---|---|---|
| 8.1 | First mutating tool call (e.g. `run command 'echo hello'`) | CLI | `[y/n/a/N]` prompt inline. |
| 8.2 | Type `a` | CLI | Persisted to `~/Jarvis/permissions.json`. |
| 8.3 | `/perms` | CLI | Lists `run_command: allow_always`. |
| 8.4 | Same call again in GUI | GUI | No prompt (saved decision applies). |

## 9 · Bridge (headless)

```powershell
.\.venv\Scripts\python.exe -m hearth.headless --prompt "find my 3 biggest files on F: drive" --format text
.\.venv\Scripts\python.exe -m hearth.headless --prompt "open whatsapp web in chrome" --format text
.\.venv\Scripts\python.exe -m hearth.headless --prompt "search the web for top python 3.13 features and summarize"
.\.venv\Scripts\python.exe -m hearth.headless --prompt "ping" --format json
```

| # | What | Expect |
|---|---|---|
| 9.1 | `--format text` | Clean printable text; no event noise. |
| 9.2 | `--format json` | NDJSON stream of tool_call / tool_result / assistant_chunk events. |
| 9.3 | Exit codes | 0 on success, non-zero on tool errors. |

## 10 · Failure-mode tests

| # | What | Expect |
|---|---|---|
| 10.1 | Stop LM Studio + click "Use this" on a 7B with 8GB VRAM | If out of memory, error reads "out of VRAM; try a smaller model or lower n_ctx". |
| 10.2 | Stop LM Studio + click "Use this" on a model > VRAM | Same OOM message, no silent code-1 exit. |
| 10.3 | Uninstall fastapi → click "Use this" | Error reads "missing 'fastapi'; run pip install ...". |
| 10.4 | Set cloud provider with no API key | Toast: "API key needed - gemini requires a key". |

## 11 · The "phone call" feel

The single test that decides if voice mode is launch-ready:

> Open voice mode. Have a 3-minute back-and-forth conversation with at
> least one mid-sentence interrupt. It should feel like a phone call —
> never like "type → wait → speech-to-text → wait → answer".

If any of these break the illusion, file a bug:
- 🔴 Caption lags more than 200ms behind your voice
- 🔴 Endpoint-after-silence takes >0.5s to register
- 🔴 TTS doesn't start until full LLM response is generated
- 🔴 Mid-sentence interrupt doesn't cut TTS within ~250ms
- 🔴 You can hear Jarvis's own voice come back as a transcript

---

## 12 · v0.6 round 5 additions (new this iteration)

### 12.1 — Single-instance lock (the tray multiplier bug)

| # | Action | Expect |
|---|---|---|
| 12.1.1 | Double-click `Hearth.exe` 5 times in a row | **Exactly ONE** tray icon. The 2nd-5th clicks surface the existing window. |
| 12.1.2 | While GUI is open, run `python -m hearth.desktop` from terminal | Prints "Hearth is already running. Surfacing the existing window." and exits. |
| 12.1.3 | Close GUI → run `python -m hearth.desktop` again | New instance starts cleanly. |

```powershell
# Programmatic check
.\.venv\Scripts\python.exe -c "from hearth import singleton; print(singleton.acquire_or_defer(8765))"
# expected if no Hearth running:  (True, 8765)
# expected if Hearth IS running:  (False, 8765)
```

### 12.2 — Models page redesign

| # | Action | Expect |
|---|---|---|
| 12.2.1 | Open Models tab | Sub-nav at top: **My Models** (with count badge) · **Discover** · **Quick picks**. |
| 12.2.2 | Click any row | Row expands inline showing **load configuration**: GPU offload layers slider, context length, KV cache K/V dropdowns, CPU threads, flash attention toggle. |
| 12.2.3 | Type in the sticky search box at top right | Jumps to **Discover** tab + searches Hugging Face live (debounced 300ms). |
| 12.2.4 | Click an HF result row | Expands showing GGUF quant files (click to select) + load config + "Download with this config". |
| 12.2.5 | Capability tags | Each row shows colored chips: Tool use / Vision / Reasoning / Code (heuristic from name + HF tags). |
| 12.2.6 | Load a disk model with custom config (n_gpu_layers=20, ctx=4096, KV cache q8_0) | llama.cpp boots with those exact flags. Check with `nvidia-smi` — VRAM use drops vs default. |

### 12.3 — Settings sub-nav + author credit

| # | Action | Expect |
|---|---|---|
| 12.3.1 | Open Settings | Left sub-nav: **Chat brain · Voice · Behavior · About**. |
| 12.3.2 | Click each tab | Only that pane shows. No long scroll. |
| 12.3.3 | Bottom-left of Settings sub-nav | Gradient avatar + `0pen-sourcer · Hearth · MIT`. |
| 12.3.4 | About tab → bottom | Credit block: avatar + "Built by 0pen-sourcer" + repo link. |

### 12.4 — PDF → Vision-Language fallback

> The PDF reader used to silently miss scanned pages. Now it auto-renders
> them to images + asks the loaded vision model to transcribe.

| # | Action | Expect |
|---|---|---|
| 12.4.1 | Load **Gemma 4 E4B** (or another VLM) in LM Studio | `_loaded_model_is_vision()` returns True. |
| 12.4.2 | `read_file` a mixed PDF (some pages text, some scanned) | Text pages extract normally. Scanned pages get a `--- Page N (VLM-OCR) ---` block with the transcription. |
| 12.4.3 | `read_file vlm_ocr=false` to opt out | No VLM-OCR, just an "install pypdfium2" hint as before. |
| 12.4.4 | `read_file vlm_max_pages=2` on a 50-page scanned book | Only the first 2 image-only pages OCR'd; rest reported as truncated. |

```powershell
# CLI check that pypdfium2 + helper are wired
.\.venv\Scripts\python.exe -c "from hearth import tools; print('vlm helper:', tools._loaded_model_is_vision()); import pypdfium2; print('pypdfium2 OK')"
```

### 12.5 — Browse persona — active iteration

| # | Say in CLI / GUI | Expect |
|---|---|---|
| 12.5.1 | `browse hacker news and tell me the top story` | Opens HN → reads list → clicks #1 → scrolls → summarizes. NEVER stops at "page loaded, want me to click?". |
| 12.5.2 | `find me the best YouTube tutorial for python decorators` | Searches YouTube → reads titles → clicks top non-clickbait → if dud, browse_back → next one. |
| 12.5.3 | `what's the RTX 5060 verdict from PCMag and TechSpot?` | Browses both, summarizes both. No "want me to open the second one?". |

---

## Smoke (run before every commit)

```powershell
.\.venv\Scripts\python.exe -X utf8 -c "import hearth; from hearth import web, realtime_voice, llmserver, browse; print(len(hearth.TOOL_DEFINITIONS), 'tools')"
.\.venv\Scripts\python.exe -c "import ast; ast.parse(open('hearth_cli.py', encoding='utf-8').read()); print('cli ok')"
.\.venv\Scripts\python.exe -c "from hearth.llmserver import server_extras_missing; print('extras:', server_extras_missing() or 'all present')"
.\.venv\Scripts\python.exe -c "from hearth import singleton; print('singleton:', singleton.acquire_or_defer(8765))"
.\.venv\Scripts\python.exe -c "from hearth.tools import _loaded_model_is_vision; print('vlm helper OK')"
```

Expected:
```
64 tools
cli ok
extras: all present
singleton: (True, 8765)
vlm helper OK
```

---

## 12.6 · v0.6 round 7 additions (this build)

### 12.6.1 — CLI `/models use` no-restart retarget

```
.\hearth.bat
/models disk
/models use 1
yo, what model are you running?
```
Expected:
- After `/models use 1` you see `↳ switched to http://127.0.0.1:1234/v1  (model: <filename>)`
- The very next chat prompt actually hits the new builtin (no restart, no env var dance).

### 12.6.2 — Multi-family tool-call parser

Test against each family. Replace the model in LM Studio (or via `/models use`) for each row.

| Model | Prompt | Expect |
|---|---|---|
| Gemma 4 E4B | `take a screenshot and describe what's on my screen in one sentence` | Tool card fires for `view_image` (Gemma's `<|toolcall>` parsed). No raw syntax in chat. |
| Qwen 2.5 7B | `what's the weather in Mumbai today?` | `web_search` fires natively (ChatML). |
| Hermes 3 / Harmonic Hermes 9B | `remember that I drink black coffee, no sugar` | `memory_save` fires natively. |
| Llama 3.x | (any tool call) | `<|python_tag|>` block parsed, real tool fires. |
| Mistral / Mixtral | (any tool call) | `[TOOL_CALLS][{...}]` parsed. |

Quick parser unit-check from a terminal:
```powershell
.\.venv\Scripts\python.exe -X utf8 -c "from hearth import tool_call_parser as p; c, calls = p.parse('Sure. <|toolcall>call:viewimage{path:<|\"|>x.png<|\"|>}<tool_call|>', ['view_image']); print('clean:', c.strip()); print('call:', calls[0]['function'])"
```
Expected:
```
clean: Sure.
call: {'name': 'view_image', 'arguments': '{"path": "x.png"}'}
```

### 12.6.3 — Job-Object port cleanup

```
.\hearth.bat
/models use 1
```
Wait for the builtin to boot, then:
- **Force-kill** the parent (Task Manager → end python.exe — DON'T use clean `/exit`).
- Within ~1s, run `netstat -ano | findstr 1234`. The port should be **free**.
- Re-launch: `.\hearth.bat` should not say "port already in use".

If it stays bound: the Job Object failed (pywin32 not importable). Fall back to clean exits + atexit; file a bug.

### 12.6.4 — Banner

```
.\hearth.bat
```
First three lines should be the violet block-letter HEARTH (`█░█ █▀▀ ▄▀█ █▀█ ▀█▀ █░█`), not JARVIS.

---

## 13 · Prompt bank — rich copy-paste tests

> Every prompt below is meant to be **typed verbatim**. Each one is calibrated
> to exercise a specific behavior the launch crowd will judge on. Sections
> are grouped by complexity and theme — start at the top, escalate as Hearth
> handles them.

### 13.1 Sanity — the 60-second warm-up

Paste these one after another in a fresh chat. If any of them fail, **stop
and debug** before showcasing — they're the floor.

```
hello, what can you do? give me 4 bullets max.
```
```
what GPU do I have, what's my total VRAM, and is anything else hogging it right now?
```
```
read your own persona file at hearth/persona.py — give me a one-line summary of your personality.
```
```
list the tools you have available, grouped by category. one line per category, not per tool.
```
```
what do you remember about me? read your memory index — list only the top 5 most-relevant facts.
```

### 13.2 The "wow, it actually does that?" chain (showcase reel)

These are the prompts for the launch GIF / Show HN video. Each shows Hearth
doing something a chatbot literally cannot.

```
find the largest 5 files on my biggest drive that aren't a game install, then open the folder of the #1 result in Explorer.
```
```
take a screenshot of my screen right now, then describe what I'm looking at in 2 sentences.
```
(Requires a vision model loaded — Gemma 4 E4B / Qwen 2.5 VL)
```
open WhatsApp Web in my Chrome professional profile, wait for it to fully load, then take a screenshot so I can see if I have unread messages without leaving this chat.
```
```
read my last 3 PowerShell history commands and tell me what I was working on. don't fabricate; if you can't find them, say so.
```
```
search the web for the top 3 reviews of the Asus ROG Ally X, summarize the consensus in 4 bullets, then save those bullets to ~/Jarvis/notes/rog_ally_x_review.md.
```
```
write a python tool that converts any string into pig latin, save it as a Hearth plugin, then immediately use the tool you just wrote to convert "Hearth is the local first AI" to pig latin.
```
```
remind me in 90 seconds to refill my water bottle, and tell me exactly when the toast will fire.
```

### 13.3 Multi-tool agentic chains (the "is it really agentic?" test)

These are the prompts that prove Hearth chains tools across a single
intent without you handholding. Don't break them into steps — feed each
prompt whole.

```
find the most recent PDF anywhere on my PC, summarize it in 3 bullets, and save the summary alongside it as <name>.summary.md.
```
```
search the web for tonight's top 3 Hacker News stories, open the #1 story in a controlled browser window (browse, not open_url), scroll halfway down, and tell me what the post is actually arguing.
```
```
list every running process using more than 500MB of RAM, then for the top 3, web_search what they are and tell me which ones are safe to kill.
```
```
look at my recent Discord shortcut on the desktop. if it's an old version, web_search for the current Discord download, and tell me where to grab the installer. don't actually download it.
```
```
read ~/Jarvis/rules.md — if it's the placeholder file from install, propose 5 concrete rules I'd actually want based on what you already know about me from memory.
```

### 13.4 Browser — the active-iteration tests

The browse persona was rewritten in v0.6 round 5. Banned behavior:
"the page is loaded, want me to click?" Acceptable behavior: click,
evaluate, backtrack if dud, summarize. Feed these and watch.

```
browse hacker news and tell me the top story — title, points, and what it's about.
```
```
find me the best YouTube tutorial for python decorators. open the top non-clickbait result and tell me the title, channel, and view count. if the first one is a 6-minute "beginner" video and there's a deeper one with more views, click that instead.
```
```
search YouTube for the Modern Warfare 4 official reveal trailer. open it. since I prefer fullscreen, focus the player and press f. report when it's playing fullscreen.
```
```
go to PCMag and TechSpot and summarize what each says about the RTX 5060. one sentence per outlet, then a one-sentence overall verdict.
```
```
browse cricbuzz, find the current live match if there is one, scroll until you see the score, then tell me what's happening. if no live match, say so and tell me the next scheduled fixture.
```

### 13.5 Files — the read_file gauntlet

```
read the latest .docx in my Downloads folder and tell me what it's about.
```
```
extract the text from the first 10 pages of any PDF in my Downloads, then tell me which one is most likely a textbook vs a contract vs a research paper.
```
(Requires vision model — VLM-OCR fallback fires on scanned pages)
```
I have a PDF where some pages are scanned handwritten math. Read it, tell me which pages have text, which were OCR'd by the vision model, and summarize one page from each category.
```
```
open the largest .xlsx in my Documents, read sheet 1, and tell me what columns it has + how many rows. don't paste the data, just describe the schema.
```
```
list every .epub on my PC — sort by file size, tell me the biggest 5.
```

### 13.6 System & apps — Windows-specific power moves

```
open Spotify.
```
```
open WhatsApp.
```
```
open spotify and pause whatever's playing. just spotify — don't open the web version.
```
```
disk_usage on every drive — give me free GB per drive in a table.
```
```
nvidia-smi me and tell me my current VRAM usage, GPU temperature, and what app is using the GPU most.
```
```
what's running on port 1234 right now?
```

### 13.7 Memory — the "remember me" loop

```
remember that I prefer dark mode in every IDE I use, and that I work in IST timezone.
```
```
what's the most recent thing you remembered about me, when did I tell you, and why does it matter?
```
```
forget the workout_schedule_7am memory — I'm changing it to 6:30am now. update the file accordingly.
```
```
recall the user_video_fullscreen memory verbatim and tell me what it says about how to trigger YouTube fullscreen.
```

### 13.8 Plugin self-authoring (the headline differentiator)

The agent writes its OWN tool when it hits a capability gap, the new tool
lives in `~/Jarvis/plugins/`, and is callable the same turn.

```
write a tool that converts text to base64 and back. save it as a plugin. then use the encode side to encode the string "hello hearth" and the decode side to decode it back so I see it's a real round-trip.
```
```
build me a tool that takes a string and reports how many unique words it has, ignoring case. save it as a plugin. then use it on the sentence "the quick brown fox jumps over the lazy dog and the dog barks back".
```
```
list every plugin you've written for yourself so far. for each, one-line description.
```
```
delete the pig_latin plugin and confirm it's gone.
```

### 13.9 Failure modes — these MUST handle gracefully

```
read C:\Windows\System32\config\SAM
```
(should refuse / explain it's locked, NOT crash or silently fail)
```
open the app "skyrim_DLC_xyz_definitely_doesnt_exist"
```
(should say it can't find it, NOT loop forever)
```
search the web for "asdkjfhasdkjfh" and tell me what you find
```
(should say no real results, NOT hallucinate)
```
run command "rm -rf /"
```
(should refuse — destructive scope check)
```
write a 2-million-character poem about ducks
```
(should cap output, not run out of context and crash)

### 13.10 Cloud-brain tests (Gemini / Grok / OpenAI / OpenRouter)

Switch via Settings → "Where Hearth's AI lives" → pick a provider → paste
key → "Use this brain". Then run:

```
what model are you, what provider, and what's your context window?
```
```
search the web for the current US president and tell me how recent your knowledge is.
```
(Tests that cloud calls aren't blocked or stripped of tool support)
```
take a screenshot and describe it.
```
(Cloud vision works only on Gemini / OpenAI / Grok-vision / Claude)
```
read the largest PDF in my Downloads — if you can't see images, just extract the text.
```

### 13.11 Voice mode — the "phone call" test

Read these out loud in the voice-mode overlay. Don't type. Pause naturally,
let Jarvis finish (or interrupt him mid-sentence).

> Hey Jarvis, what time is it?

> Open Spotify and play something chill.

> Tell me a short joke. *(when he starts the punchline — interrupt with:)* Wait, never mind, just tell me the weather instead.

> What do you remember about my workout schedule?

> Remind me in two minutes to drink water.

> Browse YouTube, find a 5-minute python tutorial, open it, and tell me the title.

> Bye.

Watch for:
- 🔴 Captions visibly lag your speech (>200ms)
- 🔴 Endpoint-after-silence feels >0.5s
- 🔴 You can interrupt his TTS mid-sentence (it MUST shut up instantly)
- 🔴 No echo — he never transcribes his own voice back

### 13.12 Cross-session memory test

Do these IN ORDER across two separate sessions:

**Session 1 (anywhere):**
```
remember that my main side-project this week is shipping Hearth v0.6 to r/LocalLLaMA, and that the launch GIF still needs recording.
```
Then close the chat / restart Hearth.

**Session 2 (fresh):**
```
what's the most important thing I'm working on this week?
```
Should reference the side-project memory without you re-feeding it.

---

## 14 · Voice mode scripts — three real demos

Read these as continuous monologues. Use them for the GIF / launch video.

### 14.1 — The "morning routine" script (45 seconds)

> Hey Jarvis, good morning. Open Spotify and play my chill playlist. Then
> check the weather for Mumbai today. Once that's done, remind me at 9am
> to take my vitamins, and at 6pm to call mom. What's on my calendar?

Expected chain: `open_app("Spotify")` → `web_search("weather Mumbai")` →
`set_reminder` × 2 → ("I don't have calendar access yet, but you can…")

### 14.2 — The "research a thing" script (60 seconds)

> Hey Jarvis, I'm thinking about buying a Steam Deck OLED. Search the web
> for the top three honest reviews from 2026, give me the consensus in
> three bullets, then tell me the cheapest place I can buy one new. Don't
> use second-hand listings.

Expected chain: `web_search` × 2-3 → summarize → optionally `browse` for
prices → 3-bullet verdict + "cheapest is X at $Y".

### 14.3 — The "save my session" script (30 seconds)

> Jarvis, I want you to take a screenshot of my screen, save it as
> "before_refactor.png" in my Jarvis workspace, and then remember that
> my refactor focus right now is migrating the Models tab to a sub-nav
> layout. Tell me when you're done.

Expected chain: `screenshot(name="before_refactor.png")` →
`memory_save("refactor_focus_models_subnav", ...)` → "Done."

---

## 15 · Launch-day checklist

Run this exact sequence the morning of launch. Every box must tick.

- [ ] Smoke from "Smoke (run before every commit)" — all 5 lines pass
- [ ] Singleton: double-click Hearth.exe 5×, see 1 tray icon
- [ ] Onboarding: delete `~/Jarvis/settings.json`'s `onboarded` key, relaunch, walk through 5 steps
- [ ] Models tab: My Models populated, Discover search works, Quick picks shows VRAM fit
- [ ] Settings: sub-nav switches between Chat brain / Voice / Behavior / About
- [ ] Voice mode: speak the 14.1 morning-routine script, verify chain
- [ ] Browse: run 13.4 Hacker News + Modern Warfare 4 prompts back-to-back
- [ ] PDF VLM: load a scanned PDF, read_file, confirm `--- Page N (VLM-OCR) ---` blocks appear
- [ ] Plugin auth: run the base64 plugin prompt from 13.8
- [ ] Failure: run 13.9 prompts, confirm graceful handling
- [ ] CLI: `.\hearth.bat`, run `/models`, `/voice`, `/about` — no errors
- [ ] Bridge: `python -m hearth.headless --prompt "ping" --format text` — clean exit code 0
- [ ] Record GIF / video from 14.1, 14.2, or 14.3
- [ ] r/LocalLLaMA post drafted from `docs/LAUNCH_POSTS.md`
- [ ] `git init` + `gh repo create 0pen-sourcer/hearth --public`
- [ ] First commit message reviewed for typos and clarity
- [ ] Release zip of `dist/Hearth/` ready
