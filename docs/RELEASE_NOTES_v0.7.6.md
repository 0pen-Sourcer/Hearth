Hearth is now Inglen. An ingle is the fire in a hearth, and the new name is one we can stand behind as the project grows. Only the name you read changes. Updating from Hearth keeps your settings, memory, chats and models where they are, and the folders, the program files and the settings names stay as they were. A new install calls the assistant Inglen; an older install keeps the name it had, JARVIS, and its ~/Jarvis folder. Either can be renamed in Settings, Behavior.

v0.7.6 brings a new look, a Team room where you can watch helpers work and talk to Inglen, Claude Code, Codex or Cursor as the brain if you already pay for one, adapters for twenty-three coding agents Inglen can hand work to, a memory those agents can share, pictures and video made on your own graphics card, desktop control you can watch happen, phone chats rebuilt with pairing codes, Inglen Plus with a phone app you can call your assistant from, a voice mode you can talk over on speakers, a new first-run setup, support for AMD and Intel graphics cards, and one switch for how much Inglen does without asking. Underneath, it is a large fix release. Most of it is about Inglen doing what it says it did, and telling you plainly when it can't, across security, voice, memory, commands, the phone bridges, desktop control, models and the terminal.

If you are on v0.7.2 or newer, Inglen patches itself, no reinstall. On v0.7.0 or v0.7.1 you need the installer one more time, then you are on the small updates for good. Updating never touches your chats, memory, or models.

## New look

Settings has an Appearance page with Classic, the original violet look, alongside new Light, Dark and System themes in a warmer palette with six accent colours. A new install opens in Dark with Rose, and an install set up before keeps Classic. Every look but Classic carries the Nook, a rounded corner bracket with a dot in your accent, and Classic's mark is an i with a small flame for its dot. The tray icon, the window's own icon, the tab and desktop notices follow whichever you pick, and picking one repaints the window from where you clicked. Everything about how the chat looks lives on that page now, from generation stats to pop-ups, so Behavior is about what Inglen does.

On an empty chat the mark tells you where the model stands. With none loaded it is an empty ring, while one loads a stroke runs along the bracket and then follows the load's percentage, and when the model is ready the dot fills and settles.

Detail in chats, Simple or Full, decides how much of the work you see. Simple, the default, folds a run of tool calls into one line such as "Read 3 files, searched the web" that you can open, and keeps the status bar quiet. Full shows each tool call as its own card and every counter. Every Settings page shows in both, and the rows only an expert needs, like reviewing every command, reasoning effort and the per-tool permission table, sit in a closed Advanced section at the foot of their page. Chats can be pinned to the top of the sidebar, and a pin survives a reinstall.

The tray menu matches the look, dark or light, and opens your pinned and recent chats straight in the window, next to New chat, Voice mode and listening for its name. The terminal's colours are shades of the same look, its accent included, while errors and diff lines stay red and green.

The MCP servers page reads plainly. It has the servers Inglen uses with each one's service icon and a status dot, and how to use Inglen from other apps. A server that fails says why in one sentence, such as "Couldn't start: node isn't installed", with the raw error a click away.

## A first run that sets Inglen up for you

Setup is eight short steps, and it acts on what you tell it. Your name, what you do and how you like to be spoken to now reach Inglen on every message. Before, the window saved them to settings nothing read, so Inglen never knew them. It asks which language you speak, and a language with its own voices brings them up to pick from. It asks whether you will hear it on headphones or speakers, how much it may do on its own, and which look and how much detail you want, applied as you pick them. Beside those choices a small chat plays one task out the way you set it up, so you can see what Ask or Auto means before you pick. With no model on your PC yet, it offers one that fits your graphics card, the most downloaded chat model its own makers publish, and shows what else your choices need, the engine and Inglen's voice and hearing, each with its size. What you keep ticked downloads while you finish. The last step brings over what ChatGPT or Claude knows about you, and Finish starts a six-stop tour of the window, each stop with something to try. You can take the tour again from Settings, About. The terminal asks the same questions, and /me asks them again whenever you like.

## Team

When Inglen splits a job across helpers, the Team view shows each one: what it is doing right now, when it finishes, why it didn't, and when it needs your OK for something. Every helper gets a first name, and each one's full log, step by step, is one click away. The terminal has the same list under /team.

The room view shows it as a house by the fire, and the fire burns. Helpers work at desks, read at the shelves or raise a hand when they need you, and Inglen moves with the work, to a shelf while it reads and to a free desk while it writes. Click anyone and they turn and tell you where they are with their task. You can talk to Inglen from a box under the room and read its answer over its head without going back to the chat, and if you ask it to brew itself a coffee, it walks over to the counter. While it thinks it paces, when it saves a memory it pins a note to the board, and it answers where it stands. Its bubble says what it is doing in plain words, and a command waiting for your yes can be approved right there in the room.

