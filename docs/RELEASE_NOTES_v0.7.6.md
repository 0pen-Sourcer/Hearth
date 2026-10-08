v0.7.6 brings a new look, a Team room where you can watch helpers work and talk to JARVIS, Claude Code, Codex or Cursor as JARVIS's brain if you already pay for one, adapters for twenty-three coding agents JARVIS can hand work to, a new first-run setup, support for AMD and Intel graphics cards, and one switch for how much Hearth does without asking. Underneath, it is a large fix release: most of it is about Hearth doing what it says it did, and telling you plainly when it can't, across security, voice, memory, commands, the phone bridges, desktop control, models and the terminal.

If you are on v0.7.2 or newer, Hearth patches itself, no reinstall. On v0.7.0 or v0.7.1 you need the installer one more time, then you are on the small updates for good. Updating never touches your chats, memory, or models.

## New look

Settings has an Appearance page with Classic, the violet look with the H logo and still the default, alongside new Light, Dark and System themes in a warmer palette with six accent colours and a mark that is an actual hearth. The tray icon and the window's own icon follow whichever you pick, and picking one repaints the window from where you clicked. Everything about how the chat looks lives on that page now, from generation stats to pop-ups, so Behavior is about what JARVIS does.

Simple, the default, keeps the machinery out of the way. A run of tool calls folds into one line such as "Read 3 files, searched the web" that you can open, the status bar stays quiet, and engine settings, MCP servers and the per-tool permission table stay out of sight. Advanced shows all of it. Chats can be pinned to the top of the sidebar, and a pin survives a reinstall.

## A first run that sets Hearth up for you

Setup asks a few things and acts on them. Your name, what you do and how you like to be spoken to now reach JARVIS on every message. Before, the window saved them to settings nothing read, so JARVIS never knew them. It asks whether you will hear it on headphones or speakers, which decides whether you can talk over it, how much it may do on its own, and which look and how much detail you want, applied as you pick them. It ends on things worth trying instead of an empty box. The terminal asks the same questions, and /me asks them again whenever you like.

## Team

When JARVIS splits a job across helpers, the Team view shows each one: what it is doing right now, when it finishes, why it didn't, and when it needs your OK for something. Every helper gets a first name, and each one's full log, step by step, is one click away. The terminal has the same list under /team.

The room view shows it as a house by the fire. Helpers work at desks, read at the shelves or raise a hand when they need you, and JARVIS moves with the work, to a shelf while it reads and to a free desk while it writes. Click anyone and they turn and tell you where they are with their task. You can talk to JARVIS from a box under the room and read its answer over its head without going back to the chat, and if you ask it to brew itself a coffee, it walks over to the counter. While it thinks it paces, when it saves a memory it pins a note to the board, and it answers where it stands. Its bubble says what it is doing in plain words, and a command waiting for your yes can be approved right there in the room.

You can tell a working helper something part-way through. Click it, type in its panel, and it reads your message before its next step. JARVIS can do the same on its own, and so can Claude Code and Codex helpers; another agent's program that runs its task to the end can't take one, and the panel says so.

Helpers talk to each other. Each one on a team is told who else is on it, and one can ask another a question and wait for the answer, or send JARVIS word part-way instead of only at the end. In the room the one with something to say walks over and says it, the other answers, and JARVIS thanks a helper that reports in. A finished helper takes its break in one place and heads home after a while, instead of wandering the room.

Starting a helper yourself asks what it should do first, then who does it: one of Hearth's own, named for the job (search the web, work on code, search my files, sum up a file), or an agent you have installed.

A room holds eight desks. Past that the next room opens, a winter cabin, a library and an ocean room in turn, each in its own style, and a small wooden sign over the picture switches between them with a pixel wipe. Clicking someone's card takes you to their room. Appearance picks which room JARVIS calls home and offers the accent that suits it. A working helper can be stopped from its panel or with /team stop, a finished or failed one heads out of the door after a while, and walking side-on now has a real stride. One sentence can start a mixed team, with some helpers on this PC's model, some on a provider's model named in plain words like "haiku", and some handed to Codex, OpenCode or another agent you have installed.

