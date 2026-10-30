---
name: Bug report
about: Something Inglen does that it shouldn't, or doesn't do that it should
title: '[bug] '
labels: bug
assignees: ''
---

## What happened

<!-- A clear, concrete description. "It tried to open Brave but launched Edge instead" beats "browser stuff is broken". -->

## What you expected

<!-- One line. -->

## Steps to reproduce

1.
2.
3.

## What you saw

<!-- Paste the relevant part of the chat or terminal session, or a screenshot. Include your message and any tool calls and their results. Wrap text in triple backticks. -->

```
❯ <what you typed>
> <what Inglen said / did>
```

## Activity log

<!-- Tail your activity log if the bug involves a tool call -->

```
type: /log 20  in the Inglen terminal, or:
Get-Content "$HOME\Inglen\logs\activity.jsonl" -Tail 20
(an install from before the rename keeps $HOME\Jarvis instead)
```

## Environment

- Inglen version (Settings, About) or commit:
- Installer (Full or Lite) or from source:
- OS:
- Brain (this PC's model with LM Studio / Ollama / Inglen's engine, a cloud provider, or an agent like Claude Code or Codex):
- Model:
- Approval mode (Ask, Auto-edit, Auto):

## Anything else?

<!-- Screenshots, screen recordings, the actual file that broke something, etc. -->
