"""The marks of the agent programs, model providers and chat apps Hearth works
with: each brand's own app icon, from the address its site declares, fetched
once and kept beside the favicons. Shown only as the mark of a tool Hearth
works with. A brand whose address stops answering falls back to its site's
favicon."""
from __future__ import annotations

import os
import time
import urllib.request
from typing import Optional, Tuple

# key -> (icon address, site whose favicon stands in)
ICONS = {
    "claude-code": ("https://claude.ai/apple-touch-icon.png", "claude.com"),
    "codex": ("https://cdn.oaistatic.com/assets/apple-touch-icon-mz9nytnj.webp", "chatgpt.com"),
    "cursor-agent": ("https://cursor.com/marketing-static/favicon-light.svg", "cursor.com"),
    "opencode": ("https://opencode.ai/apple-touch-icon-v3.png", "opencode.ai"),
    "gemini-cli": ("https://geminicli.com/icon.png", "gemini.google.com"),
    "copilot": ("https://github.githubassets.com/favicons/favicon.svg", "github.com"),
    "antigravity": ("https://antigravity.google/favicon.svg", "antigravity.google"),
    "grok": ("https://x.ai/favicon.ico", "x.ai"),
    "kimi": ("https://www.kimi.com/pwa-192.png", "kimi.com"),
    "qwen-code": ("https://qwenlm.github.io/favicon.png", "qwen.ai"),
    "crush": ("https://charm.land/touchicon_120w.cb99943f6c77d35b.png", "charm.sh"),
    "goose": ("https://goose-docs.ai/img/favicon.ico", "goose-docs.ai"),
    "aider": ("https://aider.chat/assets/icons/apple-touch-icon.png", "aider.chat"),
    "droid": ("https://factory.com/favicon.svg", "factory.ai"),
    "amp": ("https://ampcode.com/app-icon.svg?v=4", "ampcode.com"),
    "pi": ("https://pi.dev/favicon.svg", "pi.dev"),
    "cline": ("https://cline.bot/assets/branding/favicons/favicon-256x256.png", "cline.bot"),
    "continue": ("https://continue.dev/favicon.png", "continue.dev"),
    "amazon-q": ("https://a0.awsstatic.com/libra-css/images/site/touch-icon-ipad-144-smile.png", "aws.amazon.com"),
    "kiro": ("https://kiro.dev/icon.svg", "kiro.dev"),
    "kilo": ("https://kilo.ai/favicon/favicon.svg?v=2", "kilo.ai"),
    "augment": ("https://www.augmentcode.com/favicon.svg", "augmentcode.com"),
    "openhands": ("https://www.openhands.dev/favicon.svg", "all-hands.dev"),
    # model providers, by their id in Hearth's provider table
    "anthropic": ("https://cdn.prod.website-files.com/67ce28cfec624e2b733f8a52/67d31dd7aa394792257596c5_webclip.png",
                  "anthropic.com"),
    "openai": ("https://cdn.oaistatic.com/assets/apple-touch-icon-mz9nytnj.webp", "openai.com"),
    "gemini": ("https://www.gstatic.com/lamda/images/gemini_sparkle_aurora_33f86dc0c0257da337c63.svg",
               "gemini.google.com"),
    "xai": ("https://x.ai/favicon.ico", "x.ai"),
    "openrouter": ("https://openrouter.ai/apple-touch-icon.png", "openrouter.ai"),
    "groq": ("https://groq.com/favicon.svg", "groq.com"),
    # chat apps
    "telegram": ("https://telegram.org/img/website_icon.svg?4", "telegram.org"),
    "whatsapp": ("https://static.whatsapp.net/rsrc.php/y1/r/FJbTMJqMap7.svg", "whatsapp.com"),
    "discord": ("", "discord.com"),
}
ALIASES = {"google": "gemini", "cursor": "cursor-agent", "claude": "claude-code"}


def key_for(name: str) -> str:
    k = (name or "").strip().lower()
    k = ALIASES.get(k, k)
    return k if k in ICONS else ""


def brand_icon(name: str) -> Optional[Tuple[bytes, str]]:
    """(bytes, mime) of a brand's mark, or None."""
    from .web import _FAVICON_DIR, _ICON_MAX, _favicon, _icon_mime
    key = key_for(name)
    if not key:
        return None
    url, site = ICONS[key]
    os.makedirs(_FAVICON_DIR, exist_ok=True)
    path = os.path.join(_FAVICON_DIR, "brand-" + key)
    miss = path + ".miss"
    if os.path.isfile(path):
        with open(path, "rb") as f:
            data = f.read()
        return data, _icon_mime(data)
    if url and not (os.path.isfile(miss) and time.time() - os.path.getmtime(miss) < 86400):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Hearth"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = r.read(_ICON_MAX + 1)
            if 64 < len(data) <= _ICON_MAX and _icon_mime(data) != "application/octet-stream":
                with open(path, "wb") as f:
                    f.write(data)
                return data, _icon_mime(data)
        except Exception:
            pass
        open(miss, "w").close()
    return _favicon(site) if site else None
