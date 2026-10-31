# Inglen user guide

For when you've just installed Inglen and want to know what it does and where things are. The assistant inside it is called Inglen on a new install unless you give it another name in Settings, Behavior, Agent name. An older install keeps the name and the `~/Jarvis` folder it already had. This guide says Inglen.

---

## Getting started

1. **Install.** Download [Inglen for Windows](https://github.com/0pen-sourcer/Hearth/releases/latest). Full (about 1 GB) brings its own model engine; Lite (about 500 MB) is for when you already run LM Studio, Ollama or a cloud key. Both install to your user folder without admin rights. Windows Defender may flag the unsigned installer; it's a false positive, and you can restore the file from Windows Security, Protection history.
2. **Pick a brain.** Setup finds what you already have: an agent like Claude Code or Codex, a model file on your disk, or a running LM Studio or Ollama. Pick one, or download a model from the Models tab later, or paste a cloud key.
3. **Talk to it.** Type, or press the mic and speak.

Setup is eight short steps. Besides the brain it asks the language you speak and the voice, headphones or speakers, your name and how Inglen should talk, how much may run without asking, the look and how much detail chats show, and offers to bring over what ChatGPT or Claude knows about you. It ends with a six-stop tour of the window, which you can take again from Settings, About.

Running from source instead? See "Install from source" in the [README](../README.md).

---

## The window at a glance

1. **Top bar.** The model menu on the left picks what Inglen thinks with. The GPU chip shows your graphics card, and the mic on the right starts voice mode.
2. **Sidebar.** New chat and your chats, each with when it was last used (a chat started in the terminal has a prompt mark, a reminder's chat a clock), then Team, Memory, Reminders, Skills, Files, Logs, Models and Settings.
3. **Sticker.** The model menu button shows the brain in use and its loading progress, and a local model kept for helpers if you turned that on.
4. **Chat bar.** Think sets how hard the model reasons, the mic talks instead of typing, + attaches a file, and the switch beside Send sets what runs without asking (Ask, Auto-edit or Auto).
5. **The mark on an empty chat.** In every look but Classic it shows the model's state, with an empty ring when none is loaded, a line running along it while one loads, and a filled dot when it's ready.
6. **Tray icon.** Opens your pinned and recent chats, a new chat or voice mode, and turns on listening for Inglen's name.

---

## Brains

Inglen can think with any of these, switched from the model menu at any time:

- **A model on this PC**, through Inglen's own engine, LM Studio, Ollama or any OpenAI-compatible server. Private, free, and offline.
- **A cloud model** with your own key from Anthropic, OpenAI, Google Gemini, OpenRouter, xAI or Groq.
- **An agent you already pay for**: Claude Code, Codex or Cursor, on your plan and with no key. Other agents that speak the Agent Client Protocol work too once installed, such as OpenCode, Copilot CLI, Goose and Grok Build.

Whatever the brain, Inglen keeps its tools, memory and voice, and the approval switch decides what asks first. The Think menu lists the effort levels the chosen model has; on Codex and Claude Code it also has a Fast switch. With a cloud brain, a model on this PC can stay loaded (a switch in the model menu) to run helpers for free.

**Which local model?** Any model of about 7B or larger trained for tool calling. The Models tab marks each file by whether it fits your graphics card. Small models handle everyday work well and can lose the thread on long multi-step jobs; for those, a cloud model or an agent brain is the better pick.

---

## Helpers and the Team room

Bigger jobs go to helpers. Ask for them in plain words ("have Codex write the tests while a helper on Claude Haiku looks up the API"), or press **New helper** in the Team tab.

- **Where they run.** One of Inglen's own helpers runs on the assistant's brain, this PC's model, or a cloud model you name. Agent programs (Claude Code, Codex, Cursor, OpenCode and more) run on your own install and account.
- **Watching them.** The Team room shows each helper at a desk; past eight, the next room opens. Click anyone to see who asked, the task, every step, and what it changed.
- **Talking to them.** Type in a working helper's panel to tell it something part-way. Helpers also message each other and Inglen as they work; in the room, the sender walks over and says it.
- **Their changes.** An agent program working in a git repository gets its own copy of it. Its changes wait in its panel to be brought in, discarded or undone. Inglen's own helpers write directly, and what they changed can be undone.
- **Stopping one.** The Stop button in its panel, or `/team stop <n>` in the terminal.
- **Racing.** Ask for the same task from two agents ("have Codex and Claude Code both fix this bug and run the tests") and you get one comparison to choose from.

---

## Voice

Press the mic in the top bar. The window turns into a dot grid that listens and speaks; press Esc to leave.

- Speak, pause, and Inglen answers out loud. The mic opens again for your reply.
- **Interrupting.** Talk over a reply and it stops on your first word, then answers what you said instead. This works on speakers too: the window cancels the echo of its own voice the way a video call does, so it doesn't hear itself. The first few seconds on speakers it is still learning the room.
- **Approvals.** A step that needs your OK is asked out loud, with Allow and Deny under it. Saying "yes" is enough for what Auto mode would run anyway; deleting, moving, sending and the like take a click on Allow, and it says so.
- **The desktop HUD.** When Inglen isn't the focused window, a small dot grid floats over your other apps. Clicks pass through it; hold Ctrl to drag it.
- **The microphone.** Voice mode uses the mic picked in Settings, Voice. Windows' microphone privacy setting decides whether apps may use it; if it's blocking the mic, voice mode says so and has a button that opens that setting.
- **Wake word.** Right-click the tray icon and turn on **Listen for "Inglen"** (it follows the agent's name). Saying the name opens voice mode.
- Voices, speed, the mic and the listening model are in Settings, Voice.
- **In the terminal**, `/listen` uses the PC's own mic and speakers, with no echo cancelling: there, use headphones, or turn on Speakers mode in Settings, Voice, which stops listening while it talks (start with its name, "Inglen, stop", to cut in).

---

## Approvals

A switch beside the send box decides what runs without asking:

- **Ask** checks before anything acts on your machine.
- **Auto-edit** lets file edits inside the workspace through.
- **Auto** runs routine work on its own. Deleting, moving, sending, uploading, pushing, publishing and installing ask, unless you asked for exactly that step or Inglen made the file in the past week (both below).

In Auto, a reviewer reads your own messages in the chat and lets a step through without asking when you clearly asked for exactly that step ("delete the old log"). It never reads the brain's words or what a tool brought back. A model is used as the reviewer only once it has passed a check, which runs on its own the first time Auto meets it; Settings, Behavior, Auto's reviewer shows the result and turns it off. Shutting down, disks, accounts, security settings, hidden commands and saved passwords always ask.

**Undo.** Before a file is written, edited, deleted or moved, Inglen keeps a copy of what was there. Type `/undo` in the window for a list of recent changes to put back; in the terminal `/undo` lists them and `/undo <n>` puts one back. Because of that, Auto deletes or moves things Inglen made itself in the past week without asking.

When something needs your OK, a strip above the chat box asks: **Yes (once)**, **No**, **Always** or **Never** for that tool, or type what to do instead. Writes stay inside the workspace (`~/Inglen`, or `~/Jarvis` on an older install) unless you allow another folder. In the terminal, `/mode` sets the switch and `/perms` shows saved answers (`/perms forget <tool>`, `/perms reset`).

---

## Try these

| Say | What happens |
|---|---|
| `find my [movie / game / folder]` | Searches your drives and lists matches, best first |
| `play that` (after a find) | Opens it in your default player |
| `what's eating up my disk` | The biggest folders and files |
| `summarize this PDF` (drop it in) | Reads it and sums it up, even hundreds of pages |
| `what's the latest on [topic]` | Searches the web and reads the top results |
| `remind me to stretch in 30 minutes` | A reminder, read back in words |
| `remember I prefer FastAPI` | Saved to memory, used in later chats |
| `what did we talk about last week about that bug` | Searches every past chat |
| `open Chrome with my work profile` | Opens it with the right profile |
| `set up a team to build a landing page` | Starts helpers you can watch in the Team room |

---

## Reminders

Say it however you like. Inglen works out the exact time and reads back the day and time in words.

| Say | Set for |
|---|---|
| `in 25 minutes` | now + 25 minutes |
| `tomorrow at 7am` | tomorrow 07:00 |
| `next monday at 10am` | the coming Monday, 10:00 |
| `every weekday at 9am` | a recurring reminder |

When it's time you get a Windows notification, and Inglen says it in a chat of its own, marked in the sidebar with a clock and a dot until you open it. With **Send reminders to my phone too** on (Settings, Connectors), it reaches your Telegram or Discord as well. All of them are listed in the Reminders tab, where you can change one (what it says, when, how often and until when), pause one without losing it (a repeating one picks up at its next time when you resume it), type a new one, and pick from ideas based on what Inglen knows about you.

---

## Your phone

Message Inglen from Telegram, Discord or (experimentally) WhatsApp, and it runs on your PC and replies, with voice notes, photos and files going both ways. Set it up in **Settings, Connectors**, or ask Inglen to connect it. Someone new who messages your bot gets a short code, and nothing runs for them until you approve it in Connectors or with `/pair`. A step that would ask for your OK on the PC asks in the chat, and each phone conversation shows in the window's chat list with a title the brain gives it. Full steps in [PHONE.md](PHONE.md); writing your own connector is in [CONNECTORS.md](CONNECTORS.md).

### Inglen Plus

Plus is a paid add-on with a phone app of its own, no bot needed. In **Settings, Plus**, paste the licence key from your receipt, press **Pair a phone**, scan the code with the phone's camera, and check that both screens show the same six numbers. The phone then reaches this PC from anywhere through an end-to-end encrypted relay.

- **On the phone.** Your chats, the window's included, with each reply's steps as they happen; Allow and Deny for risky steps; the brain and the approval mode; files and pictures from the PC; the Team room; recent file changes with Undo; and the sessions of Claude Code, Codex and other coding agents on the PC, which you can read and carry on. Changes on the phone show in the window at once, and the other way round.
- **Notifications.** A question waiting (with Allow and Deny right on it), a finished reply, and a coding agent finishing on the PC.
- **Calls.** Press the call button and talk. Both sides' words show as they're said, the timer starts when the PC is listening, and a soft tone says it's ready. Put the app away and the call carries on, with Mute and End in the notification. A spoken yes is enough for what Auto would run; anything riskier asks for a tap. The call's settings change the voice and the language, and turn the tones off. If the phone goes quiet, the PC ends the call.
- **Inglen calls you.** Off until you turn on **Let Inglen call me** under Plus. Then pick the reasons (a reminder coming due, background work that finished or needs you, a question waiting for a few minutes), how long a question waits, and quiet hours (10 pm to 8 am to start with). It never calls while you're using the PC, and waits a while after a call you declined or missed. An unanswered call shows as missed.

---

## Memory and chats

- **Memory.** Every fact Inglen learns is a small file in the workspace's `memory` folder. It keeps an index of them in view and looks up what a message needs, and ones never used are archived rather than deleted. Read, change or delete them in the Memory tab.
- **Saving on its own.** Once a chat goes quiet, Inglen notes lasting facts from it, on a model loaded on this PC when there is one and otherwise on the brain you use. Turn that off in Settings, Behavior, Remember things on its own, and it saves only when you ask. Telling it in a chat not to save something works too.
- **What's going on.** When a chat goes quiet for a few minutes, Inglen notes where it stands: the goal, what you settled on (a budget, a shortlist) and what's still left, like the phone you haven't bought yet or the form waiting on a payment. The newest of those come along with the first message of a new chat, so "guess what, I got it" gets a real guess. The **in mind** button under a reply lists each thing it used, with the chat it came from, and lets you forget one or mark it done. The Memory tab lists them under **Going on**. The same switch turns this off.
- **Past chats.** Every chat is searchable by words and by time, so "what did we decide about X last week" works.
- **Shared with your coding agents.** The Memory tab's **Share with agents** has a switch for each of Claude Code, Codex, Cursor and OpenCode that you have installed. One that's on can use your memory as a tool, with your facts, past chats and what's going on, and nothing else of Inglen's.
- **One set of chats.** A chat from the terminal shows in the window and can be carried on there, and `/chats` and `/chat <n>` open any chat in the terminal.

---

## Dropping files

Drag files onto the window. Inglen reads PDF, Word, Excel, PowerPoint, EPUB, notebooks, RTF, CSV, JSON, HTML and compressed files; it looks inside zip and tar archives without unpacking them; and with a model that can see, it reads images.

Type `@` in the message box to pick a past chat by its title or a file or folder by its name; it goes along with the message. The Files tab previews a file in place, and its **Open** menu lists the apps that can open it.

---

## Images and video

Ask for a picture or a short video in plain words. **Models, My Models** lists image and video models beside the chat ones (filter by Chat, Images or Video), each with Load, Unload and Delete. If the image engine (stable-diffusion.cpp) isn't on your PC yet, the same page installs it for your graphics card. It finds models in `~/.hearth/image-models` or in a ComfyUI or WebUI models folder you add under **Image and video setup**, which also says what each model family still needs. A ready model on your PC is used first; with a key for xAI Grok, OpenAI or Google Gemini saved, the cloud works too, whatever the brain. Say "on my PC" or "in the cloud" to choose. A model is recognised by its file name for now, so one with a name all its own isn't listed yet.

---

## Watching it use your desktop

When Inglen clicks and types in your apps, a soft edge in your accent colour breathes round the screen and a small pill says what it's doing. Move the mouse or type and it stops to let you have the screen. When it's done the edge fades, a short chime plays, and the window you were in comes back to the front.

---

## Skills

A skill is a folder that teaches Inglen a workflow, like making a PDF or a slide deck. Built-in ones cover documents and diagrams. Skills use the Agent Skills format that Claude and Codex read too, so most skills on GitHub work here: paste a link or `owner/repo` in the Skills tab, ask in chat, or run `/skill install owner/repo`. Each one has a switch; one that's off stays on disk but Inglen won't use it. Writing and sharing your own: [SKILLS.md](SKILLS.md).

---

## The terminal

The **Inglen CLI** shortcut, or `inglen.bat` from source, does everything the window does, with slash commands and voice. `@path` attaches a file. The prompt stays open while Inglen replies: type to steer it, Esc to stop it, Ctrl+T to show its thinking. Commands that choose something (`/brain`, `/model`, `/mode`, `/think`, `/chats`) open a list you move through with the arrow keys.

```
/help            every command
/status          what is thinking, how full this chat is, plan limits
/brain           switch what thinks: local, a provider or an agent
/model           pick a model on the current brain
/models          models on disk, downloads, the provider's list
/tools           the tools
/clear           start a new chat
/compact         summarize old turns now
/context         set the context window
/tokens          how full the context is
/history         the last messages
/chats           every chat, from the window too
/chat            carry on a chat here
/workspace       the workspace folder
/log             recent activity
/mem             what I remember
/curate          merge duplicate memories
/rules           the house rules file
/name            my name
/me              what I know and may do
/team            helpers working for me
/agents          agent programs on this PC
/agent           run a helper and wait
/learn           look at this PC again
/migrate         bring memory over from another agent
/import-memory   bring memory over from ChatGPT or Claude
/jobs            background jobs
/mcp             Model Context Protocol
/skill           skills
/voice           speaking: on, off, speed, mic
/stt             the listening model
/listen          talk instead of typing
/sleep           wait for the wake word
/wake            wake up
/think           reasoning on or off, or how hard
/multi           multi-line input
/mode            what runs without asking: ask, edits, auto
/perms           saved permissions
/allow           let me write under a folder
/disallow        take that back
/allowed         folders I may write in
/lockdown        keep reads inside the workspace
/about           version and stats
/update          get a newer version
/repair          restore damaged files
/undo            put back a file change
/phone           reach me from your phone
/pair            let someone in with their code
/exit            quit
```

---

## Settings

- **Chat brain**: what Inglen thinks with, as cards grouped by where it runs.
- **Voice**: voice, speed, microphone, speakers or headphones, the listening model.
- **Behavior**: the agent's name, reasoning, house rules, Auto's reviewer, saving memories on its own, reading only inside the workspace, bringing memory over, the local server and per-tool permissions.
- **Appearance**: Classic, Light, Dark or System, the accent, **Detail in chats** (Simple folds a reply's steps into one line you can open; Full shows each as its own card, with every counter in the status bar), the Team room, and how the chat looks (stats, diffs, bubbles, sounds, pop-ups).
- **Connectors**: Telegram, Discord, WhatsApp, who is let in, and email.
- **Plus**: the licence, pairing a phone, the phones you've paired, and whether Inglen may call you.
- **Built-in server**: the engine's settings.
- **MCP servers**: servers whose tools Inglen uses, each with its service's icon and a status dot (a failed one says why in a sentence; click it for the full error), and the config for using Inglen's tools from other apps.
- **About**: version, updates, repair, start at login, and the tour.

Every page shows the same whichever detail you picked. Rows only an expert needs, like reviewing every command Auto runs, reasoning effort, tool permissions or the server's port, are in a closed **Advanced** section at the foot of their page. Everything saves as you change it, with no restart.

---

## Where things live

```
~/Inglen/                  the assistant's home (~/Jarvis on an older install; HEARTH_WORKSPACE moves it)
├── memory/                one file per fact, MEMORY.md as the index
├── conversations/         every chat, window and terminal
├── reminders.json         reminders
├── settings.json          settings
├── profile.md             who you are and how you like replies
├── rules.md               your house rules, read every turn
├── logs/                  what it did, by day, and each part's log
├── subagents/             each helper's transcript
├── skills/                skills you installed or wrote
└── voices/                voice and listening model files
```

Reads default to your whole disk; writes, deletes and moves stay in the workspace unless you allow a folder. `HEARTH_LOCKDOWN=1` keeps reads inside the workspace too.

---

## When something goes wrong

- **The app won't open**: check `logs/hearth_tray.log` in the workspace (`~/Inglen`, or `~/Jarvis` on an older install). If Windows Defender quarantined `Hearth.exe`, restore it from Windows Security, Protection history.
- **A model won't load**: the error says why (too big for the card, a newer model than the engine, a format the engine can't read). Update the engine in Models, Engine, or pick a smaller file. A model made for another build of llama.cpp (its page says which) loads once you add that build under Models, Engine, Add an engine.
- **An agent brain goes quiet**: after 90 seconds with no word from it, the chat says it may still be thinking, and Stop ends the turn. When the agent wrote an error to its own log (a used-up free limit, say), the turn ends with those words. Otherwise check it's signed in (`claude auth status`, `codex login status`, `cursor-agent status`), or open `/agents` in the terminal.
- **Voice talks over itself on speakers**: in the window it cancels its own echo; if it still hears itself, headphones fix it. In the terminal's `/listen`, turn on Speakers mode in Settings, Voice.
- **Something broken**: Settings, About, Repair puts Inglen's own files back without touching your chats or memory.
- The Logs tab shows every tool call and its result, which usually explains what happened.

---

## More

- Every tool: [TOOLS.md](TOOLS.md)
- Issues and source: [github.com/0pen-sourcer/Hearth](https://github.com/0pen-sourcer/Hearth)
