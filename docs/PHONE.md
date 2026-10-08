# Reach Hearth from your phone

Message Hearth from anywhere and it runs the full agent on your PC, then replies,
files included. Only the chat or user IDs you allow are answered; everyone else is
ignored. No OAuth, no public server, no port forwarding. Nothing leaves your
machine except through the service you set up yourself.

The channels:
- **Telegram** (recommended). The official bot API, nothing to break.
- **Discord**. Also official and safe for your account; good if you live in Discord.
- **ntfy push**. One-way reminders to your phone, even when the PC is asleep.
- **WhatsApp** (experimental). An unofficial link to a real account, with a ban
  risk, so use a spare number only.

A bridge runs alongside Hearth and stops when Hearth closes.

---

## The easy way: Settings, Connectors

No files to edit, no commands to run. Paste a token and your ID, press **Save**, done.
Hearth keeps the bot running and **starts it again on every launch**.

### Telegram (about 2 minutes)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts and copy
   the **bot token** (it looks like `123456789:ABCdef…`).
2. Get **your chat ID**. Message **@userinfobot** in Telegram and it replies with
   your numeric ID. Or paste the token, press **Start**, message your bot once, and
   the status line shows the incoming ID.
3. In Hearth, open **Settings, Connectors, Telegram**. Paste the token and your
   chat ID, press **Save**, then **Start**.
4. Text your bot. It runs the agent on your PC and replies, with any file it made.

### Discord (about 3 minutes)

1. Go to **discord.com/developers**, choose **New Application**, then **Bot**, copy
   the **token**, and turn on **MESSAGE CONTENT INTENT**.
2. Get **your user ID**. In Discord, open Settings, Advanced, turn on **Developer
   Mode**, then right-click your name and choose **Copy User ID**.
3. Invite the bot to a server you own, or just DM it.
4. In Hearth, open **Settings, Connectors, Discord**. Paste the token and your
   user ID, press **Save** and **Start**. DM it or @mention it in a channel.

### ntfy push (reminders on your phone)

Install the **ntfy** app (iOS or Android), subscribe to a topic you pick (for
example `hearth-<something-random>`), and set the same topic in **Settings,
Connectors, ntfy**. Reminders now buzz your phone even when the PC is asleep.

---

## Advanced: run a bridge from the terminal or headless

If you run Hearth headless (no desktop app), configure a bridge by hand.

**Telegram**: create `~/.hearth/phone_bridge.json`
```json
{ "bot_token": "123456:ABC…", "allowed_chat_ids": [<your id>], "ntfy_topic": "" }
```
point it at a brain (the same `LOCAL_API_BASE`, `LOCAL_API_KEY` and `LOCAL_MODEL`
environment the terminal uses) and run
```
python -m hearth.telegram_bridge
```

**Discord**: create `~/.hearth/discord_bridge.json`
```json
{ "bot_token": "…", "allowed_user_ids": [<your id>], "ntfy_topic": "" }
```
then run `python -m hearth.discord_bridge`.

The desktop app's **Connectors** panel writes these same files for you, so you only
need this route for a server or headless setup.

---

## WhatsApp (experimental)

WhatsApp has **no official bot API for personal accounts**, so this uses an
unofficial library that logs in as a real account, which carries a **ban risk**.
Use a **spare number**, never your main one, and don't spam it. Set it up under
**Settings, Connectors, WhatsApp** (scan the QR code to link) or run
`python -m hearth.whatsapp_bridge`. Editing a sent message isn't reliable on
WhatsApp, so unlike Telegram and Discord it can't show live tool progress; it
sends the final reply with a short "used: …" footer.

---

## What the bot can do

Anything Hearth can: read and write files, run the shell, search the web, drive
apps, generate images, set reminders, run helpers. On Telegram and Discord it shows
**live tool calls** as it works, then the answer. To send you a file it names the
path, and the bridge attaches it. Tool calls are approved on these channels without
asking (you're the owner, gated by the allow list), though a destructive command is
still refused, so keep your allowed IDs tight and your tokens out of any repo.
