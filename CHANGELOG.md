# Changelog

Notable changes to Hearth. Format loosely follows [Keep a Changelog](https://keepachangelog.com); semver is not strict pre-1.0.

## v0.7-preview

First public release. Windows-first; macOS/Linux support is partial.

### Interfaces
- CLI with a voice loop, command history, slash-command autocomplete, in-session model control, and a context-usage footer.
- Desktop app and web UI sharing the CLI backend: multi-chat sidebar, a Models tab for downloading and loading GGUFs with per-model load settings, voice mode, file drop, inline permission prompts, and a settings panel.
- Headless bridge (`python -m hearth.headless`) that runs a single prompt non-interactively and emits JSONL events to stdout.
- MCP server (`python -m hearth.mcp_server`) exposing every built-in tool to MCP-aware chat hosts.

### Models
- Runs against any OpenAI-compatible server. Auto-detects LM Studio, Ollama, and llama.cpp at boot.
- Optional bundled llama.cpp server installed via `install.ps1 -BuiltinLLM cuda|cpu`, so no separate LLM app is required. Preview-quality; tool-call reliability lags a dedicated runner.
- Optional cloud endpoints (Gemini, OpenAI, Grok, OpenRouter) selectable in Settings, switchable mid-session without restart.
- Tool-call parser that recognizes Gemma, Hermes, Qwen 2.5/3, Llama 3.x, Mistral, Phi, Granite, and Cohere Command-R formats plus a generic `<function=NAME>` form.

### Tools and capabilities
- File read/write/edit/list/move/delete. `read_file` extracts text from PDF, DOCX, XLSX, PPTX, EPUB, IPYNB, CSV, JSON, HTML, RTF, and `.gz`/`.bz2`/`.xz`. Archive inspect/extract without unpacking. `summarize_file` for short summaries. `read_pdf_large` for map-reduce summarization of large PDFs.
- Shell command execution (PowerShell / cmd) with captured, sanitized output.
- App, file, folder, and URL launching with one tool.
- Real browser driving (Chrome / Brave / Edge): open a page, list clickable elements, click, type, scroll, with a persistent session across calls.
- Screenshot capture and image viewing through a vision-capable model.
- Web search and fetch via DuckDuckGo.
- Persistent per-fact memory with recall tracking, automatic cold-fact archiving and warm-back, and overlap detection on save.
- Reminders (one-shot and recurring) set in natural language, optionally firing a tool when due, with catch-up on next launch for reminders missed while closed.
- Skills that generate documents on request: PDF, PPTX, XLSX, diagrams (SVG/HTML), ASCII art, and PDF split/merge. User-provided skill folders are invokable by name; the assistant can author new skills.
- Sub-agents (researcher, coder, archivist, librarian, summarizer, PDF coordinator) with per-agent tool allowlists and a tightened prompt, runnable synchronously or in the background with results delivered on the next turn. Fork depth is bounded.
- Background jobs for long-running operations, returning a job id immediately.
- MCP client: consume other MCP servers via an `mcp.json` in the workspace, surfacing their tools alongside the built-ins.
- Voice: streaming Kokoro text-to-speech and faster-whisper speech-to-text with continuous-listen barge-in and optional wake word. CUDA auto-detect; CPU by default. Preview-quality.
- Self-extending tools: the assistant can author a validated plugin to fill a capability gap; hand-written plugins auto-load from the workspace.
- A `soul.md` self-identity file loaded into every prompt, and end-to-end agent rename (avatar, persona, and workspace folder).
- Optional cloud image/video generation and local Stable Diffusion (Forge) image generation.
- Memory migration from prior agent layouts (API keys are never copied).

### Behavior
- Default tool set is trimmed to a core group; niche tools are revealed on demand via a `load_tools` meta-tool to keep the prompt small. `HEARTH_ALL_TOOLS=1` loads everything up front.
- A loop guard detects and breaks repeated, no-progress, or ping-pong tool-call patterns.
- Writes confined to the workspace by default; risky tools prompt for permission per session, and permission decisions persist and are shared between the CLI and the app.
- Procedural local time injected into the system prompt each turn.

## 0.5.0

- CLI as the primary interface, with voice in/out, permission prompts, and context auto-detection.
- Desktop app (native window) and web UI sharing one backend; system tray; optional wake word.
- File reading across PDF/DOCX/XLSX/PPTX/EPUB/IPYNB/CSV/JSON/HTML/RTF and archive inspect/extract.
- First-run onboarding wizard writing answers to memory and house rules.
- Smart file finder scanning non-system drives with kind hints.
- Headless bridge mode emitting JSONL events.

## 0.4.0

- First version cut for public release. Package renamed to `hearth`; the assistant persona remains JARVIS.
- Smart file finder, scoped search guards, voice input via faster-whisper, ambient interrupt, wake-word filter.
- LM Studio context auto-detection.
- Windows installer (`install.ps1`), MIT license, contributing/security docs, and CI smoke tests.