You can tell a working helper something part-way through. Click it, type in its panel, and it reads your message before its next step. Inglen can do the same on its own, and so can Claude Code and Codex helpers; another agent's program that runs its task to the end can't take one, and the panel says so. A message for a helper that has already finished starts it again as a follow-up, with what it was asked and what it reported.

Helpers talk to each other. Each one on a team is told who else is on it, and one can ask another a question and wait for the answer, or send Inglen word part-way instead of only at the end. In the room the one with something to say walks over and says it, the other answers, and Inglen thanks a helper that reports in. A finished helper takes its break in one place and heads home after a while, instead of wandering the room.

Starting a helper yourself asks what it should do first, then who does it: one of Inglen's own, named for the job (search the web, work on code, search my files, sum up a file, or anything else, for a helper that can do all of it and picks up whatever tools it needs), or an agent you have installed.

A room holds eight desks. Past that the next room opens, a winter cabin, a library and an ocean room in turn, each in its own style, and a small wooden sign over the picture switches between them with a pixel wipe. Clicking someone's card takes you to their room. Appearance picks which room Inglen calls home and offers the accent that suits it. A working helper can be stopped from its panel or with /team stop, a finished or failed one heads out of the door after a while, and walking side-on now has a real stride. One sentence can start a mixed team, with some helpers on this PC's model, some on a provider's model named in plain words like "haiku", and some handed to Codex, OpenCode or another agent you have installed.

Two helpers can't trip over each other. An agent program sent to work in a git repository works on its own copy of it, started from your folder as it is, unsaved changes included, and nothing it does reaches your folder until you say so. Its panel lists what it changed with the diff, and Bring in, Discard or Undo; the terminal has the same under /team changes, bring, discard and undo. Inglen's own helpers write directly, but a file one of them is working on is theirs until they finish, and what they changed can be undone. You can also give one task to two agents, say Codex and Claude Code, each on its own copy, have a check like your tests run on both, and pick the one to keep.

## Claude Code, Codex or Cursor as the brain

If you already pay for Claude, Inglen can think with it. Pick Claude Code from the model menu at the top of the window or in Settings, Chat brain, or type /brain claude-code in the terminal, and every message runs through Claude Code on your own install and plan, with no API key. It keeps all of Inglen: its tools reach your desktop, apps, memory and voice through Inglen, it stays your assistant, with your name and preferences, and each chat is one continuing Claude session, so it remembers what you said earlier. When it wants to do something risky, the same approval switch and prompt you already use ask you first, out loud too in voice mode. Its use counts toward your Claude plan's usage limits, the same as Claude Code itself; an API key is billed separately.

Codex works the same way on a ChatGPT plan. Pick it from the same menu, or type /brain codex. The Codex desktop app is enough, since it carries the command-line part Inglen runs. Its replies stream in as they are written, and something you type while it works reaches it part-way. Inglen keeps Codex under the same approval switch as everything else: every command it wants to run and every file it wants to change comes to Inglen first, which lets it through or asks you, exactly as the switch says, and a file change shows as a diff. On models that have it, the Think menu has Codex's Fast switch, which answers quicker and uses more of your plan. Its usage counts toward your plan's Codex limit, and when that runs out Inglen tells you when it resets.

Claude Code's fast mode has a switch in the Think menu too; if your plan or that model doesn't have it, Claude Code says so after the reply and Inglen stops offering it there.

Cursor works too, on a Cursor plan, through its command-line agent (the editor alone is not enough). Cursor's own shell is turned off for these runs and, while the switch is on Ask, so are its direct file writes, so commands and changes go through Inglen's tools and wait for your yes.

Other agents can be the brain too, once installed, if they speak the Agent Client Protocol: OpenCode, Copilot CLI, Goose, Grok Build and Gemini CLI, and Pi, Amp and Antigravity through their adapter programs. Their commands and file changes come to Inglen's approval switch the same way, and a chat carries on in the same session. OpenCode has been run this way, including its free models; the others follow the protocol and haven't been run here yet.

The model menu opens each agent into its own models, read from the agent itself, so they are the ones your install and plan really have: Opus, Sonnet and Haiku for Claude Code, the GPT models on your ChatGPT plan for Codex, and Cursor's list with a search box. The Think button sets how hard that model reasons, with the levels that model takes. The context shown is the agent model's own. With an agent as the brain no local model is needed. Helpers Inglen starts use your local model when one is loaded and the same agent when none is.

An agent that goes quiet doesn't leave the chat thinking forever. After a minute and a half with no word from it and none of its steps running, the window, the terminal and the phone say it may still be thinking and that Stop ends it; nothing is cut off on a timer, since a model can think silently for a long time. When the agent wrote an error to its own log instead of sending it, such as OpenCode's free limit running out, the turn ends with the agent's own words.