Two helpers can't trip over each other. An agent program sent to work in a git repository works on its own copy of it, started from your folder as it is, unsaved changes included, and nothing it does reaches your folder until you say so. Its panel lists what it changed with the diff, and Bring in, Discard or Undo; the terminal has the same under /team changes, bring, discard and undo. Hearth's own helpers write directly, but a file one of them is working on is theirs until they finish, and what they changed can be undone. You can also give one task to two agents, say Codex and Claude Code, each on its own copy, have a check like your tests run on both, and pick the one to keep.

A team starts from one plan that says who does what, and when JARVIS changes the plan every member still working hears about it before its next step. A helper can be kept to one folder for a task, so it reads what it needs but changes files only there, or given fewer tools than its role has.

## Claude Code, Codex or Cursor as JARVIS's brain

If you already pay for Claude, JARVIS can think with it. Pick Claude Code from the model menu at the top of the window or in Settings, Chat brain, or type /brain claude-code in the terminal, and every message runs through Claude Code on your own install and plan, with no API key. It keeps all of Hearth: its tools reach your desktop, apps, memory and voice through Hearth, it stays JARVIS with your name and preferences, and each chat is one continuing Claude session, so it remembers what you said earlier. When it wants to do something risky, the same approval switch and prompt you already use ask you first, out loud too in voice mode. Its use counts toward your Claude plan's usage limits, the same as Claude Code itself; an API key is billed separately.

Codex works the same way on a ChatGPT plan. Pick it from the same menu, or type /brain codex. The Codex desktop app is enough, since it carries the command-line part Hearth runs. Its replies stream in as they are written, and something you type while it works reaches it part-way. Hearth keeps Codex under the same approval switch as everything else: every command it wants to run and every file it wants to change comes to Hearth first, which lets it through or asks you, exactly as the switch says, and a file change shows as a diff. On models that have it, the Think menu has Codex's Fast switch, which answers quicker and uses more of your plan. Its usage counts toward your plan's Codex limit, and when that runs out Hearth tells you when it resets.

Claude Code's fast mode has a switch in the Think menu too; if your plan or that model doesn't have it, Claude Code says so after the reply and Hearth stops offering it there.

Cursor works too, on a Cursor plan, through its command-line agent (the editor alone is not enough). Cursor's own shell is turned off for these runs and, while the switch is on Ask, so are its direct file writes, so commands and changes go through Hearth's tools and wait for your yes.

Other agents can be the brain too, once installed, if they speak the Agent Client Protocol: OpenCode, Copilot CLI, Goose, Grok Build and Gemini CLI, and Pi, Amp and Antigravity through their adapter programs. Their commands and file changes come to Hearth's approval switch the same way, and a chat carries on in the same session. OpenCode has been run this way, including its free models; the others follow the protocol and haven't been run here yet.

The model menu opens each agent into its own models, read from the agent itself, so they are the ones your install and plan really have: Opus, Sonnet and Haiku for Claude Code, the GPT models on your ChatGPT plan for Codex, and Cursor's list with a search box. The Think button sets how hard that model reasons, with the levels that model takes. The context shown is the agent model's own. With an agent as the brain no local model is needed. Helpers JARVIS starts use your local model when one is loaded and the same agent when none is.

## Other coding agents

JARVIS can hand a task to another coding agent you already have installed and bring back its answer. Say "have Claude Code read the README and summarize it" and it does, in the folder you name, on your own install and account. Hearth has adapters for twenty-three of them. Claude Code is tested end to end. Codex, Cursor and OpenCode have been run from Hearth and reached their own service. Antigravity, Gemini CLI, Grok, Kimi Code, Qwen Code, Crush, Copilot CLI, Goose, Aider, Droid, Amp, Pi, Cline, Continue, Amazon Q, Kiro, Kilo Code, Augment and OpenHands follow their own documented one-shot modes and have not been run here yet, so treat them as expected rather than verified. Gemini CLI no longer signs in with a personal Google account; Google points those users to Antigravity.

