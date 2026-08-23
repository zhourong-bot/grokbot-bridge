#!/usr/bin/env python3
"""桥体检: 一条命令查装机是否健康 + 运行态是否正常。任何人装完先跑这个。
用法: doctor.py [--json]
退出码: 0=全 PASS/仅 WARN, 1=有 FAIL。"""
import os, sys, json, time, subprocess, pathlib

BRIDGE = pathlib.Path(__file__).resolve().parent
CHECKS = []


def add(level, name, detail=""):
    CHECKS.append({"level": level, "name": name, "detail": detail})


def _launchd_loaded(label):
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
        return any(label in line for line in out.splitlines())
    except Exception:
        return False


def main():
    as_json = "--json" in sys.argv

    # 1. secret.key 存在 + 权限
    key = BRIDGE / "secret.key"
    if not key.exists():
        add("FAIL", "secret.key", "缺密钥, 跑: python3 envelope.py init-key")
    else:
        mode = oct(key.stat().st_mode & 0o777)
        add("PASS" if mode == "0o600" else "WARN", "secret.key", "权限 %s (建议 600)" % mode)

    # 2. 目录布局
    need = ["outbox/new", "inbox/new", "inbox/processing", "inbox/failed", "delivered", "state", "logs"]
    missing = [d for d in need if not (BRIDGE / d).is_dir()]
    add("FAIL" if missing else "PASS", "目录布局", "缺: %s" % missing if missing else "齐全")

    # 3. 常驻 launchd(可选; 无则 WARN, 也许走别的调度)
    land = _launchd_loaded("grokbridge.land")
    rec = _launchd_loaded("grokbridge.reconcile")
    add("PASS" if land else "WARN", "land 常驻", "已 load" if land else "未 load(手动/别的调度也可)")
    add("PASS" if rec else "WARN", "reconcile 常驻", "已 load" if rec else "未 load")

    # 4. botreg 验证态(至少一个 bot 已握手)
    reg = BRIDGE / "bots.json"
    verified = []
    if reg.exists():
        try:
            d = json.loads(reg.read_text())
            verified = [r for r, b in d.get("bots", {}).items() if b.get("verified_at")]
        except Exception:
            pass
    add("PASS" if verified else "WARN", "已验证 bot", ("已握手: %s" % verified) if verified else "无(先 handshake.sh <role>)")

    # 4b. 执行器启用态。若 PATH 上有 CLI 却一个都没启用, 多半是 config 写错(如注释毒化), WARN。
    try:
        import shutil as _sh
        import bridgeconf as bc
        ex = bc.enabled_executors()
        on_path = [n for n in ("claude", "codex") if _sh.which(n)]
        if ex:
            add("PASS", "执行器启用", "%s" % ex)
        elif on_path:
            add("WARN", "执行器启用", "PATH 上有 %s 却都没启用——检查 config.sh(是否被注释/off 关掉)" % on_path)
        else:
            add("PASS", "执行器启用", "无(仅 deliver 回流, 无本机执行)")
    except Exception as e:
        add("WARN", "执行器启用", "探测失败: %s" % e)

    # 5. processing 孤儿
    orph = list((BRIDGE / "inbox" / "processing").glob("*.json")) if (BRIDGE / "inbox" / "processing").is_dir() else []
    add("WARN" if orph else "PASS", "processing 孤儿", "%d 个(reconcile 下轮回收)" % len(orph) if orph else "无")

    # 6. run 台账超期
    overdue = 0
    rp = BRIDGE / "state" / "run_pending.jsonl"
    if rp.exists():
        now = time.time()
        for line in rp.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if now >= json.loads(line).get("deadline", 0):
                    overdue += 1
            except Exception:
                pass
    add("WARN" if overdue else "PASS", "run 台账超期", "%d 个待 reconcile 判定" % overdue if overdue else "无")

    # 7. 死信 DLQ
    dlq = list((BRIDGE / "inbox" / "failed").glob("*.json")) if (BRIDGE / "inbox" / "failed").is_dir() else []
    add("WARN" if dlq else "PASS", "死信队列", "%d 条(dlq.py list 查看)" % len(dlq) if dlq else "空")

    # 8. 最近活动(land 日志 mtime)
    land_log = BRIDGE / "logs" / "land.log"
    if land_log.exists():
        age = int(time.time() - land_log.stat().st_mtime)
        add("PASS", "land 最近活动", "%d 秒前" % age)
    else:
        add("WARN", "land 最近活动", "无日志(还没处理过件)")

    fails = [c for c in CHECKS if c["level"] == "FAIL"]
    if as_json:
        print(json.dumps({"ok": not fails, "checks": CHECKS}, ensure_ascii=False, indent=2))
    else:
        icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
        for c in CHECKS:
            print("%s %-16s %s" % (icon[c["level"]], c["name"], c["detail"]))
        print("\n%s" % ("❌ 有 FAIL, 需修复" if fails else "✅ 健康(FAIL=0)"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