## Other coding agents

Inglen can hand a task to another coding agent you already have installed and bring back its answer. Say "have Claude Code read the README and summarize it" and it does, in the folder you name, on your own install and account. Inglen has adapters for twenty-three of them. Claude Code is tested end to end. Codex, Cursor and OpenCode have been run from Inglen and reached their own service. Antigravity, Gemini CLI, Grok, Kimi Code, Qwen Code, Crush, Copilot CLI, Goose, Aider, Droid, Amp, Pi, Cline, Continue, Amazon Q, Kiro, Kilo Code, Augment and OpenHands follow their own documented one-shot modes and have not been run here yet, so treat them as expected rather than verified. Gemini CLI no longer signs in with a personal Google account; Google points those users to Antigravity.

Inglen can also look in on the coding agents' own sessions on your PC. Ask what Claude Code or Codex got to in a session and it reads it; ask it to tell that session something and it sends the message and brings back the reply, asking you first in Ask mode.

Installing or updating one of these agents shows in the Downloads list with the packages it has fetched, how long it has run and a Cancel button, and a finished row clears itself a few seconds later.

A helper on one of these agents can be given one of its own models, and otherwise uses the one you picked for that agent as the brain. They follow the same approval switch as Inglen's own tools. Handing a task over returns at once, so Inglen stays free to talk, and the agent's answer arrives as a report when it is done. In the chat and the Team room each agent carries a small badge in its maker's colour, so you can see at a glance who did what.

## Approval modes

One switch in the chat bar decides what runs without asking, and Shift+Tab cycles it. Ask asks before anything that acts on your machine. Auto-edit lets file edits in your workspace through and asks about the rest. Auto runs routine work without asking. Deleting, moving or stopping things, sending email or phone messages, uploads from the shell, pushing, publishing and installing ask, unless you asked for exactly that step or Inglen made the file this week; changing security settings always asks. Once a chat has read a page, an email or a downloaded file, a request to an address nobody gave asks first in every mode (SECURITY.md has more). The terminal has the same modes under /mode, and helpers and other agents follow them too. The old all-or-nothing auto-approve switch now works as Auto, so it no longer lets a destructive command through unasked.

Auto has a reviewer. When a step would stop to ask, a quick check reads what you said in the chat and the step itself, never the brain's own words or anything a tool brought back, and lets the step go ahead when you clearly asked for exactly that. "Delete the old log" deletes the old log without a second question. It runs on whatever brain you use, and a model becomes the reviewer only after it passes a set of a couple of hundred labelled steps, checked in the background the first time Auto meets it or from Settings, Behavior, which shows how it did. Shutting down, disks, accounts, security settings, hidden commands and saved passwords always ask, whatever you said.

File changes can be undone. Before one of Inglen's own tools writes, edits, deletes or moves a file it keeps a copy of what was there for a week, and /undo in the window or the terminal lists the recent changes and puts one back. A shell command, an agent program's own edits and anything over 200 MB aren't covered. In Auto, something Inglen made itself in the past week can be deleted or moved without asking, since it can be put back.

## Security

This release closes several security problems in earlier versions, among them ways a web page or a crafted file could reach Inglen's local server or get a risky action past the approval prompt, and ways a write or a read could get outside the folders it is confined to. Update as soon as you can; Inglen patches itself on v0.7.2 and newer. A detailed advisory follows once most installs have updated.

From this version on, an update patch is applied only when it carries a valid signature from the project's release key and every file in it matches the signed list. A patch that doesn't is refused before a single file is written, and the Downloads list says why.

Agents in a team follow your permissions too, and run a risky tool only if you have already said to always allow it. Tools from MCP servers ask before they run and no longer receive your cloud key or mail password. The WhatsApp bridge's own-chat option now answers only your own chat. The old full-screen terminal interface is gone.

## Voice

Inglen speaks and listens in eight languages, not one. The voice bank has always installed 54 voices covering English, Hindi, Spanish, French, Italian, Portuguese, Japanese and Mandarin, and only the English ones were reachable. They are all in the picker now, each one reading its own language properly rather than being handed English sounds. Transcription has the multilingual Whisper sizes alongside the English-only ones, with a spoken-language setting that detects each utterance on its own unless you name one. Speech in a script that ends its sentences differently starts playing as soon as the first sentence is ready, the same as English always did.

Voice mode turns on reliably. Turning it off used to leave the microphone held and a speech model in memory, around 800 MB each time, so leaving voice mode to change a setting and coming straight back could fail to start at all, or sit on warming up forever. Every session now ends cleanly and a new one starts on its own.

