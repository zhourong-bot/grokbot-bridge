#!/usr/bin/env python3
"""Claude 对话级指针。jsonl 全记；claude_current.json 只给跟 Grok 说话的车道。"""
import json, os, sys, time, tempfile, shutil
from pathlib import Path

def _bridge():
    return Path(os.environ.get("GROKBRIDGE") or str(Path(__file__).resolve().parent))

# 哪些 cwd 算"跟 Bot 说话的车道"(其会话成为 current 指针)。
# 默认只认 marker 文件 .grokbridge; 也可在 config/env 设 GROKBRIDGE_LANE_CWD=substr1,substr2。
def _lane_substrs():
    return tuple(s for s in os.environ.get("GROKBRIDGE_LANE_CWD", "").split(",") if s)


LANE_MARKERS = (".grokbridge",)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def paths(bridge=None):
    b = Path(bridge) if bridge else _bridge()
    return {
        "bridge": b,
        "jsonl": b / "state" / "claude_sessions.jsonl",
        "cur": b / "state" / "claude_current.json",
        "thread": b / "threads" / "claude.md",
    }


def _read_cur(cur_path):
    if not cur_path.exists():
        return None
    try:
        j = json.loads(cur_path.read_text(encoding="utf-8"))
        return j if j.get("session_id") else None
    except Exception:
        return None


def is_grok_lane(cwd, cur=None):
    cwd = str(cwd or "")
    if any(s in cwd for s in _lane_substrs()):
        return True
    if cwd:
        p = Path(cwd)
        if any((p / m).exists() for m in LANE_MARKERS):
            return True
    if cur and cwd and str(cur.get("cwd") or "") == cwd:
        return True
    return False


def append_jsonl(jsonl_path, rec):
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _handoff_line(thread, rec, prev):
    thread.parent.mkdir(parents=True, exist_ok=True)
    if not thread.exists():
        thread.write_text(
            "# Claude ↔ Grok 对话本\n\n指针: state/claude_current.json\n\n",
            encoding="utf-8",
        )
    last_orig = rec.get("last_orig") or (prev or {}).get("last_orig") or ""
    block = (
        "\n## %s  HANDOFF\n\n"
        "sid=%s\nprev=%s\ncwd=%s\nlast_orig=%s\n"
        % (
            _now(),
            rec.get("session_id"),
            (prev or {}).get("session_id") or "",
            rec.get("cwd") or "",
            last_orig,
        )
    )
    with open(thread, "a", encoding="utf-8") as f:
        f.write(block)


def write_current(p, rec, prev=None):
    p["cur"].parent.mkdir(parents=True, exist_ok=True)
    tmp = p["cur"].with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(p["cur"]))
    if prev and prev.get("session_id") and prev.get("session_id") != rec.get("session_id"):
        _handoff_line(p["thread"], rec, prev)


def record(sid, cwd, source="hook", last_orig=None, force_lane=False, bridge=None):
    p = paths(bridge)
    rec = {"ts": _now(), "session_id": sid, "cwd": cwd or "", "source": source}
    if last_orig:
        rec["last_orig"] = last_orig
    append_jsonl(p["jsonl"], rec)
    cur = _read_cur(p["cur"])
    if force_lane or is_grok_lane(cwd, cur):
        write_current(p, rec, prev=cur)
        rec["lane"] = "current"
    else:
        rec["lane"] = "audit"
    return rec


def latest(prefer_cwd=None, bridge=None):
    p = paths(bridge)
    cur = _read_cur(p["cur"])
    if not cur:
        return None
    if prefer_cwd and prefer_cwd not in str(cur.get("cwd") or ""):
        return None
    return cur


def resolve_sid(payload=None, prefer_cwd=None, bridge=None):
    """Pointer wins. Envelope reply_session only if pointer empty."""
    payload = payload or {}
    cwd = prefer_cwd or payload.get("cwd")
    rec = latest(cwd, bridge=bridge) or latest(bridge=bridge)
    if rec and rec.get("session_id"):
        return rec["session_id"], "current"
    env_sid = payload.get("reply_session")
    if env_sid:
        return env_sid, "envelope"
    return None, "none"


def from_hook():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    sid = data.get("session_id") or data.get("sessionId")
    cwd = data.get("cwd") or os.environ.get("PWD") or ""
    if not sid:
        return
    record(sid, cwd, source="hook")


def selftest():
    root = Path(tempfile.mkdtemp(prefix="gb-claude-ptr-"))
    os.environ["GROKBRIDGE"] = str(root)
    os.environ["GROKBRIDGE_LANE_CWD"] = "myproj"
    (root / "state").mkdir()
    (root / "threads").mkdir()
    lane = str(root / "proj")  # not the lane path
    grok = str(root / "x" / "myproj" / "app")
    Path(grok).mkdir(parents=True)
    Path(lane).mkdir(parents=True)
    (Path(lane) / ".grokbridge").write_text("1\n")

    r1 = record("sid-old", grok, source="test")
    assert r1["lane"] == "current", r1
    assert latest()["session_id"] == "sid-old"

    r2 = record("sid-noise", str(Path.home()), source="test")
    assert r2["lane"] == "audit", r2
    assert latest()["session_id"] == "sid-old", "noise overwrote pointer"

    r3 = record("sid-new", grok, source="test")
    assert r3["lane"] == "current"
    assert latest()["session_id"] == "sid-new"
    text = paths()["thread"].read_text(encoding="utf-8")
    assert "HANDOFF" in text and "sid=sid-new" in text and "prev=sid-old" in text, text

    r4 = record("sid-mark", lane, source="test")
    assert r4["lane"] == "current", "marker file should claim lane"
    assert latest()["session_id"] == "sid-mark"

    sid, src = resolve_sid({"reply_session": "sid-stale", "cwd": grok})
    assert sid == "sid-mark" and src == "current", (sid, src)

    # empty pointer -> envelope
    paths()["cur"].unlink()
    sid, src = resolve_sid({"reply_session": "sid-stale", "cwd": grok})
    assert sid == "sid-stale" and src == "envelope", (sid, src)

    shutil.rmtree(root)
    os.environ.pop("GROKBRIDGE", None)
    print("SELFTEST_OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--hook" in sys.argv:
        from_hook()
    else:
        print(json.dumps(latest() or {"session_id": None}, ensure_ascii=False))
