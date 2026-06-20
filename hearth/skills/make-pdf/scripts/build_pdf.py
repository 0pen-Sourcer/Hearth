"""Markdown -> PDF builder used by the `make-pdf` skill.

Minimal reportlab-only converter — no pandoc, no LaTeX. Handles H1/H2/H3,
bullets, fenced code, plain paragraphs, and local image refs. Anything
exotic falls back to monospaced verbatim so the PDF still renders.

Run:
    python build_pdf.py --md path/to/source.md --out path/to/out.pdf
                        [--title "Document Title"]

Exits 0 on success and prints the absolute output path.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _import_reportlab():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                         Preformatted, Image, PageBreak,
                                         Table, TableStyle)
        from reportlab.lib import colors
        return locals()
    except ImportError as e:
        print(f"reportlab missing: {e}. Install with: pip install reportlab",
              file=sys.stderr)
        sys.exit(2)


_DEFAULT_STYLE = {
    "accent":      "#6b4ec7",
    "body_color":  "#1a1a24",
    "heading_color": "#2d2440",
    "rule_color":  "#d8d2ec",
    "footer_color": "#666666",
    "font":        "Helvetica",
    "font_bold":   "Helvetica-Bold",
    "font_mono":   "Courier",
    "body_size":   11,
    "h1_size":     22,
    "h2_size":     15,
    "h3_size":     12,
    "leading":     16,
    "show_rule":   True,
    "show_date":   True,
}


def _md_to_flowables(md_text: str, R: dict, base_dir: Path, style: dict,
                     content_width: float = 451.0):
    styles = R["getSampleStyleSheet"]()
    colors = R["colors"]
    accent = colors.HexColor(style["accent"])
    body_color = colors.HexColor(style["body_color"])
    heading_color = colors.HexColor(style["heading_color"])
    font = style["font"]
    font_bold = style["font_bold"]
    body = R["ParagraphStyle"](
        "body", parent=styles["BodyText"], fontName=font,
        fontSize=style["body_size"], leading=style["leading"],
        spaceAfter=8, textColor=body_color)
    h1 = R["ParagraphStyle"](
        "h1", parent=styles["Heading1"], fontName=font_bold,
        fontSize=style["h1_size"], leading=style["h1_size"] + 6,
        spaceBefore=0, spaceAfter=14, textColor=accent)
    h2 = R["ParagraphStyle"](
        "h2", parent=styles["Heading2"], fontName=font_bold,
        fontSize=style["h2_size"], leading=style["h2_size"] + 5,
        spaceBefore=18, spaceAfter=8, textColor=heading_color)
    h3 = R["ParagraphStyle"](
        "h3", parent=styles["Heading3"], fontName=font_bold,
        fontSize=style["h3_size"], leading=style["h3_size"] + 4,
        spaceBefore=12, spaceAfter=4, textColor=heading_color)
    bullet = R["ParagraphStyle"](
        "bullet", parent=body, leftIndent=22, bulletIndent=8,
        spaceAfter=4, bulletFontName=font_bold,
        bulletFontSize=style["body_size"])
    cell = R["ParagraphStyle"](
        "cell", parent=body, fontSize=max(8, style["body_size"] - 1),
        leading=max(10, style["leading"] - 3), spaceAfter=0, spaceBefore=0)
    cell_h = R["ParagraphStyle"]("cellh", parent=cell, fontName=font_bold,
                                 textColor=colors.white)
    flow = []
    in_code = False
    code_buf = []
    table_buf = []

    def _flush_table():
        nonlocal table_buf
        rows = table_buf
        table_buf = []
        # need a header + a |---| separator row to treat it as a table
        if len(rows) < 2 or not re.match(r"^\|?[\s:|-]+\|?$", rows[1]):
            for r in rows:  # not a real table -> render as plain lines
                flow.append(R["Paragraph"](_md_inline(r, style), body))
            return
        def _cells(r):
            return [c.strip() for c in r.strip().strip("|").split("|")]
        header = _cells(rows[0])
        data = [_cells(r) for r in rows[2:]]
        ncol = len(header)
        grid = [[R["Paragraph"](_md_inline(c, style), cell_h) for c in header]]
        for d in data:
            d = (d + [""] * ncol)[:ncol]
            grid.append([R["Paragraph"](_md_inline(c, style), cell) for c in d])
        # column widths weighted by content length, normalized so the table
        # ALWAYS fits content_width (this is what stops the right-edge overflow).
        weights = [max(1, max((len(g[i].text) for g in grid), default=1))
                   for i in range(ncol)]
        tot = float(sum(weights)) or 1.0
        # Floor each column at half of an even share so a short-header column
        # isn't crushed to one character per line; distribute the rest by
        # content length. Always sums to content_width (so it still fits).
        min_frac = 0.5 / ncol
        free = 1.0 - min_frac * ncol
        col_w = [content_width * (min_frac + free * (w / tot)) for w in weights]
        t = R["Table"](grid, colWidths=col_w, hAlign="LEFT", repeatRows=1)
        accent_c = colors.HexColor(style["accent"])
        t.setStyle(R["TableStyle"]([
            ("BACKGROUND", (0, 0), (-1, 0), accent_c),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f3f0fb")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(style["rule_color"])),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        flow.append(R["Spacer"](1, 4))
        flow.append(t)
        flow.append(R["Spacer"](1, 8))

    for raw in md_text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                if code_buf:
                    flow.append(R["Preformatted"]("\n".join(code_buf),
                                                   styles["Code"]))
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(raw)
            continue
        # Markdown table rows buffer until the block ends, then render fitted.
        if line.strip().startswith("|") and line.strip().endswith("|"):
            table_buf.append(line.strip())
            continue
        if table_buf:
            _flush_table()
        if not line.strip():
            flow.append(R["Spacer"](1, 6))
            continue
        if line.startswith("# "):
            flow.append(R["Paragraph"](_md_inline(line[2:], style), h1))
        elif line.startswith("## "):
            flow.append(R["Paragraph"](_md_inline(line[3:], style), h2))
        elif line.startswith("### "):
            flow.append(R["Paragraph"](_md_inline(line[4:], style), h3))
        elif line.startswith(("- ", "* ", "+ ")):
            flow.append(R["Paragraph"](_md_inline(line[2:], style), bullet,
                                         bulletText="•"))
        elif line.startswith("![") and "](" in line and line.endswith(")"):
            alt, _, rest = line[2:].partition("](")
            img_path = rest[:-1]
            full = (base_dir / img_path) if not os.path.isabs(img_path) else Path(img_path)
            if full.is_file():
                try:
                    flow.append(R["Image"](str(full), width=4 * R["inch"],
                                            height=3 * R["inch"]))
                except Exception:
                    flow.append(R["Paragraph"](f"[image: {alt}]", body))
            else:
                flow.append(R["Paragraph"](f"[missing image: {alt}]", body))
        else:
            flow.append(R["Paragraph"](_md_inline(line, style), body))
    if code_buf:
        flow.append(R["Preformatted"]("\n".join(code_buf), styles["Code"]))
    if table_buf:
        _flush_table()
    return flow


def _md_inline(text: str, style: dict) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)
    mono = style.get("font_mono", "Courier")
    text = re.sub(r"`([^`]+)`",
                  rf'<font face="{mono}" size="10">\1</font>', text)
    return text


def _make_footer(style: dict):
    from reportlab.lib import colors as _c
    from datetime import datetime
    rule_color = _c.HexColor(style["rule_color"])
    footer_color = _c.HexColor(style["footer_color"])
    show_rule = bool(style.get("show_rule", True))
    show_date = bool(style.get("show_date", True))
    font = style.get("font", "Helvetica")

    def _footer(canvas, doc):
        w, h = doc.pagesize
        canvas.saveState()
        if show_rule:
            canvas.setStrokeColor(rule_color)
            canvas.setLineWidth(0.8)
            canvas.line(72, h - 56, w - 72, h - 56)
        canvas.setFont(font, 9)
        canvas.setFillColor(footer_color)
        canvas.drawCentredString(w / 2.0, 0.5 * 72, str(canvas.getPageNumber()))
        if show_date:
            canvas.drawRightString(w - 72, 0.5 * 72,
                                    datetime.now().strftime("%b %d, %Y"))
        canvas.restoreState()
    return _footer


def _resolve_pagesize(name: str, R: dict):
    name = (name or "").upper()
    from reportlab.lib import pagesizes
    return getattr(pagesizes, name, R["A4"])


def main() -> int:
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, help="Path to source markdown")
    ap.add_argument("--out", required=True, help="Path for output PDF")
    ap.add_argument("--title", default="", help="Document title (frontmatter)")
    ap.add_argument("--style", default="",
                    help="Inline JSON dict of style overrides. PREFER "
                         "--style-file because PowerShell mangles inline "
                         "JSON quoting.")
    ap.add_argument("--style-file", default="",
                    help="Path to a .json file with style overrides. Write "
                         "it with write_file then pass the path here — "
                         "this avoids PowerShell quote-escape hell. Keys: "
                         "accent, body_color, heading_color, rule_color, "
                         "footer_color, font, font_bold, font_mono, body_size, "
                         "h1_size, h2_size, h3_size, leading, show_rule, "
                         "show_date.")
    ap.add_argument("--page", default="A4",
                    help="Page size: A4 (default), LETTER, LEGAL, A5.")
    args = ap.parse_args()

    src = Path(args.md)
    if not src.is_file():
        print(f"error: source not found: {src}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    style = dict(_DEFAULT_STYLE)
    sf = getattr(args, "style_file", "") or ""
    if sf:
        try:
            style.update(json.loads(Path(sf).read_text(encoding="utf-8")))
        except Exception as e:
            print(f"warn: --style-file ignored, bad JSON: {e}", file=sys.stderr)
    if args.style:
        try:
            style.update(json.loads(args.style))
        except Exception as e:
            print(f"warn: --style ignored, bad JSON: {e}", file=sys.stderr)

    R = _import_reportlab()
    md = src.read_text(encoding="utf-8")
    # Dedup: if --title matches the markdown's first H1, drop the H1 so
    # the cover title doesn't render twice. (The model frequently passes
    # the same string to both, producing "Local-First AI / Local-First AI".)
    if args.title:
        first_h1 = ""
        for raw in md.splitlines():
            s = raw.strip()
            if not s:
                continue
            if s.startswith("# "):
                first_h1 = s[2:].strip()
            break
        if first_h1 and first_h1.lower() == args.title.strip().lower():
            lines = md.splitlines()
            for i, raw in enumerate(lines):
                if raw.strip().startswith("# "):
                    lines.pop(i)
                    while i < len(lines) and not lines[i].strip():
                        lines.pop(i)
                    break
            md = "\n".join(lines)
    flow = []
    if args.title:
        styles = R["getSampleStyleSheet"]()
        title_style = R["ParagraphStyle"](
            "title", parent=styles["Title"], fontName=style["font_bold"],
            fontSize=style["h1_size"] + 4, leading=style["h1_size"] + 8,
            spaceAfter=18, alignment=1,
            textColor=R["colors"].HexColor(style["accent"]))
        flow.append(R["Paragraph"](args.title, title_style))
    _pagesize = _resolve_pagesize(args.page, R)
    _content_width = _pagesize[0] - 2 * R["inch"]   # page width minus L+R margins
    flow.extend(_md_to_flowables(md, R, src.parent, style, _content_width))

    doc = R["SimpleDocTemplate"](
        str(out), pagesize=_pagesize,
        leftMargin=R["inch"], rightMargin=R["inch"],
        topMargin=R["inch"], bottomMargin=R["inch"],
        title=args.title or out.stem,
    )
    footer = _make_footer(style)
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    print(str(out.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
