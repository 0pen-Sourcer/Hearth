"""Image + video generation via OpenAI-compatible providers (xAI Grok Imagine).

Architecture:
  - generate_image: synchronous POST. Returns saved file path immediately.
  - generate_video: async. POSTs the request, returns a task_id. Polling
    happens via check_video_task(task_id). Agent should NOT block on a
    30+ second video; it returns task info and either the user / a
    follow-up tool call retrieves the finished mp4.

Provider awareness:
  - Supports xAI Grok Imagine, OpenAI Images, and Gemini's image API
    (gemini-2.5-flash-image / "nano-banana"). All exposed under one
    `generate_image(prompt, ...)` tool; the provider is detected from
    LOCAL_API_BASE.
  - Anything else surfaces a clear "switch brain to one of: grok / openai
    / gemini" error instead of silently 400-ing.

Files land in WORKSPACE/generated/ with timestamped names so they're easy
to find later. The path is what tools return; downstream renderers
(CLI auto-open, GUI inline render) format based on the marker prefix.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, Optional

from .tools import WORKSPACE


GENERATED_DIR = Path(WORKSPACE) / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

# Per-provider state file for polling tasks across CLI/GUI restarts.
# Lets the user say "is my video ready?" two days later and we still know.
_TASKS_PATH = Path(WORKSPACE) / "cache" / "imagine_tasks.json"
_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

# Map LOCAL_API_BASE prefixes to a provider id. We branch on this for the
# request shape — xAI is OpenAI-compat for /v1/images but uses its own
# /v1/videos/{id} polling endpoint OpenAI doesn't have.
_PROVIDER_HINTS = {
    "api.x.ai":              "xai",
    "generativelanguage":    "gemini",   # NOT supported yet
    "api.openai.com":        "openai",   # supports /v1/images but no /v1/videos
    "openrouter.ai":         "openrouter",  # NOT supported for media
}


def _detect_provider(base: str) -> str:
    b = (base or "").lower()
    for hint, prov in _PROVIDER_HINTS.items():
        if hint in b:
            return prov
    return "unknown"


def _supported_for_images(provider: str) -> bool:
    # xAI + OpenAI use the OpenAI-shape /v1/images/generations endpoint.
    # Gemini ("Nano Banana") uses a completely different shape — it's
    # a chat-style :generateContent call with the image returned inline_data
    # in the response. Handled in _generate_image_gemini() below.
    return provider in ("xai", "openai", "gemini")


def _supported_for_video(provider: str) -> bool:
    # Only xAI's /v1/videos is on the OpenAI-compat surface today.
    # Gemini's video model (Veo) ships through a different beta path and
    # is gated behind a separate quota — defer to v0.7.
    return provider == "xai"


# Gemini model defaults — picked for the price/quality sweet spot. Pro is
# only worth it for production assets, not chat-driven exploration.
_GEMINI_DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"


# ---------------------------------------------------------------------------
# Low-level HTTP helpers (no httpx dependency — keep it stdlib)
# ---------------------------------------------------------------------------

def _post_json(url: str, headers: Dict[str, str], body: Dict[str, Any],
               timeout: float = 120.0) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Pull the body for the error message — providers put the real
        # reason in there (e.g. "model does not support parameter X").
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise RuntimeError(f"HTTP {e.code}: {err_body[:400]}") from None


def _get_json(url: str, headers: Dict[str, str],
              timeout: float = 30.0) -> Dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise RuntimeError(f"HTTP {e.code}: {err_body[:400]}") from None


def _download_to(url: str, dest: Path, timeout: float = 120.0) -> int:
    """Stream a URL into dest atomically via a .part file. Returns bytes."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        with open(tmp, "wb") as f:
            total = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
    tmp.replace(dest)
    return total


# ---------------------------------------------------------------------------
# Task store (JSON file) — keeps async video tasks discoverable across runs
# ---------------------------------------------------------------------------

