"""What a model takes in, whether it can reason, and how much it holds.

Each fact comes from where the model is offered when that place says it: the
model file on this PC, the agent's own model list, the provider's list. What
those leave out is looked up in models.dev, the open model catalog OpenCode
reads its own model details from, kept a day. A fact nobody gives is left out
rather than guessed.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

CATALOG_URL = "https://models.dev/api.json"
_CACHE = Path(os.path.expanduser("~/.hearth/models_dev.json"))
_OPENCODE_COPY = Path(os.path.expanduser("~/.cache/opencode/models.json"))
_DAY = 86400

# Hearth's name for a provider or agent, where the catalog uses another.
_CATALOG_ID = {"gemini": "google", "grok": "xai", "claude-code": "anthropic", "codex": "openai"}

_LOCK = threading.Lock()
_INDEX: Dict[str, Dict[str, Dict[str, Any]]] = {}
_LOADED = [0.0]


def _load(fetch: bool = True) -> Dict[str, Any]:
    fresh = _CACHE.is_file() and time.time() - _CACHE.stat().st_mtime < _DAY
    if not fresh and fetch:
        try:
            import urllib.request
            req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "Hearth"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read()
            json.loads(raw)
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE.write_bytes(raw)
        except Exception:
            pass
    for p in (_CACHE, _OPENCODE_COPY):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d:
                return d
        except (OSError, ValueError):
            continue
    return {}


def _index(fetch: bool = True) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """The catalog by provider and model. Without fetch, only what is on disk
    is read, so a chat turn never waits on the network for it."""
    with _LOCK:
        if not _INDEX or (fetch and time.time() - _LOADED[0] > _DAY):
            cat = _load(fetch)
            if cat:
                _INDEX.clear()
                for pid, prov in cat.items():
                    if isinstance(prov, dict):
                        _INDEX[pid.lower()] = {str(k).lower(): v for k, v in (prov.get("models") or {}).items()
                                               if isinstance(v, dict)}
                try:
                    _LOADED[0] = time.time() if fetch else _CACHE.stat().st_mtime
                except OSError:
                    _LOADED[0] = 0.0
        return _INDEX


def catalog_entry(provider: str, model_id: str, fetch: bool = True) -> Optional[Dict[str, Any]]:
    """The catalog's record for a model, by the provider it came from first."""
    idx = _index(fetch)
    mid = (model_id or "").strip().lower()
    if mid.startswith("models/"):
        mid = mid[len("models/"):]
    if not idx or not mid:
        return None
    pid = _CATALOG_ID.get((provider or "").lower(), (provider or "").lower())
    tries = [(pid, mid)]
    if "/" in mid:                       # "vendor/model" as OpenRouter and OpenCode name them
        vendor, rest = mid.split("/", 1)
        tries += [(vendor, rest), (pid, rest), (_CATALOG_ID.get(vendor, vendor), rest)]
    for p, m in tries:
        hit = idx.get(p, {}).get(m)
        if hit:
            return hit
    bare = mid.split("/")[-1]
    hit = next((ms[bare] for ms in idx.values() if bare in ms), None)
    if hit:
        return hit
    # A shorter name for the same model, as agents give them ("sonnet-4.5" for
    # "claude-sonnet-4-5"): the one catalog id that ends with it.
    short = bare.replace(".", "-")
    norm = lambda k: k.split("/")[-1].replace(".", "-")
    found = [(norm(k), v) for p, ms in idx.items() for k, v in ms.items() if norm(k).endswith("-" + short)]
    # Hosts list it under longer names of their own; the plain name is the shortest.
    return min(found, key=lambda f: len(f[0]))[1] if found else None


def reasoning_levels(options: Any) -> Dict[str, Any]:
    """What a catalog entry's reasoning_options say: the effort levels it takes
    (efforts, without "none"), whether reasoning can be switched off (can_off),
    and the value that switches it off (off) when the levels name one. A
    switch beside levels that have no "none" is a separate setting this
    field can't reach, so such a model counts as one that can't be switched off."""
    opts = options if isinstance(options, list) else [options] if isinstance(options, dict) else []
    out: Dict[str, Any] = {}
    toggle = False
    for o in opts:
        if not isinstance(o, dict):
            continue
        if o.get("type") == "effort" and isinstance(o.get("values"), list):
            vals = [str(v).lower() for v in o["values"] if v]
            out["efforts"] = [v for v in vals if v != "none"]
            if "none" in vals:
                out["off"] = "none"
        elif o.get("type") == "toggle":
            toggle = True
    if "efforts" in out or toggle:
        out["can_off"] = "off" in out or (toggle and "efforts" not in out)
    return out


