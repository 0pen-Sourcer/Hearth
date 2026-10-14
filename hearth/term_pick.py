"""Arrow-key pickers for the terminal: up/down (or the number) and Enter, Esc to leave."""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts.choice_input import ChoiceInput
from prompt_toolkit.styles import Style

STYLE = Style.from_dict({
    "selected-option": "fg:#ffaf00 bold",
    "number": "fg:#808080",
    "bottom-toolbar": "noreverse fg:#808080",
})


def _escape_leaves() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _(event):
        event.app.exit(result=None)
    return kb


async def pick(message: str, options: Sequence[Tuple[Any, str]], default: Any = None,
               hint: str = "↑↓ enter · esc leaves") -> Optional[Any]:
    """The value picked, or None when left with Esc."""
    opts: List[Tuple[Any, Any]] = [(v, ANSI(label)) for v, label in options]
    if not opts:
        return None
    import sys
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