def _load_tasks() -> Dict[str, Dict[str, Any]]:
    if not _TASKS_PATH.is_file():
        return {}
    try:
        return json.loads(_TASKS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_tasks(tasks: Dict[str, Dict[str, Any]]) -> None:
    try:
        _TASKS_PATH.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    except OSError:
        pass


def _record_task(task: Dict[str, Any]) -> None:
    tasks = _load_tasks()
    tasks[task["task_id"]] = task
    # Cap to most recent 50 so the file doesn't grow unbounded
    if len(tasks) > 50:
        oldest_keys = sorted(tasks.keys(),
                             key=lambda k: tasks[k].get("created_at", 0))[: len(tasks) - 50]
        for k in oldest_keys:
            tasks.pop(k, None)
    _save_tasks(tasks)


# ---------------------------------------------------------------------------
# Filename helper
# ---------------------------------------------------------------------------

def _safe_filename(prompt: str, ext: str) -> str:
    """Slug-ify the first ~5 words of a prompt + timestamp. Keeps file names
    informative without being unwieldy. Always returns a safe ASCII name."""
    words = re.findall(r"[A-Za-z0-9]+", prompt.lower())[:5]
    slug = "-".join(words)[:48] or "image"
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{slug}_{ts}.{ext.lstrip('.')}"


# ---------------------------------------------------------------------------
# IMAGE — synchronous
# ---------------------------------------------------------------------------

def _gemini_image_endpoint(base: str, model: str) -> str:
    """Gemini uses a per-model :generateContent endpoint, not /v1/images.
    The user's `base` might already include /v1beta/openai/ (the OAI-compat
    proxy) — we strip back to the host and re-target the native path."""
    # Normalize: take just scheme://host[/optional v1*]
    b = (base or "").rstrip("/")
    # If base points at the openai-compat shim, walk back to /v1beta
    for tail in ("/v1beta/openai", "/v1beta", "/v1"):
        if b.lower().endswith(tail):
            b = b[: -len(tail)]
            break
    return f"{b}/v1beta/models/{model}:generateContent"


def _generate_image_gemini(prompt: str, base: str, api_key: str,
                            model: Optional[str], n: int,
                            aspect_ratio: str, resolution: str) -> Dict[str, Any]:
    """Gemini's native image generation endpoint. Different shape:
        - POST .../v1beta/models/<model>:generateContent
        - contents[].parts[].text = prompt
        - generationConfig.responseModalities = ["TEXT","IMAGE"]
        - Image returned as inline_data.data (base64) in candidates[0].content.parts
    Doesn't support batch n>1 in one call — we loop for n. Native PNG output."""
    model = model or _GEMINI_DEFAULT_IMAGE_MODEL
    url = _gemini_image_endpoint(base, model)
    # Gemini's imageSize uses bare K/2K/4K (no lowercase "k"), aspect uses same
    # values as xAI (1:1, 16:9, etc.) — just shape into their config block.
    gemini_size = {"1k": "1K", "2k": "2K", "512": "512", "4k": "4K"}.get(
        (resolution or "1k").lower(), "1K"
    )
    body: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt.strip()}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "responseFormat": {
                "image": {"aspectRatio": aspect_ratio, "imageSize": gemini_size}
            },
        },
    }
    # Gemini accepts the key via header `x-goog-api-key` OR `?key=` query
    # param. Header version is cleaner and doesn't leak the key into logs.
    headers = {"x-goog-api-key": api_key}

    paths: list[str] = []
    for i in range(max(1, min(4, int(n)))):
        try:
            resp = _post_json(url, headers, body, timeout=180.0)
        except Exception as e:
            return {"ok": False, "error": f"gemini image gen failed: {e}"}
        # Walk candidates → content.parts[] → find the inline_data
        b64 = None
        try:
            for cand in resp.get("candidates", []):
                for part in (cand.get("content", {}) or {}).get("parts", []):
                    inline = part.get("inline_data") or part.get("inlineData")
                    if inline and inline.get("data"):
                        b64 = inline["data"]
                        break
                if b64:
                    break
        except Exception:
            b64 = None
        if not b64:
            # The model sometimes refuses (safety) and returns text-only. Surface it.
            text_only = ""
            try:
                text_only = resp["candidates"][0]["content"]["parts"][0].get("text", "")[:200]
            except Exception:
                pass
            return {
                "ok": False,
                "error": f"gemini returned no image data{(' — model said: ' + text_only) if text_only else ''}",
            }
        raw = base64.b64decode(b64)
        name = _safe_filename(prompt, "png")
        if n > 1:
            name = name.rsplit(".", 1)[0] + f"_{i + 1}.png"
        dest = GENERATED_DIR / name
        dest.write_bytes(raw)
        paths.append(str(dest))
    return {
        "ok": True, "paths": paths, "count": len(paths),
        "model": model, "provider": "gemini", "prompt": prompt.strip(),
    }


