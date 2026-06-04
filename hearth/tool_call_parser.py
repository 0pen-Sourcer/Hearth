"""Multi-family tool-call parser.

llama-cpp-python's builtin chat handlers only know two formats:
`chatml-function-calling` (Qwen 2.5, Hermes, Mistral classic) and `functionary`.
Every other open-weights family — Gemma, Llama 3.x, Phi 4, Command-R, Granite,
Qwen 3 thinking variants — emits its OWN tool-call syntax as raw text in the
chat stream. Hearth's builtin server doesn't parse those, so the user sees
gibberish like ``<|toolcall>call:viewimage{path:<|"|>...<|"|>}<tool_call|>``
in the chat window and the tools never fire.

This module is the fix. We post-process the model's full reply, detect
known patterns, and translate them into proper OpenAI ``tool_calls`` JSON.
Hearth's main loop then executes them as if the underlying server had
parsed them natively. LM Studio does the same dance internally — we just
implement it ourselves.

The API is small on purpose:

    cleaned_text, tool_calls = parse(raw_text, tool_names)

``raw_text`` is the model's entire reply (or any prefix). ``tool_names`` is
the list of tools the agent currently has access to so we can normalize the
model's mangled tool name (Gemma drops underscores, etc.) back to the real
name. ``tool_calls`` mirrors the OpenAI format::

    [{
        "id": "call_<n>",
        "type": "function",
        "function": {"name": "view_image", "arguments": '{"path": "..."}'},
    }, ...]

``cleaned_text`` is the model's reply with every detected tool-call block
stripped out (so the user only sees prose, not the syntax).

Adding a new family is dropping a `Pattern` into `_PATTERNS` and writing a
2-line extractor. See the existing ones for the shape.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Tool-name normalization
# ---------------------------------------------------------------------------
def _slug(s: str) -> str:
    """Strip everything except [a-z0-9] so 'view_image', 'viewImage',
    'view-image', 'viewimage' all hash to 'viewimage'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _resolve_tool_name(raw: str, tool_names: List[str]) -> str:
    """Map the model's emitted name back to a real tool name in our registry.

    Gemma collapses underscores (`view_image` → `viewimage`), some Hermes
    finetunes title-case (`ViewImage`), etc. We slug-compare both sides and
    return the canonical name. If nothing matches, return raw — the caller
    can decide whether to execute it or surface a "no such tool" error.
    """
    if not raw:
        return raw
    raw_slug = _slug(raw)
    for n in tool_names:
        if _slug(n) == raw_slug:
            return n
    return raw


# ---------------------------------------------------------------------------
# Argument coercion
# ---------------------------------------------------------------------------
def _force_json_str(args: Any) -> str:
    """OpenAI tool_calls expect arguments as a JSON-encoded STRING, not a
    dict. Normalize: a dict gets json.dumps'd; a string is taken verbatim
    (it might already be JSON); anything else is wrapped in {"value": ...}."""
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        return json.dumps(args, ensure_ascii=False)
    return json.dumps({"value": args}, ensure_ascii=False)


