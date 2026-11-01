"""Search, fetch and MCP servers reached over the web, checked without the internet.

Search used to ask one backend at a time with 25 s each, so a backend that hung
(Parallel does on some networks) held every search, and an empty answer was
"(no results)". web_fetch gave every page's menus and footers as text, ignored
max_chars, had no way to read past a cut, and sent a PDF's bytes as text. An MCP
server added by its address was refused ("no 'command'"), and a picture a tool
returned arrived as base64 in the model's context.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_web_tools.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import tools, websearch  # noqa: E402

# ---------------------------------------------------------------- search

check("search has several backends to ask", len(getattr(websearch, "BACKENDS", {})) >= 3)
if not hasattr(websearch, "BACKENDS"):
    print("\nFAILED (no backend set to test)")
    sys.exit(1)

ROW = lambda n: {"title": f"T{n}", "url": f"https://site{n}.example/page", "snippet": f"about {n}"}  # noqa: E731


def hangs(q, limit):
    time.sleep(30)
    return []


def refuses(q, limit):
    raise websearch._Refused("bot check")


real = dict(websearch.BACKENDS)
websearch.DEADLINE = 4.0
websearch.BACKENDS.clear()
websearch.BACKENDS.update({"slow": (hangs, "Slow"), "blocked": (refuses, "Blocked"),
                           "good": (lambda q, l: [ROW(1), ROW(2)], "Good"),
                           "also": (lambda q, l: [ROW(2), ROW(3)], "Also")})
t = time.time()
text, how = websearch.search("anything", 6)
took = time.time() - t
check(f"a backend that hangs doesn't hold the search ({took:.1f}s)", took < 4.5)
check("answers from every backend that answered are merged, each address once",
      text.count("https://site2.example/page") == 1 and "site1" in text and "site3" in text)
check("the result says where it came from", "Good" in text and "Also" in text)
check("a backend that refused sits out the next searches", not websearch.provider_status()[1]["ok"])

websearch.BACKENDS.clear()
websearch.BACKENDS.update({"blocked": (refuses, "Blocked")})
websearch.LAST_RESORT = ("wikipedia", refuses, "Wikipedia")
tools._WEB_SEARCH_STATE.update(fails=0, cooldown_until=0)
said = tools.execute_tool("web_search", {"query": "x"})
check(f"nothing answering is an error that names why, not '(no results)': {said[:90]!r}",
      said.startswith("Error") and "Blocked" in said and "browse" in said)
websearch.BACKENDS.clear()
websearch.BACKENDS.update(real)

DDG = ('<div class="result"><h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2F&amp;rut=1">'
       'Welcome to <b>Python</b>.org</a></h2><a class="result__snippet" href="x">The official home &amp; more</a></div>')
check("DuckDuckGo's page gives the real address and clean text", websearch.parse_ddg(DDG) == [
    {"title": "Welcome to Python .org", "url": "https://python.org/", "snippet": "The official home & more"}])
LITE = ("<tr><td><a rel=\"nofollow\" href=\"https://a.example/\" class='result-link'>A page</a></td></tr>"
        "<tr><td class='result-snippet'>What A says</td></tr>")
check("its Lite page too", websearch.parse_ddg_lite(LITE) == [
    {"title": "A page", "url": "https://a.example/", "snippet": "What A says"}])
EXA = ("Title: One\nURL: https://one.example\nPublished: N/A\nHighlights:\n## Head\nthe text\n\n"
       "Title: Two\nURL: https://two.example\nText:\nmore")
check("Exa's blocks become rows", [r["url"] for r in websearch.parse_exa(EXA)] == ["https://one.example", "https://two.example"]
      and websearch.parse_exa(EXA)[0]["snippet"] == "Head the text")

# ---------------------------------------------------------------- fetch

PAGE = """<html><head><title>Tides explained</title></head><body>
<nav><a href="/">Home</a> <a href="/x">Menu item</a></nav>
<header class="site-header">Site banner</header>
<div class="cookie-banner">We use cookies</div>
<svg><title>search icon</title></svg>
<article><h1>Tides</h1><p>Tides are the rise and fall of the sea, caused by the
<a href="https://en.wikipedia.org/wiki/Moon">Moon</a>.</p><ul><li>Spring tides</li><li>Neap tides</li></ul>
<p>""" + "More about the sea. " * 60 + """</p></article>
<aside>Related posts</aside><footer>Copyright</footer><script>var x = 1;</script></body></html>"""

served = {}


def fake_get(url, timeout=15):
    if "403" in url:
        import urllib.error
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    return served[url]


tools._http_get = fake_get
served["https://tides.example/"] = (200, "text/html; charset=utf-8", PAGE.encode())
out = tools.execute_tool("web_fetch", {"url": "https://tides.example/"})
check("the article is read, with its title and headings", "# Tides explained" in out and "# Tides\n" in out
      and "- Spring tides" in out)
check("menus, banners, sidebars, footers and scripts are left out",
      not any(w in out for w in ("Menu item", "Site banner", "cookies", "Related posts", "Copyright", "var x",
                                 "search icon")))
check("a link keeps where it goes", "(https://en.wikipedia.org/wiki/Moon)" in out)
short = tools.execute_tool("web_fetch", {"url": "https://tides.example/", "max_chars": 600})
check(f"max_chars is honoured ({len(short)} chars)", len(short) < 800 and "next offset 600" in short)
more = tools.execute_tool("web_fetch", {"url": "https://tides.example/", "max_chars": 600, "offset": 600})
check("offset reads on from there", "characters 600-" in more and "# Tides explained" not in more)

try:
    from reportlab.pdfgen import canvas   # type: ignore
    buf = os.path.join(tempfile.mkdtemp(), "t.pdf")
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "Attention is all you need")
    c.save()
    served["https://papers.example/x"] = (200, "application/pdf", open(buf, "rb").read())
    pdf = tools.execute_tool("web_fetch", {"url": "https://papers.example/x"})
    check("a PDF comes back as its text, with no temporary path in it",
          "Attention is all you need" in pdf and tempfile.gettempdir().lower() not in pdf.lower())
except ImportError:
    print("skip  PDF (reportlab missing)")
said = tools.execute_tool("web_fetch", {"url": "https://blocked.example/403"})
check(f"a refusal says what it means and what to do: {said[:80]!r}", "refused" in said and "browse" in said)
served["https://empty.example/"] = (200, "text/html", b"<html><body><div id='root'></div><script>app()</script></body></html>")
check("a page built by JavaScript says so", "JavaScript" in tools.execute_tool("web_fetch", {"url": "https://empty.example/"}))

# ---------------------------------------------------------------- MCP servers by address

from hearth import mcp_client  # noqa: E402

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
SERVER = os.path.join(tempfile.mkdtemp(), "remote_mcp.py")
with open(SERVER, "w", encoding="utf-8") as f:
    f.write(f"""
