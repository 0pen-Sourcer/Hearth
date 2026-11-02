# Roadmap

Inglen runs your Windows machine for you, including files, shell, apps, a real browser, and
the desktop itself. You reach it from a terminal, a desktop app, your phone, an MCP
client, or a headless script. It thinks with a model on your PC, a cloud model, or an
agent you already pay for like Claude Code or Codex, with no account, and no telemetry
unless you opt in to an install count.

This is where it is and where it's going. Dates are intent, not promises.

## Where it is now (v0.7.6-preview)

- Reading and writing files, running commands in PowerShell, cmd or bash, opening
  apps, and driving a real browser, including the Chrome you already use.
- Memory that persists across sessions, notes lasting facts on its own, looks up what
  a message needs, and records it when something about you changes.
- Claude Code, Codex, Cursor, OpenCode and other agents as its brain, on your own plan,
  still using Inglen's tools and asking before risky steps.
- Helpers and whole teams you can watch in a little pixel room. They run on your PC's
  model, a cloud model or another agent, message each other, work on their own copy
  of a git repository, and their changes wait for you to bring them in or undo them.
- Reminders, background jobs, and skills you can install from a link or have it write
  for itself on the spot.
- Desktop control through the accessibility tree. `smart_click` takes a point from
  the vision model, snaps it to the real named control underneath, clicks that, and
  re-checks the screen to confirm it worked. A marked capture numbers every control
  so it can click by number. This is reliable on native Windows apps and on
  Chromium/Electron apps, and weaker on canvas-drawn UIs and games that don't
  expose their controls.
- Voice in eight languages, local listen (Whisper) and speak (Kokoro), hands-free,
  with a wake word. You can talk over a reply in the window on headphones or speakers.
  In the terminal, on speakers, you start with its name ("Inglen, stop").
- Your phone through Telegram, Discord or WhatsApp, running the full agent on your PC,
  with pairing codes for anyone new and approvals asked in the chat.
- Inglen Plus, a paid add-on: a phone app that reaches your PC from anywhere through an
  end-to-end encrypted relay, with approvals, the Team room and voice calls.
- An optional bundled llama.cpp engine. If you already run LM Studio or Ollama,
  point Inglen at that instead.
- Pictures and short videos made on your own graphics card with stable-diffusion.cpp,
  which Inglen installs and runs itself, reading a ComfyUI or WebUI models folder in
  place. Cloud image models work from any brain too.
- Desktop actions you can watch, with an edge round the screen while Inglen works and
  your window handed back when it's done.

There are over a hundred tools, and they aren't equal. About forty are core. The rest
are specialized (image and video generation, email, some desktop-control cases) and
depend on setup you may not have. Those stay held back until the model actually needs
one, so you don't trip over them.

## What's next

- A signed installer, so Windows stops warning about an unknown app.
- Image models with names of their own, picked up by trying them rather than by
  their file names.

## Further out

- A proactive side, with reminders and background agents that reach out to you first,
  watch for changes that matter, and wait for your go-ahead before anything with
  consequences.
- Making `smart_click` reliable on the hard surfaces, with an OCR fallback when the
  accessibility tree is sparse, and multi-monitor and fullscreen awareness.
- A guided mode. Instead of doing a task silently, it walks you through an app step
  by step, narrating and handing you the wheel at each point.
- Sync, so your memory, settings and skills follow you across devices, end-to-end
  encrypted, as part of Plus.
- Mac and Linux. The terminal, the web window, most tools and both engines already run
  from source on Linux today, and the whole test suite passes there. What's still
  Windows-only is the desktop-control layer and the one-click installer, and macOS
  hasn't been tried on a real Mac yet.
- A bigger skills ecosystem, with easier discovery of what other people have shared, and
  an idle curator that refines and prunes the skills the agent writes for itself.

## What won't change

- Local-first. Cloud is an option you can turn on, never something you're forced into.
- You can see what it did. Every tool call, file change and piece of reasoning a model
  shows can be opened in the chat, and the Logs page keeps the record.
- The free app needs no account and no server of ours in the middle. It's your machine
  and your data. The only exceptions are ones you choose: the Plus relay, which can't
  read what it carries, and the opt-in install count.
