v0.7.5 is the release where Hearth learned to use your whole desktop and stopped tripping over itself. It can see and act on the apps you actually use, keep its own replies from spiraling, and it clears a long run of rough edges around speed, voice, models, and setup.

If you are on v0.7.2, v0.7.3 or v0.7.4, Hearth patches itself, no reinstall. On v0.7.0 or v0.7.1 you need the installer one more time, then you are on the small updates for good. Updating never touches your chats, memory, or models.

## Watch it work your desktop

When Hearth drives your mouse and keyboard, the app it is controlling has focus, not Hearth, so before this you just saw the cursor move on its own. Now a small strip sits at the top of the screen while it works and tells you what it is doing, reading the screen, clicking Sign in, typing a reply. It never takes focus and never intercepts a click, so it cannot get in the way of what it is doing underneath.

It can also see inside the apps you actually use. Chrome, Edge, Slack, Discord, VS Code, and a Gmail tab all keep their real buttons and fields hidden until something asks, so Hearth used to find nothing but the window frame. It asks first now, then names and clicks the real control instead of guessing at pixels. Switching windows is reliable too, through the same path the taskbar uses rather than a flaky Alt Tab, and after it types into a field it checks the text actually landed.

## No more talking in circles

Thinking models had a habit of getting stuck repeating the same line until they filled the whole context and gave you nothing. That is fixed. If you want to shape how the model picks its words, temperature, top-p, top-k, min-p, and repeat penalty now live as sliders in the model's load panel, starting from that model's own defaults, so you set them once per model.

## Faster, and honest about the numbers

Hearth opens without the five second wait it used to spend probing a server that was not running yet. Long conversations stay fast instead of slowing down as they grow, because the clock in the prompt no longer throws away the model's cache every minute. While a reply streams you see the real tokens per second, and hovering a finished reply shows that turn's prompt speed, generation speed, and total time, read straight from the engine. The context meter reflects what the model actually saw rather than a guess that read low and then jumped.

## Steer it, and never lose a long run

Type while Hearth is working and your message folds into the job it is already doing, instead of being dropped or forced to wait. A long task is saved as it goes now, so stopping partway or closing the app mid-run keeps everything it already did, rather than the whole turn vanishing. When a chat grows too large to summarize any further, Hearth says so once and keeps going by trimming the oldest turns instead of spinning on it.

## Voice that stays out of its own way

If you had no microphone, Hearth could grab a system audio device and start transcribing your speakers, waking on stray audio and looping on its own voice. It refuses those devices outright now and only ever opens a real mic, or turns voice off with a clear message if there is not one. On speakers, a half-duplex switch in Settings holds the mic closed while it speaks, for anyone who still hears an echo. And if your mic disconnects mid-conversation, voice stops cleanly and tells you, instead of spinning on errors.

The mic picker is cleaner, too. It lists your real devices by their full names instead of the raw driver entries, a Refresh button picks up a headset you plug in after opening Hearth, and a Test button gives you a live meter so you can watch the bar move as you talk and know the right mic is set before you start.

## Models and the engine

Big mixture-of-experts models like Qwen3 A3B run on a modest card now. Hearth keeps their weights in memory and runs the attention layers on the GPU with the experts in system RAM, so a model far larger than your VRAM still works. The built-in GPU engine installs cleanly, retrying the file locks that antivirus causes and checking the download is a real archive before opening it. You can manage it all without leaving Hearth, showing any model or engine build in your file explorer and deleting the ones you are done with to free disk. Changing a model's context size while it is loaded actually reloads it, the top bar shows what is really running, and switching to a cloud brain frees the local model's VRAM. Your models can also live on a different drive now, so a full C is not the only option. Point Settings at a folder with room and your chats and memory stay where they are while the models sit elsewhere.

## Files, vision, and email

Drop in a document, spreadsheet, PDF, or code file and its text rides straight into your message, so even a small local model uses it without a nudge. When Hearth writes or edits a file, the tool card shows a green and red diff of what changed, in both the app and the CLI. When the loaded model has no vision, asking it to look at an image gets a plain "I cannot see this" and how to fix it, rather than a made-up description. And you can now connect an inbox from Settings, Connectors, with an app password that stays on your machine, so Hearth reads your mail and sends replies when you ask.

## Smaller fixes

A rate-limited web search backs off instead of hammering the same dead query. Clicking Hearth in the tray reliably surfaces the window. A reply with code no longer flickers while it streams. On a bigger card the built-in server runs more requests at once on its own. Hearth ignores requests to its local server from other sites on your network, so a page you have open cannot poke at it. And a couple of PowerShell quirks that sank otherwise-fine commands get fixed up automatically.

---

Windows installer only. Full includes the GPU engine, Lite is for people already running Ollama or LM Studio. Linux runs from source. Not code signed yet, so Windows shows the blue unknown-publisher box, click More info then Run anyway.
