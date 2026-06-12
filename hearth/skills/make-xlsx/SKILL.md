---
name: make-xlsx
description: Build an Excel (.xlsx) spreadsheet from rows of data or a TSV/CSV outline. Use when the user asks for a "spreadsheet", "Excel", "xlsx", or to "organize this as a table I can sort in Excel".
version: 1.0.0
---

# Make an XLSX

Bundled `scripts/build_xlsx.py` wraps `openpyxl` (already a Hearth dep
for read_file on .xlsx).

## Input format

**TAB-SEPARATED.** Real tab characters (`\t`) between fields, NOT commas.
The builder will fall back to comma-detection if you mess it up, but
don't rely on that — write tabs. First row = header.

```
Name<TAB>Role<TAB>Years
Ada<TAB>Eng<TAB>5
Linus<TAB>Eng<TAB>34
```

When you `write_file`, escape tabs literally in the `content` arg:
`"Name\tRole\tYears\nAda\tEng\t5\n..."` — NOT comma-separated.

## Steps

1. `write_file` the TSV to `<workspace>/generated/<slug>.tsv`.
2. `run_command`:
   ```
   python <hearth>/skills/make-xlsx/scripts/build_xlsx.py \
     --tsv <workspace>/generated/<slug>.tsv \
     --out <workspace>/generated/<slug>.xlsx \
     --sheet "<sheet name>"
   ```
3. Script prints the output path on stdout.
4. **Open it for them.** `run_command('powershell -Command "Invoke-Item ''<full path>''"')` so Excel pops it open.

## Style defaults
- Header row bold + freeze pane
- Auto-fit column widths (first 30 rows sampled)
- Currency / number / date columns NOT auto-formatted — the model can
  request a follow-up format pass if needed

## Don't
- Don't try to add charts here. If the user wants a chart, generate a
  PNG with matplotlib and reference it from a doc, or use the chart
  options in Excel after the file lands.
- Don't write 50K rows of made-up data. Confirm the row count with the
  user if it's over 200.
