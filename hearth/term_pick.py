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
               hint: str = "↑↓ to move · enter to pick · esc to leave") -> Optional[Any]:
    """The value picked, or None when left with Esc."""
    opts: List[Tuple[Any, Any]] = [(v, ANSI(label)) for v, label in options]
    if not opts:
        return None
    if default is not None and all(v != default for v, _ in opts):
        default = None
    app = ChoiceInput(
        message=ANSI(message), options=opts, default=default, style=STYLE, symbol="›",
        bottom_toolbar=ANSI("\x1b[2m " + hint + "\x1b[0m") if hint else None,
        key_bindings=_escape_leaves(),
    )._create_application()
    app.erase_when_done = True          # the list goes; the caller prints what was picked
    try:
        return await app.run_async()
    except (KeyboardInterrupt, EOFError):
        return None
