# Hearth user guide

For when you've just installed Hearth and want to know what it does and where things are.

---

## Getting started

1. **Install.** Download [Hearth for Windows](https://github.com/0pen-sourcer/Hearth/releases/latest). Full (about 1 GB) brings its own model engine; Lite (about 500 MB) is for when you already run LM Studio, Ollama or a cloud key. Both install to your user folder without admin rights. Windows Defender may flag the unsigned installer; it's a false positive, and you can restore the file from Windows Security, Protection history.
2. **Pick a brain.** Setup finds what you already have: an agent like Claude Code or Codex, a model file on your disk, or a running LM Studio or Ollama. Pick one, or download a model from the Models tab later, or paste a cloud key.
3. **Talk to it.** Type, or press the mic and speak.

Setup also asks your name, how JARVIS should talk, headphones or speakers, how much may run without asking, and the look. It ends with a short tour, which you can take again from Settings, About.

Running from source instead? See "Install from source" in the [README](../README.md).

---

## The window at a glance

1. **Top bar.** The model menu on the left picks what JARVIS thinks with. The GPU chip shows your graphics card, and the mic on the right starts voice mode.
2. **Sidebar.** New chat and your chats, each with when it was last used (a chat started in the terminal has a prompt mark, a reminder's chat a clock), then Team, Memory, Reminders, Skills, Files, Logs, Models and Settings.
3. **Sticker.** The model menu button shows the brain in use and its loading progress, and a local model kept for helpers if you turned that on.
4. **Chat bar.** Think sets how hard the model reasons, the mic talks instead of typing, + attaches a file, and the switch beside Send sets what runs without asking (Ask, Auto-edit or Auto).

---

## Brains

JARVIS can think with any of these, switched from the model menu at any time:

- **A model on this PC**, through Hearth's own engine, LM Studio, Ollama or any OpenAI-compatible server. Private, free, and offline.
- **A cloud model** with your own key from Anthropic, OpenAI, Google Gemini, OpenRouter, xAI or Groq.
- **An agent you already pay for**: Claude Code, Codex or Cursor, on your plan and with no key. Other agents that speak the Agent Client Protocol work too once installed, such as OpenCode, Copilot CLI, Goose and Grok Build.

Whatever the brain, JARVIS keeps Hearth's tools, memory and voice, and asks before anything risky. The Think menu lists the effort levels the chosen model has; on Codex and Claude Code it also has a Fast switch. With a cloud brain, a model on this PC can stay loaded (a switch in the model menu) to run helpers for free.

**Which local model?** Any model of about 7B or larger trained for tool calling. The Models tab marks each file by whether it fits your graphics card. Small models handle everyday work well and can lose the thread on long multi-step jobs; for those, a cloud model or an agent brain is the better pick.

---

## Helpers and the Team room

Bigger jobs go to helpers. Ask for them in plain words ("have Codex write the tests while a helper on Claude Haiku looks up the API"), or press **New helper** in the Team tab.

- **Where they run.** One of Hearth's own helpers runs on JARVIS's brain, this PC's model, or a cloud model you name. Agent programs (Claude Code, Codex, Cursor, OpenCode and more) run on your own install and account.
- **Watching them.** The Team room shows each helper at a desk; past eight, the next room opens. Click anyone to see who asked, the task, every step, and what it changed.
- **Talking to them.** Type in a working helper's panel to tell it something part-way. Helpers also message each other and JARVIS as they work; in the room, the sender walks over and says it.
- **Their changes.** An agent program working in a git repository gets its own copy of it. Its changes wait in its panel to be brought in, discarded or undone. Hearth's own helpers write directly, and what they changed can be undone.
- **Stopping one.** The Stop button in its panel, or `/team stop <n>` in the terminal.
- **Racing.** Ask for the same task from two agents ("have Codex and Claude Code both fix this bug and run the tests") and you get one comparison to choose from.

---

## Voice

Press the mic in the top bar. The window turns into a dot grid that listens and speaks; press Esc to leave.

- Speak, pause, and JARVIS answers out loud. The mic opens again for your reply.
- **Interrupting.** On headphones, talk over a reply and it stops on your first word, then answers what you said instead. On speakers it ignores what the mic hears while it talks, so it doesn't answer itself, except anything that starts with its name: "Jarvis, stop" cuts the reply.
- **The desktop HUD.** When Hearth isn't the focused window, a small dot grid floats over your other apps. Clicks pass through it; hold Ctrl to drag it.
- **Wake word.** Right-click the tray icon and turn on **Wake word**. Saying "Jarvis" opens voice mode.
- Voices, speed, the mic and the listening model are in Settings, Voice.

---

## Approvals

A switch beside the send box decides what runs without asking:

- **Ask** checks before anything acts on your machine.
- **Auto-edit** lets file edits inside the workspace through.
- **Auto** runs routine work on its own, and still asks before anything that deletes, moves, sends, uploads, pushes, publishes or installs.

When something needs your OK, a strip above the chat box asks: **Yes (once)**, **No**, **Always** or **Never** for that tool, or type what to do instead. Writes stay inside the workspace (`~/Jarvis`) unless you allow another folder. In the terminal, `/mode` sets the switch and `/perms` shows saved answers (`/perms forget <tool>`, `/perms reset`).

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

Say it however you like. Hearth works out the exact time and reads back the day and time in words.

| Say | Set for |
|---|---|
| `in 25 minutes` | now + 25 minutes |
| `tomorrow at 7am` | tomorrow 07:00 |
| `next monday at 10am` | the coming Monday, 10:00 |
| `every weekday at 9am` | a recurring reminder |

When it's time you get a Windows notification, and JARVIS says it in a chat of its own, marked in the sidebar with a clock and a dot until you open it. With an [ntfy](https://ntfy.sh) topic set, it reaches your phone too. All of them are listed in the Reminders tab, where you can change one (what it says, when, how often and until when), pause one without losing it (a repeating one picks up at its next time when you resume it), type a new one, and pick from ideas based on what JARVIS knows about you.

---

## Your phone

Message JARVIS from Telegram, Discord or (experimentally) WhatsApp, and it runs on your PC and replies, files included. Set it up in **Settings, Connectors**. Full steps in [PHONE.md](PHONE.md).

---

## Memory and chats

- **Memory.** Every fact JARVIS learns is a small file in `~/Jarvis/memory`. It keeps an index of them in view and looks up what a message needs, and ones never used are archived rather than deleted. Read, change or delete them in the Memory tab.
- **Saving on its own.** After a reply JARVIS notes lasting facts from the chat. Turn that off in Settings, Appearance, Remember things on its own, and it saves only when you ask. Telling it in a chat not to save something works too.
- **Past chats.** Every chat is searchable, so "what did we decide about X last week" works.
- **One set of chats.** A chat from the terminal shows in the window and can be carried on there, and `/chats` and `/chat <n>` open any chat in the terminal.

---

## Dropping files

Drag files onto the window. Hearth reads PDF, Word, Excel, PowerPoint, EPUB, notebooks, RTF, CSV, JSON, HTML and compressed files; it looks inside zip and tar archives without unpacking them; and with a model that can see, it reads images.

---

## Skills

A skill is a folder that teaches JARVIS a workflow, like making a PDF or a slide deck. Built-in ones cover documents and diagrams. Skills use the Agent Skills format that Claude and Codex read too, so most skills on GitHub work here: paste a link or `owner/repo` in the Skills tab, ask in chat, or run `/skill install owner/repo`. Each one has a switch; one that's off stays on disk but JARVIS won't use it. Writing and sharing your own: [SKILLS.md](SKILLS.md).

---

## The terminal

`hearth.bat` (from source) or **Hearth CLI** (from the installer) does everything the window does, with slash commands and voice. `@path` attaches a file. The prompt stays open while JARVIS replies: type to steer it, Esc to stop it, Ctrl+T to show its thinking. Commands that choose something (`/brain`, `/model`, `/mode`, `/think`, `/chats`) open a list you move through with the arrow keys.

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
/phone           reach me from your phone
/exit            quit
```

---

## Settings

- **Chat brain**: what JARVIS thinks with, as cards grouped by where it runs.
- **Voice**: voice, speed, microphone, speakers or headphones, the listening model.
- **Behavior**: the agent's name, reasoning, house rules, reading only inside the workspace, bringing memory over, the local server and per-tool permissions.
- **Appearance**: Classic, Light, Dark or System, Simple or Advanced, the accent, the Team room, and how the chat looks (stats, diffs, bubbles, sounds, pop-ups), with the switch for saving memories on its own.
- **Connectors**: phone bridges, ntfy and email.
- **About**: version, updates, repair, start at login, and the tour.

Everything saves as you change it, with no restart.

---

## Where things live

```
~/Jarvis/                  the assistant's home (HEARTH_WORKSPACE moves it)
├── memory/                one file per fact, MEMORY.md as the index
├── conversations/         every chat, window and terminal
├── reminders.json         reminders
├── settings.json          settings
├── profile.md             who you are and how you like replies
├── rules.md               your house rules, read every turn
├── soul.md                who it is, written by itself
├── logs/                  what it did, by day, and each part's log
├── subagents/             each helper's transcript
├── skills/                skills you installed or wrote
└── voices/                voice and listening model files
```

Reads default to your whole disk; writes, deletes and moves stay in the workspace unless you allow a folder. `HEARTH_LOCKDOWN=1` keeps reads inside the workspace too.

---

## When something goes wrong

- **The app won't open**: check `~/Jarvis/logs/hearth_tray.log`. If Windows Defender quarantined `Hearth.exe`, restore it from Windows Security, Protection history.
- **A model won't load**: the error says why (too big for the card, a newer model than the engine, a format the engine can't read). Update the engine in Models, Engine, or pick a smaller file. A model made for another build of llama.cpp (its page says which) loads once you add that build under Models, Engine, Add an engine.
- **An agent brain gives no answer**: check it's signed in (`claude auth status`, `codex login status`, `cursor-agent status`), or open `/agents` in the terminal.
- **Voice talks over itself on speakers**: pick speakers in Settings, Voice; on headphones it can be interrupted.
- **Something broken**: Settings, About, Repair puts Hearth's own files back without touching your chats or memory.
- The Logs tab shows every tool call and its result, which usually explains what happened.

---

## More

- Every tool: [TOOLS.md](TOOLS.md)
- Issues and source: [github.com/0pen-sourcer/Hearth](https://github.com/0pen-sourcer/Hearth)