Installing or updating one of these agents shows in the Downloads list with the packages it has fetched, how long it has run and a Cancel button, and a finished row clears itself a few seconds later.

A helper on one of these agents can be given one of its own models, and otherwise uses the one you picked for that agent as the brain. They follow the same approval switch as Hearth's own tools, and an agent working longer than a few minutes carries on in the background and reports when it is done. In the chat and the Team room each agent carries a small badge in its maker's colour, so you can see at a glance who did what.

## Approval modes

One switch in the chat bar decides what runs without asking, and Shift+Tab cycles it. Ask asks before anything that acts on your machine. Auto-edit lets file edits in your workspace through and asks about the rest. Auto runs routine work without asking. Deleting, moving or stopping things, sending or uploading anything, pushing, publishing, installing and changing security settings still ask. The terminal has the same modes under /mode, and helpers and other agents follow them too. The old all-or-nothing auto-approve switch now works as Auto, so it no longer lets a destructive command through unasked.

## Security

This release closes several security problems in earlier versions, among them ways a web page or a crafted file could reach Hearth's local server or get a risky action past the approval prompt, and ways a write or a read could get outside the folders it is confined to. Update as soon as you can; Hearth patches itself on v0.7.2 and newer. A detailed advisory follows once most installs have updated.

Agents in a team follow your permissions too, and run a risky tool only if you have already said to always allow it. Tools from MCP servers ask before they run and no longer receive your cloud key or mail password. The WhatsApp bridge's own-chat option now answers only your own chat. The old full-screen terminal interface is gone.

## Voice

Hearth speaks and listens in eight languages, not one. The voice bank has always installed 54 voices covering English, Hindi, Spanish, French, Italian, Portuguese, Japanese and Mandarin, and only the English ones were reachable. They are all in the picker now, each one reading its own language properly rather than being handed English sounds. Transcription has the multilingual Whisper sizes alongside the English-only ones, with a spoken-language setting that detects each utterance on its own unless you name one. Speech in a script that ends its sentences differently starts playing as soon as the first sentence is ready, the same as English always did.

Voice mode turns on reliably. Turning it off used to leave the microphone held and a speech model in memory, around 800 MB each time, so leaving voice mode to change a setting and coming straight back could fail to start at all, or sit on warming up forever. Every session now ends cleanly and a new one starts on its own.

The microphone you pick is the one that gets used. It was remembered by a number that Windows changes whenever a headset connects or a USB device comes and goes, so it quietly pointed at something else. It is remembered by name now. When the Windows default input is Stereo Mix, which is really your speakers, Hearth tells you and lists your microphones instead of guessing, where it used to pick a virtual device that records silence and then look like voice was broken.

The wake word hears whole phrases. It used to drop the quiet parts of speech and keep only the end of a long sentence, which is exactly where the name isn't. On the same recorded phrases it now wakes on all fifteen where it woke on nine, including a full command like "Jarvis, open Spotify and play the new album". While voice mode is on, the wake listener lets go of the microphone, so a Bluetooth headset that only allows one app at a time works in voice mode.

On speakers you can still cut in. Speakers mode keeps Hearth from hearing itself by ignoring what the mic picks up while it talks, which also meant you couldn't stop it. Now anything that starts with its name gets through, so "Jarvis, stop" or "Jarvis, open Spotify instead" cuts the speech and goes through as your message. Its own voice coming back through the speakers didn't set it off once in testing.

When speech fails, Hearth says so. Synthesis and playback run behind a queue, which meant a failure after the request had been accepted was silent in every direction, with nothing written anywhere to explain it. Now it names what went wrong, and the preview button tells you rather than just going quiet. A transcription that fails is no longer indistinguishable from not having said anything. Saving a setting no longer unloads the voice, so the next reply is not held up reloading it.