The microphone you pick is the one that gets used. It was remembered by a number that Windows changes whenever a headset connects or a USB device comes and goes, so it quietly pointed at something else. It is remembered by name now. When the Windows default input is Stereo Mix, which is really your speakers, Inglen tells you and lists your microphones instead of guessing, where it used to pick a virtual device that records silence and then look like voice was broken.

The wake word hears whole phrases. It used to drop the quiet parts of speech and keep only the end of a long sentence, which is exactly where the name isn't. On the same recorded phrases it now wakes on all fifteen where it woke on nine, including a full command like "open Spotify and play the new album" after the name. While voice mode is on, the wake listener lets go of the microphone, so a Bluetooth headset that only allows one app at a time works in voice mode.

Voice mode in the window works like a call now, the same one the phone app makes. The window opens your microphone with echo cancelling, noise suppression and gain control, and plays its voice back through the same path, so it doesn't hear itself and you can talk over a reply on speakers as well as on a headset. Talking over it stops the voice at once and your words go through instead. Replies are spoken from their first clause, steps show as they run, and a step that needs your OK is asked out loud with Allow and Deny on the voice screen. Saying yes is enough only for what Auto mode would run anyway; anything riskier waits for a click, and Inglen tells you so. The microphone is granted to Inglen's own page and nothing else, without a browser prompt, and Windows' microphone privacy setting stays in charge. When it blocks the mic, voice mode says so and has a button to that setting. On macOS, and anywhere the page can't open a microphone, voice mode listens through the PC's own mic as before.

In the terminal, /listen uses the PC's own mic and speakers. There, Speakers mode keeps Inglen from hearing itself by ignoring what the mic picks up while it talks, and anything that starts with its name still gets through, so "Inglen, stop" or "Inglen, open Spotify instead" cuts the speech and goes through as your message.

When speech fails, Inglen says so. Synthesis and playback run behind a queue, which meant a failure after the request had been accepted was silent in every direction, with nothing written anywhere to explain it. Now it names what went wrong, and the preview button tells you rather than just going quiet. A transcription that fails is no longer indistinguishable from not having said anything. Saving a setting no longer unloads the voice, so the next reply is not held up reloading it.

The mic in the message box dictates into the box with the same listener voice mode uses, the microphone you picked in Settings included, and leaves the sending to you. Pop-ups is one choice now, all of them, only what needs you, or none.

Inglen no longer crashes when a headset was unplugged. The wake word kept retrying the missing microphone while Settings listed the sound devices, and two parts of Inglen starting the sound system at the same moment took the whole app down. They take turns now.

Voice starts on an updated install. The update now carries the maths library the speech stack loads at startup, along with the rest of its dependencies, so a patched install behaves the same as a fresh one. The microphone test runs in its own process, so a headset you plug in after opening Inglen is picked up without a restart.

## AMD and Intel graphics cards

Inglen used to read your graphics card through NVIDIA's own tool, so on an AMD or Intel card the status bar showed nothing in use and model suggestions knew nothing about your card. It now reads every vendor's card the way Task Manager does, load and memory included. The built-in engine follows your card too. NVIDIA gets the CUDA build as before, and AMD and Intel cards get the Vulkan build, which runs the model on the card instead of the processor.

## Faster on local models

The instructions Inglen sends with every message are about a fifth of their old size and the tool descriptions about half, so on a 32K local model the fixed part of every request went from nearly two thirds of the context to about a third. Replies start sooner and a long chat lasts longer before it has to be compacted. On the standalone engine Inglen now uses the model's own tool-calling format instead of reading calls out of plain text.

When a model asks for several things at once, all of them run. Mistral and Command-R models had every call after the first dropped, and a reply with twenty calls where one had a small formatting slip ran nineteen and lost the last without a word. Tools Inglen keeps in reserve until they are needed were partly invisible to the model, so it now always sees their names and can pull any of them in.

## Commands

Commands run in bash as well as PowerShell and cmd, wherever Git Bash or WSL is installed, and the model is told which shells your machine has so it writes the right syntax rather than hitting a wall on "&&". File names in Hindi, Chinese or with accents come back as they are instead of as question marks, and a Python script printing them no longer crashes. A command that fails reports that it failed, where an error followed by anything that worked used to read as success. The same applies to commands running in the background.

## Memory

A memory with a title in Hindi, Chinese or Japanese used to overwrite the whole memory index, and the index could drift until it listed only part of what Inglen remembers. The index is now rebuilt from the memories themselves and repairs itself whenever the two disagree, so everything you told it is in view again.

When something changes, Inglen records the change. Telling it you moved to Linux used to leave it believing you were on Windows, because a fact it already knew was never updated, and it now replaces the old fact and keeps the previous version in the archive. It also no longer archives the wrong fact when a new one replaces an old one, or merges facts that only differ by a number, like two different exam scores.

Recall reads each memory file once and reuses it, so a lookup stays fast as your memory grows. Saving a fact returns immediately, with the archiving of older facts moved into the background and running on its own schedule.

