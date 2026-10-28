# Contributing to Inglen

Inglen started as one person's tool and got opened up to the world because the gap between "your model can chat" and "your model can run your machine" is too big to leave unfilled. The faster Inglen generalizes beyond one developer's setup, the better it gets for everyone, so PRs are welcome and triaged seriously.

## TL;DR

1. Open an [Issue](https://github.com/0pen-sourcer/hearth/issues) describing the change, *or* claim an existing one before you start. Saves both of us doing duplicate work.
2. Fork, branch, code, test locally with `python -m hearth.dev_tools`.
3. Open a PR using the [template](.github/PULL_REQUEST_TEMPLATE.md). Small + focused merges fast; sprawling rewrites stall.

Discussion happens in [GitHub Discussions](https://github.com/0pen-sourcer/hearth/discussions).

## The shortlist of where help is most useful

These are the items that genuinely change the experience for everyone. Pick one of these for the fastest "yes, please merge" path.

### 1. Mac / Linux ports

Most of [`hearth/tools.py`](hearth/tools.py) already runs on Linux and macOS: files, shell, screenshots, app launching, windows, the clipboard, web, reminders and notifications. What's still Windows-only is the desktop-control layer (reading a window's controls and clicking them by name), the one-click installer and the packaged build. [`hearth/desktop_a11y.py`](hearth/desktop_a11y.py) already has experimental backends for macOS (the Accessibility API) and Linux (AT-SPI) that haven't been run on real hardware; testing and finishing either would be the biggest single contribution. Search for `sys.platform` to find the Windows branches.

### 2. More personas

[`hearth/persona.py`](hearth/persona.py) is one JARVIS-flavored prompt with a few tone overlays (`HEARTH_PERSONA=bro|chill|professional|formal`). More overlays are welcome: a short block of voice and manner in the same style, ideally paired with a default voice in [`hearth/voice.py`](hearth/voice.py).

### 3. New tools

A tool is one function in [`hearth/tools.py`](hearth/tools.py) (`_my_tool(p: Dict) -> str`) and a definition in `TOOL_DEFINITIONS`. The runtime wires it everywhere (window, terminal, phone bridges, MCP, the dev_tools REPL), and `docs/TOOLS.md` is generated from the definitions (`python scripts/gen_tools_doc.py`). Tool ideas that would help everyone:
- `calendar_*` to read and write the user's Outlook or Google Calendar.
- A Linux or macOS backend for `desktop_snapshot` and `desktop_click`.

### 4. Agents as brains

Any agent that speaks the Agent Client Protocol can already be JARVIS's brain through [`hearth/acp_brain.py`](hearth/acp_brain.py); only OpenCode has been run end to end. If you use Copilot CLI, Goose, Grok Build, Pi, Amp or Antigravity, running it as the brain and reporting what breaks (or fixing its entry in `claude_brain.ACP`) is very useful.

### 5. Share a skill (no code needed)

The highest-leverage contribution isn't even to this repo. A skill is a folder with a `SKILL.md` that teaches Inglen a workflow; see [docs/SKILLS.md](docs/SKILLS.md). Write one that does something useful on a real PC ("archive last month's screenshots", "set up a new Python project"), push it to a repo, and add it to the community index, [awesome-hearth-skills](https://github.com/0pen-sourcer/awesome-hearth-skills). Anyone installs it with `/skill install <you>/<repo>`. The more good skills exist, the more reasons there are to run Inglen.

## What we won't merge

- New backends (cloud APIs) that change the "local-first" promise. Cloud opt-in is fine, default-on isn't.
- Heavy dependencies for niche features. `kokoro-onnx` is ~80MB and earns its place; an electron UI doesn't.
- Refactors without a behavior change unless they unlock something specific. We're not optimizing for a clean codebase, we're optimizing for shipping a useful tool fast.
- Anything that breaks an existing user's setup without a clear migration path.

## Development setup

```powershell
git clone https://github.com/0pen-sourcer/hearth.git
cd hearth
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run the REPL to fire individual tools
python -m hearth.dev_tools

# Run the terminal app (a model in LM Studio or Ollama, a cloud key, or an agent like Claude Code)
.\hearth.bat

# Run the window
python -m hearth.tray --open
```

Smoke test that nothing's broken:

```powershell
python -c "import hearth; print(len(hearth.TOOL_DEFINITIONS), 'tools loaded')"
# prints the tool count, over a hundred

# The tests are plain scripts; each prints its checks and exits non-zero on a failure
python -X utf8 tests/test_team.py
```

## Code style

- Match what's already there. Inglen is pragmatic, not pedantic. Reads like 2026 Python; uses type hints where they help; avoids `class` for things that fit in a function.
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

- Code question: [Discussions](https://github.com/0pen-sourcer/hearth/discussions)
- Bug: [Issue](https://github.com/0pen-sourcer/hearth/issues/new?template=bug_report.md)
- Idea: [Issue](https://github.com/0pen-sourcer/hearth/issues/new?template=feature_request.md)
- Security: see [SECURITY.md](SECURITY.md)