Voice starts on an updated install. The update now carries the maths library the speech stack loads at startup, along with the rest of its dependencies, so a patched install behaves the same as a fresh one. The microphone test runs in its own process, so a headset you plug in after opening Hearth is picked up without a restart.

## AMD and Intel graphics cards

Hearth used to read your graphics card through NVIDIA's own tool, so on an AMD or Intel card the status bar showed nothing in use and model suggestions knew nothing about your card. It now reads every vendor's card the way Task Manager does, load and memory included. The built-in engine follows your card too. NVIDIA gets the CUDA build as before, and AMD and Intel cards get the Vulkan build, which runs the model on the card instead of the processor.

## Faster on local models

The instructions Hearth sends with every message are about a fifth of their old size and the tool descriptions about half, so on a 32K local model the fixed part of every request went from nearly two thirds of the context to about a third. Replies start sooner and a long chat lasts longer before it has to be compacted. On the standalone engine Hearth now uses the model's own tool-calling format instead of reading calls out of plain text.

When a model asks for several things at once, all of them run. Mistral and Command-R models had every call after the first dropped, and a reply with twenty calls where one had a small formatting slip ran nineteen and lost the last without a word. Tools Hearth keeps in reserve until they are needed were partly invisible to the model, so it now always sees their names and can pull any of them in.

## Commands

Commands run in bash as well as PowerShell and cmd, wherever Git Bash or WSL is installed, and the model is told which shells your machine has so it writes the right syntax rather than hitting a wall on "&&". File names in Hindi, Chinese or with accents come back as they are instead of as question marks, and a Python script printing them no longer crashes. A command that fails reports that it failed, where an error followed by anything that worked used to read as success. The same applies to commands running in the background.

## Memory

A memory with a title in Hindi, Chinese or Japanese used to overwrite the whole memory index, and the index could drift until it listed only part of what Hearth remembers. The index is now rebuilt from the memories themselves and repairs itself whenever the two disagree, so everything you told it is in view again.

When something changes, Hearth records the change. Telling it you moved to Linux used to leave it believing you were on Windows, because a fact it already knew was never updated, and it now replaces the old fact and keeps the previous version in the archive. It also no longer archives the wrong fact when a new one replaces an old one, or merges facts that only differ by a number, like two different exam scores.

Recall reads each memory file once and reuses it, so a lookup stays fast as your memory grows. Saving a fact returns immediately, with the archiving of older facts moved into the background and running on its own schedule.

Passive memory works on reasoning models. Hearth notices durable facts on its own without you saying "remember that", and it now has the room to finish writing them and can read the reply even when a model buries it in its reasoning. It also never competes with you for the model. If you send a message while it is working, your message goes first and the facts are picked up on the next pass.

## Reminders

Reminders are set for the time you meant. Hearth works out the exact time from what you said, and the reminder reads back the day and the time in plain words, so a slip shows up at once rather than at midnight. With the window and the terminal both open a reminder fires once instead of twice, and a reminder set while another one is firing is no longer lost.

A reminder can watch a file or folder instead of the clock, like "when a PDF lands in Downloads, summarize it". It waits until the change holds still, so a download in progress isn't reported half done.

## Phone and teams

A bridge started from the window stopped answering after a while and still showed as running, and now it keeps going. Its output goes to a log the window can show, for Telegram and WhatsApp as well as Discord. WhatsApp sends files back the way Telegram and Discord do, and all three send files whose paths have spaces in them. Announcements from the developer reach you now, where none ever could before.

A phone bridge now stops when the Hearth that started it closes, so restarting can't leave two of them answering every message. When a team is started from the terminal, each agent gets a live pane showing what it does in words, what its tools returned, and an agent that failed says so rather than showing as done; started from the window, the Team room shows them instead. If a team member could not start, Hearth is told which one and why, instead of believing that part of the job is being done.