def from_catalog(provider: str, model_id: str, fetch: bool = True) -> Dict[str, Any]:
    e = catalog_entry(provider, model_id, fetch) or {}
    out: Dict[str, Any] = {}
    ins = (e.get("modalities") or {}).get("input")
    if ins:
        out["inputs"] = [str(x) for x in ins]
    if "reasoning" in e:
        out["reasoning"] = bool(e["reasoning"])
    if e.get("reasoning"):
        out.update(reasoning_levels(e.get("reasoning_options")))
    ctx = (e.get("limit") or {}).get("context")
    if isinstance(ctx, int) and ctx > 0:
        out["ctx"] = ctx
    cost = e.get("cost") or {}
    if cost and not any(cost.get(k) for k in ("input", "output")):
        out["free"] = True
    if e.get("name"):
        out["name"] = e["name"]
    return out


def turn_cost(provider: str, model_id: str, use: Dict[str, Any]) -> Optional[float]:
    """What a turn's tokens cost at the catalog's prices (dollars per million),
    or None when the catalog has no price for the model. Cached input is
    charged at its own rate where the catalog lists one."""
    cost = (catalog_entry(provider, model_id) or {}).get("cost") or {}
    rate = lambda k, alt=None: float(cost[k]) if isinstance(cost.get(k), (int, float)) else alt
    pin, pout = rate("input"), rate("output")
    if pin is None and pout is None:
        return None
    pin, pout = pin or 0.0, pout or 0.0
    read, write = int(use.get("cache_read") or 0), int(use.get("cache_write") or 0)
    fresh = max(0, int(use.get("in") or 0) - read - write)
    return (fresh * pin + read * rate("cache_read", pin) + write * rate("cache_write", pin)
            + int(use.get("out") or 0) * pout) / 1e6


def merge(own: Dict[str, Any], provider: str, model_id: str) -> Dict[str, Any]:
    """The source's own facts, with the catalog's filling what they leave out."""
    out = dict(from_catalog(provider, model_id))
    out.update({k: v for k, v in own.items() if v not in (None, "", [])})
    return out


def for_file(path: str) -> Dict[str, Any]:
    """A model file on this PC: what it holds, read from the file itself."""
    from . import llmserver
    out: Dict[str, Any] = {"name": Path(path).stem, "where": "This PC"}
    ctx = llmserver.gguf_context_length(path)
    if ctx:
        out["ctx"] = ctx
    # It sees images only with a vision projector beside it, which is what a load gives it.
    out["inputs"] = ["text", "image"] if llmserver._find_mmproj(path) else ["text"]
    try:
        out["size_gb"] = round(os.path.getsize(path) / 1e9, 2)
    except OSError:
        pass
    return out


def for_agent(agent: str, model_id: str) -> Dict[str, Any]:
    from . import claude_brain
    info = claude_brain.agent_models(agent)
    m = next((x for x in info.get("models") or [] if x.get("id") == model_id), None) or {}
    real = m.get("model") or model_id
    own = {"name": m.get("label") or real, "about": m.get("about") or "", "ctx": m.get("ctx"),
           "efforts": m.get("efforts") or [], "where": info.get("label") or agent}
    if m.get("efforts"):
        own["reasoning"] = True
    if agent == "codex":
        own["inputs"] = _codex_inputs(real)
    out = merge(own, agent, real)
    # The levels are the agent's own; the catalog's for the same model may not be ones it takes.
    out["efforts"] = own["efforts"]
    out.pop("can_off", None)
    out.pop("off", None)
    return out


def _codex_inputs(slug: str):
    try:
        from .codex_brain import _codex_home
        with open(os.path.join(_codex_home(), "models_cache.json"), encoding="utf-8") as f:
            for m in (json.load(f) or {}).get("models") or []:
                if m.get("slug") == slug:
                    return [str(x) for x in m.get("input_modalities") or []]
    except Exception:
        pass
    return []


def for_provider(pid: str, model_id: str) -> Dict[str, Any]:
    from . import headless
    label, base = headless.PROVIDERS.get(pid, (pid, ""))
    own: Dict[str, Any] = {"name": model_id.replace("models/", ""), "where": label}
    listed = headless.listed_facts(base, model_id) if base else {}
    if base and not listed:
        try:
            from .web import _provider_key
            headless.provider_models(base, _provider_key(pid), timeout=10)
            listed = headless.listed_facts(base, model_id)
        except Exception:
            pass
    own.update(listed)
    return merge(own, pid, model_id)
