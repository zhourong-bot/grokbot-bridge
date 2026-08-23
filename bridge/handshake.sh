#!/bin/bash
# 回声握手: 确认某角色 webhook 真的通到"对的 bot"的唯一可靠手段。
# 判据不是 HTTP 200, 而是"唯一暗号原样经桥落回"。成功才在 botreg 标记 verified。
# 用法: handshake.sh <role> [timeout_s=180]
set -euo pipefail
BRIDGE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || echo python3)"
ROLE="${1:?用法: handshake.sh <role> [timeout_s]}"
TIMEOUT="${2:-180}"

# 角色必须已在注册表(先 botreg.py set <role> <url> <key>)
URL="$("$PY" "$BRIDGE/botreg.py" geturl "$ROLE" 2>/dev/null || true)"
[ -n "$URL" ] || { echo "[handshake] 角色 '$ROLE' 未注册。先跑: $PY botreg.py set $ROLE <url> <key>"; exit 3; }

NONCE="PING-$("$PY" -c 'import secrets;print(secrets.token_hex(3).upper())')"
echo "[handshake] 角色=$ROLE  暗号=$NONCE  (只有它原样回来才算绑对)"

BRIEF="【回声握手】这是身份/连通确认, 不是干活任务。请立刻用本机 maildir 桥回一条信封确认: from=grokbot, to=claude, action=deliver, orig_task_id=\"$NONCE\"(务必用这个暗号做 orig_task_id, 这样落地文件名就带暗号), payload.brief 里必须①原样写回暗号 $NONCE ②自报你的身份(你是哪个 bot/对话/routine 名/在做什么项目), 一两句即可。用本机 bridge/secret.key 做 HMAC 签名。若你不是我预期的那个 bot, 也请照回, 让我发现绑错。"

T0=$(date +%s)
echo "[handshake] 发探针(握手模式绕过硬门)..."
GROK_HANDSHAKE=1 bash "$BRIDGE/send_doorbell.sh" --bot "$ROLE" "$BRIEF" webhook >/dev/null

echo "[handshake] 等暗号经桥落回(kqueue 事件驱动, 超时 ${TIMEOUT}s)..."
if OUT=$("$PY" "$BRIDGE/wait_deliver.py" "$T0" "$TIMEOUT" "$NONCE" 2>/dev/null); then
  FILE=$(echo "$OUT" | awk '{print $2}')
  if grep -q "$NONCE" "$FILE" 2>/dev/null; then
    IDENT=$(head -c 400 "$FILE" | tr '\n' ' ')
    TS=$(date +%Y-%m-%dT%H:%M:%S%z)
    "$PY" "$BRIDGE/botreg.py" mark-verified "$ROLE" "$NONCE" "$TS" "$IDENT"
    echo "[handshake] ✅ 绑对了。暗号 $NONCE 原样回来。bot 自报: $IDENT"
    exit 0
  fi
  echo "[handshake] ⛔ 落回文件里没有暗号内容, 视为未确认。file=$FILE"; exit 5
fi
echo "[handshake] ⛔ 超时未见暗号回来 = 未确认 = 当作没送到(绝不拿 HTTP 200 当数)。角色 '$ROLE' 保持未验证。"
exit 5
