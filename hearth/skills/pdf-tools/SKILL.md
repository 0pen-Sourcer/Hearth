---
name: pdf-tools
description: Operate on an EXISTING PDF — split, merge, extract/delete pages, rotate, search for text across pages, or pull images. Use when the user wants to "split this PDF", "merge these PDFs", "grab pages 3-7", "find where it mentions X", "rotate", or OCR a scanned PDF. NOT for creating a styled PDF (that's make-pdf) or just reading one (that's read_file/summarize_file).
version: 1.0.0
---

# PDF tools — split / merge / search / rotate (on existing PDFs)

Use `pymupdf` (imported as `fitz`) — pure-Python wheel, fast, no native
binaries, already a likely dep. Write a tiny script per task. Output to
`<workspace>/PDFs/` so deliverables stay together. (`read_file` already
extracts text for reading/summarizing — this skill is for MANIPULATING.)

```python
import fitz  # pymupdf
```

## Split — pull a page range into a new PDF
```python
src = fitz.open("in.pdf")
out = fitz.open()
out.insert_pdf(src, from_page=2, to_page=6)   # 0-indexed; pages 3-7
out.save("out_p3-7.pdf")
```

## Merge — combine several PDFs in order
```python
out = fitz.open()
for p in ["a.pdf", "b.pdf", "c.pdf"]:
    out.insert_pdf(fitz.open(p))
out.save("merged.pdf")
```

## Delete / reorder pages
```python
doc = fitz.open("in.pdf")
doc.delete_page(0)                 # drop page 1
doc.select([3, 1, 2, 0])           # reorder by index
doc.save("reordered.pdf")
```

## Rotate
```python
doc = fitz.open("in.pdf")
for pg in doc: pg.set_rotation(90)
doc.save("rotated.pdf")
```

## Search across pages — "where does it mention X?"
```python
doc = fitz.open("in.pdf")
for i, pg in enumerate(doc):
    if pg.search_for("invoice total"):
        print(f"page {i+1}: found")
```

## Extract images
```python
doc = fitz.open("in.pdf")
for i, pg in enumerate(doc):
    for j, img in enumerate(pg.get_images(full=True)):
        pix = fitz.Pixmap(doc, img[0])
        pix.save(f"p{i+1}_img{j}.png")
```

## Scanned PDF (no text layer) → OCR
If `pg.get_text()` is empty, the page is a scan. Render it to an image and run
the user's OCR path (tesseract via `pytesseract` if installed, or
`view_image` so a vision model reads it). Don't claim text you didn't extract.
```python
pix = doc[0].get_pixmap(dpi=200); pix.save("page1.png")  # then view_image / OCR it
```

## Hard rules
- pymupdf is `import fitz`. In the packaged app, don't `pip install` — it's
  bundled; if truly missing, say so rather than failing silently.
- Page numbers are 0-indexed in code; the USER means 1-indexed — translate.
- Save outputs to `<workspace>/PDFs/`, never overwrite the source in place.
- For a scanned PDF, OCR or view_image it — never fabricate the text.
