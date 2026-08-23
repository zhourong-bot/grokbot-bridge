#!/usr/bin/env python3
"""maildir 落地器: flock 单实例, claim inbox/new -> processing, HMAC+orig_task_id 幂等,
   deliver 写 delivered/<orig>.md, run 触发 claude/codex, echo 仅回执。
用法: land_result.py [--dry-run]
"""
import json, sys, time, subprocess, os, fcntl, pathlib, re, shlex
import bridgeconf as bc
from envelope import verify
from maildirutil import layout, orig_id, claim, finish
from notify_reply import notify

BRIDGE = pathlib.Path(__file__).resolve().parent
BUS = bc.BUS_PATH
SEEN = BRIDGE / "seen_task_ids.txt"
RUN_PENDING = BRIDGE / "state" / "run_pending.jsonl"
LOCK = BRIDGE / ".land.lock"
LOG = BRIDGE / "logs" / "land.log"
DRY = "--dry-run" in sys.argv
CLAUDE_BIN = bc.CLAUDE_BIN
CODEX_BIN = bc.CODEX_BIN


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + msg
    print(msg)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def seen_ids():
    return set(SEEN.read_text().split()) if SEEN.exists() else set()


def mark_seen(tid):
    with open(SEEN, "a") as f:
        f.write(tid + "\n")
        f.flush()
        os.fsync(f.fileno())


