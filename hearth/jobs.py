"""Long-running command tracking — fire-and-watch shell jobs.

`run_command` is synchronous: the model fires a shell call, the agent blocks
until it returns, output lands in the tool result. That works for fast calls
(ls, cat, tasklist) but is a poor fit for things that legitimately take
minutes — `pip install torch`, a full HF download, a build, a heavy `web_search`.
Blocking the agent loop on those wastes user time AND eats the context budget
on every progress line.

This module adds a background mode: `start_job` spawns the command in a child
process, streams stdout+stderr to a file on disk, and returns a job_id
immediately. The model can keep working — visit files, answer the user, kick
off other tools — then check back with `job_status`/`job_output`/`job_kill`.

Storage layout:
  ~/Jarvis/jobs/<job_id>.json   metadata (cmd, status, exit_code, started_at)
  ~/Jarvis/jobs/<job_id>.out    streamed stdout+stderr (line-by-line append)

job_id format is `j-YYYYMMDD-HHMMSS-<6 hex>` — sortable + human-readable.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from .tools import WORKSPACE


JOBS_DIR = Path(WORKSPACE) / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# Cap job output size on disk so a runaway process can't fill the drive.
# 5 MB is enough to keep ~50K lines of normal command output.
_MAX_OUTPUT_BYTES = 5 * 1024 * 1024
# After this many days, completed jobs are auto-pruned from disk on next call.
_JOB_TTL_DAYS = 7

# Live process handles, keyed by job_id. Lost on process restart (which is
# fine — the job file on disk still reflects the last-known state, and a
# restarted Hearth can read it and report "status: completed" honestly).
_procs: Dict[str, subprocess.Popen] = {}
_procs_lock = threading.Lock()

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _new_job_id() -> str:
    return f"j-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _meta_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _out_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.out"


def _read_meta(job_id: str) -> Optional[Dict[str, Any]]:
    p = _meta_path(job_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(job_id: str, meta: Dict[str, Any]) -> None:
    try:
        _meta_path(job_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass


def _prune_old() -> None:
    """Drop job records older than _JOB_TTL_DAYS. Best-effort, called on
    list_jobs() so the directory doesn't grow forever."""
    cutoff = time.time() - (_JOB_TTL_DAYS * 86400)
    try:
        for p in JOBS_DIR.glob("j-*.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    _out_path(p.stem).unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def _spawn(command: str, cwd: str, shell: str) -> subprocess.Popen:
    """Spawn the child with stdout+stderr both pointed at the .out file in
    line-buffered append mode. Hidden console on Windows."""
    if sys.platform == "win32" and shell != "cmd":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        return subprocess.Popen(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=_NO_WINDOW,
        )
    if sys.platform == "win32":
        return subprocess.Popen(
            command, cwd=cwd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=_NO_WINDOW,
        )
    return subprocess.Popen(
        ["/bin/sh", "-c", command], cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )


def _drain(job_id: str, proc: subprocess.Popen) -> None:
    """Background thread: read stdout line-by-line, append to disk, update
    meta on completion. Caps output at _MAX_OUTPUT_BYTES so we don't fill
    the disk on a runaway process."""
    out_path = _out_path(job_id)
    written = 0
    truncated = False
    try:
        with open(out_path, "w", encoding="utf-8", errors="replace") as f:
            assert proc.stdout is not None
            for line in proc.stdout:
                if written < _MAX_OUTPUT_BYTES:
                    f.write(line)
                    f.flush()
                    written += len(line.encode("utf-8", errors="replace"))
                elif not truncated:
                    f.write(f"\n[OUTPUT TRUNCATED at {_MAX_OUTPUT_BYTES // (1024*1024)} MB]\n")
                    f.flush()
                    truncated = True
    except (OSError, ValueError):
        pass
    finally:
        try:
            exit_code = proc.wait(timeout=5)
        except Exception:
            exit_code = None
        meta = _read_meta(job_id) or {}
        # Don't overwrite a "killed" status set by kill_job — that's the
        # human-truthful state even if the process happened to exit cleanly.
        if meta.get("status") != "killed":
            meta["status"] = "completed" if exit_code == 0 else "failed"
        meta["exit_code"] = exit_code
        meta["finished_at"] = time.time()
        meta["output_truncated"] = truncated
        _write_meta(job_id, meta)
        with _procs_lock:
            _procs.pop(job_id, None)


def start_python_job(label: str, fn, args: Optional[dict] = None,
                     description: str = "") -> Dict[str, Any]:
    """Run a Python callable in a daemon thread; its return value is
    stashed at ~/Jarvis/jobs/<job_id>.result.json when it completes.
    Returns {ok, job_id, log_path, result_path}.

    Use this for slow IN-PROCESS work like disk_usage on a whole drive
    or a deep recursive scan — work that doesn't make sense as a shell
    subprocess but blocks the agent loop for minutes if run inline.
    """
    if not callable(fn):
        return {"ok": False, "error": "fn must be callable"}
    args = args or {}
    job_id = _new_job_id()
    meta = {
        "job_id": job_id,
        "command": f"python:{label}",
        "cwd": str(WORKSPACE),
        "shell": "python",
        "description": description or label,
        "status": "running",
        "started_at": time.time(),
        "exit_code": None,
        "pid": os.getpid(),
        "kind": "python",
    }
    _write_meta(job_id, meta)
    out_path = _out_path(job_id)
    result_path = JOBS_DIR / f"{job_id}.result.json"
    # Touch the output file so get_job's tail-read doesn't error out
    # before the worker produces its first line.
    try:
        out_path.write_text(f"[start] {label}\n", encoding="utf-8")
    except OSError:
        pass

    def _worker():
        try:
            result = fn(args) if args else fn()
            ok = True
            error = ""
        except Exception as e:
            result = None
            ok = False
            error = f"{type(e).__name__}: {e}"
        end_ts = time.time()
        m = _read_meta(job_id) or meta
        m["status"] = "completed" if ok else "failed"
        m["ended_at"] = end_ts
        m["exit_code"] = 0 if ok else 1
        if error:
            m["error"] = error
        _write_meta(job_id, m)
        try:
            payload = {"ok": ok, "result": result, "error": error,
                       "elapsed_s": round(end_ts - meta["started_at"], 2)}
            result_path.write_text(
                json.dumps(payload, ensure_ascii=False, default=str, indent=2),
                encoding="utf-8")
        except OSError:
            pass
        # Append a final marker so tail-readers see the close
        try:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(f"[end] status={m['status']} elapsed={m.get('ended_at', 0) - meta['started_at']:.1f}s\n")
                if error:
                    f.write(f"[error] {error}\n")
        except OSError:
            pass

    threading.Thread(target=_worker, name=f"hearth-job-{job_id}",
                     daemon=True).start()
    return {"ok": True, "job_id": job_id, "log_path": str(out_path),
            "result_path": str(result_path), "description": meta["description"]}


def get_job_result(job_id: str) -> Dict[str, Any]:
    """Return the JSON result of a completed python-job, or status info if
    still running / failed. Cheap to poll."""
    meta = _read_meta(job_id)
    if meta is None:
        return {"ok": False, "error": f"no such job: {job_id}"}
    result_path = JOBS_DIR / f"{job_id}.result.json"
    if meta.get("status") in ("running", "starting"):
        return {"ok": True, "status": meta["status"],
                "elapsed_s": round(time.time() - meta.get("started_at", time.time()), 1),
                "note": "still running; try again later"}
    if result_path.is_file():
        try:
            return {"ok": True, "status": meta["status"],
                    **json.loads(result_path.read_text(encoding="utf-8"))}
        except Exception as e:
            return {"ok": False, "error": f"result parse: {e}"}
    return {"ok": True, "status": meta.get("status", "unknown"),
            "error": meta.get("error", "no result file"),
            "log_path": str(_out_path(job_id))}


def start_job(command: str, cwd: Optional[str] = None,
              shell: str = "powershell",
              description: str = "") -> Dict[str, Any]:
    """Start a background job. Returns {ok, job_id, log_path}.

    The job's stdout+stderr stream to ~/Jarvis/jobs/<job_id>.out. Status
    transitions: starting → running → (completed|failed|killed).
    """
    if not command or not command.strip():
        return {"ok": False, "error": "command is empty"}
    cwd = cwd or WORKSPACE
    job_id = _new_job_id()
    meta = {
        "job_id": job_id,
        "command": command,
        "cwd": str(cwd),
        "shell": shell,
        "description": description or (command[:80] + ("…" if len(command) > 80 else "")),
        "status": "starting",
        "started_at": time.time(),
        "exit_code": None,
        "pid": None,
    }
    _write_meta(job_id, meta)
    try:
        proc = _spawn(command, str(cwd), shell)
    except Exception as e:
        meta["status"] = "failed"
        meta["error"] = f"spawn failed: {type(e).__name__}: {e}"
        _write_meta(job_id, meta)
        return {"ok": False, "error": meta["error"], "job_id": job_id}
    meta["pid"] = proc.pid
    meta["status"] = "running"
    _write_meta(job_id, meta)
    with _procs_lock:
        _procs[job_id] = proc
    threading.Thread(target=_drain, args=(job_id, proc), daemon=True).start()
    return {"ok": True, "job_id": job_id, "log_path": str(_out_path(job_id)),
            "description": meta["description"]}


def get_job(job_id: str, tail_lines: int = 40) -> Dict[str, Any]:
    """Return current status + last N lines of output. Cheap to poll."""
    meta = _read_meta(job_id)
    if meta is None:
        return {"ok": False, "error": f"no such job: {job_id}"}
    out_path = _out_path(job_id)
    output_tail = ""
    if out_path.exists():
        try:
            data = out_path.read_text(encoding="utf-8", errors="replace")
            lines = data.splitlines()
            if tail_lines and len(lines) > tail_lines:
                output_tail = "\n".join(lines[-tail_lines:])
            else:
                output_tail = data
        except OSError:
            pass
    out = dict(meta)
    out["ok"] = True
    out["output_tail"] = output_tail
    if meta.get("started_at"):
        end = meta.get("finished_at") or time.time()
        out["elapsed_s"] = round(end - meta["started_at"], 1)
    return out


def wait_job(job_id: str, timeout_s: float = 30.0,
             tail_lines: int = 200) -> Dict[str, Any]:
    """Block up to timeout_s for the job to finish. Returns the latest status
    + output. If timeout fires while running, returns status='running' so the
    caller can decide to wait again."""
    deadline = time.time() + max(0.0, timeout_s)
    poll = 0.25
    while True:
        meta = _read_meta(job_id) or {}
        if meta.get("status") in ("completed", "failed", "killed"):
            return get_job(job_id, tail_lines=tail_lines)
        if time.time() >= deadline:
            return get_job(job_id, tail_lines=tail_lines)
        time.sleep(poll)
        if poll < 2.0:
            poll = min(2.0, poll * 1.5)


def kill_job(job_id: str) -> Dict[str, Any]:
    """Terminate a running job. No-op if it already finished."""
    meta = _read_meta(job_id)
    if meta is None:
        return {"ok": False, "error": f"no such job: {job_id}"}
    if meta.get("status") in ("completed", "failed", "killed"):
        return {"ok": True, "already": True, "status": meta["status"]}
    # Stamp status='killed' to disk BEFORE terminating. _drain races with us
    # to update meta when the process exits, and it checks status != 'killed'
    # to decide whether to overwrite. If we kill first then write, _drain can
    # read meta in the "still running" window and clobber us with "failed".
    meta["status"] = "killed"
    meta["finished_at"] = time.time()
    _write_meta(job_id, meta)
    with _procs_lock:
        proc = _procs.get(job_id)
    if proc is None:
        return {"ok": True, "note": "process handle lost — marked killed"}
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as e:
        return {"ok": False, "error": f"kill failed: {type(e).__name__}: {e}"}
    return {"ok": True, "status": "killed"}


def list_jobs(active_only: bool = False) -> List[Dict[str, Any]]:
    """List jobs we know about, newest first. Prunes stale records on the way."""
    _prune_old()
    rows: List[Dict[str, Any]] = []
    for p in JOBS_DIR.glob("j-*.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if active_only and m.get("status") not in ("starting", "running"):
            continue
        rows.append({
            "job_id": m.get("job_id"),
            "status": m.get("status"),
            "description": m.get("description"),
            "started_at": m.get("started_at"),
            "exit_code": m.get("exit_code"),
            "elapsed_s": round((m.get("finished_at") or time.time()) - (m.get("started_at") or time.time()), 1),
        })
    rows.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
    return rows
