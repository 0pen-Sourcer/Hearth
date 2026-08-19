This release is about speed, honesty, and a stack of things that were broken. Hearth starts faster, answers faster on local models, shows you what it is actually doing while it works, and the rough edges around installing the GPU engine, connecting a cloud model, and voice mode are cleared up.

If you are on v0.7.2, v0.7.3 or v0.7.4, Hearth updates itself with a small patch, no reinstall. On v0.7.0 or v0.7.1 you need the installer one more time, and after that you are on the small updates for good. Updating never touches your chats, memory or models.

**Faster to open**

On some machines Hearth would sit on a "getting ready" screen for five or six seconds every time it opened. That was it waiting on a model-server check that stalls when nothing is running yet. It gives up on a dead server in about half a second now, so the app is ready as soon as you open it, and it stays quicker while you use it because that same check runs on a loop the whole time.

**Faster to answer on local models**

A running clock was baked into the prompt, and because it changed every minute it kept throwing away the model's cache and forcing a full re-read of your whole conversation on every message. The time still reaches the model, it just rides along without breaking the cache now, so a long back and forth stays fast instead of getting slower as it grows.

**You can see how fast it is running**

While a reply streams you see the live tokens-per-second, and hovering a finished reply shows that turn's real prompt speed, generation speed, and total time, read straight from the engine rather than guessed. The context meter is honest too. It now reflects the tokens the model actually saw instead of a rough estimate that read low and then jumped. If you would rather not see any of it, there is a switch in Settings to hide the whole thing.

**You can redirect it mid-task**

Type while Hearth is still working and your message folds into the job it is already doing, the way you would nudge someone mid-sentence, instead of being dropped or forced to wait for the turn to finish. Long chats also stay alive longer. When a conversation grows too big to summarise any further, Hearth says so once and keeps going by trimming the oldest turns rather than spinning on it.

**The built-in GPU engine installs cleanly**

It used to fail partway with a "file in use" or "bad zip" error and then get stuck failing on the same broken download forever. It retries the file lock that Windows and antivirus cause, checks the download is a real archive before opening it, and throws a bad one away so the next try starts clean. The size it shows before you download now matches what it really pulls.

**Cloud models are smoother**

Pointing Hearth at something like Groq now lists the provider's real models instead of coming up empty after a restart, which was Hearth losing your saved API key every relaunch. Switching to a cloud brain frees the local model's VRAM, and the top bar stops showing the old local model as if it were still active.

**Voice mode is steadier**

The wake word takes you into voice mode even when a window is already open, closing and reopening voice no longer gets stuck on "warming up the mic," and a fresh session starts on a clean mic instead of dumping every stray thing it heard while the window was closed. It never reads a tool call's raw text out loud anymore, and a spoken question gets a short spoken answer instead of a whole screen read at you.

**Voice no longer talks to itself, and never listens to your speakers**

If you had no microphone connected, Hearth could fall back to a "Stereo Mix" style device that captures whatever your speakers are playing, so it transcribed videos and music, woke on a stray "hey Jarvis" from a clip, and could loop on its own voice. Hearth now refuses those system-audio devices outright and only ever opens a real microphone, or turns voice off with a clear message if there isn't one. You can still interrupt it by voice as normal; if you run on speakers instead of a headset and it ever hears itself, there is a half-duplex switch in Settings that holds the mic closed while it speaks. And if your microphone disconnects mid-conversation, voice stops cleanly and tells you, instead of spinning on errors.

**See exactly what a file edit changed**

When Hearth writes or edits a file, the tool card now shows a green and red diff of what actually changed instead of a wall of raw text, in both the app and the CLI. A large write is trimmed so it never floods the view. There is a switch in Settings to turn diffs off if you prefer the plain result. And a model or model folder you dropped into a subfolder of your models directory now shows up in the list, not just files sitting flat at the top.

**Drop a file in and it just gets read**

Attach a document, spreadsheet, PDF, or code file and its text now rides straight into your message, so even a small local model uses it without you having to nudge it to open the file. Two files with the same name no longer overwrite each other. Images still go to a vision model to actually be seen.

**Honest about what it can see**

When the loaded model has no vision, asking Hearth to look at an image now gets a plain "I cannot see this" and how to fix it, instead of the model inventing a description of a blank. On the built-in server, dropping a model's matching projector file beside it turns real image understanding on.

**Looks after its own memory**

Hearth merges duplicate notes and drops stale ones on a quiet background schedule, instead of only tidying up when it happens to save something. Asking what you were doing recently or yesterday brings back your recent chats now, not the oldest thing that matched the words.

**Models tell the truth about their state**

Changing the context size on a model that is already loaded restarts it on the new size, where before it would claim it was ready and quietly keep the old one. The top bar no longer shows a model that has already been swapped out for a cloud brain, so what you see loaded is what is really loaded. Hearth also won't boot a model at a context so small its own prompt can't fit, which used to fail on the first message.

**Big mixture-of-experts models on a small card**

A mixture-of-experts model like Qwen3 A3B is huge on disk but only runs a slice of itself per word, so a modest GPU can handle it if the experts sit in system RAM. Hearth's built-in server now recognises these models and keeps their weights in memory instead of reading them off your drive mid-word, which is what otherwise thrashes the disk and crawls. The attention layers run on the GPU and the experts stay in RAM. Advanced flags let you tune the split.

**Commands and bridges**

Two PowerShell quirks used to sink otherwise-fine commands, a program path in quotes that needs a special prefix, and a plain "python" that is not on the system path. Both get fixed up automatically now. And if you message Hearth on Discord, Telegram or WhatsApp while no model is running, it replies that it is not up yet and how to wake it, instead of a raw error dump.

**Smaller edges**

When a web search gets rate-limited and keeps coming back empty, Hearth now notices and stops hammering it, telling the model to answer from what it has or try another way instead of retrying the same dead search over and over. Clicking Hearth while it is tucked in the system tray now opens the window instead of doing nothing. The desktop window stopped occasionally opening as a plain browser tab on machines where it should have come up as its own window. The show-in-folder button only opens a real model file, and Hearth now ignores requests to its local server that come from other sites on your network, so a page you happen to have open cannot reach in and poke at it.

---

Windows installer only. Full includes the GPU engine, Lite is for people already running Ollama or LM Studio. Linux runs from source. Not code signed yet, so Windows shows the blue unknown-publisher box, click More info then Run anyway.