def bus_write(agent, summary, detail):
    BUS.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S.000%z"),
            "seq": int(time.time() * 1e9),
            "run_id": "bridge-" + time.strftime("%Y%m%dT%H%M%S"),
            "agent": agent, "level": "info", "event": "bridge.result",
            "summary": summary[:300], "exit_code": None, "detail": detail}
    with open(BUS, "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# launchd 不继承 shell 的代理环境; 某些地区 codex/claude 要走本地代理才连得上模型。
# 默认空(直连); 需要时在 config.sh 设 GROKBRIDGE_PROXY=http://127.0.0.1:<port>。
PROXY = bc.PROXY


def _child_env():
    env = os.environ.copy()
    if PROXY:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[k] = PROXY
    return env


def trigger(to, brief, orig):
    if to == "claude":
        if not CLAUDE_BIN:
            return "claude-disabled(未装/未配 CLAUDE_BIN)"
        cmd = [CLAUDE_BIN, "-p", brief]
    elif to == "codex":
        if not CODEX_BIN:
            return "codex-disabled(未装/未配 CODEX_BIN)"
        # -C 指到 bridge 目录: workspace-write 沙箱才允许被触发的 codex 读写 relay 文件。
        cmd = [CODEX_BIN, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
               "-C", str(BRIDGE), brief]
    else:
        return "unknown-to:" + to
    if DRY:
        return "DRY would run: " + cmd[0] + " " + cmd[1] + " ..."
    if not pathlib.Path(cmd[0]).exists():
        return "missing-bin:" + cmd[0]
    # H3b: 用 sentinel 记子进程退出码, 让 reconcile 分辨"跑成/非零/卡死", 而非盲报每个 run 失败。
    sentinel = BRIDGE / "state" / ("runexit_" + orig + ".txt")
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        inner = " ".join(shlex.quote(c) for c in cmd)
        wrapped = ["/bin/sh", "-c", "%s; echo $? > %s" % (inner, shlex.quote(str(sentinel)))]
        subprocess.Popen(wrapped, stdin=subprocess.DEVNULL, env=_child_env(),
                         stdout=open(BRIDGE / "logs" / f"trigger_{to}.log", "a"),
                         stderr=subprocess.STDOUT)
        return "triggered:" + to
    except Exception as e:
        return "trigger-fail:%s:%s" % (to, e)


def fail(fp, reason):
    failed = BRIDGE / "inbox" / "failed"
    finish(fp, failed)
    (failed / (fp.name + ".reason")).write_text(reason + "\n", encoding="utf-8")
    log("[REJ] %s %s" % (fp.name, reason))


# H2: deliver 落盘白名单 —— 只允许写到 bridge/delivered/ 下, 挡任意路径覆盖(.zshrc/plist/secret.key)。
DELIVERED_ROOT = (BRIDGE / "delivered").resolve()


def _safe_dest(dest):
    d = pathlib.Path(dest).resolve()
    try:
        d.relative_to(DELIVERED_ROOT)
    except ValueError:
        return None
    return d


# H1-lite: run brief 危险模式扫描 —— 挡被 prompt-injection 诱导的毁灭性命令。
# 不做强制人工审批(那会毁掉自动化), 只拦明显破坏性/持久化模式。
_DANGER = [
    r"rm\s+-rf\s+[~/]", r":\(\)\s*\{", r"\|\s*(ba)?sh\b", r"curl\s[^|]*\|\s*(ba)?sh",
    r"wget\s[^|]*\|\s*(ba)?sh", r"LaunchAgents", r"LaunchDaemons", r"secret\.key",
    r"\.zshrc", r"\.zprofile", r"\.bash_profile", r"crontab", r"launchctl\s+(load|bootstrap)",
    r"mkfs", r"\bdd\s+if=", r"chmod\s+-R?\s*777\s+/", r">\s*/etc/", r"sudo\s",
]


def scan_danger(brief):
    for pat in _DANGER:
        if re.search(pat, brief or "", re.I):
            return pat
    return None


def record_run_pending(orig, to, ttl_s):
    """H3b: run 触发是异步 Popen, 立即返回 success 不代表 codex/claude 真跑成。
    登记期待终态, 供 reconcile 超时后报"可能静默失败"。"""
    RUN_PENDING.parent.mkdir(parents=True, exist_ok=True)
    row = {"orig_task_id": orig, "to": to, "trigger_ts": time.time(),
           "deadline": time.time() + max(int(ttl_s or 0), 300)}
    with open(RUN_PENDING, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def deliver_brief(env):
    payload = env.get("payload") or {}
    brief = payload.get("brief", "") if isinstance(payload, dict) else ""
    orig = orig_id(env)
    dest = payload.get("path") if isinstance(payload, dict) else None
    if dest:
        out = _safe_dest(dest)
        if out is None:
            raise ValueError("payload.path 越界(仅允许 delivered/ 下): %s" % dest)
    else:
        out = BRIDGE / "delivered" / (orig + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = BRIDGE / "inbox" / "tmp" / (orig + ".deliver.tmp")
    tmp.write_text(brief, encoding="utf-8")
    os.replace(str(tmp), str(out))
    return str(out)


def main():
    layout(BRIDGE)
    lockf = open(LOCK, "a+")
    try:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("[lock] 已有 land_result 在跑, 退出")
        return
    seen = seen_ids()
    n = 0
    new_dir = BRIDGE / "inbox" / "new"
    proc_dir = BRIDGE / "inbox" / "processing"
    done_dir = BRIDGE / "inbox" / "done"
    for src in sorted(new_dir.glob("*.json")):
        fp = claim(src, proc_dir)
        if fp is None:
            continue
        try:
            env = json.loads(fp.read_text())
        except Exception as e:
            fail(fp, "非JSON: %s" % e); continue
        ok, msg = verify(env)
        if not ok:
            fail(fp, msg); continue
        oid = orig_id(env)
        if oid in seen:
            finish(fp, done_dir)
            log("[SKIP] orig=%s 已处理(幂等)" % oid)
            continue
        brief = env.get("payload", {}).get("brief", "") if isinstance(env.get("payload"), dict) else ""
        dest_path = None
        if env["action"] == "deliver":
            try:
                dest_path = deliver_brief(env)
            except ValueError as e:
                fail(fp, str(e)); continue
            res = "delivered:" + dest_path
        elif env["action"] == "run":
            hit = scan_danger(brief)
            if hit:
                fail(fp, "run brief 命中危险模式(拒执行): %s" % hit)
                bus_write(env["to"], "bridge REFUSED run from %s: danger=%s" % (env["from"], hit),
                          {"orig_task_id": oid, "pattern": hit})
                continue
            res = trigger(env["to"], brief, oid)
            if res.startswith("triggered:"):
                record_run_pending(oid, env["to"], env.get("ttl_s"))
        elif env["action"] == "echo":
            res = "echo-ack"  # echo 不执行, 只回执(连通性探测用), 与 run 区分开
        else:
            res = "no-op(" + env["action"] + ")"
        detail = {"task_id": env.get("task_id"), "orig_task_id": oid, "path": dest_path}
        bus_write(env["to"], "bridge result from %s action=%s -> %s" % (env["from"], env["action"], res), detail)
        nres = notify(env, dest_path)
        mark_seen(oid); seen.add(oid)
        finish(fp, done_dir)
        log("[OK] orig=%s %s notify=%s" % (oid, res, nres)); n += 1
    log("处理 %d 条" % n)


if __name__ == "__main__":
    main()
