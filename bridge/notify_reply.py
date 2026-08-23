#!/usr/bin/env python3
"""落地后：追加 threads/，再按来源推 Claude 或 Codex。不阻塞。

Codex 桌面里已经打开的会话有 active-writer 锁，外部
`codex exec resume <same-session>` 不能把消息塞进当前窗口。Codex
回流的可靠主通道是 threads/codex.md + delivered/<orig>.md；这里同时写
state/codex_attention.jsonl，供下一轮/钩子/人工状态检查读取。
"""
import json, os, subprocess, time
from pathlib import Path

import bridgeconf as bc
BRIDGE = Path(__file__).resolve().parent
CLAUDE_SESS = BRIDGE / "state" / "claude_sessions.jsonl"
THREAD = BRIDGE / "threads" / "codex.md"
LOG = BRIDGE / "logs" / "notify.log"
ATTENTION = BRIDGE / "state" / "codex_attention.jsonl"
CODEX_BIN = bc.CODEX_BIN or ""
CLAUDE_BIN = bc.CLAUDE_BIN or ""

def latest_claude(prefer_cwd=None):
    try:
        from register_claude_session import latest as _cur
        rec = _cur(prefer_cwd)
        if rec and rec.get("session_id"):
            return rec["session_id"]
    except Exception:
        pass
    return None

def latest_session(prefer_cwd=None):
    """兼容旧 send_doorbell：默认 Claude。"""
    return latest_claude(prefer_cwd)

def _log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")

def append_thread(who, text, vendor=None):
    dest = THREAD if vendor == "codex" else (BRIDGE / "threads" / "claude.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(dest, "a", encoding="utf-8") as f:
        f.write("\n## %s  %s\n\n%s\n" % (stamp, who, text.rstrip()))

def record_codex_attention(env, payload, orig, dest_path):
    ATTENTION.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "orig_task_id": orig,
        "task_id": env.get("task_id"),
        "reply_session": payload.get("reply_session"),
        "cwd": payload.get("cwd"),
        "delivered": dest_path,
        "thread": str(THREAD),
        "brief_head": (payload.get("brief") or "")[:300],
        "status": "thread_appended",
    }
    with open(ATTENTION, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def _want_codex(env, payload):
    if payload.get("reply_vendor") == "codex":
        return True
    if env.get("from") == "codex":
        return True
    if (env.get("to") == "codex") and env.get("action") == "deliver":
        return True
    return False

def notify(env, dest_path=None):
    payload = env.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    orig = env.get("orig_task_id") or payload.get("orig_task_id") or env.get("task_id")
    path = dest_path or payload.get("path") or ""
    brief = payload.get("brief") or ""
    want_codex = _want_codex(env, payload)
    thread_path = THREAD if want_codex else (BRIDGE / "threads" / "claude.md")
    msg = (
        "【Grok Bot 桥回流】任务 %s 已完成。\n"
        "结果文件: %s\n"
        "对话本: %s\n"
        "请立刻读取结果文件并验收，不要等用户粘贴。\n"
    ) % (orig, path, thread_path)
    append_thread("Grok", "orig=%s\n%s\n%s" % (orig, path, brief[:1500]),
                  vendor="codex" if want_codex else "claude")
    if want_codex:
        return _notify_codex(env, payload, orig, msg, dest_path)
    # M3: 只信信封里显式绑定的 reply_session, 不猜"最新会话"——
    # 否则多个 Claude 窗口并存时, A 的回流会被 --resume 塞进正干别的的 B 窗口, 污染上下文。
    sid = payload.get("reply_session")
    if not sid:
        _log("no bound reply_session; thread-only(不猜会话, 避免污染其它窗口)")
        return "thread-appended-claude:no-session"
    if not Path(CLAUDE_BIN).exists():
        _log("claude bin missing %s orig=%s" % (CLAUDE_BIN, orig))
        return "thread-appended-claude:no-bin"
    cmd = [CLAUDE_BIN, "-p", "--resume", sid, "--output-format", "text", msg]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=open(LOG, "a"),
                         stderr=subprocess.STDOUT, cwd=str(BRIDGE))
        _log("claude resume %s orig=%s" % (sid, orig))
        return "notified-claude:" + sid
    except Exception as e:
        _log("claude resume fail: %s orig=%s" % (e, orig))
        return "thread-appended-claude:resume-fail"

def _notify_codex(env, payload, orig, msg, dest_path=None):
    record_codex_attention(env, payload, orig, dest_path or payload.get("path") or "")
    sid = payload.get("reply_session")
    if not sid:
        try:
            from register_codex_session import latest as latest_codex
            rec = latest_codex(payload.get("cwd") or str(BRIDGE))
            sid = (rec or {}).get("session_id")
        except Exception:
            sid = None
    if os.environ.get("GROKBRIDGE_CODEX_RESUME", "").lower() not in ("1", "true", "yes"):
        _log("codex thread-only %s orig=%s" % (sid or "no-session", orig))
        return "thread-appended-codex:" + (sid or "no-session")
    if not Path(CODEX_BIN).exists():
        _log("codex bin missing %s orig=%s" % (CODEX_BIN, orig))
        return "thread-appended-codex:no-bin"
    cmd = [CODEX_BIN, "exec", "resume"]
    if sid:
        cmd += [sid, msg]
    else:
        cmd += ["--last", msg]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=open(LOG, "a"),
                         stderr=subprocess.STDOUT, cwd=str(BRIDGE))
        _log("codex exec resume %s orig=%s" % (sid or "--last", orig))
        return "notified-codex:" + (sid or "last")
    except Exception as e:
        _log("codex resume fail: %s" % e)
        return "codex-fail"

if __name__ == "__main__":
    print(latest_claude() or "none")
