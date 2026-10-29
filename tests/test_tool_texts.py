"""What the model is told about its tools reads as a product's, not as notes to
a developer: no shouting in capitals, no "NEXT STEP" orders, no asides to
"future-you", no internal markers like __JARVIS_IMAGE__.

The owner: "the tool prompts, and the total big prompt we are sending needs to
be corrected as Product tool calls, not Hobby projects". Audit and what was
changed: shared notes/PROMPT_AUDIT_2026-10-29.md.

    .\\.venv\\Scripts\\python.exe tests\\test_tool_texts.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-tool-texts-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import persona, tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# Words in capitals that are names, not emphasis.
NAMES = set("URL URLs PDF PATH GUI CLI API JSON HTML CSS UI OS PC GPU VRAM CPU LLM MCP USB ID IDs ASCII UTC HTTP "
            "HTTPS TTS STT OCR CSV TSV XLSX PNG JPG MP4 SVG EXE UAC UWP ACP SD KV RAM SSD HDD DPI GB MB KB TB AI IP DNS "
            "SSH SFTP FTP YAML TOML SQL LM AM PM VS TOOL DOCX EPUB IPYNB PPTX RTF IMAP INBOX PID YYYY SMTP ISO GPT GGUF "
            "TXT SDXL".split())
ORDERS = re.compile(r"NEXT STEP|RIGHT NOW|\bDo NOT\b|\bDO NOT\b|\bMUST\b|future-you|__JARVIS_|COORDINATE CONTRACT")

shouting, orders, long_ones = {}, {}, {}
for t in tools.TOOL_DEFINITIONS:
    text = t["description"] + " " + json.dumps(t.get("parameters") or {}, ensure_ascii=False)
    caps = sorted({w for w in re.findall(r"\b[A-Z]{3,}\b", text) if w not in NAMES})
    if caps:
        shouting[t["name"]] = caps
    if ORDERS.search(text):
        orders[t["name"]] = ORDERS.findall(text)
    if len(t["description"]) > 1600:
        long_ones[t["name"]] = len(t["description"])
check(f"no tool description shouts in capitals {shouting}", not shouting)
check(f"or gives orders like a note to a developer {orders}", not orders)
check(f"none runs past 1600 letters {long_ones}", not long_ones)

src = {f: open(os.path.join(ROOT, "hearth", f), encoding="utf-8").read() for f in ("tools.py", "plugins.py")}
said = {f: sorted(set(re.findall(r'f?"[^"\n]*(?:NEXT STEP:|Do NOT|DO NOT|RIGHT NOW|COORDINATE CONTRACT)[^"\n]*"', s)))
        for f, s in src.items()}
check(f"no result a tool returns gives such orders {said}", not any(said.values()))
check("a picture a tool names is a plain line", tools.media_line("image", "C:/w/cat.png", 312_000, "png")
      == "Image: C:/w/cat.png (305 KB, png)")
check("and old chats' markers are still read", tools.media_in("__JARVIS_IMAGE__ C:/w/cat.png (10 bytes, png)")
      == [("image", "C:/w/cat.png")])
sp = persona.system_prompt()
check("the system prompt has no internal markers or orders in capitals", not ORDERS.search(sp))

print("\n" + ("all tool text checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