Passive memory works on reasoning models. Inglen notices durable facts on its own without you saying "remember that", and it now has the room to finish writing them and can read the reply even when a model buries it in its reasoning. It also never competes with you for the model. If you send a message while it is working, your message goes first and the facts are picked up on the next pass. Saving on its own has a switch in Settings, Remember things on its own, and when you tell it in a chat not to save something, it doesn't.

Inglen no longer attaches memories picked by keyword to every message, which pulled in notes that had nothing to do with what you asked. The list of what it knows stays in its instructions and it looks up what it needs, the way it already did for everything else. Opening a memory in the Memory tab shows what it says, when it was saved, how often Inglen has recalled it, whether it was noticed on its own or saved because you asked, and a link to the chat it came from, rather than the file behind it. What setup learns about your PC, like its graphics card and drives, is kept for the app and no longer fills your memories.

How Inglen should act now lives in one place, your house rules. It used to keep a second file about itself, soul.md, which overlapped them, so whatever it had written there moves into rules.md the first time you update, and when you tell it "from now on, always..." it saves that as a memory on the spot.

Inglen keeps up with what is going on. Once a chat goes quiet it notes where things stand, the goal, what you settled on so far and what is still left, and the newest of those come along with the first message of your next chat, so "guess what, I got it" in a new chat finds the thing you meant. Small talk leaves nothing behind. The reply shows what it had in mind, each card, fact and search with the chat it came from, and each can be forgotten or marked done from there or from the Memory tab's Going on list. Asking about "yesterday" or "last week" searches those days, and picking up an old chat reads it in order. This works the same with a model on your PC, a cloud model, or Claude Code, Codex, Cursor, OpenCode and the other agents as the brain, and uses the model on your PC for it when one is loaded.

Your memory can go with you into the coding agents on your PC. One switch in the Memory tab, Share with agents, adds Inglen's memory to Claude Code, Codex, Cursor and OpenCode as a tool server they can call, with your facts, your past chats and what is going on, and nothing else of Inglen's.

## Reminders

Reminders are set for the time you meant. Inglen works out the exact time from what you said, and the reminder reads back the day and the time in plain words, so a slip shows up at once rather than at midnight. With the window and the terminal both open a reminder fires once instead of twice, and a reminder set while another one is firing is no longer lost.

A reminder that comes due is said in a chat of its own, marked in the sidebar with a clock and an unread dot, instead of landing in the middle of whatever chat is open. Typing a task in the Reminders page starts it in a new chat and leaves the open one alone, and the page suggests reminders from what Inglen knows about you and what is coming up. A reminder can be changed in place, its words, time, repeat and the last day it repeats, and paused instead of cancelled, from its row or by asking, and a repeating one picks up at its next time when you resume it. One that came due while Inglen was closed says when it was due. A repeating reminder that comes due while you are away from the PC waits instead of talking to an empty room, and the next one, once you are back, says how many came by.

## Phone and teams

Telegram, Discord and WhatsApp are rebuilt on one shared core. Someone new who messages your bot gets a short pairing code, and nothing runs for them until you approve it in Settings, Connectors, with /pair in the terminal, or by asking Inglen on the PC. Nobody can change who has access from inside a chat app, and WhatsApp never answers strangers. Each person's conversation is an Inglen chat you can open in the window, marked with its app and kept across restarts. A step that would ask for your OK on the PC asks in the chat instead, and you reply yes. Voice notes are transcribed on your PC, photos and files go both ways, and a message sent while a reply is running joins it. You can connect one by asking Inglen to connect your Telegram, and write your own connector as one Python file, shown step by step in docs/CONNECTORS.md.

Reminders that used to reach your phone through ntfy now go to your connected Telegram or Discord, from a switch in Connectors, and ntfy is gone. Chats started from a chat app get a real title from the brain after the first real exchange, as window and terminal chats do, and asking Inglen to send something to your phone from one of those chats answers right there, files included.

## Inglen Plus and calls

Inglen Plus is a paid add-on that puts your assistant on your phone from anywhere, with no bot to set up. Turn it on with the licence key from your receipt in Settings, Plus, press Pair a phone, scan the code with the phone's camera, and check that both screens show the same six numbers. Messages between the phone and your PC pass through a relay that only ever sees them sealed with keys from that pairing.

The phone app has your chats, the window's included, and a reply arrives as it is written with each step and what it ran. Steps that need your OK ask with Allow and Deny. You can switch the brain and the approval mode, open files and pictures from the PC, watch the Team room, put back a recent file change, and read and carry on the Claude Code, Codex and other coding agent sessions on your PC. The window and the phone see each other's changes as they happen. When the app is closed, a notification tells you a question is waiting, with Allow and Deny on it, that a reply is done, or that a coding agent finished on the PC.

