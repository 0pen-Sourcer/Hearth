# Roadmap

Hearth runs your Windows machine for you: files, shell, apps, a real browser, and
the desktop itself. You reach it from a terminal, a desktop app, your phone, an MCP
client, or a headless script, and it runs on whatever OpenAI-compatible model you
point it at, local or cloud, with no account and no telemetry.

This is where it is and where it's going. Dates are intent, not promises. I build
this in my spare time, so things land when they're actually good, not on a schedule.

## Where it is now (v0.7-preview)

The core is stable and I use it every day:

- Reading and writing files, running the shell, opening apps, and driving a real
  Chromium browser you can watch. These are the tools that get used constantly and
  they're solid.
- Memory that persists across sessions and folds the relevant facts back into
  context on its own.
- Reminders, background jobs, sub-agents, and skills you can install from a link
  or have it write for itself on the spot.
- Desktop control through the accessibility tree: `smart_click` takes a point from
  the vision model, snaps it to the real named control underneath, clicks that, and
  re-checks the screen to confirm it worked, instead of guessing at a pixel. This is
  reliable on native Windows apps and on Chromium/Electron apps; it's weaker on
  canvas-drawn UIs and games that don't expose their controls, and improving that is
  on the list below.
- Voice: local listen (Whisper) and speak (Kokoro), hands-free with barge-in. This
  works but it's preview-quality, expect rough edges.
- The bundled llama.cpp server is optional and also preview-quality. If you already
  run LM Studio or Ollama, point Hearth at that instead.

Honest note on the tool count: there are around 100 tools, but they aren't equal.
About forty are core and rock-solid. The rest are more specialized (image and video
generation, local Stable Diffusion via Forge, email, some desktop-control cases) and
depend on setup you may not have, a cloud key, a Forge install, an app that exposes
its controls. Those stay hidden until the model actually needs one, so you don't
trip over them.

## What I'm working on next

- Making `smart_click` reliable on the hard surfaces: an OCR fallback when the
  accessibility tree is sparse, and a plain coordinate click when there's no named
  control to snap to.
- A guided mode: instead of doing a task silently, it walks you through an app step
  by step, narrating and handing you the wheel at each point.
- Multi-monitor and fullscreen awareness for the control loop.
- General reliability polish on the long-tail tools so fewer of them need setup to
  be useful.

## Further out

- Mac and Linux. The CLI, the web UI, and most tools already run from source on
  Linux today (files, shell, screenshots, app launching, web, reminders). What's
  still Windows-only is the desktop-control layer and the one-click installer, so
  full parity on other platforms is the real work here.
- A bigger skills ecosystem on top of what's already there: easier discovery of
  what other people have shared, and an idle curator that refines and prunes the
  tools the agent writes for itself.
- A proactive side: reminders and background agents that reach out to you first
  instead of waiting to be asked.

## What I won't change

- Local-first. Cloud is an option you can turn on, never something you're forced into.
- Everything is visible. Every tool call, every file change, every bit of reasoning
  is on screen. Nothing happens behind your back.
- No account, no telemetry, no server I run in the middle. It's your machine and
  your data.
