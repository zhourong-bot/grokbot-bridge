#!/usr/bin/env python3
"""事件驱动 waiter:监听 delivered/ 目录,文件一出现即退出(替代 sleep 轮询,近零延迟)。
macOS/BSD 用原生 kqueue; 其它平台(Linux/Windows)自动回退到 1s 轮询, 无需装 fswatch。
用法: wait_deliver.py <since_epoch> [timeout_s=1800] [name_substr]
命中: 打印 "DELIVERED <path> lag=<秒>" 并 exit 0;超时: 打印 TIMEOUT 并 exit 2。
"""
import sys, os, time, select, pathlib

BRIDGE = pathlib.Path(__file__).resolve().parent
DELIV = BRIDGE / "delivered"
DELIV.mkdir(parents=True, exist_ok=True)

since = float(sys.argv[1]) if len(sys.argv) > 1 else time.time()
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 1800
substr = sys.argv[3] if len(sys.argv) > 3 else ""


def found():
    cands = []
    for f in DELIV.glob("*"):
        try:
            mt = f.stat().st_mtime
        except FileNotFoundError:
            continue
        if mt > since:
            cands.append((f, mt))
    if substr:
        # L3: 优先精确 stem 匹配, 避免 orig 互为前缀时误命中早退; 无精确才回退子串
        exact = [(f, mt) for f, mt in cands if f.stem == substr]
        pool = exact if exact else [(f, mt) for f, mt in cands if substr in f.name]
    else:
        pool = cands
    return max(pool, key=lambda x: x[1]) if pool else None


# 先查一次(文件可能已经在了)
hit = found()
if hit:
    print(f"DELIVERED {hit[0]} lag={hit[1]-since:.2f}s")
    sys.exit(0)

deadline = time.time() + timeout

if hasattr(select, "kqueue"):
    # macOS/BSD: 目录 vnode 事件即时唤醒, 5s 心跳兜底。
    kq = select.kqueue()
    dirfd = os.open(str(DELIV), os.O_RDONLY)
    ev = select.kevent(dirfd,
                       filter=select.KQ_FILTER_VNODE,
                       flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                       fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_LINK)
    try:
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                print("TIMEOUT"); sys.exit(2)
            kq.control([ev], 1, min(5.0, remain))
            hit = found()
            if hit:
                print(f"DELIVERED {hit[0]} lag={hit[1]-since:.2f}s")
                sys.exit(0)
    finally:
        os.close(dirfd)
else:
    # Linux/Windows 兜底: 1s 轮询(Linux 想要即时可另跑 inotifywait 触发)。
    while True:
        if time.time() >= deadline:
            print("TIMEOUT"); sys.exit(2)
        hit = found()
        if hit:
            print(f"DELIVERED {hit[0]} lag={hit[1]-since:.2f}s")
            sys.exit(0)
        time.sleep(1.0)
