"""QA check for a freshly-built PDF — works for ANY model, no vision needed.

Renders each page and measures TWO things the build step can't see (it writes
the script blind; reportlab paginates at render time):

  1. content-bottom: how far DOWN the page the content actually reaches
     (the lowest inked row). This is the real "is there a big gap at the
     bottom" signal — total ink % alone can't tell a balanced page from one
     with everything crammed up top and an empty lower third.
  2. ink %: overall density, as a secondary blank-page catch.

Flags:
  - ORPHAN trailing page: last page's content ends very high up (a couple of
    lines spilled over). Fix: tighten so the doc ends on a full page.
  - BOTTOM GAP: a non-final page whose content stops well above the bottom
    margin — looks unbalanced/unfinished (may be intentional on a cover).
  - BLANK page.

Usage:
    python qa_check.py <path-to.pdf>

Always exits 0 — advisory, meant to be read by the model that just built the
PDF so it can decide whether to tighten/rebalance and rebuild.
"""
from __future__ import annotations
import sys


def _page_metrics(page):
    """Return (ink_pct, content_bottom_pct, max_gap_pct) for one page.
    - content_bottom_pct = lowest inked row as % of page height (catches a
      footer-less trailing orphan).
    - max_gap_pct = the LARGEST empty horizontal band between the first and
      last inked rows, as % of page height. This is the real "big gap"
      signal: it catches body content floating above an empty stretch even
      when a footer pins the very bottom (which makes content_bottom ~97%)."""
    from PIL import Image, ImageChops
    img = page.render(scale=0.5).to_pil().convert("RGB")
    w, h = img.size
    bg = img.getpixel((1, 1))  # margin corner = background, theme-agnostic
    diff = ImageChops.difference(img, Image.new("RGB", img.size, bg)).convert("L")
    mask = diff.point(lambda p: 255 if p > 30 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return 0.0, 0.0, 0.0
    ink_pct = 100.0 * mask.histogram()[255] / (w * h)
    content_bottom_pct = 100.0 * bbox[3] / h
    # Per-band ink profile to find the largest INTERNAL empty gap.
    bands = 50
    inked_band = []
    for k in range(bands):
        y0, y1 = int(k * h / bands), int((k + 1) * h / bands)
        inked_band.append(mask.crop((0, y0, w, y1)).getbbox() is not None)
    idx = [k for k, v in enumerate(inked_band) if v]
    max_gap = cur = 0
    if idx:
        for k in range(idx[0], idx[-1] + 1):
            cur = 0 if inked_band[k] else cur + 1
            max_gap = max(max_gap, cur)
    max_gap_pct = 100.0 * max_gap / bands
    return ink_pct, content_bottom_pct, max_gap_pct


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python qa_check.py <pdf>")
        return 0
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("QA: pypdfium2 not installed — skipping render check.")
        return 0
    try:
        pdf = pdfium.PdfDocument(sys.argv[1])
    except Exception as e:
        print(f"QA: couldn't open PDF ({type(e).__name__}: {e}) — does the path exist?")
        return 0

    n = len(pdf)
    rows = [_page_metrics(pdf[i]) for i in range(n)]
    print(f"QA: {n} page(s)")
    flags = []
    for i, (ink, bottom, gap) in enumerate(rows):
        is_last = (i == n - 1)
        tag = ""
        if ink < 0.3:
            tag = "  <-- BLANK page"
            flags.append("blank")
        elif is_last and n > 1 and bottom < 45:
            tag = (f"  <-- ORPHAN: content ends at {bottom:.0f}% down — only a little spilled here. "
                   f"TIGHTEN so the doc ends on a full page")
            flags.append("orphan")
        elif gap >= 24:
            tag = (f"  <-- BIG GAP: a {gap:.0f}%-tall empty band mid-page (e.g. body floating above "
                   f"the footer). Rebalance/fill unless intentional")
            flags.append("gap")
        print(f"  page {i + 1}: {ink:5.1f}% ink, reaches {bottom:.0f}% down, biggest empty band {gap:.0f}%{tag}")

    if flags:
        print("QA RESULT: FIX NEEDED — tighten or rebalance so pages fill to the bottom margin "
              "(reduce spaceAfter / trim / move a block up / merge a section), then rebuild and re-check.")
    else:
        print("QA RESULT: OK — pages fill cleanly to the bottom, no orphan/blank/gap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