You can call Inglen from the phone. One shape in your accent shows whether it is connecting, listening, thinking, speaking or muted, and both your words and its words appear as they are said. The call's timer starts when the PC is ready to listen, with a soft tone, and two falling notes mark the end. Put the app away and the call keeps going, with Mute and End in a notification, or Allow and Deny when a step is waiting. As in the window, a spoken yes is enough only for what Auto would run, and anything riskier asks for a tap. You can change the voice and the language from the call, and they stay set on the PC. If the phone goes quiet, the PC ends the call.

Inglen can call you too, if you let it. Let Inglen call me in Settings, Plus is off until you turn it on, and then it calls only for the reasons you pick, such as a reminder coming due, work in the background that finished or needs you, or a question that has waited a few minutes for an answer. It never calls in your quiet hours, 10 pm to 8 am unless you change them, never while you are using the PC, and not again soon after a call you declined or missed. An answered call starts with why it called.

A bridge started from the window stopped answering after a while and still showed as running, and now it keeps going. Its output goes to a log the window can show, for Telegram and WhatsApp as well as Discord. WhatsApp sends files back the way Telegram and Discord do, and all three send files whose paths have spaces in them. Announcements from the developer reach you now, where none ever could before.

A phone bridge now stops when the Inglen that started it closes, so restarting can't leave two of them answering every message. When a team is started from the terminal, each agent gets a live pane showing what it does in words, what its tools returned, and an agent that failed says so rather than showing as done; started from the window, the Team room shows them instead. If a team member could not start, Inglen is told which one and why, instead of believing that part of the job is being done.

## Desktop control

You can see when Inglen is working on your screen. Its first desktop action lights a soft edge round the screen in your accent colour that breathes while it works, with a small pill saying what it is doing. The edge lets clicks through and stays out of its own screenshots. When the turn ends, however it ends, the edge fades, a short chime says it is done, and the window you had in front comes back. It works the same with Claude Code, Codex or another agent as the brain.

A click on a window that sits behind another brings that window forward first, so it no longer lands on whatever was on top. Apps and browser windows Inglen opens stay open after its turn, and typing into a document adds to what is there instead of replacing it, unless you ask for that. Screenshots reach the model at a size it reads quickly. A 2560 by 1440 screen took a 9B model on an 8 GB card over three minutes and takes about six seconds now, still reading tab and file names.

Inglen can lay your windows out in one go. Ask for the browser on the left and the editor on the right, or chat top-right and the game below it, and it places them exactly, with no screenshot or clicking involved.

It can also number what it sees. Taking a marked capture draws a number on every control it can identify, and it then clicks one by number instead of estimating a pixel, which is where visual clicking usually goes wrong.

For web pages it can drive the Chrome you already use, with your profile, logins and tabs, once Chrome is started with its debugging port. Attached, it reads the page itself, so filling a box is direct rather than a click followed by blind typing, and sites see an ordinary browser session.

Bringing a window to the front is verified rather than assumed. Windows can accept the request and quietly not move focus, and Inglen used to carry on regardless. It now confirms the window really is in front, and says so plainly when it cannot, rather than typing into whatever was there.

Every keystroke Inglen sends now checks the target window first. If focus moved since it clicked, or if the front window is Inglen itself, the typing is refused and Inglen re-targets rather than putting text somewhere you did not ask for. The same check means Inglen stops as soon as you take the mouse or keyboard back mid task.

Clicking and typing can run as a single action, so nothing can slip in between the two. Inglen can also write into an app without pulling it to the front. Point it at a window by name, including one in the background, and it reads that window's real controls and fills the field directly instead of taking over your screen.

Declining an action and telling Inglen what to do instead now makes it do that thing, rather than only acknowledging it.

## Cloud models

Reasoning is visible on OpenRouter and other providers that stream their thinking in a different field than local servers do. You can set how hard a model thinks on models that support it, with the levels the provider lists for that model, and turn thinking off where the model allows it. Anthropic models run on Anthropic's own API, so their thinking shows as it happens and the Think button's level reaches the model, which Anthropic's OpenAI-compatible endpoint ignored. A cloud model reports its real context window rather than a fixed guess, so a large context model shows what it actually has. Images reach cloud models that can see them. The model list is sorted and searchable instead of hundreds of unordered lines, and a cloud brain reads as online rather than loaded, since nothing is held in your VRAM. When a provider asks Inglen to slow down it waits as long as asked and tries again. A quota that is used up, or an account out of credits, is said once in the provider's own words with when it resets, and until then Inglen says "out of usage until" that time instead of asking again. A coding agent out of usage on its plan says so and gets no more turns until it resets. When a turn fails, the brain hears about it on the next one, so "try again" means something.

