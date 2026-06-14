# Changelog

All notable changes to Hearth land here. Format vaguely follows [Keep a Changelog](https://keepachangelog.com); we don't sweat strict semver pre-1.0.

## v0.7-preview — 2026-06-14

First public release.

- Local-first by default. Runs against any OpenAI-compatible local server (LM Studio, Ollama, vLLM, llama.cpp). Cloud is optional: paste a key in Settings → Chat brain to switch to Gemini, OpenAI, Grok, or OpenRouter without restart.
- 70+ built-in tools: files, shell, web search + fetch, browser driving, screenshot + vision, voice in/out, memory, reminders, image + video generation, MCP both directions, and more.
- **Skills** — ask for a PDF / slide deck / spreadsheet and Hearth picks the right format and style, builds the file, opens it. Drop your own skill folders into the workspace and they're invokable by name. The agent can author new skills itself when it notices a workflow it ran twice.
- **Sub-agents** — fork focused workers in parallel (sync or background). Each has its own tool allowlist and a tightened system prompt. Background workers surface their results in chat the moment they finish — no "are you done?" nudging.
- **Voice** — Kokoro TTS streams sentence-by-sentence; faster-whisper STT with mid-sentence interrupt; optional wake-word.
- **Real browser driving** — Hearth opens Chrome / Brave / Edge and you watch it click. Stop button cancels at any time.
- **Self-curating memory** — per-fact markdown files with hot / cold archive + warm-back on recall. Cross-title dedup catches when the same fact is being saved under different names.
- **Renameable agent** — change the name in Settings; the chat avatar, persona, and workspace folder all update.
- **Migrate** from Hermes or OpenClaw memory layouts (API keys are never copied).
- **MCP both ways** — Hearth consumes other MCP servers (drop an mcp.json in your workspace) and exposes its own tools as one.
- **Reminders that fire tools, not just toasts** — schedule a reminder that also runs `web_search` or any other tool when it pops.
- **Builds its own tools** when it hits a capability gap (validated, sandboxed, persisted across restarts).

Windows-first at v0.7-preview. Mac/Linux PRs welcome.

- **Sub-agent fork system (`hearth/subagents.py`)** — `spawn_subagent(persona, prompt, mode='sync'|'background', max_turns)` runs a scoped LLM loop with a tight tool allowlist, a tightened system prompt (skips the main persona), and optional memory-aware briefing (auto `memory_recall` injection). 6 personas shipped: `researcher`, `coder`, `archivist`, `librarian`, `summarizer`, `pdf_coordinator` (map-reduce orchestrator that fans out N summarizer children). Wildcard `allowed_tools: ['*']` inherits the parent's full toolset. Background mode returns immediately with an `agent_id`; the child's result auto-arrives as a `<task-notification>` user-role message in the parent's next turn (no polling). JSONL sidechain transcript at `~/Jarvis/subagents/<id>.jsonl`. Depth-3 fork-bomb guard. Cost-class routing (`cheap` forces local even when parent is on cloud — fan-out of 50 chunks stays free). Sync subagents inherit the parent's cancel signal; background ones survive Ctrl-C deliberately. `/agent` slash + `spawn_subagent` / `list_subagent_personas` / `get_subagent_result` tools.
- **MCP client runtime (`hearth/mcp_client.py`)** — Hearth now ALSO consumes other MCP servers. Drop a standard `mcp.json` at `~/Jarvis/mcp.json` (Settings → MCP servers → Edit, or `/mcp edit`). On launch, each server spawns as a child subprocess via stdio; its tools surface as `mcp_<server>_<tool>` in `to_openai_tools()`, callable by the model like any built-in. Calls route through the live session; results bounce back to the chat. End-to-end tested with an in-repo echo+add server (`scripts/mcp_test_server.py`). `/mcp` slash for status / edit-config / run-as-server.
- **MCP server SyntaxError fix** — the dynamic tool factory was emitting `def _tool(title: Optional[str] = None, message: str):` (required-after-optional → SyntaxError). All tools now register cleanly.
- **Auto-background long ops (`hearth/jobs.py:start_python_job`)** — `disk_usage` on a drive root (`C:\`) or with `max_depth<=0` would block the agent loop for many minutes walking the whole tree. Now auto-routes through a daemon-thread job runner: returns a `job_id` in milliseconds, scan continues in background. `list_jobs` + `get_job_result` tools + `/jobs` slash. Same pattern available for any in-process Python work.
- **Loop guard catches decline spirals** (`hearth/loop_guard.py:_is_failure`) — repeated user declines of the same tool call now count as failures for the same-call repeat detector. After 4 identical declines, the model gets a hard "stop retrying it — tell the user plainly what's wrong" directive instead of cycling.
- **Compaction with target_chars** — `compact_history(target_chars=N)` retries with truncated kept tool results + halved keep_recent if the first pass still leaves you over budget. Stops the case where a kept tail of long tool results (browse / read_file) blew the budget right after a successful summary.
- **`/mcp`, `/agent`, `/jobs`, `/migrate`, `/name` slash commands** in CLI (full parity with GUI).
- **Migrate from Hermes / OpenClaw (`hearth/migrate.py`)** — `python -m hearth.migrate --from hermes --apply` reads `~/.hermes/memories/USER.md` + `MEMORY.md` (split on `\n§\n`), maps each entry to a Hearth memory file, runs the regex sub-category classifier. OpenClaw equivalent parses `MEMORY.md` H2 sections + `memory/YYYY-MM-DD*.md` daily notes. Optional `--include-skills` (parks SKILL.md dirs under `~/Jarvis/imported_skills/`) and `--include-config` (pulls model/provider into `settings.json`). **API keys are never copied.** Onboarding step 6 auto-detects + offers import; Settings → Behavior → Migrate panel surfaces it too.
- **Workspace permission extend prompt** — `_resolve_write` used to raise `PermissionError` immediately when a write was outside `~/Jarvis`. Now calls the registered host callback to ASK the user; on approve, the parent dir joins `EXTRA_WORKSPACES` so subsequent writes don't re-prompt. GUI: special "write outside workspace" permission strip with the path. CLI: `[y]es / [a]lways / [n]o` inline. Fixes the "I pressed Allow but JARVIS still declined" pain.
- **CLI Ctrl-C cancel** — `_respond_cancel` event raised by Ctrl-C during a stream; the response loop checks it between chunks, bails cleanly, prompt_toolkit re-takes the terminal. Sync subagents inherit the same signal. No more half-killed CLI on interrupt.
- **TEST_PLAYBOOK_v08.md** documents every new surface with concrete commands + expected outputs.

### v0.7 (memory hot/cold + agent rename + reminders v2 + brain-switch sanity)

- **Embered facts memory (`hearth/memory.py`)** — per `(type, sub_category)` bucket soft cap (default 6000 chars, `JARVIS_MEM_SUBCAT_CAP`). When over cap, the **coldest** facts auto-archive to `~/Jarvis/memory/_archive/` (never deleted). Coldness = `days_since_recall / (recall_count + 1)` — recent + popular stays, stale + ignored goes cold. Each fact tracks `recall_count` + `last_recalled_at`. Sibling pull on recall walks the archive too, marks results `(cold)`. After 3 hits, an archived fact auto-promotes back to hot store + re-indexes. Just-saved fact always protected from eviction. `_score_memories` falls back to archive when the hot store has zero matches (0.6× score multiplier so hot always wins ties).
- **Renameable agent end-to-end (`/api/agent/rename`)** — Settings → Behavior → Agent name input + Rename button. Validates name (alphanumeric + spaces, 1-20 chars), persists, hot-swaps `persona.NAME`, sets `HEARTH_PERSONA_NAME` env, atomically renames `~/Jarvis/` → `~/<NewName>/` via a detached helper subprocess (waits for tray PID to exit before moving the dir), respawns the tray with the new `JARVIS_WORKSPACE`. Chat avatar letter + persona signature + workspace folder all reflect the change. Onboarding step 5 asks during first-run. CLI `/name` for hot-swap (folder rename is GUI-only — needs tray respawn).
- **Action reminders (`hearth/reminders.py:set_reminder`)** — optional `action_tool` + `action_args` arguments. When the reminder fires, the bound tool runs alongside the toast; result is appended to the toast body. Plus: missed-on-boot catchup (the watcher startup pass fires anything that came due while Hearth was off, with "while you were away" prefix; recurring reminders advance to the next slot relative to NOW so a weekly that missed 3 cycles doesn't fire 3 toasts). `snooze_reminder` tool resurrects fired one-shots + pushes due time. Auto-prune one-shots that fired >30 days ago. Reminders panel in Settings → Behavior with per-reminder snooze + cancel buttons.
- **Brain-switch sanity** — `LOCAL_MODEL` env is now cleared whenever the builtin server starts (manual sticker pick + autoboot paths). Without this, switching from a cloud brain to local would forward the previous brain's model id to the new server and the new server would 404 it, producing empty replies. Per-provider context table moved into `headless.resolve_context_tokens()` so the chat path, the bottom-bar pill, and the `/api/context-budget` endpoint all read the same value. Brain-switch loading indicator: state pill + brand chip + disabled "Use this brain" button while the switch is in flight. Local-mode probe on switch: honest toast reports what's actually serving ("LM Studio" / "built-in" / "nothing").
- **MCP Settings tab (GUI)** — outbound `mcp.json` editor with JSON validation, live bridges status box, inbound snippet generator (the JSON to paste into LM Studio / Claude Desktop / Cursor).
- **Forge folder picker** — Settings → Behavior text field + Detect button (`/api/forge/detect` calls `_autodetect_forge_dir`). `forge_dir` added to settings defaults.
- **Procedural time injection** — system prompt gets `Current local time: YYYY-MM-DD HH:MM (DayName, tz X).` on every turn. "what should I do?" / "good morning" / "remind me tomorrow" work without a get_time tool call.
- **Memory graph (GUI)** — D3 force-directed constellation with zoom/pan/drag. Search highlights matched node + its sibling-cluster neighbors instead of hiding non-matches. 220ms debounce + 80 warm-up ticks before fade-in (no initial star-pattern flash).
- **Chat title typewriter** — auto-generated titles type in char-by-char in the sidebar instead of jump-cutting. Manual rename via the pencil also typewriters.
- **VRAM force-load** — "Could not start" modal now has a "Force load (spill to RAM)" button. `start_builtin(force=True)` bypasses the guardrail AND auto-downgrades `n_gpu_layers` to a partial-offload count (via `estimate_safe_gpu_layers`) so llama.cpp actually spills weights to RAM instead of OOMing.
- **Image gen tool card** — animated status text moved BELOW the card so the body stays a clean "prompt only" header. Finished image renders as its own block under the assistant message (lightbox + native video controls for `generate_video`).
- **Server log noise** — `--verbose false` on the llama.cpp spawn (400+ "CUDA Graph id reused" per session gone). `status()` poll passes Bearer key so the 401 storm from auth-gated builtin probes is gone.

### v0.6 round 7 (this build)
- **Multi-family tool-call parser** (`hearth/tool_call_parser.py`). Recognizes Gemma 3/4's `<|toolcall>call:NAME{...}<tool_call|>` syntax, Hermes / Qwen 2.5 / Qwen 3 ChatML, Llama 3.x `<|python_tag|>...<|eom_id|>`, Mistral `[TOOL_CALLS]`, Phi 3/4 `<|tool|>`, IBM Granite `<|tool_call|>...<|tool_call_end|>`, Cohere Command-R `Action: \`\`\`json [...]\`\`\``, and generic XML `<function=NAME>{...}</function>`. Normalizes mangled tool names (Gemma's `viewimage` → `view_image`), strips the raw syntax from the chat surface, injects clean OpenAI `tool_calls` into the assistant message, and routes through the regular executor. Adding a new family is one `_Pattern` + 2-line extractor. 9/9 unit tests pass on real-world emissions.
- **CLI `/models use <n>` no longer requires a restart.** New `_retarget_to(url, key, path)` helper live-rebuilds the OpenAI client + updates `LOCAL_API_BASE` / `LOCAL_API_KEY` / `headless` module globals + refreshes `current_model` + auto-redetects context. `/models get <id>` does the same after a download finishes.
- **HEARTH violet banner**. Boot art changed from "JARVIS" to "HEARTH" block-letters in the same violet gradient as the GUI (`#8b5cf6 → #a78bfa`). Persona name stays configurable via `HEARTH_PERSONA_NAME`.
- **Windows Job Object on every spawned llama_cpp.server child** (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`). When Hearth's parent dies — clean exit, Ctrl-C, taskbar close, force-kill, unhandled crash — the OS kills the child. Port 1234 is always free for the next launch. Belt + suspenders with the existing atexit hook.
- **`external_server_running` honesty**. Only treats 401/403 as "alive" when we supplied an API key (i.e. we know we're probing our own auth-gated builtin). Stops phantom "External server detected" pills when a random unauthenticated listener squats port 1234.
- **`_list_models` builtin-aware**. Matches LOCAL_API_BASE to builtin URL by **port** (not exact string) so `localhost` vs `127.0.0.1` drift can't suppress the loaded model. Passes the Bearer key on the generic `/v1/models` fallback so auth-gated servers actually answer.
- **PDF → VLM-OCR fallback** (`hearth/tools.py:_extract_pdf`). Detects scanned/image-only pages, renders them via pypdfium2, sends to the currently loaded vision model. Honors `vlm_ocr=false` to opt out per call. Solves the "Hearth read page 1 of my math PDF and stopped" case.
- **Multi-family parser available to CLI, GUI, and bridge.** All three surfaces share `hearth.tool_call_parser` via `headless.run_once`, so a model that emits Gemma-style syntax in the CLI gets the same auto-execution as in the desktop window.

### v0.6 round 6
- **Real-time voice mode** (silero VAD + RealtimeSTT). 0.3s endpoint, 0.1s partial-transcript cadence, instant mid-sentence barge-in via `on_vad_detect_start` callback. Phone-call feel.
- **Models tab redesign** — sub-nav (My Models / Discover / Quick picks), per-row inline expansion with full llama.cpp load config (GPU offload, ctx, KV cache K/V quant, threads, flash-attn) saved per-model to `~/Jarvis/model_configs.json`. Capability tags drawn ONLY from HF tags (no heuristic name-guessing).
- **Settings sidebar** (Chat brain / Voice / Behavior / About) — left sub-nav, gradient avatar + "@0pen-sourcer" footer.
- **Onboarding overlay** — 6-step full-screen card flow (welcome / brain / cloud / voice / personalize / done).
- **Hardened install path**: CUDA 12 runtime DLLs (`nvidia-cuda-runtime-cu12` + `nvidia-cublas-cu12` + `nvidia-cuda-nvrtc-cu12`) installed via pip wheels so users without a system-wide CUDA Toolkit get a working builtin LLM. `hearth/__init__.py` calls `os.add_dll_directory` on the wheel bin paths before any `import llama_cpp`.
- **`llama_cpp.server` extras** (fastapi / uvicorn / sse-starlette / pydantic-settings / starlette-context) pinned in install.ps1 + Hearth.spec — without them the prebuilt llama-cpp-python wheel can't serve.

### Original Unreleased baseline:

### Added
- **Machine self-knowledge — Hearth learns your PC instead of disk-scanning for it.** New `hearth/environment.py` detects GPU/VRAM, RAM, the installed models (via the server API), and a NON-recursive top-level drive map, then writes them to local memory. Runs automatically at first-run onboarding (CLI **and** GUI/desktop — they share the path), on demand via the CLI **`/learn`** command, and the model can refresh it itself with the new **`learn_environment`** tool. This is the productized version of "a capable agent walks in already knowing your hardware/layout." Directly kills the failure where the model spent **118s** on `Get-ChildItem -Recurse` hunting for LM Studio model files and found nothing.
- **`list_models` tool** — lists the models on the connected server (LM Studio / Ollama / cloud) by querying its API, instantly. The model uses this for "what models do I have" instead of scanning the disk.
- **Proactive memory injection (`memory.recall_for_prompt`).** Instead of only injecting a passive *index* of memory titles and hoping the model calls `memory_recall`, Hearth now folds the saved facts most relevant to the current turn into the system message — fenced and framed as **authoritative reference** — so the model actually uses what it knows. Precision-gated (stopword filtering + a 2-distinct-term / score floor) so unrelated turns add **zero tokens**; bounded otherwise. Wired into the CLI and the shared `run_once` so the GUI gets it too.
- **Local self-improving plugin system (`hearth/plugins.py`).** Two layers: (1) any `~/Jarvis/plugins/*.py` following the contract (`TOOL` dict + `run(args)->str`) auto-loads as a first-class tool at startup; (2) the new **`create_plugin`** tool lets JARVIS author its OWN tool when it hits a capability gap — validated, permission-gated, persisted, registered live the same turn. 100% local, no cloud/account. Loading is fully sandboxed: a broken plugin is skipped and can never take down the core tools; plugins can't shadow a built-in name.
- **Tool-loop guard (`hearth/loop_guard.py`).** Replaces the old "stop after N calls" cap. Outcome-hash based + tiered: identical *mutating* dup → skipped (no duplicate side effects, e.g. 4 copies of one reminder); same call returning the same outcome → warn then stop (no-progress); same call failing, or one tool failing across many args → warn then stop; ping-pong (A↔B) detection. Warnings are injected into context (model self-corrects); a generous turn cap (`HEARTH_MAX_TURNS`, default 25) is the only hard ceiling. All thresholds env-tunable.
- **Cloud model support.** CLI + bridge read `LOCAL_API_KEY` / `OPENAI_API_KEY`, so Hearth runs against Gemini, xAI/Grok, OpenAI, OpenRouter, etc. Tested end-to-end on Gemini 2.5 Flash and Grok-4.3. One-click launchers `gemini.ps1` / `grok.ps1` (gitignored — they hold your key). Honest-launch rule: the demo stays LOCAL; cloud is a transparent optional feature.
- **`truststore`** at package init — uses the OS certificate store for TLS, so cloud endpoints work behind corporate/AV TLS-inspecting proxies (fixes `CERTIFICATE_VERIFY_FAILED`). Pinned in the PyInstaller bundle.
- **API-error classification (`hearth/errors.py`).** Maps endpoint failures (unreachable / auth / rate_limit / context_overflow / no_model / server_error / timeout) to one clear human next step instead of dumping a raw stack or HTML. Shared by CLI + bridge.
- **`whoami` tool** — the agent can introspect its own model/endpoint/local-vs-cloud/context/tools/memory instead of guessing or spawning shell commands.
- **`/about` CLI command** — version, endpoint (local/cloud), context, tools, memories, workspace, repo.
- **`/voice speed <n>`** — change TTS playback rate at runtime (0.5x–2.5x), no restart.
- **Context-fit pre-flight warning** at boot — if persona + tool schemas barely fit the loaded context, warns to load ≥24K. Plus a graceful boot line distinguishing cloud / local-ok / LM-Studio-up-but-no-model / unreachable.
- **Decline-with-reason** — at the `[y/n/a/N]` permission prompt, type free text and it's handed to the model as "do this instead" rather than a bare deny.
- **Kokoro TTS auto-download** — fetches the voice model on first use if missing (env `JARVIS_NO_AUTODOWNLOAD=1` to opt out).
- **Textual TUI preview (`hearth/tui.py`, `python -m hearth.tui`)** — a richer terminal UI; a thin shell over the bridge so it reuses the loop-guard + error-handling.

### Changed
- **Memory recall ranking** now weights *breadth of match* (how many distinct query words a fact touches) over raw repetition, filters stopwords so common words like "run" don't spuriously match `run_command`, and uses recency as the tiebreak. Better hits for both the `memory_recall` tool and the proactive injection.
- **Persona SPEED section** — added the `list_models`-not-disk-scan rule for the model case, and a reminder that the machine's drive map / hardware / models already live in memory, so the model consults that and goes straight to the right folder instead of rediscovering the layout mid-task.
- **Niche tools off by default.** The Forge/Stable-Diffusion trio is gated behind `HEARTH_ENABLE_FORGE=1` — cleaner default toolset + ~450 fewer prompt tokens/turn. (44 default tools, +`create_plugin`.)
- **Permission directives no longer leak to screen** — declines/skips show a clean `↳ declined → <your words>` label; the firm instruction stays in the model's context only.
- **Persona** — explicit identity disambiguation (the assistant is JARVIS; the human is NOT named Jarvis; `~/Jarvis` is the assistant's scratch dir, not the user) and a ban on trailing "want me to…?" chatbot offers (show the result, not a pitch).

### Fixed
- **"No user query found in messages" crash** — `trim_to_budget` ignored the ~6K tokens of tool schemas that ride in every prompt, so a restored conversation "fit" on paper while the real prompt overflowed the model context and LM Studio dropped the user turn. Budgeting now reserves the tool-schema cost. (The model-switch / restored-history crash.)
- **`python` running the Microsoft Store stub** — `_rewrite_python_invocation` now handles compound commands (`cd X && python y.py`), routing python/pip to the venv interpreter so scripts don't silently no-op against the WindowsApps stub. (Root cause of the "tictactoe spiral".)
- **Cloud request shape** — `chat_template_kwargs` and `stop` are now sent only to local endpoints (Grok 400s on `stop`; Gemini 400s on `chat_template_kwargs`); `null` assistant content coerced to `""` (Gemini rejects null).
- **Malformed tool-call markup stripped** — Gemma `<|channel>call:` and Hermes/Llama `<tool_call><function=…>` XML are removed from screen AND history (the markup was poisoning history → a 500 cascade).
- **CLI now reads the real API key** — it was hardcoding `"jarvis-local"`, the actual cause of the persistent `API_KEY_INVALID` on cloud.
- **`hearth.bat`** — em-dashes were breaking cmd.exe parsing (`'t'/'d'/'f'/'else' not recognized`); pure ASCII now.

## [0.5.0] — 2026-05-27 — *"local Jarvis ships"*

First public release. CLI is the recommended interface. The desktop app, system tray, wake word, and bundled .exe are all wired and working but parked as a v0.6 preview — see `docs/USER_GUIDE.md`.

### Added (over the v0.5 development cycle)
- **Desktop app (`hearth/desktop.py`)** — full-featured native window via PyWebView (Edge WebView2 on Windows). Top-bar model picker with auto-load (REST + `lms` CLI fallback), live GPU chip (util/VRAM/temp with color-coded warning tiers), sidebar nav (Chat / Memory / Files / Logs / Settings), file drop overlay (any file → workspace/uploads → auto-prompts model to read), memory browser (search, view, delete), workspace file browser with `read` + `summarize` per-file quick actions, activity-log tail, settings panel with custom switches persisting to `~/Jarvis/settings.json`, toast notifications, modal previews, dot-strip pulse during streaming, status bar with model + ctx + tools + mem + live state. Falls back to your default browser if PyWebView isn't installed. Launch: `python -m hearth.desktop`.
- **Web UI rewrite (`hearth/ui.html` + `hearth/web.py`)** — same UI as the desktop app, served over HTTP for browser access. 11 new API endpoints (`/api/state`, `/api/models`, `/api/models/load`, `/api/models/eject`, `/api/memory` CRUD, `/api/files`, `/api/file`, `/api/upload`, `/api/settings` GET+POST, `/api/gpu`, `/api/logs`, `/api/tools`, `/api/persona`, `/api/run_tool`). Stdlib-only — no Flask, no FastAPI. Launch: `python -m hearth.web`. The old chat-only bare-bones UI is replaced.
- **Subprocess UnicodeDecodeError fix in `_run_command`** — was using `text=True` which crashed the reader thread on non-UTF-8 bytes (0xDB and friends from raw file dumps or non-UTF-8 console code-page output). Crashes there came back as empty tool results, and the model spam-retried with shell calls — once burning 8 iterations / 5.6 minutes on a single prompt. Now captures bytes, decodes with `errors="replace"` so no stray byte ever escapes.
- **`summarize_file` tool** — wraps smart `read_file` with a "summarize this in 3-5 bullets" directive + tighter content cap. Lets the model do "summarize X.pdf" in ONE tool call instead of read_file + frame-it-themselves. Honors hint-style read_file returns (archives, images) verbatim so the model still gets pointed at list_archive / view_image when relevant.
- **`pip install` SUCCEEDED/FAILED status hint** in `run_command` output — when the model invokes pip via `run_command`, the result now prepends one of `[pip install SUCCEEDED — all already installed]` / `[pip install SUCCEEDED — newly installed: X]` / `[pip install FAILED]`. Stops the "model retries pip install 6 times because it can't tell 'Requirement already satisfied' is success" failure mode.
- **`locate_path` scans C-Z drives** (was C-P) AND defers the result-limit check to the final ranked list instead of early-bailing per source. SteamLibrary on G:, drives on Q:-Z:, and any source after drive_root now actually contribute candidates. Also skips Windows system folders (`$RECYCLE.BIN`, `System Volume Information`) at drive roots.
- **NEXT-STEP auto-nudge in the headless bridge** — when a tool result contains a `NEXT STEP:` directive (e.g. `open_app` fail → "call open_in_browser with..."), and the model's next turn just narrates instead of acting, the bridge fires a stronger nudge than the trigger-phrase wrapper: "execute that NEXT STEP NOW — call the tool. Don't narrate, don't describe, don't ask. Act." Catches the "describes the fallback instead of doing it" drift mode that trigger-phrase detection misses.
- **PowerShell quoting fix in `_rewrite_python_invocation`** — bare `"C:\path\python.exe" -m pip ...` is parsed by PowerShell as a string literal followed by stray `-m` ("Unexpected token"). Now prepends `& ` (the PS call operator) on Windows so pip / python invocations through `run_command` actually run. Pre-existing bug, surfaced by the file-reader battery.
- **Smart `read_file` — reads everything, not just text.** Auto-routes by extension to dedicated extractors: PDF (pypdf, page-sliced via start_line/end_line), DOCX (python-docx — body paras + tables), XLSX/XLSM (openpyxl, sheet-by-sheet, head 50 rows per sheet), PPTX (python-pptx, slide text), EPUB (stdlib zipfile + XHTML strip), IPYNB (stdlib json — markdown + code + text outputs), CSV/TSV (stdlib csv — first 30 rows + total count), JSON (structure-first descriptor + raw head; JSONL = first 20 records), HTML/XML (HTMLParser-stripped text), RTF (regex-stripped), and single-stream `.gz` / `.bz2` / `.xz` (decompress + head 8KB). Archives and media return one-line hints instead of binary garbage. Crash-safe fallback to text mode if an extractor explodes. The model no longer needs to know which tool to reach for — `read_file(path=...)` just works.
- **`list_archive` tool** — lists contents of .zip/.jar/.whl/.apk/.tar/.tar.gz/.tar.bz2/.tar.xz without extracting. Stdlib zipfile + tarfile, returns path + size per entry. .rar/.7z get a hint to use 7-Zip via run_command (stdlib can't read them).
- **`extract_archive_file` tool** — pull ONE file out of an archive into the workspace without unpacking the whole thing. Refuses `..` in inner_path. Suffix-matches if the inner_path is ambiguous-but-unique. Sandboxed write into workspace. Pair with the smart `read_file` to look inside any archived doc in two calls.
- **Anti-yield runtime wrapper** — when the model emits an "I'll search for that…" / "Let me check…" / "Going to run…" announcement WITHOUT actually calling the tool, the runtime detects this (via `_looks_like_yield()` against a curated trigger list), injects a "you didn't actually call the tool — do it now" nudge, and re-prompts ONCE per user turn. Closes the visible 25% drift mode on Qwen 9B / Gemma 4. 10/10 on trigger-phrase smoke tests.
- **`view_image` vision-detect** — checks the loaded model id for known vision sigs (`vl`, `vision`, `gemma-3`, `llava`, `moondream`, `internvl`, `minicpm-v`). On vision-capable models, rewrites the tool result as a multimodal image_url block (model actually sees the image). On text-only models, replaces with explicit "this model isn't vision-capable, don't make stuff up" text + suggestion to open_app for the user instead. Stops the 80-second stalls and the "I see VS Code on the left, terminals in the middle" hallucinations when running Qwen 3.5 9B (non-VL).
- **Voice cleanup for TTS** — Kokoro now skips runs of newlines (no more 5-second silent pauses on `\n\n` paragraph breaks), strips Windows file paths (so `Saved: C:\Users\you\...png` becomes just `Saved png`), and replaces 8+ digit runs (timestamps like `20260521_233527`) with "the file" so it doesn't get pronounced as "twenty trillion two hundred sixty billion…".
- **Web UI (chat-in-browser)** — `python -m hearth.web` launches a stdlib-only HTTP server on :8765, opens the browser to a single-file dark/warm-flame chat UI with status pills, pulsing dot grid that animates while streaming, tool-call cards, and a `/think` toggle. Pure HTML/CSS/JS, no new deps. Note: this is the "casual chat" UI; a true standalone takeover-app (Tauri shell) is deferred to a sister project.
- **Onboarding wizard** — first-run interactive setup. Asks 5 quick questions (name, role, tone preference, topics to avoid, preferred browser+profile) and writes the answers to memory + `~/Jarvis/rules.md`. Skip with `JARVIS_NO_ONBOARDING=1` or Ctrl-C. Solves the "Hearth says 'you mogged them bro' to an FBI agent" problem — user context is captured before turn 1.
- **Chrome / Brave / Edge profile name resolution** — `open_in_browser` now maps friendly display names ("personal", "work", "school") to on-disk directories ("Default", "Profile 12", etc.) by reading the browser's `Local State` JSON. No more silently opening the wrong profile because `--profile-directory=personal` matched nothing.
- **Headless / bridge mode** — `python -m hearth.headless --prompt "..."` runs a single prompt non-interactively and emits JSONL events (user / thinking / tool_call / tool_result / assistant / done). Lets another agent (or a CI harness) drive Hearth without typing. `--format text` for pretty human output.
- **`find_file` now scans non-system drives** (D:, E:, F:, G:, ...). Drives-first ordering, per-directory budget cap (1500 files), kind-aware folder prioritization (`movies/` walked before a `photos/` folder for kind=video), `$RECYCLE.BIN` / `System Volume Information` excluded, depth-calculation bug fixed.
- **`find_file` matches directory names** (so "where's my <project> folder" works).
- **Persona "Recipe" section** — concrete chains for intelligent fallback: `open_app` fail on web service → `open_in_browser`; news queries → `web_search → web_fetch → summarize`; play movie → `find_file → open_app`.
- **`open_app` error message nudges to fallback** — when an app isn't installed, the error tells the model to try `open_in_browser` with the canonical URL.
- **`run_command` routes pip/python through `sys.executable`** — so `pip install X` lands in Hearth's venv, not the system Python.
- **`view_image` actually feeds the image to vision-capable models** — CLI now rewrites the tool response to a multimodal block. No more hallucinated screenshot descriptions.
- **HF warnings suppressed at package import** (`HF_HUB_DISABLE_SYMLINKS_WARNING`, `HF_HUB_DISABLE_IMPLICIT_TOKEN`, `HF_HUB_DISABLE_PROGRESS_BARS`, etc.) + Python `warnings.filterwarnings` belt-and-suspenders. `/listen on` no longer dumps a wall of yellow warning text.
- **`/listen on` first-use status** — "warming up STT (whisper base.en; first run downloads ~150MB)" so the silent download doesn't look frozen.
- **Listen feedback-loop fix** — RMS threshold raised 5× during TTS playback so speaker echo doesn't re-trigger the listener; 400ms post-stop debounce. Tunable via `JARVIS_STT_TTS_THRESHOLD_MULT` and `JARVIS_STT_POST_STOP_DEBOUNCE`.

### Changed during v0.5 dev
- **Denial message rewritten** — when user rejects a tool, the result now screams "USER DECLINED" with explicit "do NOT retry with the same args" so the model stops being confused into thinking it was a technical failure.
- **Persona generalized** — removed verbatim HENTAIVIRUS / banned-phrases examples that the model was regurgitating. Rules are abstract now; model uses judgment.
- **Persona "How to decline" section** — bans the four common dodge phrases ("I'm programmed for utility", "let's keep things professional", "I'm here to help with X not Y", "I don't do dirty jokes") with replacements ("not my style today", "I'd rather not", "skip").
- **Persona disambiguation rules**: view_image (Jarvis sees) vs open_app (user sees in gallery); list_directory vs open_app for "what's in X"; "my Desktop" = real Desktop, not workspace/Desktop; no `close_app` exists.
- **Persona proactive memory use** — added explicit rule: if user asks vague preference questions ("what game should I play", "what should I eat") and a relevant fact is in the loaded memory index, USE it. Don't make the user repeat himself.

### Fixed during v0.5 dev
- **`find_file` no longer dies if budget eaten by alphabetically-earlier folders** (was missing `D:\Movies` because an earlier `photos/`-type folder ate the budget first).
- **PowerShell em-dash parse bug in install.ps1** — em-dashes inside strings got misread under Windows-1252 default and broke brace nesting. All em-dashes replaced with `-`.
- **`install.ps1` adds Pillow** to required deps (so `screenshot` works without a manual `pip install`).
- **Read-file-on-binary trap** — persona now says read text files only; for media/binaries, just present the paths.

## [0.4.0-prelaunch] — 2026-05-21 — *"public launch prep"*

First version cut for a public GitHub release. Renamed from the internal codename "Jarvis" to **Hearth** (the framework) — the agent persona is still **JARVIS**.

### Added
- **`find_file` tool** — smart finder that walks workspace, Desktop, Documents, Downloads, Pictures, Videos, Music, ~/Code, ~/Projects, and the current dir. With `kind` hint (image/video/audio/doc/code/archive/spreadsheet) and `deep` flag. Sorts hits by name-rank then mtime. Dedups across overlapping roots.
- **Scope guards on `grep_search` + `glob_files`** — drive-root paths (`C:\`, `D:\`, `/`) refused with a pointer to `find_file`. File-scan budget (10k files) prevents runaway walks.
- **Voice IN via faster-whisper** — `/listen` for one-shot, `/listen on` for continuous-with-interrupt. Auto-downloads `base.en` model on first use (~150MB).
- **Ambient mode interrupt** — start talking while TTS is playing and it stops cleanly before recording your utterance.
- **Wake-word filter** on `/listen on` — set `JARVIS_WAKE_WORD="jarvis"` (or `"hey jarvis"`) and ambient mode only fires when the utterance starts with that phrase. Position-0 strict matching, punctuation-tolerant.
- **`/allow <path>`** + `JARVIS_EXTRA_WORKSPACES` env var to extend writeable dirs beyond the default workspace.
- **Auto-detect LM Studio context window** via `/api/v0/models` `loaded_context_length` — no more guessing `JARVIS_CONTEXT=8192`.
- **MIT LICENSE, CONTRIBUTING.md, SECURITY.md, issue/PR templates, GitHub Actions smoke CI** (Windows + macOS + Linux × Python 3.11/3.12).
- **`install.ps1`** — one-line installer: venv, deps, workspace, opt-in voice models, LM Studio detection, first-run instructions.
- **38 tools** (up from 26 in the pre-public iteration).
- **Persona v3 hardening** — anti-spiral block, banned opening phrases, "you aren't bounded" rule. No more substring-panic when a question contains an edgy-looking typo.

### Changed
- **Package renamed**: `jarvis_brain/` → `hearth/`. Imports moved from `from jarvis_brain import ...` to `from hearth import ...`.
- **CLI renamed**: `jarvis.py` → `hearth_cli.py`. Launcher `Jarvis.bat` → `hearth.bat`.
- **Memory `_normalize_type`** is now lenient — any reasonable synonym ("User Preference", "settings", "creds") maps to one of the canonical four types instead of erroring.
- **`glob_files` pipe parsing** — accepts `"*.png|*.jpg"`, `"*.py;*.md"`, comma-separated, or a real JSON array.
- **Spinner cancel race fix** — first word of streamed replies after tool calls no longer gets erased by the spinner's cleanup.
- **Persona rebuilt every turn** so `memory/MEMORY.md` index + `rules.md` are always live.

### Removed
- Old `jarvis_brain` import paths. Clean break — there's only one user of the old paths (the maintainer), and the rename is mechanical.
- Bundled `Windows Terminal/` directory from the repo — Microsoft's binary, not ours to redistribute.
- Stale roadmap items from `docs/ARCHITECTURE.md` (semantic embeddings, ASCII expression mode — pushed to v1.0+).

### Fixed
- **Context auto-detect** stops auto-compacting wastefully when LM Studio's loaded context is much larger than the previous 8192 default.
- **`/listen` continuous mode** — typed input and spoken input race cleanly; whichever arrives first wins, the other cancels.
- **MCP server `view_image`** — returns a real `Image` content block in LM Studio's chat instead of a generic text response.
- **Reasoning display** — handles both inline `<think>…</think>` AND OpenAI `reasoning_content` (Qwen3.5, DeepSeek-R1 styles).

### Known issues (active backlog for 0.5.x)
- Anti-yield re-prompt — small models still occasionally announce "running it now" without calling the tool. Persona pushes hard; runtime nudge coming.
- Tool-fail auto-retry — failures are exposed to the model unchanged; sometimes it gives up instead of correcting. Coming in 0.5.x.
- Mac / Linux — `hearth/tools.py` has Windows-specific branches for app launching, registry reads, screenshot. PRs welcome ([CONTRIBUTING.md](CONTRIBUTING.md)).

## Pre-0.5 (internal)

The pre-public phase (private "Jarvis" iteration) added: memory v1 (per-fact markdown + always-loaded index), Kokoro TTS streaming, MCP bridge for LM Studio chat UI, permission prompts, vision via `@image.png`, context auto-compact at 75%, Persona v3 (proactive, anti-chatbot rules).

Not separately versioned — this CHANGELOG starts at the public launch.
