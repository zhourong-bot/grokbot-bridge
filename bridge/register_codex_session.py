#!/usr/bin/env python3
"""记下当前/最近 Codex session，供桥回流 exec resume。失败静默。"""
import json, os, sys, time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
OUT = BRIDGE / "state" / "codex_sessions.jsonl"
CUR = BRIDGE / "state" / "codex_current.json"
SESS_ROOT = Path.home() / ".codex" / "sessions"

def latest_from_disk(prefer_cwd=None):
    if not SESS_ROOT.exists():
        return None
    files = sorted(SESS_ROOT.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    def meta(p):
        try:
            line = p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
            j = json.loads(line)
            pl = j.get("payload") or {}
            return {
                "session_id": pl.get("session_id") or pl.get("id"),
                "cwd": pl.get("cwd") or "",
                "path": str(p),
            }
        except Exception:
            return None
    rows = [m for m in (meta(p) for p in files[-40:]) if m and m.get("session_id")]
    if not rows:
        return None
    if prefer_cwd:
        hit = [r for r in rows if prefer_cwd in str(r.get("cwd") or "")]
        if hit:
            return hit[-1]
    return rows[-1]

def write_rec(rec):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    CUR.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def from_hook():
    data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
    except Exception:
        data = {}
    sid = data.get("session_id") or data.get("sessionId") or (data.get("payload") or {}).get("session_id")
    cwd = data.get("cwd") or os.environ.get("PWD") or ""
    if not sid:
        rec = latest_from_disk(cwd) or latest_from_disk()
        if rec:
            rec["source"] = "disk"
            write_rec(rec)
        return
    write_rec({"session_id": sid, "cwd": cwd, "source": "hook"})

def latest(prefer_cwd=None):
    if CUR.exists():
        try:
            j = json.loads(CUR.read_text())
            if j.get("session_id"):
                if not prefer_cwd or prefer_cwd in str(j.get("cwd") or ""):
                    return j
        except Exception:
            pass
    return latest_from_disk(prefer_cwd) or latest_from_disk()

if __name__ == "__main__":
    if "--hook" in sys.argv:
        from_hook()
    else:
        rec = latest_from_disk() or {}
        if rec:
            rec["source"] = "manual"
            write_rec(rec)
        print(json.dumps(rec or {"session_id": None}, ensure_ascii=False))
