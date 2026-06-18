# Reach Hearth from your phone

Opt-in features:

1. **Telegram bridge** — two-way chat from anywhere. Text the bot, it runs the
   full agent on your PC and texts back (files included). *Recommended* —
   official API, nothing to break.
2. **Discord bridge** — two-way chat via a Discord bot (DM it or @mention it).
   Also official + ban-free; good if you live in Discord.
3. **ntfy push** — one-way reminder notifications to your phone, so a reminder
   reaches you even when the PC is asleep.
4. **WhatsApp bridge** — two-way over WhatsApp. *Experimental* — an unofficial
   link to a real account (ban risk; spare number only). See its section first.

None use OAuth, none need a public server or a port forwarded, and none send your
data anywhere except the service you set up yourself.

---

## 1. Telegram bridge (two-way)

You message a private Telegram bot; it relays to Hearth running on your PC, which
replies with the answer and any file it produced. Only chat IDs you allow are
answered — everyone else is ignored.

### Setup (~2 minutes)

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → copy the
   **bot token** it gives you (looks like `123456789:ABCdef...`).
2. Message your new bot once (say anything) so it has a chat with you.
3. Find your numeric **chat id**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and look for
   `"chat":{"id":...}`. (Or just run the bridge below — it logs the chat id of
   anyone who messages it.)
4. Create `~/.hearth/phone_bridge.json`:

   ```json
   {
     "bot_token": "123456789:ABCdef...",
     "allowed_chat_ids": [123456789],
     "ntfy_topic": "hearth-pick-something-random"
   }
   ```

5. Point it at a model the same way the CLI does (it defaults to a local LM Studio
   server), then run:

   ```powershell
   python -m hearth.telegram_bridge
   ```

   Leave it running. Now text your bot from your phone.

### Notes

- The bridge auto-approves tool calls — the chat-id allowlist is the security
  boundary, so keep `allowed_chat_ids` tight and the token out of any repo.
- Replies longer than Telegram's limit are split automatically; files the agent
  creates (a PDF, a screenshot) are sent back as attachments.
- It keeps a short rolling history per chat, so follow-up messages have context.
- This is a single-owner bridge, not a multi-user gateway.

---

## 1b. Discord bridge (two-way, official)

DM a Discord bot (or @mention it in a server) and it relays to Hearth, which
replies with the answer + any file it produced. Official bot API — no ban risk,
nothing that breaks on a protocol change.

### Setup (~3 minutes)

1. `pip install discord.py` (optional dep, off by default).
2. At <https://discord.com/developers/applications> → New Application → **Bot**,
   copy the token, and under **Privileged Gateway Intents** enable
   **MESSAGE CONTENT INTENT** (needed to read message text).
3. Invite the bot to a server you own, or just DM it.
4. Get your numeric user id: Discord Settings → Advanced → Developer Mode on,
   then right-click your name → Copy User ID.
5. Create `~/.hearth/discord_bridge.json`:

   ```json
   {"bot_token": "...", "allowed_user_ids": [123456789], "ntfy_topic": ""}
   ```

6. Point it at a brain (same env as the CLI) and run:

   ```powershell
   python -m hearth.discord_bridge
   ```

Only `allowed_user_ids` are answered. In a server it responds when @mentioned; in
a DM it always responds. Run status: `/phone`.

---

## 2. ntfy push (reminders to your phone)

[ntfy.sh](https://ntfy.sh) is a free, no-account push service. Pick a hard-to-guess
topic name and Hearth will POST reminders to it; the ntfy app on your phone
(subscribed to the same topic) shows them as notifications.

### Setup (~1 minute)

1. Install the **ntfy** app on your phone (Android / iOS) or open
   [ntfy.sh](https://ntfy.sh) in a browser.
2. Subscribe to a topic name only you know, e.g. `hearth-7f3a9c2`.
3. Tell Hearth the topic — either in `phone_bridge.json` above (`ntfy_topic`), or
   as an environment variable before launching:

   ```powershell
   $env:HEARTH_NTFY_TOPIC = "hearth-7f3a9c2"
   .\hearth.bat
   ```

Now every reminder that fires also pushes to your phone. To use a self-hosted ntfy
server instead of the public one, set `HEARTH_NTFY_SERVER` to its base URL.

### Privacy

The reminder text is sent to the ntfy server you choose. The topic name is the
only thing protecting it, so make it long and random — anyone who knows the topic
can read messages posted to it. Self-host ntfy if you want full control.

---

## 3. WhatsApp bridge (two-way, experimental)

Chat with Hearth over WhatsApp. It links a WhatsApp account as a companion device
(the same "Linked Devices" mechanism as WhatsApp Web) using [`neonize`](https://pypi.org/project/neonize/)
— pure Python, **no Node, no Chromium**. You scan a QR once; the session persists.

> ⚠ **Read before using.** WhatsApp has no bot API for personal accounts, so this
> drives a *real* account through an unofficial protocol. Meta's anti-automation
> ML watches for "too-perfect bot" patterns (instant replies, zero typing delay,
> 24/7 presence) and **can ban the number** — for occasional personal use behind
> an allowlist the risk is low but **not zero, so use a spare / secondary number,
> not your primary.** It can also break when WhatsApp changes their protocol
> (then `pip install -U neonize` and re-pair). **Telegram is the recommended
> channel** — official, ban-free, and you'll be able to test it in a minute. Use
> WhatsApp only if you specifically need it and have a number you can risk.

### Setup

1. `pip install neonize` (pulls in `segno` for the QR). It's an optional,
   experimental dependency — not installed by default. WhatsApp is currently a
   **run-from-source** feature — start it from a clone, not the packaged .exe.
2. Create `~/.hearth/whatsapp_bridge.json`:

   ```json
   {
     "allowed_numbers": ["<your number, digits only, country code, no +>"],
     "allow_self_chat": true,
     "ntfy_topic": ""
   }
   ```

   `allowed_numbers` is who may talk to it (you). `allow_self_chat` lets you
   message the linked number's own chat.
3. Point it at a brain (same `LOCAL_API_BASE` / `LOCAL_API_KEY` / `LOCAL_MODEL`
   env the CLI uses), then run:

   ```powershell
   python -m hearth.whatsapp_bridge
   ```

4. Scan the QR it prints: WhatsApp → **Settings → Linked Devices → Link a Device**.
   The session is saved under `~/.hearth/whatsapp/` and survives restarts.

Like the Telegram bridge it's single-owner: the allowlist is the gate and tool
calls auto-approve (you're the owner). Group messages are ignored. Check status
anytime with `/phone`.
