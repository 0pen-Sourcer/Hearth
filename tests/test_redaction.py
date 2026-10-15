"""Masking secrets out of anything headed somewhere public.

The redactor exists so a commit message, a devlog, an issue body or a shared
log does not carry a live key. It only knows the shapes it was taught, and it
had not been taught several that sit on disk on a machine running Hearth: the
Groq key the brain picker can hold, and the Discord and Telegram bot tokens
belonging to bridges that ship in the product. Those went through in full.

The labels matter as much as the masking. What comes back is the list of what
leaked, which is how you know which key to go and rotate, and a key masked
under the wrong provider's name sends you to rotate the wrong one.

Every sample here is synthetic, matched to the documented shape.

    .\\.venv\\Scripts\\python.exe tests\\test_redaction.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import redact as R                                      # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# (provider, a token of that shape, the label it should be reported under)
SAMPLES = [
    ("xAI", "xai-abcdefghijklmnopqrstuvwxyz", "xAI key"),
    ("Anthropic", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "Anthropic key"),
    ("OpenAI", "sk-proj-abcdefghijklmnopqrstuvwxyz012345", "OpenAI key"),
    ("OpenRouter", "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123", "OpenRouter key"),
    ("Google", "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r", "Google API key"),
    ("Google, newer format", "AQ.Ab8RN6KxQ1w2E3r4T5y6U7i8O9p0AsDfGhJkL", "Google API key"),
    ("GitHub", "ghp_abcdefghijklmnopqrstuvwxyz0123", "GitHub token"),
    ("HuggingFace", "hf_abcdefghijklmnopqrstuvwxyzABCD", "HF token"),
    ("AWS", "AKIAIOSFODNN7EXAMPLE", "AWS key"),
    # The ones it did not know, and which a machine running Hearth holds.
    ("Groq", "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", "Groq key"),
    ("Telegram", "7123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
     "Telegram bot token"),
    ("Discord", "MTAxMjM0NTY3ODkwMTIzNDU2.GhIjKl.mNoPqRsTuVwXyZ0123456789abcdef",
     "Discord bot token"),
    ("Google OAuth", "GOCSPX-1a2B3c4D5e6F7g8H9i0JkLmNoPq", "Google OAuth secret"),
    ("Replicate", "r8_ABCdefGHIjklMNOpqrSTUvwxYZ0123456789", "Replicate token"),
    ("Stripe", "sk_live_51HxAbCdEfGhIjKlMnOpQrStUv", "Stripe key"),
    ("npm", "npm_abcdefghijklmnopqrstuvwxyz0123456789", "npm token"),
]

for provider, token, want_label in SAMPLES:
    out, found = R.redact_secrets_only(f"the key is {token} and that is it")
    check(f"a {provider} key does not survive", token not in out)
    labels = [f.get("type") for f in found]
    check(f"and is reported as {want_label!r}"
          + ("" if want_label in labels else f", got {labels}"),
          want_label in labels)

# A whole settings blob, the shape that would end up in a shared bug report.
blob = ('{"provider_keys": {"grok": "xai-abcdefghijklmnopqrstuvwxyz", '
        '"groq": "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", '
        '"openrouter": "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123"}}')
out, found = R.redact_secrets_only(blob)
check("every key in a settings blob is masked, not just the ones it knew first",
      "xai-" not in out and "gsk_" not in out and "sk-or-" not in out)
check("and each is named separately so you know what to rotate",
      len({f.get("type") for f in found}) == 3)

# The docstring promises it targets shapes rather than guessing at entropy, so
# ordinary prose, paths, versions and hashes have to come through untouched.
INNOCENT = [
    "def run(args): return args.get('path')",
    "version 1.2.3 built on 2026-09-24 at 06:30",
    "the file is at ./hearth/tools.py line 4200",
    "run pip install faster-whisper==1.0.3",
    "commit 3923ec4 fixed the search index",
    "http://127.0.0.1:1234/v1 is the local endpoint",
    "a normal sentence about nothing in particular at all",
    "model Ornith-1.5-9B-Q4_K_M.gguf loaded in 51.4s",
    "see https://github.com/example/project/releases/latest",
    "12345678:this-is-not-a-token",
    "Access-Control-Allow-Origin is never set to a wildcard",
]
for text in INNOCENT:
    out, found = R.redact_secrets_only(text)
    check(f"ordinary text is left alone: {text[:42]!r}"
          + ("" if not found else f" -> {out}"), not found and out == text)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all redaction checks passed")
