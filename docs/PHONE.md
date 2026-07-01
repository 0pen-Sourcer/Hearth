# Reach Hearth from your phone

Message Hearth from anywhere and it runs the full agent on your PC, then replies —
files included. Only the chat/user IDs you allow are answered; everyone else is
ignored. No OAuth, no public server, no port forwarding. Nothing leaves your
machine except the service you set up yourself.

Channels:
- **Telegram** — *recommended*. Official bot API, nothing to break.
- **Discord** — also official + ban-free; good if you live in Discord.
- **ntfy push** — one-way reminders to your phone, even when the PC is asleep.
- **WhatsApp** — *experimental*, unofficial link to a real account (ban risk;
  spare number only).

---

## The easy way — Settings → Reach from phone

No files to edit, no commands to run. Paste a token + your ID, hit **Save**, done.
Hearth keeps the bot running and **auto-starts it on every launch**.

### Telegram (~2 min)

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → copy the
   **bot token** (looks like `123456789:ABCdef…`).
2. Get **your chat ID**: message **@userinfobot** in Telegram — it replies with
   your numeric id. (Or paste the token, hit **Start**, message your bot once, and
   the status line shows the incoming id.)
3. In Hearth: **Settings → Reach from phone → Telegram**. Paste the token and your
   chat ID, click **Save**, then **Start**.
4. Text your bot. It runs the agent on your PC and replies — with any file it made.

### Discord (~3 min)

1. Go to **discord.com/developers** → **New Application** → **Bot** → copy the
   **token**, and turn on **MESSAGE CONTENT INTENT**.
2. Get **your user ID**: Discord → Settings → Advanced → turn on **Developer
   Mode**, then right-click your name → **Copy User ID**.
3. Invite the bot to a server you own, or just DM it.
4. In Hearth: **Settings → Reach from phone → Discord**. Paste the token and your
   user ID, **Save**, **Start**. DM it or @mention it in a channel.

### ntfy push (reminders to your phone)

Install the **ntfy** app (iOS/Android), subscribe to a topic you pick (e.g.
`hearth-<something-random>`), and set the same topic in **Settings → Reach from
phone → ntfy**. Reminders now buzz your phone even when the PC is asleep.

---

## Advanced — run a bridge from the CLI / headless

If you run Hearth headless (no desktop app), configure a bridge by hand:

**Telegram** — create `~/.hearth/phone_bridge.json`:
```json
{ "bot_token": "123456:ABC…", "allowed_chat_ids": [<your id>], "ntfy_topic": "" }
```
point it at a brain (the same `LOCAL_API_BASE` / `LOCAL_API_KEY` / `LOCAL_MODEL`
env the CLI uses) and run:
```
python -m hearth.telegram_bridge
```

**Discord** — create `~/.hearth/discord_bridge.json`:
```json
{ "bot_token": "…", "allowed_user_ids": [<your id>], "ntfy_topic": "" }
```
then `python -m hearth.discord_bridge`.

The desktop app's **Reach from phone** panel writes these same files for you, so
you only need this route for a server / headless setup.

---

## WhatsApp (experimental)

WhatsApp has **no official personal-account bot API**, so this uses an unofficial
library that logs in as a real account — which carries a **ban risk**. Use a
**spare number**, never your main one, and don't spam it. Configure it under
**Settings → Reach from phone → WhatsApp** (scan the QR to link), or run
`python -m hearth.whatsapp_bridge`. Editing a sent message isn't reliable on
WhatsApp, so unlike Telegram/Discord it can't show live tool progress — it just
sends the final reply with a short "used: …" footer.

---

## What the bot can do

Anything Hearth can: read/write files, run the shell, search the web, drive apps,
generate images, set reminders, run sub-agents. On Telegram and Discord it shows
**live tool calls** as it works (`read_file → web_search → …`), then the answer.
To send you a file, it just names the path — the bridge attaches it. Tool calls
auto-approve on these channels (you're the owner, gated by the allow-list), so
keep your allowed IDs tight and your tokens out of any repo.
