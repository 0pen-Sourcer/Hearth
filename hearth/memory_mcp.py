"""Hearth's memory as an MCP server of its own, for the coding agents on this PC.

Claude Code, Codex, Cursor or OpenCode add it (see shared_memory.py) and then
read and write the same memory Hearth uses: saved facts about the person and
their work, past chats, and what's going on lately. Nothing else of Hearth's
is offered here: no shell, files or desktop.
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

# stdout carries the protocol; anything printed while importing goes to stderr.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

from mcp.server.fastmcp import FastMCP  # type: ignore  # noqa: E402

from hearth import tools as _tools  # noqa: E402

mcp = FastMCP(
    "Hearth memory",
    instructions=(
        "The person's own memory, shared by Hearth and every agent they use on this PC. "
        "Before asking them something about themselves, their setup or their work, look here "
        "(memory_recall, search_chats). When they tell you something lasting about themselves "
        "or their work, save it (memory_save) so their other agents know it too. "
        "whats_going_on says what is in motion lately."
    ),
)


def _run(name: str, args: dict) -> str:
    return str(_tools.execute_tool(name, {k: v for k, v in args.items() if v is not None}))


@mcp.tool()
def memory_recall(query: str = "", limit: int = 5) -> str:
    """Find saved facts about the person and their work: preferences, hardware, projects,
    people, setups. A word or two for the topic; empty lists the newest."""
    return _run("memory_recall", {"query": query, "limit": limit})


@mcp.tool()
def memory_save(title: str, type: str, description: str, body: str = "",
                tags: Optional[List[str]] = None, volatility: Optional[str] = None) -> str:
    """Save a lasting fact about the person or their work so all their agents know it.
    One topic per memory under a stable title; save over the same title when it changes.
    type: user (who they are), feedback (how they want things done), project (ongoing work),
    reference (where something lives). description: one line. volatility: durable, seasonal
    or transient."""
    return _run("memory_save", {"title": title, "type": type, "description": description,
                                "body": body or description, "tags": tags, "volatility": volatility})


@mcp.tool()
def memory_list() -> str:
    """Every saved memory's title and one-line description."""
    return _run("memory_list", {})


@mcp.tool()
def memory_forget(title: str) -> str:
    """Remove a saved memory by its title (it's archived, not destroyed)."""
    return _run("memory_forget", {"title": title})


@mcp.tool()
def search_chats(query: str = "", after: str = "", before: str = "", limit: int = 5) -> str:
    """Search the person's past conversations with Hearth for what was said or decided.
    Time words in the query, or after/before (YYYY-MM-DD), limit it to then."""
    return _run("search_chats", {"query": query, "after": after or None, "before": before or None, "limit": limit})


@mcp.tool()
def read_chat(chat: str, start: int = 0) -> str:
    """Read one past conversation in order, by its title or id from search_chats."""
    return _run("read_chat", {"chat": chat, "start": start})


@mcp.tool()
def whats_going_on() -> str:
    """What's in motion across their chats: each chat's goal, where it stands, what's left."""
    from hearth import memory_cards
    rows = [c for c in memory_cards.everything() if c.get("status") == "open"][:10]
    if not rows:
        return "Nothing going on right now."
    return "\n".join(f"- {memory_cards.card_text(c)} ({c.get('ago')}, chat {c.get('chat')})" for c in rows)


if __name__ == "__main__":
    sys.stdout = _REAL_STDOUT
    mcp.run()
