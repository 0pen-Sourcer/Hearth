"""Hearth Plus Relay: JARVIS on the phone, through Hearth's relay server.

The PC connects outward to the relay (nothing to open on the router), one room
per paired phone, and everything between them is end-to-end encrypted
(hearth/relay_crypto.py). Messages from the phone run through the same core as
the other chat apps (hearth/channel_core.py): the conversation is a Hearth
chat, risky steps ask first (as Allow / Deny on the phone), files go both ways.

State in ~/.hearth/relay.json: the PC's key, the paired phones, and a pairing
in progress. Pairing: "Pair a phone" makes a QR code; the phone scans it and
sends its key with proof it saw the code; both show six digits; confirming on
the PC remembers the phone.

    python -m hearth.relay_bridge
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from . import channel_core as cc
from . import env as _env
from . import relay_crypto as R

_env.setdefault("NO_GUI", "1")

STATE_FILE = os.path.join(os.path.expanduser("~"), ".hearth", "relay.json")
DEFAULT_RELAY = os.environ.get("HEARTH_RELAY_URL") or ""
PAIR_LIFE_S = 600
_LOCK = threading.Lock()


# ---------------------------------------------------------------- state

def _read() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, STATE_FILE)


def relay_url() -> str:
    try:
        from .web import _load_settings
        u = (_load_settings().get("relay_url") or "").strip()
    except Exception:
        u = ""
    return (u or DEFAULT_RELAY).rstrip("/")


def _pc_key():
    with _LOCK:
        d = _read()
        if not d.get("pc_key"):
            d["pc_key"] = R.b64(R.raw_private(R.new_private()))
            _write(d)
    return R.private_from(R.unb64(d["pc_key"]))


def phone_id(phone_public: bytes) -> str:
    return hashlib.sha256(phone_public).hexdigest()[:12]


def start_pairing(name: str = "") -> Dict[str, Any]:
    """A new pairing: what the QR code holds, as a link that opens the phone app."""
    url = relay_url()
    if not url:
        return {"ok": False, "error": "no relay address yet (Settings > Plus)"}
    pc = _pc_key()
    ps = secrets.token_bytes(16)
    payload = R.pairing_payload(url.replace("https://", "wss://").replace("http://", "ws://"), R.raw_public(pc), ps)
    with _LOCK:
        d = _read()
        d["pending"] = {"ps": R.b64(ps), "room": payload["room"], "at": time.time(), "name": name or "",
                        "phone": "", "digits": ""}
        _write(d)
    frag = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    # The pairing goes after #: a browser never sends that part to the server.
    return {"ok": True, "link": f"{url}/#pair={frag}", "room": payload["room"]}


def pairing_status() -> Dict[str, Any]:
    p = _read().get("pending") or {}
    if not p or time.time() - float(p.get("at") or 0) > PAIR_LIFE_S:
        return {"state": "none"}
    return {"state": "confirm" if p.get("digits") else "waiting", "digits": p.get("digits") or ""}


def confirm_pairing(name: str = "") -> Dict[str, Any]:
    """The person saw the same six digits on both screens: remember the phone."""
    with _LOCK:
        d = _read()
        p = d.get("pending") or {}
        if not p.get("phone") or time.time() - float(p.get("at") or 0) > PAIR_LIFE_S:
            return {"ok": False, "error": "no phone is waiting to be paired; scan the code again"}
        phones = [x for x in d.get("phones", []) if x.get("pub") != p["phone"]]
        phones.append({"pub": p["phone"], "ps": p["ps"], "room": p["room"], "name": name or p.get("name") or "Phone",
                       "at": time.time()})
        d["phones"], d["pending"] = phones, {}
        d["confirmed"] = p["room"]
        _write(d)
    return {"ok": True}


def cancel_pairing() -> Dict[str, Any]:
    with _LOCK:
        d = _read()
        d["pending"] = {}
        _write(d)
    return {"ok": True}


def phones() -> List[Dict[str, Any]]:
    return [{"id": phone_id(R.unb64(p["pub"])), "name": p.get("name") or "Phone", "at": p.get("at")}
            for p in _read().get("phones", [])]


def forget_phone(pid: str) -> Dict[str, Any]:
    with _LOCK:
        d = _read()
        keep = [p for p in d.get("phones", []) if phone_id(R.unb64(p["pub"])) != pid]
        gone = len(keep) != len(d.get("phones", []))
        d["phones"] = keep
        _write(d)
    return {"ok": gone}


# ---------------------------------------------------------------- the adapter

class Relay(cc.Adapter):
    """Sends go out as encrypted frames on the phone's live connection, or to
    its mailbox on the server when it's away."""
    name, label, max_len = "relay", "Phone", 20000

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.rooms: Dict[str, "Room"] = {}     # chat (phone id) -> room

    def _push(self, chat: str, obj: Dict[str, Any]) -> Optional[str]:
        room = self.rooms.get(str(chat))
        if room is None:
            return None
        mid = secrets.token_hex(6)
        obj = dict(obj, id=mid)
        fut = asyncio.run_coroutine_threadsafe(room.deliver(obj), self.loop)
        try:
            fut.result(15)
        except Exception:
            return None
        return mid

    def send(self, chat, text, reply_to=None):
        return self._push(chat, {"type": "text", "text": text})

    def edit(self, chat, msg_id, text):
        return self._push(chat, {"type": "edit", "of": msg_id, "text": text}) is not None

    def ask(self, chat, text):
        return self._push(chat, {"type": "ask", "text": text})

    def send_file(self, chat, path):
        try:
            if os.path.getsize(path) > 15_000_000:
                return
            with open(path, "rb") as f:
                data = R.b64(f.read())
        except OSError:
            return
        self._push(chat, {"type": "file", "name": os.path.basename(path), "data": data})

    def typing(self, chat):
        self._push(chat, {"type": "typing"})