A model on this PC can stay loaded under a cloud brain, from one switch in the model menu, and then the helpers think with it for free while the cloud model talks to you; the sticker shows it beside the brain. Settings shows every brain as a card, grouped by where it runs, with its logo and whether it is in use or has a key saved, and the model menu has a way to a provider that has no key yet. Switching to a cloud brain no longer waits for the local model to leave the graphics card, every switch ends with the same message, and when a provider turns a key or a model down, Inglen repeats what the provider said.

## Models

Loading a model shows real progress on the sticker instead of a blinking dot, and it names the model that is actually loading rather than the one that was there before. That progress is now measured rather than guessed at from the engine's log. The engine prints everything it knows about a model before it reads a single byte of it, so the bar used to jump most of the way up in the first second or two and then sit still for the whole real wait. It moves steadily for the length of the load instead, all the way through, and a model loading on its own when you open Inglen gets the same bar as one you picked by hand.

A download that was interrupted in an awkward place used to stay broken forever, failing the same way on every retry with nothing you could do about it from inside the app. It starts over cleanly now, and pausing a download really pauses it, where resuming used to write a corrupted file. Downloads keep going when you close the window to the tray, and one that was cut off by a restart or a crash picks up where it stopped the next time Inglen opens. Every model links to its page on Hugging Face, so you can check the licence and what it was trained for without leaving the app, and a model Inglen downloaded remembers exactly which repo it came from. With no server running, the model list shows what is on your disk rather than saying there is nothing. A file that isn't a chat model, like an image or video model, or one a download cut short, is marked as such in Models and kept out of the model menu.

Discover opens on the chat models downloaded most this month, the ones their makers publish marked and first. The load estimate says how much of a model won't fit on the card and runs on the processor, and when that is most of it, that replies will be slow to start and a smaller file will feel faster. Adding vision to a model says which file it will download and how big it is, asks first, and downloads like any other file, with Pause and Cancel.

The Full installer now brings a current build of llama.cpp's own server, so the built-in engine runs on every card the official build supports, RTX 50-series included. An install that only has the older Python runtime, which has no kernels for those cards and would return empty replies on longer prompts, asks you to install the engine instead, which is one click in Models.

Helpers that ask for the model already loaded share it, each with a slot of its own, instead of loading a second copy, and how many work at once now follows your graphics memory unless you pick a number.

## Images and video on your PC

Inglen can make pictures and short videos on your own graphics card. My Models lists image and video models beside your chat models. It installs the engine, stable-diffusion.cpp in the build for your card, and lists the image models it finds, in Inglen's own folder or read in place from a ComfyUI or WebUI models folder you add. It shows which are ready, loads one with a click, and says what a family still needs when a part is missing. Asking for an image uses a ready model on your PC first, and a video made there runs in the background and tells you when it is done. Models are recognised by their file names for now, so a checkpoint with a name all its own isn't picked up yet.

With a key saved for xAI Grok, OpenAI or Google Gemini, images and videos come from the cloud too, from any brain, a local model or a coding agent included, and a video tells you when it is ready instead of waiting to be asked. Only what a tool made shows in the chat, not a picture the model was looking at.

## Reading a run

A run shows what it reached outside Inglen. The sites a search or a page read touched appear as their own icons, fetched from each site, another agent appears as its badge, and while it works the line says what it is doing right now, "Reading reddit.com" rather than a tool name. A command that runs for a while counts up on its own card instead of popping up a warning, and after half a minute the card offers Run in background, which lets it finish as a background job while Inglen carries on (/bg in the terminal), and the context ring always reads as room left before the chat gets summarised.

File edits render as a diff with line numbers and a change count, in tight rows. Tool cards show a one line summary of what was passed rather than raw escaped JSON, and the terminal does the same with a glyph per kind of action and a short peek at long output. Steering Inglen mid answer separates the redirected part from what came before. Announcements from the developer stay as a card until you dismiss them.

Typing @ in the message box finds chats by title and files and folders by name, and what you pick goes with the message, a chat to be read from the start, a file attached, a folder to look in. A long paste goes as a chip that says its length instead of filling the box. A reply that used the web shows its sources as one chip that lists every link. A question Inglen asks you stays in the chat as a card with every option and your answer, and words you send while it works sit in their own box inside the reply, marked as yours.

The Files tab previews a web page, PDF or picture in place without running anything in it, and each file has an Open menu with the apps Windows offers for it, each with its own icon.

The window is responsive, so a narrow or half snapped window moves the sidebar into a drawer. Chat titles come from the first real exchange, so a chat that opens with a greeting still gets a title about the actual subject. Optional sound cues mark a finished turn, an error, or a permission request, all off by default, and your messages can sit in chat bubbles tinted with your accent, also off by default.