## Desktop control

Hearth can lay your windows out in one go. Ask for the browser on the left and the editor on the right, or chat top-right and the game below it, and it places them exactly, with no screenshot or clicking involved.

It can also number what it sees. Taking a marked capture draws a number on every control it can identify, and it then clicks one by number instead of estimating a pixel, which is where visual clicking usually goes wrong.

For web pages it can drive the Chrome you already use, with your profile, logins and tabs, once Chrome is started with its debugging port. Attached, it reads the page itself, so filling a box is direct rather than a click followed by blind typing, and sites see an ordinary browser session.

Bringing a window to the front is verified rather than assumed. Windows can accept the request and quietly not move focus, and Hearth used to carry on regardless. It now confirms the window really is in front, and says so plainly when it cannot, rather than typing into whatever was there.

Every keystroke Hearth sends now checks the target window first. If focus moved since it clicked, or if the front window is Hearth itself, the typing is refused and Hearth re-targets rather than putting text somewhere you did not ask for. The same check means Hearth stops as soon as you take the mouse or keyboard back mid task.

Clicking and typing can run as a single action, so nothing can slip in between the two. Hearth can also write into an app without pulling it to the front. Point it at a window by name, including one in the background, and it reads that window's real controls and fills the field directly instead of taking over your screen.

Declining an action and telling Hearth what to do instead now makes it do that thing, rather than only acknowledging it.

## Cloud models

Reasoning is visible on OpenRouter and other providers that stream their thinking in a different field than local servers do. You can set how hard a model thinks, low, medium or high, on models that support it. A cloud model reports its real context window rather than a fixed guess, so a large context model shows what it actually has. Images reach cloud models that can see them. The model list is sorted and searchable instead of hundreds of unordered lines, and a cloud brain reads as online rather than loaded, since nothing is held in your VRAM.

A model on this PC can stay loaded under a cloud brain, from one switch in the model menu, and then the helpers think with it for free while the cloud model talks to you; the sticker shows it beside the brain. Settings shows every brain as a card, grouped by where it runs, with its logo and whether it is in use or has a key saved, and the model menu has a way to a provider that has no key yet. Switching to a cloud brain no longer waits for the local model to leave the graphics card, every switch ends with the same message, and when a provider turns a key or a model down, Hearth repeats what the provider said.

## Models

Loading a model shows real progress on the sticker instead of a blinking dot, and it names the model that is actually loading rather than the one that was there before. That progress is now measured rather than guessed at from the engine's log. The engine prints everything it knows about a model before it reads a single byte of it, so the bar used to jump most of the way up in the first second or two and then sit still for the whole real wait. It moves steadily for the length of the load instead, all the way through, and a model loading on its own when you open Hearth gets the same bar as one you picked by hand.

A download that was interrupted in an awkward place used to stay broken forever, failing the same way on every retry with nothing you could do about it from inside the app. It starts over cleanly now, and pausing a download really pauses it, where resuming used to write a corrupted file. Downloads keep going when you close the window to the tray, and one that was cut off by a restart or a crash picks up where it stopped the next time Hearth opens. Every model links to its page on Hugging Face, so you can check the licence and what it was trained for without leaving the app, and a model Hearth downloaded remembers exactly which repo it came from. With no server running, the model list shows what is on your disk rather than saying there is nothing.

On an RTX 50-series card Hearth no longer starts its bundled runtime, which has no kernels for those GPUs and would return empty replies on longer prompts. It asks you to install the standalone engine instead, which is one click in Models.

## Reading a run

A run shows what it reached outside Hearth. The sites a search or a page read touched appear as their own icons, fetched from each site, another agent appears as its badge, and while it works the line says what it is doing right now, "Reading reddit.com" rather than a tool name. A command that runs for a while counts up on its own card instead of popping up a warning, and the context ring always reads as room left before the chat gets summarised.