class Room:
    """One phone's room on the relay: its connection, the session with the
    phone when it's there, and the pairing when it's new."""

    def __init__(self, bridge: "Bridge", room: str, ps: bytes, phone: Optional[bytes]):
        self.bridge, self.room, self.ps, self.phone = bridge, room, ps, phone
        self.ws = None
        self.chan: Optional[R.Channel] = None
        self.peer_up = False
        self.pc = bridge.pc

    @property
    def chat(self) -> str:
        return phone_id(self.phone) if self.phone else "pending"

    def box_key(self) -> bytes:
        return R.mailbox_key(R.dh(self.pc, self.phone), self.room)

    async def deliver(self, obj: Dict[str, Any]) -> None:
        if self.ws is None:
            return
        if self.chan is not None and self.peer_up:
            await self.ws.send(json.dumps(self.chan.seal(obj)))
        elif self.phone:
            await self.ws.send(json.dumps(R.seal_box(self.box_key(), obj)))

    async def run(self) -> None:
        import websockets
        url = f"{self.bridge.url}/room/{self.room}?role=pc&licence={self.bridge.licence}"
        backoff = 1.0
        while not self.bridge.stopping:
            try:
                async with websockets.connect(url, max_size=1 << 20, ping_interval=25) as ws:
                    self.ws, backoff = ws, 1.0
                    async for raw in ws:
                        await self.on_frame(json.loads(raw))
            except Exception as e:
                if "402" in str(e):
                    print("[relay] the relay needs Hearth Plus for this PC; stopping.", flush=True)
                    self.bridge.stopping = True
                    return
            self.ws, self.chan, self.peer_up = None, None, False
            if self.bridge.stopping or self.room not in self.bridge.wanted():
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def on_frame(self, f: Dict[str, Any]) -> None:
        t = f.get("t")
        if t == "peer":
            self.peer_up = bool(f.get("up"))
            if not self.peer_up:
                self.chan = None
            return
        if t == "pair":                       # a phone that scanned the code
            phone = R.unb64(f.get("phone") or "")
            if self.phone or not R.proof_ok(self.ps, phone, f.get("proof") or ""):
                return
            digits = R.check_digits(R.dh(self.pc, phone), self.room)
            with _LOCK:
                d = _read()
                if (d.get("pending") or {}).get("room") == self.room:
                    d["pending"].update(phone=R.b64(phone), digits=digits)
                    _write(d)
            print(f"[relay] a phone asks to pair; confirm {digits} on the PC", flush=True)
            return
        if t == "hello":                      # a paired phone, connecting
            phone = R.unb64(f.get("phone") or "")
            if not self.phone or phone != self.phone:
                return
            eph = R.new_private()
            phone_eph = R.unb64(f.get("eph") or "")
            to_phone, to_pc = R.session_keys(R.dh(self.pc, phone), R.dh(eph, phone_eph), self.room,
                                             R.raw_public(eph), phone_eph)
            self.chan = R.Channel(to_phone, to_pc)
            self.peer_up = True
            self.bridge.adapter.rooms[self.chat] = self
            await self.ws.send(json.dumps({"t": "hello", "eph": R.b64(R.raw_public(eph))}))
            return
        if t == "msg" and self.chan is not None:
            m = self.chan.open(f)
            if m:
                await asyncio.to_thread(self.bridge.on_message, self, m)

    async def tell_paired(self) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps({"t": "paired"}))


