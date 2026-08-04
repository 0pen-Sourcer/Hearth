This release is mostly voice mode finally feeling like a real conversation, plus a run of fixes for connecting Hearth to other tools and running it from source.

If you are on v0.7.2 or v0.7.3, Hearth updates itself with a small patch, no reinstall. On v0.7.0 or v0.7.1 you need the installer one more time, and after that you are on the small updates for good.

Voice mode is the headline. You can talk over Hearth mid-sentence now and it stops on your first word, then treats what you said as your next message instead of dropping it and making you repeat yourself. It uses the mic you picked instead of whatever the system defaults to, and if that mic will not open, which Bluetooth headsets love to do, it falls back to the default and tells you, rather than sitting on a listening screen that cannot actually hear you. On speakers it can now tell its own voice apart from yours, so it stops answering itself in a loop. The speech runs together as one smooth stream instead of the stop-start chop, the caption scrolls cleanly for a long reply, and it no longer reads markdown or stray tool tags out loud. Permission prompts can be answered by voice, just say yes or no. And the things that used to leave it stuck are gone, the mic tip that kept coming back, hanging on listening or thinking forever, going mute after one interrupt.

Connecting Hearth to another app over MCP got a lot better. The config you copy from Settings now carries everything the other side needs, so a client like Claude Desktop or Cursor can run commands and edit files instead of hitting a wall with no way to approve. Point it at a project outside Hearth's home folder and you can allow that folder once and it stays allowed. Background agents you spawn can be checked on with the id Hearth hands you, however you refer to it, and the assistant no longer gets stuck in a loop when it reaches for a skill as if it were a tool.

The set of tools the model carries each turn is leaner now. Niche things like image generation load themselves the moment they are needed instead of riding along in every prompt, which leaves more room for the actual conversation, and that room matters most on smaller local models.

Onboarding had a nasty one. If a fresh install errored partway through setup, it left just enough behind that every later launch decided you were already set up and skipped onboarding, on both the app and the CLI. Setup only counts as done when it actually finishes now, so a crash mid-setup just runs it again.

Running from source got care too. The Linux tray no longer crashes on launch over a single character in a window title, Hearth stopped downloading voice models just to check whether voice was available, auto-start at login works again on Windows, and the MCP config you copy is correct for a source checkout instead of assuming a packaged install.

Smaller stuff. Renaming the assistant carries through everywhere now, a few spots still said Jarvis after a rename. The Models tab stopped warning about VRAM when a model was already loaded, since loading a new one frees the old one first, and the context number it shows matches what the model is actually running at.

Windows installer only. Full includes the GPU engine, Lite is for people already running Ollama or LM Studio. Linux runs from source. Not code signed yet, so Windows shows the blue unknown-publisher box, click More info then Run anyway.

Updating never touches your chats, memory or models.
