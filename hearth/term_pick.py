"""Arrow-key pickers for the terminal: up/down (or the number) and Enter, Esc to leave."""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

# Without prompt_toolkit (a build that doesn't carry it, or an older one
# without ChoiceInput) the picker is a numbered list and a typed number.
try:
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.shortcuts.choice_input import ChoiceInput
    from prompt_toolkit.styles import Style
    HAVE_PT = True
except Exception:
    HAVE_PT = False

from .term_render import look_grad, xterm_hex  # noqa: E402

STYLE = Style.from_dict({
    "selected-option": f"fg:{xterm_hex(look_grad()[4])} bold",
    "number": "fg:#808080",
    "bottom-toolbar": "noreverse fg:#808080",
}) if HAVE_PT else None


async def _numbered(message: str, options: Sequence[Tuple[Any, str]], default: Any) -> Optional[Any]:
    import asyncio
    print(message)
    for i, (v, label) in enumerate(options, 1):
        print(f"  {i:>2}. {label}" + ("   (now)" if default is not None and v == default else ""))
    try:
        got = (await asyncio.to_thread(input, "  number, or Enter to leave: ")).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if got.isdigit() and 1 <= int(got) <= len(options):
        return options[int(got) - 1][0]
    return None


def _escape_leaves():
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _(event):
        event.app.exit(result=None)
    return kb


async def pick(message: str, options: Sequence[Tuple[Any, str]], default: Any = None,
               hint: str = "↑↓ enter · esc leaves") -> Optional[Any]:
    """The value picked, or None when left with Esc."""
    if not options:
        return None
    import sys
    if not HAVE_PT and sys.stdin.isatty():
        return await _numbered(message, options, default)
    if not HAVE_PT:
        print(message)
        for i, (_v, label) in enumerate(options, 1):
            print(f"  {i:>2}. {label}")
        return None
    opts: List[Tuple[Any, Any]] = [(v, ANSI(label)) for v, label in options]
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        # Piped or scripted: no screen to pick on, so the list is printed.
        print(message)
        for i, (_v, label) in enumerate(options, 1):
            print(f"  {i:>2}. {label}")
        return None
    if default is not None and all(v != default for v, _ in opts):
        default = None
    # The hint rides on the title line: a toolbar under the list has no height
    # limit and pushes itself to the bottom of a tall terminal.
    app = ChoiceInput(
        message=ANSI(message + ("   \x1b[2m" + hint + "\x1b[0m" if hint else "")), options=opts,
        default=default, style=STYLE, symbol="›", key_bindings=_escape_leaves(),
    )._create_application()
    app.erase_when_done = True          # the list goes; the caller prints what was picked
    # Nothing stretches, so the hint sits right under the list instead of at
    # the bottom of a tall terminal.
    from prompt_toolkit.filters import to_filter
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.containers import to_container

    def hold(c):
        if isinstance(c, Window):
            c.dont_extend_height = to_filter(True)
        kids = list(c.get_children())
        alt = getattr(c, "alternative_content", None)      # the branch shown without a frame
        if alt is not None:
            kids.append(to_container(alt))
        for k in kids:
            hold(k)
    hold(app.layout.container)
    try:
        return await app.run_async()
    except (KeyboardInterrupt, EOFError):
        return None