import base64
from mcp.server.fastmcp import FastMCP, Image
mcp = FastMCP("remote", host="127.0.0.1", port={PORT})

@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b

@mcp.tool()
def dot() -> Image:
    return Image(data=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="), format="png")

mcp.run(transport="streamable-http")
""")
proc = subprocess.Popen([sys.executable, SERVER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    end = time.time() + 30
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            break
        except OSError:
            time.sleep(0.3)
    with open(os.path.join(tools.WORKSPACE, "mcp.json"), "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"remote": {"url": f"http://127.0.0.1:{PORT}/mcp"},
                                  "gone": {"url": "http://127.0.0.1:9/mcp"}}}, f)
    mcp_client.bootstrap()
    end = time.time() + 40
    while time.time() < end and any(b["state"] == "starting" for b in mcp_client.list_bridges()):
        time.sleep(0.5)
    rows = {b["raw_name"]: b for b in mcp_client.list_bridges()}
    check(f"a server added by its address connects ({rows.get('remote', {}).get('error', '')})",
          rows.get("remote", {}).get("state") == "connected")
    check("its tools reach the model", "mcp_remote_add" in [t["name"] for t in mcp_client.list_remote_tools()])
    check("and run", tools.execute_tool("mcp_remote_add", {"a": 2, "b": 3}).strip() == "5")
    pic = tools.execute_tool("mcp_remote_dot", {})
    check(f"a picture it returns is saved and named, not pasted in: {pic[:70]!r}",
          pic.startswith("Image: ") and os.path.isfile(tools.media_in(pic)[0][1]) and "base64" not in pic.lower())
    check(f"an address nothing answers says so plainly: {rows.get('gone', {}).get('error', '')!r}",
          rows.get("gone", {}).get("error", "") == "Couldn't connect: that address can't be reached")
finally:
    mcp_client.shutdown()
    proc.kill()

err, _d = mcp_client.plain_failure(Exception("Client error '401 Unauthorized'"), "", "https://x.example/mcp")
check("a server that needs signing in says where the token goes", "headers" in err)
check("an address ending /sse uses the older transport, any other the current one",
      mcp_client._remote_transport("https://x.example/sse", {}).gen.__name__ == "sse_client"
      and "streamable" in mcp_client._remote_transport("https://x.example/mcp", {}).gen.__name__)

print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'PASSED'}")
sys.exit(1 if FAILS else 0)