Each message shows when it was said, a line marks where the day changes, and every chat in the list shows when it was last used. Stop ends a reply at once, also while a local model is still reading a long prompt. What a reply ran is kept for the next turn, so Inglen knows what it already did instead of doing it again. A message typed in another chat while one is replying waits in its box rather than being sent into that reply. /status, in the window or the terminal, shows what is thinking, how full the chat is and how much of your plan is left, and keeps updating while it is open.

## Logs

The Logs page reads like the rest of the app now. Every tool Inglen used is one row with what came back, in words, grouped by day, with the errors a click away and a search box. The model server's page sums up its recent requests, how much it read and wrote and how fast, above the raw lines, and the phone page folds a bridge's reconnects into one line.

## The terminal

The terminal runs on the same engine as the window. Replies stream in above a prompt that stays open, so whatever you type while it works steers it, and Esc stops it at once. Thinking folds to one line, tables are drawn as tables, and /model, /brain, /mode, /think and /chats open a list you move through with the arrow keys, as do approvals. Chats started there get a real title. A second terminal opened beside the first starts its own chat instead of writing over the other's, and the terminal and the window use one built-in model server between them instead of fighting over it.

The terminal and the window share one set of chats. A chat in the terminal is listed in the window, marked as from the terminal, and can be carried on there; whatever is added in the window reaches the terminal before its next message; and /chats and /chat open any chat in the terminal, while /clear starts a new one. /team shows each helper's steps in words.

The terminal no longer crashes on the first tool call of a session, and Ctrl+Z ends it instead of spinning forever. --help and --version answer instead of starting the whole app. /brain lmstudio and /brain builtin work, and switching to a local brain uses whatever model is actually loaded rather than naming one you may not have. A setting saved while on a cloud brain no longer switches to a random model from the provider's catalog. The terminal knows whether the loaded model can see, a prompt that overflows the context is trimmed and retried rather than resent until the turn dies, and /history shows messages that carry an image.

## Elsewhere

Searching past chats finds what you asked about. Most questions used to report no match even when there was one. The window opens fast with a long chat history, because it reads a chat only when you open it instead of loading every one in full to draw the sidebar.

Repair checks every part of the install and puts back whatever is missing, Inglen's code, its skills and art, the voice files and the model engine, and says plainly which parts need a full reinstall. Installing or repairing the engine had started failing for everyone because the engine's project marked an unrelated release as its latest, and Inglen now picks the newest real build. Updating is safe to interrupt. If the power goes or you kill Inglen while it is replacing files, the next start puts it back the way it was rather than leaving you with an install that will not open. Your chats survive being killed mid-save too, and a save that got cut off no longer leaves a ghost copy of the chat sitting in the sidebar.

Asking Inglen for a capability it does not have gets a straight answer instead of a handful of unrelated tools. And it can tell you your charge level, whether you are plugged in and roughly how long you have left. If the part of Inglen running in the background ever stops, the window starts it again and reloads itself, rather than sitting on Failed to fetch until you restart.

The Skills page is laid out like the rest of the app now. Each skill has a switch, so you can keep one on disk without Inglen reaching for it, and opening one shows what it is for and what it brings, with the instructions Inglen follows folded underneath. Skills use the Agent Skills format that Claude and Codex read too, so most skills published for those install here as they are.

Messages that read like an error log now say what happened in plain words, such as "Couldn't delete it" or "The model didn't load" with the reason. Some models are made for a build of llama.cpp other than the official one, like PrismML's ternary Bonsai files. Add that build in Models, Engine, by pasting its GitHub page or picking its folder, and when the official engine can't read a model Inglen tries the builds you added and remembers which one works. Every other model stays on the official engine.

A command card shows the command that ran above its output, and the approval prompt can show the whole command before you allow it rather than cutting it off. Repairing an install reports in the same place as other downloads, with progress and a cancel. Email setup moved to Connectors where it belongs, and a voice install that fails part way can be retried instead of dead-ending. Settings now links to the project, so the source, releases and issue tracker are one click away.

Reads can be confined to your workspace from Settings without setting an environment variable. Inglen's environment variables are named HEARTH_ now, since the agent can have any name, and the old JARVIS_ ones keep working. Windows shows the version you actually have after an update. People running from a git clone are told when there is something worth pulling. When no model is loaded, the app and the terminal both say "no model" in your accent colour instead of showing a placeholder that looked like a model.

---

Windows installer only. Full includes the GPU engine, Lite is for people already running Ollama or LM Studio. Linux runs from source and installs the model and image engines for itself, tested on Mint and Ubuntu. macOS runs the same code but hasn't been tried on a real Mac yet. Desktop control is Windows only for now. Not code signed yet, so Windows shows the blue unknown-publisher box, click More info then Run anyway. Every installer is checked before release: it starts, its voice and model engine load, and Windows Defender scans it clean.
