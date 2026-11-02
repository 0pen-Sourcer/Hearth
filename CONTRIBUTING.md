# Contributing to Inglen

Inglen is MIT licensed and pull requests are welcome. The aim is an assistant that runs well on any Windows PC first and on macOS and Linux next, so anything that makes it work beyond one developer's setup is worth sending.

## Short version

1. Open an [issue](https://github.com/0pen-Sourcer/Hearth/issues/new/choose) describing the change, or comment on an existing one before you start, so two people don't do the same work.
2. Fork, branch, code, and test locally with `python -m hearth.dev_tools`.
3. Open a pull request using the [template](.github/PULL_REQUEST_TEMPLATE.md). Small, focused changes merge fastest.

## Where help is most useful

### 1. macOS and Linux

Most of [`hearth/tools.py`](hearth/tools.py) already runs on Linux and macOS: files, shell, screenshots, app launching, windows, the clipboard, web, reminders and notifications. What's still Windows-only is the desktop-control layer (reading a window's controls and clicking them by name), the one-click installer and the packaged build. [`hearth/desktop_a11y.py`](hearth/desktop_a11y.py) already has experimental backends for macOS (the Accessibility API) and Linux (AT-SPI) that haven't been run on real hardware; testing and finishing either would be the biggest single contribution. Search for `sys.platform` to find the Windows branches.

### 2. More personas

[`hearth/persona.py`](hearth/persona.py) is one prompt with a few tone overlays (`HEARTH_PERSONA=bro|chill|professional|formal`). More overlays are welcome: a short block of voice and manner in the same style, ideally paired with a default voice in [`hearth/voice.py`](hearth/voice.py).

### 3. New tools

A tool is one function in [`hearth/tools.py`](hearth/tools.py) (`_my_tool(p: Dict) -> str`) and a definition in `TOOL_DEFINITIONS`. The runtime wires it everywhere (window, terminal, phone bridges, MCP, the dev_tools REPL), and `docs/TOOLS.md` is generated from the definitions (`python scripts/gen_tools_doc.py`). Tool ideas that would help everyone:

- `calendar_*` to read and write the user's Outlook or Google Calendar.
- A Linux or macOS backend for `desktop_snapshot` and `desktop_click`.

### 4. Agents as brains

Any agent that speaks the Agent Client Protocol can already be the assistant's brain through [`hearth/acp_brain.py`](hearth/acp_brain.py); only OpenCode has been run end to end. If you use Copilot CLI, Goose, Grok Build, Pi, Amp or Antigravity, running it as the brain and reporting what breaks (or fixing its entry in `claude_brain.ACP`) is very useful.

### 5. Share a skill (no code needed)

A skill is a folder with a `SKILL.md` that teaches Inglen a workflow; see [docs/SKILLS.md](docs/SKILLS.md). Write one that does something useful on a real PC ("archive last month's screenshots", "set up a new Python project"), push it to a repo, and add it to the community index, [awesome-hearth-skills](https://github.com/0pen-Sourcer/awesome-hearth-skills). Anyone can then install it with `/skill install <you>/<repo>`.

## What won't be merged

- New backends that change the local-first promise. A cloud option you turn on is fine; one that's on by default isn't.
- Heavy dependencies for niche features. `kokoro-onnx` earns its size; an Electron UI wouldn't.
- Refactors with no change in behavior, unless they unlock something specific.
- Anything that breaks an existing user's setup without a clear migration path.

## Development setup

```powershell
git clone https://github.com/0pen-Sourcer/Hearth.git
cd Hearth
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run the REPL to fire individual tools
python -m hearth.dev_tools

# Run the terminal app (a model in LM Studio or Ollama, a cloud key, or an agent like Claude Code)
.\inglen.bat

# Run the window
.\inglen.bat window
```

The code keeps the project's old name on purpose: the `hearth` package, `HEARTH_*` settings and `hearth.bat` are all current.

Check that nothing is broken:

```powershell
python -c "import hearth; print(len(hearth.TOOL_DEFINITIONS), 'tools loaded')"
# prints the tool count, over a hundred

# The tests are plain scripts; each prints its checks and exits non-zero on a failure
python -X utf8 tests/test_team.py
```

## Code style

- Match what's already there: type hints where they help, and a function rather than a `class` where a function fits.
- One feature per pull request. Small related cleanups can go in the same one.
- Comment the why, not the what, and leave self-explanatory code alone.
- Black and ruff are welcome but not enforced in CI.

## Commit messages

Imperative mood, present tense, one line:

```
add open_url tool
fix grep_search returning empty when path has spaces
docs: clarify Kokoro install steps
```

If a commit needs more context, add a body after a blank line.

## Code of Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Questions

- Using Inglen: [docs/SUPPORT.md](docs/SUPPORT.md)
- Bug: [bug report](https://github.com/0pen-Sourcer/Hearth/issues/new?template=bug_report.yml)
- Idea: [feature request](https://github.com/0pen-Sourcer/Hearth/issues/new?template=feature_request.yml)
- Security: see [SECURITY.md](SECURITY.md)
