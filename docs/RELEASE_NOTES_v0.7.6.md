v0.7.6 is a fix release across desktop control, memory, cloud models, and the interface.

If you are on v0.7.2 or newer, Hearth patches itself, no reinstall. On v0.7.0 or v0.7.1 you need the installer one more time, then you are on the small updates for good. Updating never touches your chats, memory, or models.

## Desktop control

Every keystroke Hearth sends now checks the target window first. If focus moved since it clicked, or if the front window is Hearth itself, the typing is refused and Hearth re-targets rather than putting text somewhere you did not ask for. The same check means Hearth stops as soon as you take the mouse or keyboard back mid task.

Clicking and typing can run as a single action, so nothing can slip in between the two. Hearth can also write into an app without pulling it to the front. Point it at a window by name, including one in the background, and it reads that window's real controls and fills the field directly instead of taking over your screen.

Declining an action and telling Hearth what to do instead now makes it do that thing, rather than only acknowledging it.

## Memory

Recall reads each memory file once and reuses it, so a lookup stays fast as your memory grows. Saving a fact returns immediately, with the archiving of older facts moved into the background and running on its own schedule.

Passive memory works on reasoning models. Hearth notices durable facts on its own without you saying "remember that", and it now has the room to finish writing them and can read the reply even when a model buries it in its reasoning. It also never competes with you for the model. If you send a message while it is working, your message goes first and the facts are picked up on the next pass.

## Cloud models

Reasoning is visible on OpenRouter and other providers that stream their thinking in a different field than local servers do. You can set how hard a model thinks, low, medium or high, on models that support it. A cloud model reports its real context window rather than a fixed guess, so a large context model shows what it actually has. Images reach cloud models that can see them. The model list is sorted and searchable instead of hundreds of unordered lines, and a cloud brain reads as online rather than loaded, since nothing is held in your VRAM.

## Voice

Voice starts on an updated install. The update now carries the maths library the speech stack loads at startup, along with the rest of its dependencies, so a patched install behaves the same as a fresh one. The microphone test runs in its own process, so a headset you plug in after opening Hearth is picked up without a restart.

## Reading a run

File edits render as a diff with line numbers and a change count, in tight rows. Tool cards show a one line summary of what was passed rather than raw escaped JSON, and the terminal does the same with a glyph per kind of action and a short peek at long output. Steering Hearth mid answer separates the redirected part from what came before. Announcements from the developer stay as a card until you dismiss them.

The window is responsive, so a narrow or half snapped window moves the sidebar into a drawer. Chat titles come from the first real exchange, so a chat that opens with a greeting still gets a title about the actual subject. Optional sound cues mark a finished turn, an error, or a permission request, all off by default, and user messages can sit in chat bubbles if you prefer, also off by default.

## Elsewhere

Reads can be confined to your workspace from Settings without setting an environment variable. Windows shows the version you actually have after an update. People running from a git clone are told when there is something worth pulling. When no model is loaded, the app and the terminal both say so in red instead of showing a placeholder that looked like a model.

---

Windows installer only. Full includes the GPU engine, Lite is for people already running Ollama or LM Studio. Linux runs from source, though desktop control is Windows only for now. Not code signed yet, so Windows shows the blue unknown-publisher box, click More info then Run anyway.