def _try_json(s: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON parse — many models emit *almost* JSON. Returns None
    on failure rather than raising so the caller can fall back to a regex
    `key: "value"` reader."""
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    # Try to repair common breakages: single quotes, trailing commas,
    # Python None/True/False instead of JSON null/true/false.
    try:
        repaired = (
            s.replace("'", '"')
             .replace("None", "null")
             .replace("True", "true")
             .replace("False", "false")
        )
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return json.loads(repaired)
    except Exception:
        return None


def _kv_pairs_to_dict(body: str) -> Dict[str, Any]:
    """Last-resort parser for arg bodies like ``path: "C:\\foo" , depth: 2``
    that aren't quite JSON. Walks key/value pairs with crude regexes."""
    out: Dict[str, Any] = {}
    # quoted values
    for k, v in re.findall(r'(\w+)\s*[:=]\s*"([^"]*)"', body):
        out[k] = v
    # numeric values
    for k, v in re.findall(r'(\w+)\s*[:=]\s*([0-9.-]+)', body):
        if k not in out:
            try:
                out[k] = float(v) if "." in v else int(v)
            except ValueError:
                out[k] = v
    return out


# ---------------------------------------------------------------------------
# Per-family extractors
# ---------------------------------------------------------------------------
def _extract_gemma(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """Gemma 3 / Gemma 4: ``<|toolcall>call:NAME{ARGS}<tool_call|>``

    Real-world example from a Gemma 4 emit::

        <|toolcall>call:viewimage{path:<|"|>C:\\foo.png<|"|>}<tool_call|>

    Note: Gemma collapses underscores in tool names and uses ``<|"|>`` as
    its quote escape token. We undo both."""
    raw_name = match.group(1) or ""
    body = match.group(2) or ""
    # Unescape Gemma's pipe-quote token
    body = body.replace('<|"|>', '"').replace("<|\"|>", '"')
    args = _try_json("{" + body + "}") if not body.lstrip().startswith("{") else _try_json(body)
    if args is None:
        args = _kv_pairs_to_dict(body)
    return {
        "name": _resolve_tool_name(raw_name, tool_names),
        "arguments": _force_json_str(args),
    }


def _extract_chatml(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """Hermes / Qwen 2.5 / Qwen 3 / NousResearch family:
    ``<tool_call>{"name": "X", "arguments": {...}}</tool_call>``

    This is the format llama-cpp-python's `chatml-function-calling` handler
    SHOULD parse natively, but it slips through whenever the user forgot to
    pass that --chat_format flag. We catch it as a safety net."""
    body = (match.group(1) or "").strip()
    obj = _try_json(body)
    if not obj:
        return None
    name = obj.get("name") or obj.get("function") or ""
    args = obj.get("arguments") or obj.get("parameters") or {}
    return {
        "name": _resolve_tool_name(name, tool_names),
        "arguments": _force_json_str(args),
    }


def _extract_llama3(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """Llama 3.1 / 3.2 / 3.3: ``<|python_tag|>NAME(ARGS)<|eom_id|>`` or
    ``<|python_tag|>{"name": "X", "parameters": {...}}<|eom_id|>``.

    Models also emit a bare ``{"name": ..., "parameters": ...}`` blob right
    after the system message when in JSON-mode. The regex catches both."""
    body = (match.group(1) or "").strip()
    obj = _try_json(body)
    if obj and isinstance(obj, dict) and ("name" in obj):
        return {
            "name": _resolve_tool_name(obj.get("name", ""), tool_names),
            "arguments": _force_json_str(obj.get("parameters") or obj.get("arguments") or {}),
        }
    # Fall back: ``name(arg1=val, arg2=val)`` python-call style
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*", body, re.DOTALL)
    if m:
        return {
            "name": _resolve_tool_name(m.group(1), tool_names),
            "arguments": _force_json_str(_kv_pairs_to_dict(m.group(2))),
        }
    return None


def _extract_mistral(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """Mistral / Mixtral / Magistral: ``[TOOL_CALLS][{"name": "X", "arguments": {...}}]``

    The arguments token always wraps a JSON array of one or more calls. The
    caller's outer loop iterates pattern matches, so we just return the first
    one here and let the outer loop pick up the rest from subsequent matches."""
    body = (match.group(1) or "").strip()
    if not body.startswith("["):
        body = "[" + body + "]"
    arr = _try_json(body)
    if not isinstance(arr, list) or not arr:
        return None
    first = arr[0] if isinstance(arr[0], dict) else None
    if not first:
        return None
    return {
        "name": _resolve_tool_name(first.get("name", ""), tool_names),
        "arguments": _force_json_str(first.get("arguments") or {}),
    }


def _extract_command_r(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """Cohere Command-R / R+: ``Action: ```json\\n[{"tool_name": "X", "parameters": {...}}]```

    Command-R uses an English "Action: " preamble + a JSON code fence. We grab
    the JSON body and treat the first item like Mistral."""
    body = (match.group(1) or "").strip()
    arr = _try_json(body)
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        item = arr[0]
        return {
            "name": _resolve_tool_name(item.get("tool_name") or item.get("name") or "", tool_names),
            "arguments": _force_json_str(item.get("parameters") or item.get("arguments") or {}),
        }
    return None


def _extract_granite(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """IBM Granite: ``<|tool_call|>{"name":"X","arguments":{...}}<|tool_call_end|>``

    Treated as a chatml variant — same JSON shape, different markers."""
    return _extract_chatml(match, tool_names)


def _extract_phi(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """Phi 3 / Phi 4: same JSON shape as ChatML but wrapped in
    ``<|tool|>{...}<|/tool|>`` or sometimes a bare JSON on a `<|assistant|>`
    follow-up. Reuse the chatml extractor."""
    return _extract_chatml(match, tool_names)


def _extract_xml_function(match: "re.Match[str]", tool_names: List[str]) -> Optional[Dict[str, Any]]:
    """Generic XML-style: ``<function=NAME>{ARGS}</function>`` — seen on a
    few finetunes (e.g. some Llama-3-based agentic finetunes)."""
    name = match.group(1) or ""
    body = (match.group(2) or "").strip()
    obj = _try_json(body) if body else None
    return {
        "name": _resolve_tool_name(name, tool_names),
        "arguments": _force_json_str(obj if obj is not None else _kv_pairs_to_dict(body)),
    }


# ---------------------------------------------------------------------------
# Pattern table
# ---------------------------------------------------------------------------
class _Pattern:
    """One pattern + the extractor that pulls structured data out of it."""
    __slots__ = ("family", "regex", "extract")

    def __init__(self, family: str, regex: str,
                 extract: Callable[[re.Match, List[str]], Optional[Dict[str, Any]]]):
        self.family = family
        self.regex = re.compile(regex, re.DOTALL | re.IGNORECASE)
        self.extract = extract


_PATTERNS: List[_Pattern] = [
    # Gemma — matches the user's real-world emit. Permissive on:
    #  - opening marker: `<|toolcall>` / `<|toolcall|>` / `<|tool_call|>` / `<|tool_call>`
    #  - closing marker: `<tool_call|>` / `<tool_call>` / `</tool_call>` / `<|tool_call|>`
    # Different Gemma fine-tunes shuffle the underscore + pipe placement,
    # and one popular Q4 emits `<|tool_call|>...</tool_call>` (XML-ish close).
    _Pattern("gemma",
             r"<\|tool[_]?call\|?>call:(\w+)\s*\{(.*?)\}(?:</?tool[_]?call\|?>|<\|tool[_]?call\|>)",
             _extract_gemma),

    # Hermes / Qwen / NousResearch ChatML tool-call block
    _Pattern("chatml",
             r"<tool_call>(.+?)</tool_call>",
             _extract_chatml),

    # IBM Granite — different markers, same JSON shape
    _Pattern("granite",
             r"<\|tool_call\|>(.+?)<\|tool_call_end\|>",
             _extract_granite),

    # Phi 3 / 4
    _Pattern("phi",
             r"<\|tool\|>(.+?)<\|/tool\|>",
             _extract_phi),

    # Llama 3.x — python_tag wrapper
    _Pattern("llama3",
             r"<\|python_tag\|>(.+?)<\|eom_id\|>",
             _extract_llama3),

    # Mistral / Mixtral [TOOL_CALLS][{...}]. Greedy capture of the whole array
    # so nested JSON braces don't trip the non-greedy. The outer `]` MUST be
    # included or _try_json will reject the malformed payload.
    _Pattern("mistral",
             r"\[TOOL_CALLS\]\s*(\[\s*\{[\s\S]*\}\s*\])",
             _extract_mistral),

    # Cohere Command-R / R+ Action: ```json [...]```
    _Pattern("command_r",
             r"Action:\s*```json\s*(\[.+?\])\s*```",
             _extract_command_r),

    # Generic XML-style <function=NAME>...</function>
    _Pattern("xml_function",
             r"<function=(\w+)>(.+?)</function>",
             _extract_xml_function),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse(text: str, tool_names: Optional[List[str]] = None
          ) -> Tuple[str, List[Dict[str, Any]]]:
    """Detect every tool-call block in `text`, return (cleaned_text, [calls]).

    `tool_names` is the list of registered tools — used to normalize each
    model's mangled name (Gemma drops `_`, etc.) back to the real one. Pass
    [] to disable normalization and accept whatever the model spit out.

    `cleaned_text` has every detected pattern excised so the chat surface
    isn't polluted with raw syntax. The returned tool_calls list is in
    OpenAI format ready to slot into an assistant message's `tool_calls`.
    """
    if not text:
        return text, []
    tool_names = tool_names or []
    calls: List[Dict[str, Any]] = []
    cleaned = text
    counter = 0

    # We iterate patterns in `_PATTERNS` order (most specific markers first
    # so a Hermes `<tool_call>` block inside a Gemma envelope still parses
    # as Gemma). Each match is excised from `cleaned` after extraction.
    for pat in _PATTERNS:
        new_cleaned = []
        last = 0
        for m in pat.regex.finditer(cleaned):
            new_cleaned.append(cleaned[last:m.start()])
            last = m.end()
            try:
                call = pat.extract(m, tool_names)
            except Exception:
                call = None
            if not call or not call.get("name"):
                # Couldn't extract — keep the original text rather than
                # silently dropping it (it might be a real <tool_call> we
                # don't yet support; better visible than vanished).
                new_cleaned.append(m.group(0))
                continue
            counter += 1
            calls.append({
                "id": f"call_{counter}",
                "type": "function",
                "function": call,
                "_family": pat.family,
            })
        new_cleaned.append(cleaned[last:])
        cleaned = "".join(new_cleaned)

    # Light cleanup: collapse the whitespace where tool-call blocks used
    # to live so the surrounding prose still reads naturally.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls


def has_tool_call(text: str) -> bool:
    """Cheap check: does `text` contain any known tool-call pattern? Used
    by the streaming guard to skip parsing when there's nothing to do."""
    if not text:
        return False
    for pat in _PATTERNS:
        if pat.regex.search(text):
            return True
    return False
