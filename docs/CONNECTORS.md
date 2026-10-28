# Your own connectors

Inglen answers on Telegram, Discord and WhatsApp out of the box ([PHONE.md](PHONE.md)). Any other
chat app can be added with one small Python file in your workspace's `connectors` folder
(`~/Jarvis/connectors/`). You write how messages come in and go out. Inglen brings the rest, the
same as for Telegram: who may talk (pairing codes), each conversation kept as an Inglen chat, the
"may I" question before a risky step, voice notes written down, files and pictures both ways, and
messages sent mid-reply joining it.

A connector shows up under **Settings, Connectors, Your own connectors**, where **Start** runs it.
From a terminal it runs with `python -m hearth.custom_connector <file name>`. JARVIS can write one
for you too.

## The shape of one

A class that inherits `hearth.channel_core.Adapter` and has

- `name` (a short id, used for its chats and its people list) and `label` (what people see),
- `send(chat, text, reply_to=None)` to post a message, returning its id when the app can edit it
  later,
- optionally `edit(chat, msg_id, text)`, `send_file(chat, path)` and `typing(chat)`, and
  `can_edit = False` when messages can't be edited (the reply then ends with what was used),
- `listen(hub)`, which runs for as long as the connector does and gives each message it receives
  to `hub.receive(chat, sender, text, name="", voice="", files=[])`,
- optionally `allowed`, ids that may talk without a pairing code, and
  `replies_to_strangers = False` when replying to unknown people would look like spam.

`hub.receive` does not wait for the reply; it comes back through your `send`. A file someone sent
is saved with `hearth.channel_core.save_upload(data, name)` and passed in `files` (or `voice` for
a voice note).

## A worked example: notes in a folder

This one answers text files dropped in a folder, for example one synced from your phone with
Syncthing. Write a note on the phone, and the answer appears beside it a moment later.

```python
"""Answer notes dropped in a folder synced from a phone."""
import os
import time

from hearth.channel_core import Adapter

INBOX = os.environ.get("HEARTH_INBOX") or os.path.expanduser("~/Sync/hearth-inbox")


class Folder(Adapter):
    name, label = "folder", "Inbox folder"
    can_edit = False
    allowed = ["me"]                     # the folder is yours: no pairing code

    def send(self, chat, text, reply_to=None):
        with open(os.path.join(INBOX, f"{chat}.reply.txt"), "a", encoding="utf-8") as f:
            f.write(text + "\n\n")

    def listen(self, hub):
        os.makedirs(INBOX, exist_ok=True)
        while True:
            for fn in sorted(os.listdir(INBOX)):
                if fn.endswith(".txt") and not fn.endswith(".reply.txt"):
                    path = os.path.join(INBOX, fn)
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                    os.remove(path)
                    hub.receive(fn[:-4], "me", text)
            time.sleep(2)
```

Save it as `~/Jarvis/connectors/folder.py`, press **Start** next to it in Connectors, and drop
`groceries.txt` with "what's on my list for tomorrow?" in the folder. The answer lands in
`groceries.reply.txt`, and the conversation is in Inglen's chats as "Inbox folder".
