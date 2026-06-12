"""TSV -> .xlsx builder used by the `make-xlsx` skill.

Reads a tab-separated file (header on row 1) and writes a polished xlsx
via openpyxl. Bold header, frozen first row, sampled-width columns.

Run:
    python build_xlsx.py --tsv path/to/data.tsv --out path/to/out.xlsx
                         [--sheet "SheetName"]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def _import_openpyxl():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment
        return locals()
    except ImportError as e:
        print(f"openpyxl missing: {e}. pip install openpyxl",
              file=sys.stderr)
        sys.exit(2)


def _col_letter(idx: int) -> str:
    s = ""
    n = idx
    while n >= 0:
        s = chr(65 + (n % 26)) + s
        n = n // 26 - 1
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheet", default="Sheet1")
    args = ap.parse_args()

    src = Path(args.tsv)
    if not src.is_file():
        print(f"error: tsv not found: {src}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    X = _import_openpyxl()
    wb = X["Workbook"]()
    ws = wb.active
    ws.title = args.sheet[:31] or "Sheet1"

    # Auto-detect TAB vs COMMA vs SEMICOLON. The skill asks for TSV but
    # small models keep writing CSV — accept either rather than producing
    # a 1-column spreadsheet that looks broken.
    raw = src.read_text(encoding="utf-8")
    head = "\n".join(raw.splitlines()[:5])
    if "\t" in head:
        delim = "\t"
    elif head.count(",") >= head.count(";"):
        delim = ","
    else:
        delim = ";"

    rows: list[list[str]] = []
    import io
    reader = csv.reader(io.StringIO(raw), delimiter=delim)
    for r in reader:
        rows.append(r)

    if not rows:
        print("error: data file is empty", file=sys.stderr)
        return 1

    for r in rows:
        ws.append(r)

    # Header bold + freeze
    header_font = X["Font"](bold=True)
    for cell in ws[1]:
        cell.font = header_font
    ws.freeze_panes = "A2"

    # Width = max(len) on first 30 rows, clamped
    sample = rows[:30]
    ncols = max(len(r) for r in sample) if sample else 0
    for c in range(ncols):
        widths = [len((r[c] if c < len(r) else "")) for r in sample]
        w = min(max(widths + [len(rows[0][c] if c < len(rows[0]) else "")]) + 2, 40)
        ws.column_dimensions[_col_letter(c)].width = max(w, 8)

    wb.save(str(out))
    print(str(out.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
