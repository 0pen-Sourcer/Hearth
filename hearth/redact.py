"""Undercover / public-output redaction.

Hearth runs on the user's own machine and sees their secrets — API keys, home
paths, email, machine name, memory. When it produces output destined for a
PUBLIC surface (a git commit message, a devlog, a file written into a repo that
will be pushed, an issue/PR body), that output must not leak any of it.

`redact_for_public(text)` masks the obvious leaks and returns what it found so a
caller can warn or block. It is conservative: it targets known secret SHAPES
(provider key prefixes, UUID tokens, emails, the user's home dir) rather than
guessing at arbitrary high-entropy strings, so it won't mangle normal prose or
code. Wire it in BEFORE anything goes public:
  - git commit messages / PR bodies
  - devlog text
  - files written under a repo that will be pushed
  - anything sent to an external service as "share this"

Pure-stdlib, no deps. Not a security boundary on its own — a backstop against the
common accidental leaks, paired with .gitignore and the persona's no-personal-
data rule.
"""
from __future__ import annotations

import os
import re
import socket
from typing import Dict, List, Tuple

# Known API-key / token shapes. Each (label, compiled-regex). Order matters:
# more specific prefixes first so they win over the generic UUID/token rules.
_KEY_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("xAI key",        re.compile(r"\bxai-[A-Za-z0-9]{16,}")),
    ("Anthropic key",  re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("OpenAI key",     re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("OpenRouter key", re.compile(r"\bsk-or-[A-Za-z0-9_-]{16,}")),
    ("GitHub token",   re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("GitHub PAT",     re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("HF token",       re.compile(r"\bhf_[A-Za-z0-9]{20,}")),
    ("Slack token",    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("AWS key",        re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer token",   re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}")),
    # UUID (Hackatime/WakaTime API keys, session ids). Generic but a real leak
    # shape; masked last so prefixed keys above match first.
    ("UUID token",     re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
]

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _home_patterns() -> List[Tuple[str, "re.Pattern[str]"]]:
    """Match the user's actual home dir (Windows + POSIX spellings) so paths
    like C:\\Users\\swaggybaba\\... collapse to a neutral placeholder."""
    home = os.path.expanduser("~")
    user = os.path.basename(home.rstrip("\\/")) or ""
    pats: List[Tuple[str, "re.Pattern[str]"]] = []
    if user:
        # C:\Users\<user>  or  /home/<user>  or  /Users/<user>
        pats.append(("home path", re.compile(
            r"(?i)([A-Za-z]:\\Users\\|/home/|/Users/)" + re.escape(user))))
    return pats


def redact_for_public(text: str, *, email: str = "", hostname: str = "",
                      agent_name: str = "") -> Tuple[str, List[Dict[str, str]]]:
    """Mask secrets/PII in `text` for a public surface.

    Returns (redacted_text, findings) where findings is a list of
    {"type": <label>, "sample": <short masked preview>} for what was hit.
    Pass `email`/`hostname` to also scrub specific values you know about;
    otherwise the regex rules still catch generic emails.
    """
    if not text:
        return text, []
    findings: List[Dict[str, str]] = []
    out = text

    def _mask(pattern, label, placeholder):
        nonlocal out
        hits = pattern.findall(out)
        if hits:
            findings.append({"type": label, "count": str(len(hits))})
            out = pattern.sub(placeholder, out)

    # 1) explicit known values first (most reliable)
    if hostname:
        _mask(re.compile(re.escape(hostname)), "machine name", "<HOST>")
    if email:
        _mask(re.compile(re.escape(email)), "email", "<EMAIL>")

    # 2) API keys / tokens
    for label, pat in _KEY_PATTERNS:
        _mask(pat, label, "<REDACTED_KEY>")

    # 3) home paths -> placeholder (keep the rest of the path intact)
    for label, pat in _home_patterns():
        if pat.search(out):
            findings.append({"type": label, "count": "?"})
            out = pat.sub(lambda m: m.group(1) + "<user>", out)

    # 4) any remaining emails
    _mask(_EMAIL, "email", "<EMAIL>")

    # 5) the machine hostname even if not passed (best-effort)
    if not hostname:
        try:
            hn = socket.gethostname()
            if hn and len(hn) > 2:
                _mask(re.compile(r"\b" + re.escape(hn) + r"\b"), "machine name", "<HOST>")
        except Exception:
            pass

    return out, findings


def has_secrets(text: str, **kw) -> bool:
    """True if redaction would change anything (a leak is present)."""
    _, findings = redact_for_public(text, **kw)
    return bool(findings)


if __name__ == "__main__":  # quick self-test
    sample = (
        "Commit by swaggybaba. key xai-fLoJTVfwDug0Hv2StO0FPheapkgjdSukAGB\n"
        "hackatime 2f688dee-b167-48ba-9b96-bb249be0f5f2\n"
        "path C:\\Users\\swaggybaba\\Jarvis\\settings.json email 25mt0633@iitism.ac.in"
    )
    red, found = redact_for_public(sample, email="25mt0633@iitism.ac.in")
    print(red)
    print("FOUND:", found)
