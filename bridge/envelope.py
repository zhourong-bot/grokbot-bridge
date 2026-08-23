#!/usr/bin/env python3
"""信封协议: HMAC-SHA256 + 闭集字段 + orig_task_id 幂等。
密钥在 bridge/secret.key (chmod 600, 不读出、不改、不外发)。
用法:
  envelope.py init-key
  envelope.py make <from> <to> <type> <action> "<brief>" [orig_task_id]
  echo '<json>' | envelope.py verify
"""
import json, hmac, hashlib, sys, time, os, pathlib, secrets

BRIDGE = pathlib.Path(__file__).resolve().parent
KEY_FILE = BRIDGE / "secret.key"
# from 与 to 用同一套身份, 避免 mac- 前缀不对称把使用者绕晕。
VALID_FROM = {"claude", "codex", "grokbot"}
VALID_TO = {"claude", "codex", "grokbot"}
VALID_TYPE = {"task", "result"}
VALID_ACTION = {"run", "echo", "deliver"}


def _key():
    if not KEY_FILE.exists():
        raise SystemExit(f"缺密钥文件 {KEY_FILE}，先跑: python3 envelope.py init-key")
    return KEY_FILE.read_bytes().strip()


def _canonical(env):
    d = {k: v for k, v in env.items() if k != "sig"}
    return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sign(env):
    return hmac.new(_key(), _canonical(env), hashlib.sha256).hexdigest()


def make(frm, to, typ, action, payload, orig_task_id=None, attempt=1, ttl_s=3600):
    if frm not in VALID_FROM: raise SystemExit(f"from 非法: {frm}")
    if to not in VALID_TO: raise SystemExit(f"to 非法: {to}")
    if typ not in VALID_TYPE: raise SystemExit(f"type 非法: {typ}")
    if action not in VALID_ACTION: raise SystemExit(f"action 非法(非闭集): {action}")
    tid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    orig = orig_task_id or (payload.get("orig_task_id") if isinstance(payload, dict) else None) or tid
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.setdefault("orig_task_id", orig)
    env = {"v": 1,
           "task_id": tid,
           "orig_task_id": orig,
           "attempt": int(attempt),
           "ttl_s": int(ttl_s),
           "from": frm, "to": to, "type": typ, "action": action,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "payload": payload}
    env["sig"] = sign(env)
    return env


def verify(env):
    for f in ("v", "task_id", "from", "to", "type", "action", "ts", "payload", "sig"):
        if f not in env:
            return False, f"缺字段 {f}"
    if env["from"] not in VALID_FROM: return False, "from 非法"
    if env["to"] not in VALID_TO: return False, "to 非法"
    if env["type"] not in VALID_TYPE: return False, "type 非法"
    if env["action"] not in VALID_ACTION: return False, "action 非法(非闭集)"
    if not hmac.compare_digest(str(env["sig"]), sign(env)): return False, "HMAC 校验失败"
    return True, "ok"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init-key":
        if KEY_FILE.exists():
            print("密钥已存在"); sys.exit(0)
        KEY_FILE.write_bytes(os.urandom(32).hex().encode())
        os.chmod(KEY_FILE, 0o600)
        print(f"已生成 {KEY_FILE} (chmod 600)")
    elif cmd == "make":
        if len(sys.argv) < 6:
            raise SystemExit('用法: envelope.py make <from> <to> <type> <action> "<brief>" [orig_task_id]')
        frm, to, typ, action = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        brief = sys.argv[6] if len(sys.argv) > 6 else ""
        orig = sys.argv[7] if len(sys.argv) > 7 else None
        print(json.dumps(make(frm, to, typ, action, {"brief": brief}, orig_task_id=orig), ensure_ascii=False))
    elif cmd == "verify":
        env = json.load(sys.stdin)
        ok, msg = verify(env)
        print(msg); sys.exit(0 if ok else 1)
    else:
        print(__doc__)
