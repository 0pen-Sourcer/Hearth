"""Offline smoke test for read_pdf_large / pdf_mapreduce.

Creates a 30-page dummy PDF with reportlab, runs run_map_reduce with STUB
summarize/reduce functions (no live LLM), and asserts chunking + reduce
wiring + caching all work. Run:

    python -X utf8 scripts/smoke_read_pdf_large.py

Exits 0 on success, non-zero on failure.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Ensure the package root is importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_pdf(path: str, pages: int = 30) -> None:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    c = canvas.Canvas(path, pagesize=letter)
    for i in range(1, pages + 1):
        c.drawString(72, 720, f"Page {i} of the dummy report.")
        c.drawString(72, 700, f"This is line two on page {i}. Year 19{i:02d}.")
        c.drawString(72, 680, f"Key figure: {i * 1000} units. Author A{i}.")
        c.showPage()
    c.save()


def main() -> int:
    try:
        import reportlab  # noqa: F401
    except ImportError:
        print("SKIP: reportlab not installed (pip install reportlab to run this smoke).")
        return 0

    from hearth import pdf_mapreduce as pmr

    tmpdir = tempfile.mkdtemp(prefix="hearth_pdf_smoke_")
    pdf = os.path.join(tmpdir, "dummy_report.pdf")
    _make_pdf(pdf, pages=30)

    map_calls = {"n": 0}
    reduce_calls = {"n": 0}

    def stub_summarize(title, start, end, text, focus=""):
        map_calls["n"] += 1
        assert text, f"chunk {start}-{end} had no extracted text"
        return f"SUMMARY[{start}-{end}] ({len(text)} chars)"

    def stub_reduce(title, n_pages, chunk_summaries, focus=""):
        reduce_calls["n"] += 1
        return (f"REDUCED {title}: {n_pages} pages, "
                f"{len(chunk_summaries)} chunk summaries")

    # chunk_pages=12 over 30 pages => windows [1-12],[13-24],[25-30] = 3 chunks
    out = pmr.run_map_reduce(pdf, chunk_pages=12, summarize_fn=stub_summarize,
                             reduce_fn=stub_reduce)
    assert map_calls["n"] == 3, f"expected 3 map calls, got {map_calls['n']}"
    assert reduce_calls["n"] == 1, f"expected 1 reduce call, got {reduce_calls['n']}"
    assert "REDUCED dummy_report.pdf: 30 pages, 3 chunk summaries" in out, out
    assert "3 chunks" in out, out
    print("PASS map-reduce: 3 chunks, 1 reduce, header OK")

    # Second run should hit the cache → ZERO new map calls.
    map_calls["n"] = 0
    reduce_calls["n"] = 0
    out2 = pmr.run_map_reduce(pdf, chunk_pages=12, summarize_fn=stub_summarize,
                              reduce_fn=stub_reduce)
    assert map_calls["n"] == 0, f"cache miss: {map_calls['n']} map calls on re-run"
    assert reduce_calls["n"] == 1, "reduce should still run on cached chunks"
    print("PASS cache: re-run did 0 map calls (chunk summaries cached)")

    # Different chunk_pages must re-chunk (new cache key).
    map_calls["n"] = 0
    out3 = pmr.run_map_reduce(pdf, chunk_pages=10, summarize_fn=stub_summarize,
                              reduce_fn=stub_reduce)
    assert map_calls["n"] == 3, f"chunk_pages=10 over 30p => 3 chunks, got {map_calls['n']}"
    print("PASS re-chunk: chunk_pages=10 => 3 fresh windows")

    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
