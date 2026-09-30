# Roadmap

Hearth runs your Windows machine for you, including files, shell, apps, a real browser, and
the desktop itself. You reach it from a terminal, a desktop app, your phone, an MCP
client, or a headless script, and it runs on whatever OpenAI-compatible model you
point it at, local or cloud, with no account and no telemetry.

This is where it is and where it's going. Dates are intent, not promises. I build
this in my spare time, so things land when they're actually good, not on a schedule.

## Where it is now (v0.7.6-preview)

The core is stable and I use it every day:

- Reading and writing files, running commands in PowerShell, cmd or bash, opening
  apps, and driving a real browser, including the Chrome you already use.
- Memory that persists across sessions, folds the relevant facts back into context
  on its own, and records it when something about you changes.
- Reminders, background jobs, sub-agents, teams of agents you can watch, and skills
  you can install from a link or have it write for itself on the spot.
- Desktop control through the accessibility tree. `smart_click` takes a point from
  the vision model, snaps it to the real named control underneath, clicks that, and
  re-checks the screen to confirm it worked. A marked capture numbers every control
  so it can click by number. This is reliable on native Windows apps and on
  Chromium/Electron apps, and weaker on canvas-drawn UIs and games that don't
  expose their controls.
- Voice in eight languages, local listen (Whisper) and speak (Kokoro), hands-free,
  with a wake word. Interrupting by voice works on headphones.
- Your phone through Telegram, Discord or WhatsApp, running the full agent on your PC.
- An optional bundled llama.cpp engine. If you already run LM Studio or Ollama,
  point Hearth at that instead.

There are around 100 tools, and they aren't equal. About forty are core. The rest
are specialized (image and video generation, local Stable Diffusion via Forge,
email, some desktop-control cases) and depend on setup you may not have. Those stay
held back until the model actually needs one, so you don't trip over them.

## What I'm working on next

- Interrupting by voice on speakers, not only headphones, with echo cancellation on the
  microphone, and "Jarvis, stop" as the interrupt while Hearth is talking.
- Onboarding that asks what it needs once, in a conversation. Your name, how you
  want it to talk, headphones or speakers, and where your files live.
- Your look, with a light theme and an accent colour in the app, and matching accents in
  the terminal.
- A view of your agents in the app, showing who is working, who is waiting on you, who is
  done, and a click to see what each one did.
- Sub-agents and teams asking permission the same way Hearth does, so your
  permission settings hold whoever is doing the work.

## Further out

- A proactive side, with reminders and background agents that reach out to you first,
  watch for changes that matter, and wait for your go-ahead before anything with
  consequences.
- Making `smart_click` reliable on the hard surfaces, with an OCR fallback when the
  accessibility tree is sparse, and multi-monitor and fullscreen awareness.
- A guided mode. Instead of doing a task silently, it walks you through an app step
  by step, narrating and handing you the wheel at each point.
- Mac and Linux. The CLI, the web UI, and most tools already run from source on
  Linux today (files, shell, screenshots, app launching, web, reminders). What's
  still Windows-only is the desktop-control layer and the one-click installer.
- A bigger skills ecosystem, with easier discovery of what other people have shared, and
  an idle curator that refines and prunes the skills the agent writes for itself.

## What I won't change

- Local-first. Cloud is an option you can turn on, never something you're forced into.
- Everything is visible. Every tool call, every file change, every bit of reasoning
  is on screen. Nothing happens behind your back.
- No account, no telemetry, no server I run in the middle. It's your machine and
  your data.
