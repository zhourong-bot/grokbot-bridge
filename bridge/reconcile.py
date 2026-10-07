#!/usr/bin/env python3
"""对账 + 自愈(每 60s 一轮, launchd 唤起):
  1) 孤儿回收(H3a): 若无 land 在跑(能拿到 land 锁), inbox/processing 里残留的都是崩溃孤儿 → 回 new 重试(attempt<3)或进 failed。
  2) run 超时告警(H3b/H4): trigger 拉起的 codex/claude 是异步 Popen, 立即算 success ≠ 真跑成。
     过 deadline 仍无 delivered 结果 → 写 bus warn(静默失败可见), 并清理台账。
  3) 门铃兜底 + M5: outbox/new 超时且尚无回流的才叮 webhook; 已 delivered/已 seen 的归档到 outbox/done, 不再空叮。
不碰 secret.key。"""
import os, time, json, urllib.request, pathlib, sys, fcntl

BRIDGE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BRIDGE))
from maildirutil import finish  # noqa
import bridgeconf as bc  # noqa

T = int(os.environ.get("GROKBRIDGE_STALE_S", "90"))
LOCK = BRIDGE / ".land.lock"
BUS = bc.BUS_PATH
SEEN = BRIDGE / "seen_task_ids.txt"
RUN_PENDING = BRIDGE / "state" / "run_pending.jsonl"
ATT = BRIDGE / "state" / "orphan_attempts.json"
DELIV = BRIDGE / "delivered"
LOG = BRIDGE / "logs" / "reconcile.log"
MAX_ATTEMPT = 3
NUDGE_MAX = 3
NUDGE_BASE_S = 60
NUDGE_CAP_S = 300
NUDGE_TTL_S = 900


def logline(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S%z ") + msg + "\n")


def bus(level, summary, detail=None):
    try:
        BUS.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S.000%z"), "seq": int(time.time() * 1e9),
                "run_id": "reconcile-" + time.strftime("%Y%m%dT%H%M%S"), "agent": "bridge",
                "level": level, "event": "bridge.reconcile", "summary": summary[:300],
                "exit_code": None, "detail": detail or {}}
        with open(BUS, "a") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_config():
    return bc.load_config()  # 复用 bridgeconf 的健壮解析(剥行内注释)


def resolve_webhook():
    """重叮走谁: 先 config.sh 默认 webhook; 没有则用 botreg 里最近验证过的一个 bot。
    (botreg-only 用户没有 config.sh webhook, 否则重叮就成了空广告。)"""
    cfg = load_config()
    url, key = cfg.get("GROK_WEBHOOK_URL") or "", cfg.get("GROK_WEBHOOK_KEY") or ""
    if url:
        return url, key
    try:
        reg = json.loads((BRIDGE / "bots.json").read_text())
        verified = [b for b in reg.get("bots", {}).values() if b.get("verified_at")]
        verified.sort(key=lambda b: b.get("verified_at") or "")
        if verified:
            return verified[-1].get("url", ""), verified[-1].get("key", "")
    except Exception:
        pass
    return "", ""


def seen_set():
    return set(SEEN.read_text().split()) if SEEN.exists() else set()


def _load_att():
    try:
        return json.loads(ATT.read_text())
    except Exception:
        return {}


def _save_att(d):
    ATT.parent.mkdir(parents=True, exist_ok=True)
    tmp = ATT.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, ATT)


def recover_orphans():
    """只有拿得到 land 锁(说明当前没有 land 在跑)才回收, 避免抢正在处理的件。"""
    proc = BRIDGE / "inbox" / "processing"
    if not proc.is_dir():
        return
    items = list(proc.glob("*.json"))
    if not items:
        return
    lockf = open(LOCK, "a+")
    try:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # land 正在跑, 这些不是孤儿, 不动
    try:
        att = _load_att()
        new_dir = BRIDGE / "inbox" / "new"
        failed = BRIDGE / "inbox" / "failed"
        for fp in items:
            n = att.get(fp.name, 0) + 1
            att[fp.name] = n
            if n >= MAX_ATTEMPT:
                finish(fp, failed)
                (failed / (fp.name + ".reason")).write_text("孤儿超重试(%d): 崩溃残留\n" % n, encoding="utf-8")
                att.pop(fp.name, None)
                bus("error", "orphan 超重试进 failed: %s" % fp.name, {"attempt": n})
                logline("[orphan->failed] %s attempt=%d" % (fp.name, n))
            else:
                finish(fp, new_dir)
                bus("warn", "orphan 回 new 重试: %s" % fp.name, {"attempt": n})
                logline("[orphan->new] %s attempt=%d" % (fp.name, n))
        _save_att(att)
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()