class Bridge:
    def __init__(self):
        self.url = relay_url().replace("https://", "wss://").replace("http://", "ws://")
        from . import plus
        self.licence = (plus._read().get("key") or "")
        self.pc = _pc_key()
        self.stopping = False
        self.loop = asyncio.new_event_loop()
        self.adapter = Relay(self.loop)
        self.hub = cc.Hub(self.adapter, configured_senders=["phone"])
        self.rooms: Dict[str, Room] = {}

    def wanted(self) -> Dict[str, Dict[str, Any]]:
        """room -> {ps, phone}: every paired phone's room, and a pairing in progress."""
        d = _read()
        out = {p["room"]: {"ps": p["ps"], "phone": p["pub"]} for p in d.get("phones", [])}
        p = d.get("pending") or {}
        if p.get("room") and time.time() - float(p.get("at") or 0) < PAIR_LIFE_S:
            out.setdefault(p["room"], {"ps": p["ps"], "phone": ""})
        return out

    def on_message(self, room: Room, m: Dict[str, Any]) -> None:
        if m.get("type") != "user":
            return
        files = []
        for f in m.get("files") or []:
            try:
                files.append(cc.save_upload(R.unb64(f.get("data") or ""), f.get("name") or "file"))
            except Exception:
                continue
        voice = ""
        if m.get("voice"):
            voice = cc.save_upload(R.unb64(m["voice"]), "voice-note.webm")
        self.hub.receive(room.chat, "phone", str(m.get("text") or ""), name="Phone", voice=voice, files=files)

    async def keep_rooms(self) -> None:
        """Join new rooms (a pairing started, a phone confirmed), leave dropped ones."""
        tasks: Dict[str, asyncio.Task] = {}
        while not self.stopping:
            want = self.wanted()
            confirmed = _read().get("confirmed")
            for room, info in want.items():
                phone = R.unb64(info["phone"]) if info["phone"] else None
                r = self.rooms.get(room)
                if r is None:
                    r = self.rooms[room] = Room(self, room, R.unb64(info["ps"]), phone)
                    tasks[room] = asyncio.create_task(r.run())
                elif phone and r.phone is None:          # just confirmed on the PC
                    r.phone = phone
                    if confirmed == room:
                        await r.tell_paired()
            for room in list(self.rooms):
                if room not in want:
                    t = tasks.pop(room, None)
                    if t:
                        t.cancel()
                    self.rooms.pop(room, None)
            await asyncio.sleep(1.0)

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.keep_rooms())
        except KeyboardInterrupt:
            pass


def run() -> None:
    if not relay_url():
        print("[relay] no relay address yet (Settings > Plus).", flush=True)
        return
    cc.use_saved_brain(lambda s: print("[relay] " + s, flush=True))
    print("[relay] up. Phones paired:", len(phones()), flush=True)
    Bridge().run()


if __name__ == "__main__":
    from .bridge_status import follow_parent
    follow_parent()
    run()
