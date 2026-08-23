#!/usr/bin/env python3
"""Bot 注册表: 把"逻辑角色名"解耦到"物理 webhook URL", 每个 bot 带握手验证状态。
核心纪律: 未经回声握手确认(verified_at 非空)的 bot, send_doorbell 拒发正式任务。
文件 bots.json chmod 600(含 url+key)。

用法:
  botreg.py list                                 列出角色/验证状态/URL主机(不显密钥)
  botreg.py set <role> <url> <key> [note]        注册/重绑角色 → 立即置为未验证(需重握手)
  botreg.py geturl <role>                        打印该角色 webhook URL(供 send_doorbell)
  botreg.py getkey <role>                        打印该角色 webhook KEY(供 send_doorbell)
  botreg.py is-verified <role>                   已验证 exit 0, 否则 exit 1
  botreg.py mark-verified <role> <nonce> <ts> [identity]   握手成功后登记
  botreg.py unverify <role>                      手动作废验证(疑似交接/换绑时)
"""
import json, os, sys, pathlib
from urllib.parse import urlparse

BRIDGE = pathlib.Path(__file__).resolve().parent
REG = BRIDGE / "bots.json"


def load():
    if REG.exists():
        return json.loads(REG.read_text())
    return {"version": 1, "bots": {}}


def save(d):
    tmp = REG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, REG)
    os.chmod(REG, 0o600)


def _bot(d, role):
    b = d["bots"].get(role)
    if not b:
        sys.stderr.write(f"[botreg] 未知角色: {role}. 现有: {list(d['bots'])}\n")
        sys.exit(3)
    return b


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    cmd = a[0]
    d = load()
    if cmd == "list":
        if not d["bots"]:
            print("(空. 用 botreg.py set <role> <url> <key> 注册)"); return
        for role, b in d["bots"].items():
            host = urlparse(b.get("url", "")).netloc or "?"
            v = b.get("verified_at")
            state = f"✅已验证 {v} nonce={b.get('last_nonce')}" if v else "⛔未验证(不可发正式任务)"
            print(f"- {role:14s} host={host:24s} {state}  note={b.get('note','')}")
    elif cmd == "set":
        role, url, key = a[1], a[2], a[3]
        note = a[4] if len(a) > 4 else ""
        prev = d["bots"].get(role, {})
        # 换绑立即作废旧验证: 新 URL 必须重新握手才可信
        d["bots"][role] = {"url": url, "key": key, "note": note or prev.get("note", ""),
                           "verified_at": None, "last_nonce": None, "identity": None}
        save(d)
        print(f"[botreg] {role} 已注册/重绑 → 未验证。请立刻握手: bash handshake.sh {role}")
    elif cmd == "geturl":
        print(_bot(d, a[1]).get("url", ""))
    elif cmd == "getkey":
        print(_bot(d, a[1]).get("key", ""))
    elif cmd == "is-verified":
        sys.exit(0 if _bot(d, a[1]).get("verified_at") else 1)
    elif cmd == "mark-verified":
        role, nonce, ts = a[1], a[2], a[3]
        ident = a[4] if len(a) > 4 else None
        b = _bot(d, role)
        b["verified_at"] = ts
        b["last_nonce"] = nonce
        if ident:
            b["identity"] = ident
        save(d)
        print(f"[botreg] {role} 已标记验证 @ {ts} nonce={nonce}")
    elif cmd == "unverify":
        b = _bot(d, a[1]); b["verified_at"] = None; save(d)
        print(f"[botreg] {a[1]} 验证已作废, 需重握手")
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
