# Hearth — Roadmap

Hearth is a local-first AI that runs your Windows machine — files, shell, apps, a
real browser, and the desktop itself — reachable as a CLI, a desktop app, your
phone, an MCP server, or a headless bridge. It runs on any OpenAI-compatible
model, local or cloud, with no account and no telemetry.

This is the trajectory, not a promise sheet — dates are intent, not contracts.

## Now — v0.7-preview

- **Acts on your machine.** 100+ tools: read/write files, run the shell, drive
  apps, a real Chromium browser you watch, screenshots + vision, persistent
  memory, reminders, loadable skills, and background sub-agents.
- **Any brain.** Auto-detects a local server (LM Studio / Ollama / llama.cpp) or
  a cloud key (Grok, OpenAI, Gemini, OpenRouter); switch mid-session.
- **Five front doors.** Desktop app, terminal CLI, phone (Telegram / Discord /
  WhatsApp), MCP server, and a scriptable headless bridge.
- **Voice.** Real-time listen (silero VAD + Whisper) and speak (Kokoro) — local,
  hands-free, with barge-in. A desktop presence overlay reacts to its own voice
  (listening / thinking / speaking) while you're in another window.
- **Computer-use.** `smart_click` fuses a vision point with the accessibility
  tree — it snaps to the real named control, clicks it, and re-screenshots to
  verify, instead of guessing a pixel. Window awareness: list / focus / manage
  windows by name.
- **Generates media.** Images and video, local via Forge or through a cloud model.
- **Private by default.** Your model and your data stay on your disk. No account,
  no cloud required, no telemetry.

## Next

- **Guided mode** — "walk me through this" inside an app: it drives, narrates,
  and hands you the wheel at each step.
- **Multi-monitor + fullscreen** awareness for the control loop.
- **Self-improving skills** — an idle curator that refines and prunes the tools
  the agent writes for itself.

## Later

- **Mac & Linux.** The framework is Python; the desktop-control layer is the port.
- **Skills & plugins ecosystem** — install a capability from a link; write a new
  tool in a single call.
- **Proactive loop** — reminders and background agents that reach you first.

## Principles

- **Local-first.** Cloud is an option, never a requirement.
- **Show the work.** Every tool call, every thought, every file change is visible.
- **No account, no telemetry.** Your machine, your data.
