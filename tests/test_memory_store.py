"""The memory store: file names, and the index the model reads every turn.

Two failures, both found in real use.

A title with no ASCII letter or digit, which is any Hindi, Chinese or Japanese
title, reduced to an empty name and fell back to "memory". That file is
MEMORY.md, the index, on Windows, where names ignore case: a fact saved under
such a title replaced the whole index, and every such title shared that one
name and overwrote the last.

The index was a separate file that six code paths edited in place after
changing a fact, with nothing checking the edit landed, and two saves at once
each wrote back their own version. One real store had 62 facts and an index
listing 24, the user's name and hardware among the missing. The index is now
rebuilt from the fact files and heals itself when it disagrees with them.

Runs entirely in a throwaway workspace and refuses to run anywhere else.

    .\\.venv\\Scripts\\python.exe tests\\test_memory_store.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading

WS = tempfile.mkdtemp(prefix="hearth-memtest-")
os.environ["JARVIS_WORKSPACE"] = WS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import memory as M                                      # noqa: E402

if not os.path.normcase(os.path.abspath(M.MEM_DIR)).startswith(
        os.path.normcase(os.path.abspath(WS))):
    sys.exit(f"refusing to run: memory resolved to {M.MEM_DIR}, not the test workspace")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def listed():
    import re
    return set(re.findall(r"\]\(([^)]+)\.md\)", open(M.INDEX_PATH, encoding="utf-8").read()))


def facts():
    return {f[:-3] for f in os.listdir(M.MEM_DIR)
            if f.endswith(".md") and f.lower() != "memory.md" and not f.startswith(".")}


# --------------------------------------------------------- names in any script
M.save("favourite editor", "user", "uses VS Code", "VS Code")
M.save("home town", "user", "from Dhanbad", "Dhanbad")
M.save("मेरा नाम", "user", "name, written in Hindi", "Ishant")
M.save("我的狗", "user", "has a dog", "a dog")
M.save("今日の予定", "user", "today's plan", "study")

index = open(M.INDEX_PATH, encoding="utf-8").read()
check("the index is still an index, not a fact", not index.startswith("---"))
check("a Hindi title did not overwrite the index", "favourite" in index)
check("titles in three scripts each got their own file", len(facts()) == 5)
check("and the index lists all five", listed() == facts())
check("a Hindi vowel sign is kept rather than splitting the word",
      M._script_slug("मेरा") == "मेरा")
check("a title that is literally 'Memory' does not become the index",
      M._slug("Memory") != "memory" and M._slug("memory") != "memory")
check("nor a device name Windows reserves", M._slug("CON") != "con")
check("a title with nothing usable still gets a stable, distinct name",
      M._slug("!!!") == M._slug("!!!") and M._slug("!!!") != M._slug("???"))

# ------------------------------------------------ names earlier builds wrote
check("an ASCII title keeps exactly the name it always had",
      M._slug("favourite editor") == "favourite_editor")
legacy = os.path.join(M.MEM_DIR, "caf_notes.md")        # how "café notes" used to be named
with open(legacy, "w", encoding="utf-8") as f:
    f.write("---\nname: café notes\ntype: user\ndescription: old\n---\n\nold\n")
M.save("café notes", "user", "new", "new")
check("updating a fact saved under its old name updates that file, not a copy",
      "caf_notes" in facts() and "café_notes" not in facts())

# ------------------------------------------------------ a drifted index heals
before = {f: hashlib.sha1(open(os.path.join(M.MEM_DIR, f + ".md"), "rb").read()).hexdigest()
          for f in facts()}
with open(M.INDEX_PATH, "w", encoding="utf-8") as f:           # what the real store looked like
    f.write("- [home town](home_town.md) — `user` from Dhanbad\n"
            "- [gone](gone_fact.md) — `user` a fact whose file no longer exists\n")
view = M.index_for_prompt()
check("reading the index rebuilds it when it disagrees with the files",
      listed() == facts())
check("so the prompt view shows facts the old index had lost", "favourite" in view)
check("and drops an entry whose file is gone", "gone_fact" not in listed())
after = {f: hashlib.sha1(open(os.path.join(M.MEM_DIR, f + ".md"), "rb").read()).hexdigest()
         for f in facts()}
check("healing never touches a fact file", before == after)

# ------------------------------------------------------- saves at the same time
def burst(tag):
    for i in range(20):
        M.save(f"{tag} fact {i}", "user", f"{tag} {i}", "x", force=True)


threads = [threading.Thread(target=burst, args=(t,)) for t in ("alpha", "beta", "gamma", "delta")]
for t in threads:
    t.start()
for t in threads:
    t.join()
# Past a bucket's size the save archives its coldest facts, by design, so not
# all 80 stay in the hot store. What must hold is that nothing is lost and the
# index matches the files exactly.
archived = {f[:-3] for f in os.listdir(os.path.join(M.MEM_DIR, "_archive"))
            if f.endswith(".md")} if os.path.isdir(os.path.join(M.MEM_DIR, "_archive")) else set()
burst_names = {M._slug(f"{t} fact {i}") for t in ("alpha", "beta", "gamma", "delta")
               for i in range(20)}
check(f"after 80 saves from four threads the index matches the files exactly "
      f"({len(listed())} listed, {len(facts())} files)", listed() == facts())
check(f"and every one of the 80 is either in the store or archived, none lost "
      f"({len(burst_names & facts())} kept, {len(burst_names & archived)} archived)",
      burst_names <= (facts() | archived))

# ------------------------------------------- archiving only what is meant
# Superseding named a fact by title and used to archive the closest title on a
# single shared word: "primary OS" archived "primary GPU" and kept the old OS.
M.save("primary GPU", "user", "RTX 5060", "RTX 5060", force=True)
M.save("primary OS", "user", "Windows 10", "Windows 10", force=True)
M.save("current OS", "user", "Windows 11", "Windows 11", supersedes="primary OS", force=True)
_arch = os.path.join(M.MEM_DIR, "_archive")
_arched = {f[:-3] for f in os.listdir(_arch)} if os.path.isdir(_arch) else set()
check("superseding archives the fact it names", "primary_os" in _arched)
check("and leaves a fact that merely shares a word alone", "primary_gpu" in facts())
M.save("new thing", "user", "x", "x", supersedes="a fact that does not exist", force=True)
check("superseding something that is not there archives nothing",
      "primary_gpu" in facts())

# The merge dropped numbers and short words, so distinct facts collapsed.
for t in ("class 10 marks", "class 12 marks", "drive C free space", "drive D free space"):
    M.save(t, "user", t, t, force=True)
_merges = M.curate(apply=False)["clusters"]
check(f"facts told apart by a number or a letter are not merged: {_merges}",
      not any(set(c["dups"]) & {"class_10_marks", "class_12_marks",
                                  "drive_c_free_space", "drive_d_free_space"}
              for c in _merges))
M.save("favourite colour", "user", "violet", "violet", force=True)
M.save("colour favourite", "user", "violet", "violet", force=True)
check("the same title written two ways still merges",
      any({"favourite_colour", "colour_favourite"} == {c["kept"], *c["dups"]}
          for c in M.curate(apply=False)["clusters"]))
_extract_src = open(os.path.join(ROOT, "hearth", "memory_extract.py"), encoding="utf-8").read()
check("the extractor leaves merging to the gated pass, not after every save",
      "curate(apply=True)" not in _extract_src)

# ---------------------------------------------------------------- forgetting
M.forget("home town")
check("forgetting removes the file and its index line",
      "home_town" not in facts() and "home_town" not in listed())

# -------------------------------------------------- over the cap, keep the new
_cap = M.INDEX_LINE_CAP
M.INDEX_LINE_CAP = 10
M.save("the very newest fact", "user", "newest", "n", force=True)
M.INDEX_LINE_CAP = _cap
check("past the line cap the index keeps the newest facts, not the oldest",
      "the_very_newest_fact" in listed())

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all memory store checks passed")
