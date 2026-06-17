"""Read and send email over IMAP/SMTP with an app password — no OAuth.

The lightest possible "my local assistant can do my email" path: the user makes
an app password (Gmail/Outlook/etc. all offer them), drops four-ish settings,
and Hearth can read recent mail and send replies. No Google Cloud project, no
OAuth dance, no third-party broker. Uses only stdlib (imaplib / smtplib /
email), so no new dependencies.

Config — env vars (HEARTH_* or JARVIS_* prefix), or ~/.hearth/email.json:
    HEARTH_EMAIL_ADDRESS   you@gmail.com
    HEARTH_EMAIL_PASSWORD  the APP PASSWORD (not your login password)
    HEARTH_IMAP_HOST       optional — auto-detected from the address domain
    HEARTH_SMTP_HOST       optional — auto-detected
    HEARTH_IMAP_PORT       optional (default 993, SSL)
    HEARTH_SMTP_PORT       optional (default 587, STARTTLS)

Security: the password is an app password the user can revoke any time; it
lives only on this machine. read_inbox/send_email are risky tools, so they hit
the normal permission prompt before running.
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Dict, List, Optional

_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "email.json")

# domain -> (imap_host, smtp_host). Covers the providers people actually use.
_PROVIDERS = {
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "googlemail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "outlook.com": ("outlook.office365.com", "smtp.office365.com"),
    "hotmail.com": ("outlook.office365.com", "smtp.office365.com"),
    "live.com": ("outlook.office365.com", "smtp.office365.com"),
    "office365.com": ("outlook.office365.com", "smtp.office365.com"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
    "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com"),
    "me.com": ("imap.mail.me.com", "smtp.mail.me.com"),
    "proton.me": ("127.0.0.1", "127.0.0.1"),  # needs Proton Bridge; hosts overridable
    "fastmail.com": ("imap.fastmail.com", "smtp.fastmail.com"),
}


def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return ""


def _config() -> Dict[str, Any]:
    """Merge env vars over an optional ~/.hearth/email.json."""
    cfg: Dict[str, Any] = {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        cfg = {}
    addr = _env("HEARTH_EMAIL_ADDRESS", "JARVIS_EMAIL_ADDRESS") or cfg.get("address", "")
    pwd = _env("HEARTH_EMAIL_PASSWORD", "JARVIS_EMAIL_PASSWORD") or cfg.get("password", "")
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    imap_d, smtp_d = _PROVIDERS.get(domain, ("", ""))
    return {
        "address": addr,
        "password": pwd,
        "imap_host": _env("HEARTH_IMAP_HOST", "JARVIS_IMAP_HOST") or cfg.get("imap_host") or imap_d,
        "smtp_host": _env("HEARTH_SMTP_HOST", "JARVIS_SMTP_HOST") or cfg.get("smtp_host") or smtp_d,
        "imap_port": int(_env("HEARTH_IMAP_PORT", "JARVIS_IMAP_PORT") or cfg.get("imap_port") or 993),
        "smtp_port": int(_env("HEARTH_SMTP_PORT", "JARVIS_SMTP_PORT") or cfg.get("smtp_port") or 587),
    }


def is_configured() -> bool:
    c = _config()
    return bool(c["address"] and c["password"] and c["imap_host"] and c["smtp_host"])


def _missing_msg(c: Dict[str, Any]) -> str:
    miss = [k for k in ("address", "password", "imap_host", "smtp_host") if not c.get(k)]
    return ("email not set up — missing " + ", ".join(miss) + ". Set HEARTH_EMAIL_ADDRESS "
            "+ HEARTH_EMAIL_PASSWORD (an app password), or ~/.hearth/email.json. "
            "Hosts auto-detect for gmail/outlook/yahoo/icloud/fastmail; set "
            "HEARTH_IMAP_HOST/HEARTH_SMTP_HOST for others.")


def _decode(s: Optional[str]) -> str:
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s


def _body_text(msg: email.message.Message, limit: int = 2000) -> str:
    """Best-effort plain-text body, truncated."""
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    parts.append(part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"))
                except Exception:
                    continue
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"))
        except Exception:
            pass
    text = "\n".join(p.strip() for p in parts if p).strip()
    return (text[:limit] + " …[truncated]") if len(text) > limit else text


def read_inbox(limit: int = 10, unread_only: bool = False,
               folder: str = "INBOX", with_body: bool = False) -> Dict[str, Any]:
    """Fetch the most recent messages' headers (and optionally bodies)."""
    c = _config()
    if not is_configured():
        return {"ok": False, "error": _missing_msg(c)}
    limit = max(1, min(int(limit or 10), 50))
    try:
        ctx = ssl.create_default_context()
        M = imaplib.IMAP4_SSL(c["imap_host"], c["imap_port"], ssl_context=ctx)
        M.login(c["address"], c["password"])
        M.select(folder, readonly=True)
        crit = "UNSEEN" if unread_only else "ALL"
        typ, data = M.search(None, crit)
        ids = data[0].split()
        ids = ids[-limit:][::-1]  # most recent first
        out: List[Dict[str, Any]] = []
        for i in ids:
            typ, md = M.fetch(i, "(RFC822)")
            if typ != "OK" or not md or not md[0]:
                continue
            msg = email.message_from_bytes(md[0][1])
            item = {
                "from": _decode(msg.get("From")),
                "subject": _decode(msg.get("Subject")),
                "date": _decode(msg.get("Date")),
            }
            try:
                d = parsedate_to_datetime(msg.get("Date"))
                if d:
                    item["date_iso"] = d.isoformat()
            except Exception:
                pass
            if with_body:
                item["body"] = _body_text(msg)
            out.append(item)
        M.close(); M.logout()
        return {"ok": True, "folder": folder, "count": len(out), "messages": out}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "error": f"IMAP login/fetch failed: {e} "
                "(if Gmail/Yahoo, use an APP PASSWORD, not your normal password)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def send_email(to: str, subject: str, body: str,
               cc: str = "", reply_to: str = "") -> Dict[str, Any]:
    """Send a plain-text email from the configured address."""
    c = _config()
    if not is_configured():
        return {"ok": False, "error": _missing_msg(c)}
    if not (to or "").strip():
        return {"ok": False, "error": "recipient 'to' is required"}
    msg = EmailMessage()
    msg["From"] = c["address"]
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")
    recipients = [a for a in [parseaddr(x)[1] for x in (to + "," + cc).split(",")] if a]
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(c["smtp_host"], c["smtp_port"], timeout=30) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(c["address"], c["password"])
            s.send_message(msg, from_addr=c["address"], to_addrs=recipients)
        return {"ok": True, "to": recipients, "subject": msg["Subject"]}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "error": f"SMTP auth failed: {e} "
                "(use an APP PASSWORD, not your normal login password)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
