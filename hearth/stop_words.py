"""Whether something the person typed or said while a turn runs tells it to stop.

"Stop", "no, don't do it", "cancel that", "you can stop now", "terminate whatever
you did": the turn halts the way the Stop button halts it, on every surface and
brain (headless.run_once). A correction that names something else ("no, I meant
the other file") is not a stop; it steers the turn as before. Read narrowly, so a
request that only starts with one of these words ("cancel my 3pm meeting", "stop
by the shop") is a request, not a stop.
"""
from __future__ import annotations

import re

# Words said before the stop itself: "no, no, stop", "okay wait, stop it", "actually, never mind".
_LEAD = (r"(?:(?:no|nope|nah|hey|okay|ok|oh|whoa|woah|wait|please|just|bro|man|dude|jarvis|inglen|actually|"
         r"nvm|eh|hmm)[\s,.!?]+)*")
# Letting it go: "leave it", "forget it", "drop it", on its own or with only a word to finish it
# ("leave it for now", "forget it, I'll do it"). "Leave the file as is" is a request, not one of these.
_LETGO = re.compile(
    rf"^{_LEAD}(?:leave (?:it|that|this)(?: alone)?|forget (?:it|that|about it)|drop (?:it|that)|skip (?:it|that)"
    r"|scrap (?:it|that)|let it be|never ?mind|nvm|don'?t bother|no need)"
    r"(?=$|[,.!?;]|\s+(?:for now|then|now|bro|man|dude|please|already|actually|i'?ll|i will|i can|i got it|thanks)\b)",
    re.I)
# A stop word that opens the message, then the end or a word that only finishes it.
_HEAD = re.compile(
    rf"^{_LEAD}(?:stop|halt|abort|cancel|terminate|enough|freeze|hold on|hold up|never ?mind)"
    r"(?=$|[,.!?]|\s+(?:it|that|this|now|everything|all|please|right|there|immediately|whatever|"
    r"what|doing|the|you)\b)", re.I)
# A stop anywhere in the message.
_ANY = re.compile(
    r"\b(?:stop (?:it|now|that|this|everything|doing|right now|there|talking|what you)"
    r"|(?:you can|you should|please|just|now) stop\b"
    r"|don'?t do (?:it|that|this|anything)\b|do not do (?:it|that|this|anything)\b"
    r"|don'?t (?:run|open|delete|touch|send|click|close|launch|continue) (?:it|that|this|anything)\b"
    r"|cancel (?:it|that|this|everything)\b|stop!)", re.I)
# Only "no" or "wait", said again and again.
_ONLY = re.compile(r"^(?:(?:no|nope|wait|stop)[\s,.!?]*)+$", re.I)


def is_stop(text: str) -> bool:
    t = " ".join(str(text or "").replace("’", "'").split()).strip()
    if not t or len(t) > 240:
        return False
    return bool(_ONLY.match(t) or _HEAD.match(t) or _LETGO.match(t) or _ANY.search(t))
