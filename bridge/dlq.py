#!/usr/bin/env python3
"""死信队列(inbox/failed)查看与重投。被拒/失败的件不再隐身。
用法:
  dlq.py list                 列出死信 + 失败原因
  dlq.py retry <name|all>     把死信移回 inbox/new 重新处理(会重新验签+扫描)
  dlq.py purge <name|all>     永久删除死信(不可恢复)
  dlq.py --outbox list        查看门铃重试耗尽的 outbox 死信(只读)
"""
import sys, json, pathlib

BRIDGE = pathlib.Path(__file__).resolve().parent
FAILED = BRIDGE / "inbox" / "failed"
NEW = BRIDGE / "inbox" / "new"


def items():
    return sorted(FAILED.glob("*.json")) if FAILED.is_dir() else []


def main():
    global FAILED
    a = sys.argv[1:]
    if a and a[0] == "--outbox":
        FAILED = BRIDGE / "outbox" / "failed"
        a = a[1:]
        if a and a != ["list"]:
            print("outbox 死信只支持 list; 重投需人工核查并使用新的 task id")
            sys.exit(2)
    cmd = a[0] if a else "list"
    if cmd == "list":
        its = items()
        if not its:
            print("(死信队列为空)"); return
        for fp in its:
            reason = ""
            rf = FAILED / (fp.name + ".reason")
            if rf.exists():
                reason = rf.read_text().strip().splitlines()[0] if rf.read_text().strip() else ""
            frm = to = act = "?"
            try:
                e = json.loads(fp.read_text()); frm, to, act = e.get("from"), e.get("to"), e.get("action")
            except Exception:
                pass
            print("- %-36s %s→%s %s | 原因: %s" % (fp.name, frm, to, act, reason))
    elif cmd == "retry":
        target = a[1] if len(a) > 1 else ""
        its = items() if target == "all" else [f for f in items() if target in f.name]
        if not its:
            print("无匹配死信"); return
        NEW.mkdir(parents=True, exist_ok=True)
        for fp in its:
            fp.rename(NEW / fp.name)
            rf = FAILED / (fp.name + ".reason")
            if rf.exists():
                rf.unlink()
            print("重投 → inbox/new: %s" % fp.name)
        print("(land_result 下次唤起会重新处理)")
    elif cmd == "purge":
        target = a[1] if len(a) > 1 else ""
        its = items() if target == "all" else [f for f in items() if target in f.name]
        if not its:
            print("无匹配死信"); return
        for fp in its:
            fp.unlink()
            rf = FAILED / (fp.name + ".reason")
            if rf.exists():
                rf.unlink()
            print("已删除: %s" % fp.name)
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