def check_run_pending():
    """H3b/H4: 用子进程退出码 sentinel(state/runexit_<orig>.txt)分辨 run 结局:
       0=跑成(清台账不报), 非零=真失败(报bus), 过 deadline 仍无 sentinel=卡死/被杀(报bus)。
       避免"成功的 run 也被误报静默失败"。"""
    if not RUN_PENDING.exists():
        return
    now = time.time()
    keep = []
    state = BRIDGE / "state"
    for line in RUN_PENDING.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        orig = row.get("orig_task_id", "")
        sentinel = state / ("runexit_" + orig + ".txt")
        if sentinel.exists():
            code = (sentinel.read_text().strip() or "?")
            if code == "0":
                logline("[run-ok] orig=%s" % orig)
            else:
                bus("error", "run 任务非零退出(疑似失败): orig=%s to=%s exit=%s" % (orig, row.get("to"), code),
                    {"orig_task_id": orig, "to": row.get("to"), "exit": code})
                logline("[run-fail] orig=%s exit=%s" % (orig, code))
            try:
                sentinel.unlink()
            except Exception:
                pass
            continue  # 有结局, 出台账
        if now >= row.get("deadline", 0):
            bus("error", "run 任务超时无退出(疑似卡死/被杀): orig=%s to=%s" % (orig, row.get("to")),
                {"orig_task_id": orig, "to": row.get("to"),
                 "waited_s": round(now - row.get("trigger_ts", now))})
            logline("[run-timeout] orig=%s to=%s" % (orig, row.get("to")))
            continue  # 报过一次即出台账, 不重复刷
        keep.append(line)
    tmp = RUN_PENDING.with_suffix(".tmp")
    tmp.write_text(("\n".join(keep) + "\n") if keep else "")
    os.replace(tmp, RUN_PENDING)


def nudge_stale():
    # Serialize state + POST across overlapping reconcile processes. Fail closed
    # if persisted state cannot be read; never silently reset a retry budget.
    state_dir = BRIDGE / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "nudge.lock", "a+") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        _nudge_stale_locked(state_dir / "nudge_attempts.json")


def _save_nudges(path, state):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _nudge_stale_locked(state_path):
    """门铃兜底 + M5: 已回流的归档不空叮, 只叮真正卡着的。"""
    new = BRIDGE / "outbox" / "new"
    if not new.is_dir():
        return
    now = time.time()
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    seen = seen_set()
    done_dir = BRIDGE / "outbox" / "done"
    really_stale = []
    for p in new.glob("*.json"):
        stem = p.stem
        # M5: 已 delivered 或已 seen = 对方已处理, 归档, 别再叮。
        if (DELIV / (stem + ".md")).exists() or stem in seen:
            finish(p, done_dir)
            state.pop(stem, None)
            logline("[outbox-archived] %s (已回流)" % p.name)
            continue
        row = state.get(stem)
        if row is None and now - p.stat().st_mtime >= T:
            row = state[stem] = {"first_stale_ts": now, "nudge_count": 0,
                                 "last_nudge_ts": None, "exhausted": False}
        if row is None:
            continue
        count = row["nudge_count"]
        delay = min(NUDGE_CAP_S, NUDGE_BASE_S * 2 ** max(0, count - 1))
        ttl_expired = now >= row["first_stale_ts"] + NUDGE_TTL_S
        budget_expired = count >= NUDGE_MAX and now >= row["last_nudge_ts"] + delay
        if row["exhausted"] or ttl_expired or budget_expired:
            # Durable terminal marker before moving: a crash cannot restart POSTs.
            row["exhausted"] = True
            _save_nudges(state_path, state)
            failed = BRIDGE / "outbox" / "failed"
            failed.mkdir(parents=True, exist_ok=True)
            reason = "nudge exhausted: %s; count=%d; first_stale_ts=%s\n" % (
                "TTL" if ttl_expired else "retry budget", count, row["first_stale_ts"])
            (failed / (p.name + ".reason")).write_text(reason, encoding="utf-8")
            finish(p, failed)
            if not row.get("error_emitted"):
                row["error_emitted"] = True
                _save_nudges(state_path, state)
                bus("error", "outbox nudge 耗尽进 failed: %s" % p.name,
                    {"task_id": stem, **row})
                logline("[outbox->failed] %s %s" % (p.name, reason.strip()))
            continue
        if count == 0 or now >= row["last_nudge_ts"] + delay:
            really_stale.append(p)
    _save_nudges(state_path, state)
    if not really_stale:
        return
    url, key = resolve_webhook()
    if not url:
        logline("no webhook url(config.sh 或 botreg 都没有)"); return
    req = urllib.request.Request(url, data=b'{"hint":"reconcile"}', method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    # Count attempts before I/O, including failed POSTs and interrupted processes.
    for p in really_stale:
        state[p.stem]["nudge_count"] += 1
        state[p.stem]["last_nudge_ts"] = now
    _save_nudges(state_path, state)
    try:
        urllib.request.urlopen(req, timeout=15).read()
        logline("nudge once for %d stale %s" % (len(really_stale), [x.name for x in really_stale]))
    except Exception as e:
        logline("nudge fail %s" % e)


def main():
    recover_orphans()
    check_run_pending()
    nudge_stale()


if __name__ == "__main__":
    main()
