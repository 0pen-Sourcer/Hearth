v0.7.6 is the release where Hearth stops fumbling the things it claimed to do. Desktop control no longer types into the wrong window, memory actually remembers on its own, and cloud models finally behave like the models they are.

If you are on v0.7.2 or newer, Hearth patches itself, no reinstall. On v0.7.0 or v0.7.1 you need the installer one more time, then you are on the small updates for good. Updating never touches your chats, memory, or models.

## Desktop control that lands where you meant

The biggest failure in older builds was invisible. Hearth would decide to click something, the click would land, and then seconds later it typed into whatever window happened to have focus by then, which was often your editor or Hearth's own chat box. Now every keystroke checks first. If focus drifted, or if the front window is Hearth itself, the typing is refused and Hearth re-targets instead of spraying text somewhere you did not ask for. The same guard means Hearth stops the moment you grab the mouse or keyboard mid-task.

Clicking and typing can also happen as one action now, so there is no gap for focus to move through at all. And Hearth can write into an app without yanking it to the front. Point it at a window by name, even one sitting in the background, and it reads that window's real controls and fills the field directly, the way pasting works, rather than stealing your screen to do it.

## Memory that keeps up

Recalling a memory could take the better part of a minute on a healthy machine. A single lookup was opening hundreds of files, and on Windows every one of those gets scanned by antivirus on the way past. Hearth now reads each file once and reuses it, so recall is quick again and stays quick as your memory grows.

Saving is instant too. The housekeeping that archives older facts used to run inside the save itself, which is why saving a single fact could stall for several seconds. That work moved into the background where it belongs, and it now runs on its own schedule instead of only when something happened to be saved.

Passive memory was quietly broken for anyone on a reasoning model, and this is the fix most people will feel. Hearth is supposed to notice durable facts on its own without you saying "remember that". Instead the model would spend its whole budget thinking and hand back nothing, or get cut off halfway through, and nothing was ever saved. Extraction now has the room it needs, reads the reply even when a model buries it in its reasoning, and never competes with you for the model. If you send a message while it is working, your message wins and the facts get picked up on the next pass.

## Cloud models behave like themselves

Reasoning is visible again on OpenRouter and other providers that stream their thinking in a different field than local servers do. You can choose how hard a model thinks, low, medium, or high, for models that support it. A cloud model reports its real context window instead of a flat guess, so a million token model no longer claims a fraction of that. Images reach cloud models that can see, rather than being refused by a check meant for local servers. The model list is sorted and searchable instead of hundreds of unordered lines. And a cloud brain reads as online rather than loaded, because nothing is sitting in your VRAM.

## Voice, again, properly

Voice failed to start on updated installs because the update was missing pieces of its speech stack. Both gaps are closed, so voice comes up on a patched install the same as a fresh one. The microphone test also runs in its own process now, which means a headset you plug in after opening Hearth is actually seen instead of ignored until restart.

## Reading a run

File edits render as a real diff, tight rows with line numbers and a change count, instead of loose colored blocks with gaps between them. Tool cards show a clean one line summary of what was passed rather than raw escaped JSON. The terminal got the same treatment, with a glyph per kind of action, compact arguments, and a short peek at long output instead of flooding your scrollback. When you steer Hearth mid answer, the redirected part is clearly separated instead of running into the previous sentence. Announcements from the developer sit as a card you dismiss when you are ready, instead of a toast that vanishes.

The window is responsive now, so a narrow or half snapped window collapses the sidebar into a drawer instead of breaking the layout. Chat titles come from the first real exchange, so a chat that opens with "hey" gets a title about what you actually discussed. Optional sound cues can tell you when a turn finished, when something failed, or when Hearth needs your permission, all off by default.

## Smaller things

Hearth can confine reads to your workspace from Settings now, no environment variable needed. Declining an action and telling Hearth what to do instead makes it do that thing, rather than only talking about it. Windows shows the version you actually have after an update. People running from a git clone get told when there is something worth pulling. And when no model is loaded, both the app and the terminal say so plainly in red, instead of showing a placeholder that looked like a loaded model.

---

Windows installer only. Full includes the GPU engine, Lite is for people already running Ollama or LM Studio. Linux runs from source, though desktop control is Windows only for now. Not code signed yet, so Windows shows the blue unknown-publisher box, click More info then Run anyway.
