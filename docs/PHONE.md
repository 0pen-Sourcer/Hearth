# Reach Inglen from your phone

Message Inglen from Telegram, Discord or WhatsApp and it runs the full agent on your PC, then
replies, with files, pictures and voice notes going both ways. There is no OAuth, no public server
and no port forwarding. Nothing leaves your machine except through the app you connect.

- **Telegram** is the one to start with. It uses the official bot API, so there is nothing to break.
- **Discord** is also official and safe for your account, and good if you live in Discord.
- **WhatsApp** is experimental. It links a real account through an unofficial library, which
  carries a ban risk, so use a spare number only.

A connected app runs alongside Inglen, starts again on every launch, and stops when Inglen closes.

## Connect an app

Open **Settings, Connectors**, or just ask Inglen to "connect my Telegram" and it walks you through
it in the chat.

**Telegram.** Message **@BotFather** in Telegram, send `/newbot`, follow the prompts and copy the
bot token (it looks like `123456789:ABCdef…`). Paste it in Connectors and press **Save**.

**Discord.** At **discord.com/developers** choose **New Application**, then **Bot**, and copy the
token. No special permissions are needed. Paste it in Connectors and press **Save**, then DM the
bot. In a server you share with it, it answers when you @mention it.

**WhatsApp.** Press **Start** under WhatsApp in Connectors and scan the QR code with WhatsApp
(Settings, Linked Devices, Link a Device). With "also answer my own self-chat" on, you can message
yourself on that number to reach Inglen.

## Letting people in

The first time you message your bot, it replies with an 8-character code. Let yourself in with
that code under **Settings, Connectors** (it lists everyone asking), with `/pair <code>` in the
terminal, or by telling Inglen on the PC. A code lasts an hour. Anyone else who finds your bot gets
a code too, and nothing runs for them unless you let them in. You can remove anyone later from
the same list.

On WhatsApp the bot never replies to people it doesn't know, since that would look like spam on a
personal number. Their request still shows up in Connectors for you to approve.

Letting people in and connecting apps only works from the PC itself, never from a chat app, so
someone you let in can't let in others.

## What it can do from the phone

Anything Inglen can do on the PC. It reads and writes files, runs commands, searches the web,
drives apps, makes images, sets reminders and starts helpers. On Telegram and Discord you watch
the tools run in a message that updates as it works, then get the answer. On WhatsApp the answer
ends with a line saying what was used.

Send a **voice note** and it is written down by the speech model on your PC. Send a **photo or a
file** and Inglen keeps it in your workspace's uploads folder and looks at it. When the reply names
a file, the file is sent back to you.

If a step would ask for your OK on the PC (deleting something, running a command, sending an
email), it asks in the chat instead. Reply **yes** to let it go on, anything else to stop. On the
Auto approval mode it asks only what Auto always asks. Messages you send while it is still working
are handed to the reply in progress.

Each person's conversation is one of your Inglen chats, marked with the app, so you can carry it on
in the window.

## Reminders on your phone

Turn on **Send reminders to my phone too** in Connectors and every reminder also goes to the
Telegram or Discord you connected. Inglen can also send you something on request ("send this to my
Telegram").

## Running a bot without the window

Each app has a small settings file in `~/.hearth` that Connectors writes for you, so on a server
you can write it by hand and start the bot from a terminal.

```
~/.hearth/phone_bridge.json      {"bot_token": "123456:ABC…"}
~/.hearth/discord_bridge.json    {"bot_token": "…"}
~/.hearth/whatsapp_bridge.json   {"allow_self_chat": true}
```

```
python -m hearth.telegram_bridge
python -m hearth.discord_bridge
python -m hearth.whatsapp_bridge
```

A bot started this way thinks with the brain saved in your settings. To skip the code for people
you already know, add their ids to the file (`allowed_chat_ids` for Telegram, `allowed_user_ids`
for Discord, `allowed_numbers` for WhatsApp, written as text).