File edits render as a diff with line numbers and a change count, in tight rows. Tool cards show a one line summary of what was passed rather than raw escaped JSON, and the terminal does the same with a glyph per kind of action and a short peek at long output. Steering Hearth mid answer separates the redirected part from what came before. Announcements from the developer stay as a card until you dismiss them.

The window is responsive, so a narrow or half snapped window moves the sidebar into a drawer. Chat titles come from the first real exchange, so a chat that opens with a greeting still gets a title about the actual subject. Optional sound cues mark a finished turn, an error, or a permission request, all off by default, and user messages can sit in chat bubbles if you prefer, also off by default.

## Logs

The Logs page reads like the rest of the app now. Every tool JARVIS used is one row with what came back, in words, grouped by day, with the errors a click away and a search box. The model server's page sums up its recent requests, how much it read and wrote and how fast, above the raw lines, and the phone page folds a bridge's reconnects into one line.

## The terminal

The terminal and the window share one set of chats. A chat in the terminal is listed in the window, marked as from the terminal, and can be carried on there; whatever is added in the window reaches the terminal before its next message; and /chats and /chat open any chat in the terminal, while /clear starts a new one. /team shows each helper's steps in words.

The terminal no longer crashes on the first tool call of a session, and Ctrl+Z ends it instead of spinning forever. --help and --version answer instead of starting the whole app. /brain lmstudio and /brain builtin work, and switching to a local brain uses whatever model is actually loaded rather than naming one you may not have. A setting saved while on a cloud brain no longer switches to a random model from the provider's catalog. The terminal knows whether the loaded model can see, a prompt that overflows the context is trimmed and retried rather than resent until the turn dies, and /history shows messages that carry an image.

## Elsewhere

Searching past chats finds what you asked about. Most questions used to report no match even when there was one. The window opens fast with a long chat history, because it reads a chat only when you open it instead of loading every one in full to draw the sidebar.

Repair checks every part of the install and puts back whatever is missing, Hearth's code, its skills and art, the voice files and the model engine, and says plainly which parts need a full reinstall. Installing or repairing the engine had started failing for everyone because the engine's project marked an unrelated release as its latest, and Hearth now picks the newest real build. Updating is safe to interrupt. If the power goes or you kill Hearth while it is replacing files, the next start puts it back the way it was rather than leaving you with an install that will not open. Your chats survive being killed mid-save too, and a save that got cut off no longer leaves a ghost copy of the chat sitting in the sidebar.

Asking Hearth for a capability it does not have gets a straight answer instead of a handful of unrelated tools. And it can tell you your charge level, whether you are plugged in and roughly how long you have left.

Messages that read like an error log now say what happened in plain words, such as "Couldn't delete it" or "The model didn't load" with the reason. PrismML's ternary models, like Bonsai in its PQ2_0 file, need PrismML's own build of the engine. Hearth reads which kind a model file is before loading it, offers that engine in Models with one click, and runs those models on it while every other model stays on the usual engine.

A command card shows the command that ran above its output, and the approval prompt can show the whole command before you allow it rather than cutting it off. Repairing an install reports in the same place as other downloads, with progress and a cancel. Email setup moved to Connectors where it belongs, and a voice install that fails part way can be retried instead of dead-ending. Settings now links to the project, so the source, releases and issue tracker are one click away.

Reads can be confined to your workspace from Settings without setting an environment variable. Hearth's environment variables are named HEARTH_ now, since the agent can have any name, and the old JARVIS_ ones keep working. Windows shows the version you actually have after an update. People running from a git clone are told when there is something worth pulling. When no model is loaded, the app and the terminal both say so in red instead of showing a placeholder that looked like a model.

---

Windows installer only. Full includes the GPU engine, Lite is for people already running Ollama or LM Studio. Linux runs from source, though desktop control is Windows only for now. Not code signed yet, so Windows shows the blue unknown-publisher box, click More info then Run anyway. Every installer is checked before release: it starts, its voice and model engine load, and Windows Defender scans it clean.
