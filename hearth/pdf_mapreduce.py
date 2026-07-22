"""Map-reduce summarization for very large PDFs.

`read_file` extracts a whole PDF as one string — fine for 30 pages, useless
for a 500-page book that won't fit any local model's context. This module
splits the PDF into page windows, summarizes each window with a cheap LLM
call (the same cost-class routing subagents use, so map-reduce of 50 chunks
stays on local even when the parent is on a cloud brain), then reduces the
per-chunk summaries into one structured overview.

Public surface (called from hearth/tools.py's read_pdf_large handler):
  run_map_reduce(path, chunk_pages, focus, summarize_fn=None) -> str
  run_in_background(path, chunk_pages, focus) -> dict   (returns a job_id)

Caching: extracted page text + per-chunk summaries are cached at
<WORKSPACE>/cache/pdf/<sha1(path+mtime+chunk_pages)>.json so a re-ask doesn't
re-extract or re-summarize. The cache key folds in mtime so editing the PDF
invalidates it, and chunk_pages so changing the window re-chunks.

`summarize_fn` is injectable so tests can pass an offline stub (echo) and
verify chunking + reduce wiring without a live LLM.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .tools import WORKSPACE

# Bound fan-out so map-reduce of a 500-page book doesn't fire 40 concurrent
# requests at one local LLM (which would thrash VRAM and serialize anyway).
_MAX_CONCURRENCY = 4
# Per-chunk text cap fed to the summarizer — keep each map call tight so it
# fits a small context with room for the prompt + response.
_CHUNK_TEXT_CAP = 12000

_CACHE_DIR = Path(WORKSPACE) / "cache" / "pdf"


# ---------------------------------------------------------------------------
# PDF text extraction — fitz (pymupdf) preferred, pypdfium2 then pypdf fallback
# ---------------------------------------------------------------------------

def _open_pages(path: str) -> Tuple[Callable[[int], str], int, str]:
    """Return (get_text(page_index)->str, n_pages, backend_name).

    Prefers fitz/pymupdf (fast + accurate), falls back to pypdfium2, then
    pypdf. Raises RuntimeError if none are importable / the file won't open.
    The returned getter is 0-indexed.
    """
    # 1) fitz / pymupdf
    try:
        import fitz  # type: ignore
        doc = fitz.open(path)
        n = doc.page_count

        def _get(i: int, _doc=doc) -> str:
            try:
                return _doc[i].get_text() or ""
            except Exception:
                return ""

        return _get, n, "fitz"
    except ImportError:
        pass
    except Exception:
        # fitz present but file failed — let pypdfium2 try before giving up.
        pass

    # 2) pypdfium2
    try:
        import pypdfium2 as pdfium  # type: ignore
        doc = pdfium.PdfDocument(path)
        n = len(doc)

        def _get(i: int, _doc=doc) -> str:
            try:
                tp = _doc[i].get_textpage()
                txt = tp.get_text_range() or ""
                tp.close()
                return txt
            except Exception:
                return ""

        return _get, n, "pypdfium2"
    except ImportError:
        pass
    except Exception:
        pass

    # 3) pypdf
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(path)
        n = len(reader.pages)

        def _get(i: int, _r=reader) -> str:
            try:
                return _r.pages[i].extract_text() or ""
            except Exception:
                return ""

        return _get, n, "pypdf"
    except ImportError:
        raise RuntimeError(
            "No PDF backend available. Install one: pip install pymupdf "
            "(or pypdfium2, or pypdf).")
    except Exception as e:
        raise RuntimeError(f"Could not open PDF {path}: {type(e).__name__}: {e}")


def _chunk_windows(n_pages: int, chunk_pages: int) -> List[Tuple[int, int]]:
    """Return [(start_page, end_page), ...] as 1-based inclusive windows."""
    chunk_pages = max(1, int(chunk_pages))
    out: List[Tuple[int, int]] = []
    p = 1
    while p <= n_pages:
        end = min(n_pages, p + chunk_pages - 1)
        out.append((p, end))
        p = end + 1
    return out


def _extract_chunks(path: str, chunk_pages: int) -> Tuple[List[Dict], int, str]:
    """Extract every page window's text. Returns (chunks, n_pages, backend).
    Each chunk: {start, end, text}."""
    get_text, n_pages, backend = _open_pages(path)
    chunks: List[Dict] = []
    for (start, end) in _chunk_windows(n_pages, chunk_pages):
        parts: List[str] = []
        for pi in range(start - 1, end):
            t = (get_text(pi) or "").strip()
            if t:
                parts.append(f"[page {pi + 1}]\n{t}")
        text = "\n\n".join(parts)[:_CHUNK_TEXT_CAP]
        chunks.append({"start": start, "end": end, "text": text})
    return chunks, n_pages, backend


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def _cache_key(path: str, chunk_pages: int) -> str:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    raw = f"{os.path.abspath(path)}|{mtime}|cp{chunk_pages}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.json"


def _load_cache(key: str) -> Optional[Dict]:
    p = _cache_path(key)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(key: str, data: Dict) -> None:
    try:
        _cache_path(key).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# LLM call — single-shot, cost-class 'cheap' (forces local on a cloud parent)
# ---------------------------------------------------------------------------

def _llm_complete(prompt: str, *, temperature: float = 0.3,
                  max_tokens: int = 700) -> str:
    """One non-streaming chat completion against the cheap-class endpoint
    (local when the parent is on cloud — see subagents._route_for_cost_class).
    Returns the assistant text, or a '[map-reduce LLM error: ...]' marker so
    the reduce step can still proceed and flag the gap."""
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return "[map-reduce LLM error: openai package not installed]"
    try:
        from .subagents import _route_for_cost_class
        base, key, model = _route_for_cost_class("cheap")
    except Exception:
        base = os.environ.get("LOCAL_API_BASE", "http://localhost:1234/v1")
        key = os.environ.get("LOCAL_API_KEY", "") or "hearth-builtin"
        model = os.environ.get("LOCAL_MODEL", "")
    try:
        client = OpenAI(base_url=base, api_key=key or "hearth-builtin", timeout=180.0)
        if not model:
            try:
                for m in (client.models.list().data or []):
                    if getattr(m, "id", ""):
                        model = m.id
                        break
            except Exception:
                pass
        if not model:
            return f"[map-reduce LLM error: no model loaded at {base}]"
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"[map-reduce LLM error: {type(e).__name__}: {e}]"


def _default_summarize(title: str, start: int, end: int, text: str,
                       focus: str = "") -> str:
    """Map step: summarize one page window. Tight prompt, names/dates/numbers
    preserved verbatim."""
    if not text.strip():
        return f"(pages {start}-{end}: no extractable text — blank or scanned)"
    focus_line = (f" Pay special attention to: {focus}." if focus else "")
    prompt = (
        f"Summarize pages {start}-{end} of \"{title}\" in about 150 words."
        f"{focus_line} Preserve names, dates, and numbers verbatim. "
        f"Do not editorialize or add information not in the text. "
        f"Output only the summary.\n\n"
        f"--- PAGES {start}-{end} ---\n{text}"
    )
    return _llm_complete(prompt, temperature=0.3, max_tokens=400)


def _reduce(title: str, n_pages: int, chunk_summaries: List[Dict],
            focus: str = "") -> str:
    """Reduce step: fold the per-chunk summaries into one structured overview."""
    joined = "\n\n".join(
        f"[pages {c['start']}-{c['end']}]\n{c['summary']}"
        for c in chunk_summaries
    )
    focus_line = (f" The user is specifically interested in: {focus}." if focus else "")
    prompt = (
        f"Below are sequential summaries of page windows from \"{title}\" "
        f"({n_pages} pages total).{focus_line} Synthesize them into ONE "
        f"structured overview of the whole document. Use this shape:\n\n"
        f"# {title} — summary\n"
        f"**Takeaway:** one sentence capturing the whole document.\n\n"
        f"## Themes\n- 3-6 cross-cutting themes.\n\n"
        f"## Key points\n- 6-10 bullets covering the document's main arcs "
        f"(NOT each window individually). Keep names/dates/numbers verbatim.\n\n"
        f"## By section\n- one bullet per major span, each tagged with its "
        f"page range.\n\n"
        f"Do not invent content. If a span's summary noted no extractable "
        f"text, say so rather than fabricating.\n\n"
        f"--- CHUNK SUMMARIES ---\n{joined}"
    )
    return _llm_complete(prompt, temperature=0.3, max_tokens=1200)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_map_reduce(path: str, chunk_pages: int = 12, focus: str = "",
                   summarize_fn: Optional[Callable[..., str]] = None,
                   reduce_fn: Optional[Callable[..., str]] = None,
                   use_cache: bool = True) -> str:
    """Map-reduce summarize a PDF. Returns a markdown string (the final
    overview + a one-line header). Caches extracted text + chunk summaries.

    `summarize_fn(title, start, end, text, focus) -> str` and
    `reduce_fn(title, n_pages, chunk_summaries, focus) -> str` are injectable
    for offline testing; default to real LLM calls."""
    if not os.path.isfile(path):
        return f"Error: PDF not found: {path}"
    summarize_fn = summarize_fn or _default_summarize
    reduce_fn = reduce_fn or _reduce
    title = os.path.basename(path)
    key = _cache_key(path, chunk_pages)

    cached = _load_cache(key) if use_cache else None
    if cached and cached.get("chunks"):
        chunks = cached["chunks"]
        n_pages = cached.get("n_pages", 0)
        backend = cached.get("backend", "cache")
    else:
        try:
            raw_chunks, n_pages, backend = _extract_chunks(path, chunk_pages)
        except RuntimeError as e:
            return f"Error: {e}"
        chunks = [dict(c) for c in raw_chunks]

    if not chunks:
        return f"Error: no pages extracted from {title}"

    # MAP — summarize each chunk that doesn't already have a cached summary,
    # in a bounded thread pool. (LLM calls are network I/O, so threads give
    # real concurrency despite the GIL; the endpoint serializes internally.)
    todo = [i for i, c in enumerate(chunks) if not c.get("summary")]

    def _work(i: int) -> None:
        c = chunks[i]
        c["summary"] = summarize_fn(title, c["start"], c["end"],
                                    c.get("text", ""), focus)

    if todo:
        workers = max(1, min(_MAX_CONCURRENCY, len(todo)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_work, todo))

    # Persist text + summaries so a re-ask skips both extract + map.
    if use_cache:
        _save_cache(key, {
            "path": os.path.abspath(path), "title": title,
            "n_pages": n_pages, "backend": backend,
            "chunk_pages": chunk_pages, "saved_at": time.time(),
            "chunks": chunks,
        })

    # REDUCE
    final = reduce_fn(title, n_pages, chunks, focus)
    n_failed = sum(1 for c in chunks
                   if str(c.get("summary", "")).startswith("[map-reduce LLM error"))
    header = (f"{title} — {n_pages} pages, {len(chunks)} chunks "
              f"({chunk_pages} pages each), backend={backend}"
              + (f", {n_failed} chunk(s) failed" if n_failed else "")
              + (f", focus={focus!r}" if focus else "") + "\n\n")
    return header + final


def run_in_background(path: str, chunk_pages: int = 12, focus: str = "") -> Dict:
    """Kick off map-reduce in a background python-job and write the final
    summary to <PDF_dir>/<PDF_name>_summary.md. Returns immediately with
    {ok, job_id, summary_path, log_path, ...} — the overnight use case.

    The model can poll get_job_result(job_id) or just read the summary file
    once the job completes."""
    if not os.path.isfile(path):
        return {"ok": False, "error": f"PDF not found: {path}"}
    from . import jobs

    abspath = os.path.abspath(path)
    out_path = os.path.splitext(abspath)[0] + "_summary.md"

    def _job(_args=None):
        text = run_map_reduce(abspath, chunk_pages=chunk_pages, focus=focus)
        try:
            Path(out_path).write_text(text, encoding="utf-8")
        except OSError as e:
            return {"written": False, "error": str(e), "summary": text[:2000]}
        return {"written": True, "summary_path": out_path,
                "preview": text[:600]}

    label = f"pdf_summary:{os.path.basename(path)}"
    started = jobs.start_python_job(
        label, _job, description=f"map-reduce summarize {os.path.basename(path)}")
    started["summary_path"] = out_path
    started["note"] = (
        f"Summarizing {os.path.basename(path)} in the background. The final "
        f"summary will be written to {out_path}. Poll with "
        f"get_job_result(job_id={started.get('job_id')!r}) or read the file "
        f"when it's done.")
    return started
