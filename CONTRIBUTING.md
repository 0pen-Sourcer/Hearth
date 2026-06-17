# Contributing to Hearth

Hearth started as one person's tool and got opened up to the world because the gap between "your model can chat" and "your model can run your machine" is too big to leave unfilled. The faster Hearth generalizes beyond one developer's setup, the better it gets for everyone — so PRs are welcome and triaged seriously.

## TL;DR

1. Open an [Issue](https://github.com/0pen-sourcer/hearth/issues) describing the change, *or* claim an existing one before you start. Saves both of us doing duplicate work.
2. Fork, branch, code, test locally with `python -m hearth.dev_tools`.
3. Open a PR using the [template](.github/PULL_REQUEST_TEMPLATE.md). Small + focused merges fast; sprawling rewrites stall.

Discussion happens in [GitHub Discussions](https://github.com/0pen-sourcer/hearth/discussions). Real-time vibe is in the Discord (link in README once live).

## The shortlist of where help is most useful

These are the items that genuinely change the experience for everyone — pick one of these for the fastest "yes, please merge" path.

### 1. Mac / Linux ports

Most of [`hearth/tools.py`](hearth/tools.py) is cross-platform; the Windows-specific branches are clustered around app launching, registry reads, screenshot capture, and PowerShell defaults. Search for `if platform.system() == "Windows"` and write the POSIX equivalent.

Specific tools that need POSIX work:
- `open_app` — `subprocess.Popen(["open", path])` on Mac, `xdg-open` on Linux
- `list_installed_apps` — `osascript -e 'tell application "System Events" to get the name of every application process'` on Mac
- `screenshot` — `screencapture -t png` on Mac, `grim` / `gnome-screenshot` on Linux
- `run_command` — default to `zsh` on Mac, `bash` on Linux

### 2. More personas

[`hearth/persona.py`](hearth/persona.py) is one JARVIS-flavored prompt. The framework is ready for `hearth/personas/*.py` — a folder of swappable system-prompt modules. Pick a public-domain character (Cortana, a Star-Trek-computer voice, your favorite shōnen mentor) and PR a new persona file. Bonus points if you match it with a matching default voice in [`hearth/voice.py`](hearth/voice.py).

### 3. New tools

The pattern is: one function in [`hearth/tools.py`](hearth/tools.py) (`_my_tool(p: Dict) -> str`) + a definition in `TOOL_DEFINITIONS`. The runtime auto-wires it everywhere (CLI, MCP, dev_tools REPL). Tool ideas that would help everyone:
- `calendar_*` — read/write the user's iCal / Outlook / Google Calendar (oauth flow)
- `notify` — send a desktop notification via win10toast / notify-send / osascript
- `summarize_file` — wrap a longer file in a single LLM call

### 4. Voice presets & wake-words

`/listen on` works but doesn't filter for a wake word. Adding `JARVIS_WAKE_WORD=hey jarvis` (and a fast keyword-spotting layer like Porcupine or [openWakeWord](https://github.com/dscripka/openWakeWord)) would make ambient mode shippable for people whose mic picks up TV.

### 5. Share a skill (no code needed)

The highest-leverage contribution isn't even to this repo. A skill is a folder with a `SKILL.md` that teaches Hearth a workflow — see [docs/SKILLS.md](docs/SKILLS.md). Write one that does something useful on a real PC ("archive last month's screenshots", "set up a new Python project"), push it to a repo, and add it to the community index, [awesome-hearth-skills](https://github.com/0pen-sourcer/awesome-hearth-skills). Anyone installs it with `/skill install <you>/<repo>`. The more good skills exist, the more reasons there are to run Hearth.

## What we won't merge

- New backends (cloud APIs) that change the "local-first" promise. Cloud opt-in is fine, default-on isn't.
- Heavy dependencies for niche features. `kokoro-onnx` is ~80MB and earns its place; an electron UI doesn't.
- Refactors without a behavior change unless they unlock something specific. We're not optimizing for a clean codebase, we're optimizing for shipping a useful tool fast.
- Anything that breaks Ishant's existing setup without a clear migration path. He's user #1.

## Development setup

```powershell
git clone https://github.com/0pen-sourcer/hearth.git
cd hearth
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install openai prompt_toolkit psutil kokoro-onnx sounddevice numpy faster-whisper mcp

# Run the REPL to fire individual tools
python -m hearth.dev_tools

# Run the full CLI (needs LM Studio running with a model loaded)
.\hearth.bat
```

Smoke test that nothing's broken:

```powershell
python -c "import hearth; print(len(hearth.TOOL_DEFINITIONS), 'tools loaded')"
# should print: 92 tools loaded (or more if you've added one)
```

## Code style

- Match what's already there. Hearth is pragmatic, not pedantic. Reads like 2026 Python; uses type hints where they help; avoids `class` for things that fit in a function.
- One feature per PR. Tangentially-related cleanups are fine in the same PR if they're small.
- No comments for the sake of comments. If the code is self-explanatory, leave it. Comment the *why*, not the *what*.
- Black/ruff-clean if you can; we don't enforce it in CI yet.

## Commit messages

Imperative mood, present tense, one line:

```
add open_url tool
fix grep_search returning empty when path has spaces
docs: clarify Kokoro install steps
```

If a commit needs more context, add a body after a blank line.

## Code of Conduct

We don't have a long one. Be normal. Don't be mean to people. Don't ship vibes-only PRs that waste reviewer time. Disagreement is fine; condescension isn't. If you wouldn't want it said to you, don't say it to someone else.

## Questions

- Code question → [Discussions](https://github.com/0pen-sourcer/hearth/discussions)
- Bug → [Issue](https://github.com/0pen-sourcer/hearth/issues/new?template=bug_report.md)
- Idea → [Issue](https://github.com/0pen-sourcer/hearth/issues/new?template=feature_request.md)
- Security → see [SECURITY.md](SECURITY.md)
- Vibes / "wait does X work?" → Discord (link in README once live)