def generate_image(
    prompt: str,
    *,
    base: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    n: int = 1,
    aspect_ratio: str = "1:1",
    resolution: str = "1k",
) -> Dict[str, Any]:
    """Synchronous image generation across providers (xAI, OpenAI, Gemini).
    Returns
        {ok, paths: [str], count, model, provider, prompt}
    or  {ok: False, error}

    Saves files under WORKSPACE/generated/. Each provider has its own shape
    handled internally — caller doesn't have to care which is active."""
    base = base or os.environ.get("LOCAL_API_BASE", "")
    api_key = api_key or os.environ.get("LOCAL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    provider = _detect_provider(base)
    if not _supported_for_images(provider):
        return {
            "ok": False,
            "error": (
                f"Image generation not supported on this endpoint ({base or '(unset)'}). "
                f"Switch to xAI (/brain grok), OpenAI (/brain openai), or Gemini "
                f"(/brain gemini) first."
            ),
        }
    if not api_key:
        return {"ok": False, "error": "Missing API key for image generation."}
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "Empty prompt."}

    # Branch by provider — only xAI + OpenAI share the OpenAI-shape endpoint.
    # Gemini uses native :generateContent with a totally different body.
    if provider == "gemini":
        return _generate_image_gemini(prompt, base, api_key, model, n,
                                       aspect_ratio, resolution)

    # OpenAI default: gpt-image-2 (dropped 2026-04, replaces gpt-image-1 as
    # the current flagship). User can override via tool arg if they want
    # gpt-image-1-mini for cost or a specific date-stamped variant.
    model = model or ("grok-imagine-image-quality" if provider == "xai" else "gpt-image-2")
    body: Dict[str, Any] = {"model": model, "prompt": prompt.strip(),
                            "n": max(1, min(4, int(n))),
                            "response_format": "b64_json"}
    if provider == "xai":
        body["aspect_ratio"] = aspect_ratio
        body["resolution"] = resolution
    elif provider == "openai":
        # OpenAI's gpt-image-1 uses `size`, not aspect_ratio. Translate.
        size = {
            ("1:1", "1k"): "1024x1024", ("1:1", "2k"): "2048x2048",
            ("16:9", "1k"): "1536x1024", ("9:16", "1k"): "1024x1536",
        }.get((aspect_ratio, resolution), "1024x1024")
        body["size"] = size

    headers = {"Authorization": f"Bearer {api_key}"}
    url = base.rstrip("/") + "/images/generations"
    try:
        resp = _post_json(url, headers, body, timeout=180.0)
    except Exception as e:
        return {"ok": False, "error": f"image generation failed: {e}"}

    items = resp.get("data") or []
    if not items:
        return {"ok": False, "error": f"provider returned no images: {str(resp)[:200]}"}

    paths: list[str] = []
    for i, item in enumerate(items):
        b64 = item.get("b64_json")
        url_v = item.get("url")
        suffix = "png"
        if b64:
            raw = base64.b64decode(b64)
            name = _safe_filename(prompt, suffix)
            if len(items) > 1:
                name = name.rsplit(".", 1)[0] + f"_{i + 1}.{suffix}"
            dest = GENERATED_DIR / name
            dest.write_bytes(raw)
            paths.append(str(dest))
        elif url_v:
            name = _safe_filename(prompt, suffix)
            if len(items) > 1:
                name = name.rsplit(".", 1)[0] + f"_{i + 1}.{suffix}"
            dest = GENERATED_DIR / name
            try:
                _download_to(url_v, dest)
                paths.append(str(dest))
            except Exception as e:
                return {"ok": False, "error": f"could not download image {i}: {e}"}
        else:
            return {"ok": False, "error": f"image {i} has neither b64_json nor url: {item}"}

    return {
        "ok": True,
        "paths": paths,
        "count": len(paths),
        "model": model,
        "provider": provider,
        "prompt": prompt.strip(),
    }


