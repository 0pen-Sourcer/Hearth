---
name: Bug report
about: Something Hearth does that it shouldn't, or doesn't do that it should
title: '[bug] '
labels: bug
assignees: ''
---

## What happened

<!-- A clear, concrete description. "Jarvis tried to open Brave but launched Edge instead" beats "browser stuff is broken". -->

## What you expected

<!-- One line. -->

## Steps to reproduce

1.
2.
3.

## CLI transcript

<!-- Paste the relevant chunk of your Hearth session. Include the user message and any tool calls / responses. Wrap in triple backticks. -->

```
❯ <what you typed>
> <what Hearth said / did>
```

## Activity log

<!-- Tail your activity log if the bug involves a tool call -->

```
type: /log 20  in the Hearth CLI, or:
Get-Content "$HOME\Jarvis\logs\activity.jsonl" -Tail 20
```

## Environment

- Hearth version / commit:
- Python version:
- OS:
- LLM server (LM Studio version / Ollama / other):
- Model loaded:
- Context size:

## Anything else?

<!-- Screenshots, screen recordings, the actual file that broke something, etc. -->
