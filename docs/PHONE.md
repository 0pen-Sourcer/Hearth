# Reach Hearth from your phone

Two independent, opt-in features:

1. **Telegram bridge** — two-way chat with Hearth from anywhere. Text the bot, it
   runs the full agent on your PC and texts back (files included).
2. **ntfy push** — one-way reminder notifications to your phone, so a reminder
   reaches you even when the PC is asleep or you're away from it.

Neither uses OAuth, neither needs a public server or a port forwarded, and
neither sends your data anywhere except the service you set up yourself.

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
