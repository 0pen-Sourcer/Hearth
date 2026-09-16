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


def _ident_neighbour(s: str, start: int, length: int) -> bool:
    """True when the run at s[start:start+length] is part of a longer word, so
    a bare None/True/False is not confused with Nonefoo or xTrue."""
    before = s[start - 1] if start > 0 else ""
    after = s[start + length] if start + length < len(s) else ""
    return (before.isalnum() or before == "_"
            or after.isalnum() or after == "_")


def _repair_json_like(s: str) -> str:
    """Rewrite almost-JSON into JSON without touching string CONTENT.

    Handles single-quoted keys and values, bare None/True/False, trailing
    commas before a closing brace or bracket, and the raw Windows path
    backslashes models emit. Everything inside a string literal is copied
    through as written, apart from the escaping JSON itself requires. A
    blanket replace would rewrite the inside of values too, which dropped an
    argument carrying an apostrophe and corrupted one carrying the word None."""
    out: List[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in ('"', "'"):
            quote = c
            j = i + 1
            body: List[str] = []
            while j < n:
                ch = s[j]
                if ch == "\\":
                    nxt = s[j + 1] if j + 1 < n else ""
                    if nxt and nxt in '"\/bfnrtu':
                        body.append(ch)
                        body.append(nxt)
                        j += 2
                        continue
                    # A lone backslash, which a raw Windows path carries.
                    body.append("\\\\")
                    j += 1
                    continue
                if ch == quote:
                    break
                if ch == '"':
                    body.append('\\"')   # inner " inside a single-quoted literal
                    j += 1
                    continue
                body.append(ch)
                j += 1
            out.append('"' + "".join(body) + '"')
            i = j + 1
            continue
        for word, repl in (("None", "null"), ("True", "true"), ("False", "false")):
            if s.startswith(word, i) and not _ident_neighbour(s, i, len(word)):
                out.append(repl)
                i += len(word)
                break
        else:
            if c == ",":
                k = i + 1
                while k < n and s[k] in " \t\r\n":
                    k += 1
                if k < n and s[k] in "}]":
                    i += 1          # trailing comma, drop it
                    continue
            out.append(c)
            i += 1
    return "".join(out)


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
    # Repair common breakages. The rewrite is string-aware; see
    # _repair_json_like for why a blanket replace was wrong.
    try:
        return json.loads(_repair_json_like(s))
    except Exception:
        pass
    # Python-literal shaped input (single quotes, None/True/False) parses
    # correctly here with no rewriting at all. literal_eval evaluates literals
    # only, it does not execute code.
    try:
        import ast
        val = ast.literal_eval(s)
        if isinstance(val, dict):
            return val
    except Exception:
        pass
    return None


def _first_json_object(s: str) -> str:
    """Return the first balanced {...} in s, honouring string literals so a
    brace inside a value does not end the scan. Empty string when there is none.

    Models leave a tool-call tag unclosed often enough that matching on the tag
    alone runs past the end of one call and into the next. Scanning the braces
    finds where the object really ends, whatever the tags do."""
    start = s.find("{")
    if start < 0:
        return "", False
    depth = 0
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"' or c == "'":
            quote = c
            i += 1
            while i < n:
                if s[i] == "\\":
                    i += 2
                    continue
                if s[i] == quote:
                    break
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
        i += 1
    return ""


def _complete_truncated_json(s: str) -> "tuple[str, bool]":
    """Close an object the model stopped writing partway through.

    A long generation is regularly cut a brace or a quote short, and the result
    is a complete, correct call with its tail missing. Discarding it loses the
    whole thing, so finish what is open: shut an unterminated string, drop a
    dangling key or comma, and close each container still on the stack. Returns
    "" when there is nothing recoverable.

    Returns (json, severed). `severed` is True when a STRING was still open at
    the end, which means a value was cut mid-way and whatever it holds is a
    fragment. False means every value arrived whole and only closing braces
    were missing, which is a different and harmless kind of incomplete.

    Only reached after a strict parse has already failed."""
    start = s.find("{")
    if start < 0:
        return "", False
    stack = []
    in_str = False
    esc = False
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in "{[":
                stack.append("}" if c == "{" else "]")
            elif c in "}]":
                if stack:
                    stack.pop()
                if not stack:
                    return s[start:i + 1], False   # balanced already
        i += 1
    if not stack:
        return "", False
    severed = in_str
    out = s[start:]
    if in_str:
        if esc:                                # trailing lone backslash
            out = out[:-1]
        out += '"'
    # A trailing comma, or a key with no value yet, is not valid JSON.
    stripped = out.rstrip()
    while stripped and stripped[-1] in ",:":
        stripped = stripped[:-1].rstrip()
        if stripped.endswith('"'):             # drop the orphaned key too
            q = stripped.rfind('"', 0, len(stripped) - 1)
            if q > 0 and stripped[:q].rstrip().endswith(("{", ",")):
                stripped = stripped[:q].rstrip()
                if stripped.endswith(","):
                    stripped = stripped[:-1].rstrip()
    out = stripped
    return out + "".join(reversed(stack)), severed


def loads_lenient(s: str) -> Optional[Dict[str, Any]]:
    """Public entry for parsing a tool call's `arguments` string. Strict JSON
    first, then the string-aware repair, then a Python-literal read. Returns
    None when the text cannot be read as an object, so the caller reports a
    malformed call to the model rather than running a guess."""
    out = _try_json(s)
    return out if isinstance(out, dict) else None


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
        # An unclosed tag makes the captured body run past the end of this call,
        # so json.loads reports trailing data and the whole call used to be
        # dropped in silence. Take the first balanced object instead.
        inner = _first_json_object(body)
        if inner:
            obj = _try_json(inner)
    if not obj:
        # The closing tag is not a reliable boundary: a file whose CONTENT
        # contains </tool_call> (documentation of this very format, say) cuts
        # the captured body early, and repairing that stub would write a
        # truncated file with confidence. Rescan from the opening tag over the
        # whole message and let the braces decide where the object really ends.
        whole = match.string[match.start():]
        wider = _first_json_object(whole)
        if wider and len(wider) > len(body):
            obj = _try_json(wider)
    truncated = False
    if not obj:
        # A long generation is often cut a brace or a quote short. The call is
        # otherwise complete, so finish it rather than losing the whole write.
        patched, severed = _complete_truncated_json(body)
        if patched:
            obj = _try_json(patched)
            truncated = bool(obj) and severed
    if obj and truncated:
        # A value cut mid-string is a fragment, and running a call built from
        # one is how a write ends up replacing a file with the part that
        # arrived. Nothing distinguishes a fragment from a finished value, so
        # a severed call never runs, whichever tool it names. Its markup stays
        # in the text, which is what marks it unreadable, and the model is
        # asked to send it again.
        #
        # Closing braces are a different matter. Every value arrived whole and
        # only the structure is missing, which a local model emitting calls as
        # text does constantly, so that still recovers.
        return None
    if obj:
        name = obj.get("name") or obj.get("function") or ""
        args = obj.get("arguments") or obj.get("parameters")
        if not args:
            # Some models flatten the call to {"name": X, "path": ..., ...}
            # rather than nesting an arguments object. Treat what is left as
            # the arguments rather than running the tool with none.
            args = {k: v for k, v in obj.items()
                    if k not in ("name", "function", "arguments", "parameters",
                                 "type", "id")}
        return {
            "name": _resolve_tool_name(name, tool_names),
            "arguments": _force_json_str(args or {}),
        }
    # Fallback for hybrid XML emitted by small local models:
    # ``<tool_call><name>view_image</name>{"path":"C:\\..."} </tool_call>``
    # or ``<tool_call><name>run_command</name><arguments>...</arguments></tool_call>``
    # or ``<tool_call><invoke name="run_command"><parameter name="command">...</parameter></invoke></tool_call>``
    m_name = re.search(r"<name[^>]*>\s*([^<]+?)\s*</name>", body, re.IGNORECASE)
    if not m_name:
        m_name = re.search(r'<invoke[^>]*name\s*=\s*["\']([^"\']+)["\']', body, re.IGNORECASE)
    if m_name:
        raw_name = m_name.group(1).strip().strip('"').strip("'").strip(":")
        after = body[m_name.end():].strip()
        # Collect <parameter name="k">v</parameter> pairs if present (invoke style)
        param_pairs = re.findall(r'<parameter[^>]*name\s*=\s*["\']([^"\']+)["\'][^>]*>([\s\S]*?)</parameter>', after, re.IGNORECASE)
        if param_pairs:
            kv: Dict[str, Any] = {}
            for k, v in param_pairs:
                kv[k.strip()] = v.strip()
            return {"name": _resolve_tool_name(raw_name, tool_names), "arguments": _force_json_str(kv)}
        # Strip optional <arguments> wrapper
        m_args = re.search(r"<arguments[^>]*>([\s\S]*?)</arguments>", after, re.IGNORECASE)
        if m_args:
            after = m_args.group(1).strip()
        obj2 = _try_json(after) if after else None
        if isinstance(obj2, dict):
            if "name" in obj2 or "function" in obj2:
                nm = obj2.get("name") or obj2.get("function") or raw_name
                ag = obj2.get("arguments") or obj2.get("parameters") or {}
                return {"name": _resolve_tool_name(nm, tool_names), "arguments": _force_json_str(ag)}
            return {"name": _resolve_tool_name(raw_name, tool_names), "arguments": _force_json_str(obj2)}
        if after:
            kv = _kv_pairs_to_dict(after)
            if kv:
                return {"name": _resolve_tool_name(raw_name, tool_names), "arguments": _force_json_str(kv)}
            cleaned = after.strip().strip('"').strip("'").strip("{}").strip()
            if cleaned and ("\\" in cleaned or "/" in cleaned):
                return {"name": _resolve_tool_name(raw_name, tool_names), "arguments": _force_json_str({"path": cleaned})}
        return {"name": _resolve_tool_name(raw_name, tool_names), "arguments": _force_json_str({})}
    # Last shape, and a real one: the tool NAME arrives as a parameter rather
    # than as a tag. A model that has seen several tool dialects mixes them,
    # and one writes the whole call as parameters, taking the name from the
    # placeholder in the format example it was shown:
    #   <function_calls>
    #   <parameter name="func_name">grep_search</parameter>
    #   <parameter name="path">C:\some\dir</parameter>
    #   </function_calls>
    # The intent is not ambiguous, so it runs rather than being thrown away and
    # leaving the model to guess at a format for the rest of the conversation.
    pairs = re.findall(
        r'<parameter[^>]*name\s*=\s*["\']([^"\']+)["\'][^>]*>([\s\S]*?)</parameter>',
        body, re.IGNORECASE)
    if pairs:
        kv = {k.strip(): v.strip() for k, v in pairs}
        for key in ("func_name", "function_name", "tool_name", "name",
                    "function", "tool"):
            if key in kv:
                nm = kv.pop(key)
                return {"name": _resolve_tool_name(nm, tool_names),
                        "arguments": _force_json_str(kv)}
    return None


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
             r"<tool_call>(.+?)(?:</tool_call>|(?=<tool_call>)|$)",
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

    # Bare-JSON fallback. Some models (Qwen 2.5 among them) intermittently drop
    # the <tool_call> wrapper and emit just {"name": ..., "arguments": {...}} as
    # the whole reply. Catch that when nothing tagged matched — but only when the
    # name resolves to a REAL registered tool, so an ordinary JSON answer the
    # model happened to produce isn't hijacked into a bogus tool call.
    if not calls:
        stripped = cleaned.strip()
        fence = re.match(r"^```(?:json)?\s*([\s\S]+?)\s*```$", stripped)
        if fence:
            stripped = fence.group(1).strip()
        if stripped.startswith("{") and '"name"' in stripped:
            obj = _try_json(stripped)
            if isinstance(obj, dict) and obj.get("name"):
                nm = _resolve_tool_name(str(obj.get("name")), tool_names)
                if nm in tool_names:
                    args = obj.get("arguments") or obj.get("parameters") or {}
                    calls.append({
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": nm, "arguments": _force_json_str(args)},
                        "_family": "bare_json",
                    })
                    cleaned = ""

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