# ---------------------------------------------------------------------------
# VIDEO — asynchronous
# ---------------------------------------------------------------------------

_IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}


def _resolve_image_input(image: str) -> Optional[str]:
    """Turn an image reference into something the video API accepts.

    - already a URL or data URI -> passed through unchanged
    - a local file path -> read + base64-encoded as a data URI
    - missing file -> None (caller surfaces a clean error)
    """
    s = (image or "").strip().strip('"').strip("'")
    if not s:
        return None
    if s.startswith(("http://", "https://", "data:")):
        return s
    p = Path(s)
    if not p.is_absolute():
        # let a bare filename resolve against the generated/ folder too
        cand = GENERATED_DIR / s
        p = cand if cand.exists() else p
    if not p.is_file():
        return None
    # Downscale best-effort so the inline base64 stays small enough for the
    # provider's request-size cap (a full 3-4 MB still encodes to ~5 MB). Falls
    # back to the raw bytes if Pillow isn't available.
    try:
        from PIL import Image
        import io as _io
        im = Image.open(p).convert("RGB")
        im.thumbnail((1280, 1280), Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        mime = _IMG_MIME.get(p.suffix.lower(), "image/png")
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"


def start_video(
    prompt: str,
    *,
    base: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    image_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Kick off a video generation. Returns
        {ok, task_id, model, provider, prompt, status: 'pending'}
    or  {ok: False, error}

    Polling: call check_video_task(task_id) until status='done', then the
    response includes 'path' = saved local mp4."""
    base = base or os.environ.get("LOCAL_API_BASE", "")
    api_key = api_key or os.environ.get("LOCAL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    provider = _detect_provider(base)
    if not _supported_for_video(provider):
        return {
            "ok": False,
            "error": (
                f"Video generation not supported on this endpoint ({base or '(unset)'}). "
                f"Only xAI's video API is wired today — switch with /brain grok "
                f"and retry. Gemini's Veo + OpenAI Sora can be added later "
                f"(both are on the OpenAI-compatible-ish surface)."
            ),
        }
    if not api_key:
        return {"ok": False, "error": "Missing API key for video generation."}
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "Empty prompt."}
    duration = max(1, min(15, int(duration)))

    model = model or "grok-imagine-video"
    body: Dict[str, Any] = {
        "model": model,
        "prompt": prompt.strip(),
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    if image_url:
        # image-to-video: the provider wants a URL or a base64 data URI, NOT a
        # bare local path. When the agent passes the just-generated image's
        # local path (the common "now animate it" case), encode it inline so it
        # works instead of erroring with "image_url expects a real URL".
        img_payload = _resolve_image_input(image_url)
        if img_payload is None:
            return {"ok": False, "error": f"image not found for animation: {image_url}"}
        # xAI's video API wants the field `image_url` (a URL or base64 data URI),
        # NOT `image` — the latter 422s "failed to deserialize". Verified live.
        body["image_url"] = img_payload

    headers = {"Authorization": f"Bearer {api_key}"}
    url = base.rstrip("/") + "/videos/generations"
    try:
        resp = _post_json(url, headers, body, timeout=60.0)
    except Exception as e:
        return {"ok": False, "error": f"video generation start failed: {e}"}

    task_id = resp.get("request_id") or resp.get("id")
    if not task_id:
        return {"ok": False, "error": f"no task id in response: {str(resp)[:200]}"}

    task = {
        "task_id": task_id,
        "kind": "video",
        "provider": provider,
        "model": model,
        "prompt": prompt.strip(),
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "base": base.rstrip("/"),
        "created_at": time.time(),
        "status": "pending",
    }
    _record_task(task)
    return {
        "ok": True,
        "task_id": task_id,
        "status": "pending",
        "model": model,
        "provider": provider,
        "prompt": prompt.strip(),
        "hint": (
            f"Video typically takes 20-60 seconds. Check progress with "
            f"check_video_task('{task_id}'). The video will save to "
            f"{GENERATED_DIR}/ when done."
        ),
    }


def check_video_task(task_id: str, *, api_key: Optional[str] = None,
                     auto_download: bool = True) -> Dict[str, Any]:
    """Poll a video task ONCE. Does not block. Returns
        {ok, task_id, status, path?, url?, error?}
    where status ∈ {pending, done, expired, failed, unknown}.
    If status=='done' and auto_download (default), the mp4 is downloaded to
    WORKSPACE/generated/ and 'path' is set."""
    tasks = _load_tasks()
    task = tasks.get(task_id)
    if not task:
        # The agent sometimes loses or fabricates the id (it did, on a real run).
        # Fall back to the most recent video task on file so "is it done?" still
        # resolves to the thing that's actually cooking.
        vids = sorted((t for t in tasks.values() if t.get("kind") == "video"),
                      key=lambda t: t.get("created_at", 0), reverse=True)
        if vids:
            task = vids[0]
            task_id = task["task_id"]
        else:
            return {"ok": False, "error": f"no known task with id {task_id}", "status": "unknown"}

    if task.get("status") == "done" and task.get("path") and os.path.isfile(task["path"]):
        return {"ok": True, "task_id": task_id, "status": "done",
                "path": task["path"], "url": task.get("url")}

    api_key = api_key or os.environ.get("LOCAL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        return {"ok": False, "error": "Missing API key.", "status": "unknown"}
    base = task.get("base") or os.environ.get("LOCAL_API_BASE", "").rstrip("/")
    if not base:
        return {"ok": False, "error": "No base URL on file for this task.", "status": "unknown"}

    poll_url = f"{base}/videos/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = _get_json(poll_url, headers, timeout=20.0)
    except Exception as e:
        return {"ok": False, "task_id": task_id, "status": "unknown", "error": f"poll failed: {e}"}

    status = (resp.get("status") or "").lower() or "unknown"
    task["status"] = status
    out: Dict[str, Any] = {"ok": True, "task_id": task_id, "status": status}

    if status == "done":
        video = resp.get("video") or {}
        video_url = video.get("url")
        if video_url:
            out["url"] = video_url
            task["url"] = video_url
            if auto_download:
                name = _safe_filename(task.get("prompt", "video"), "mp4")
                dest = GENERATED_DIR / name
                try:
                    _download_to(video_url, dest, timeout=180.0)
                    out["path"] = str(dest)
                    task["path"] = str(dest)
                except Exception as e:
                    out["error"] = f"download failed: {e}"
        out["duration"] = video.get("duration")
    elif status in ("failed", "expired"):
        out["error"] = f"task ended with status={status}"

    tasks[task_id] = task
    _save_tasks(tasks)
    return out


def list_recent_tasks(limit: int = 10) -> Dict[str, Any]:
    """Recent generation tasks — useful for `is my video done yet?` queries."""
    tasks = _load_tasks()
    items = sorted(tasks.values(), key=lambda t: -t.get("created_at", 0))[:limit]
    return {"ok": True, "count": len(items), "tasks": items}
