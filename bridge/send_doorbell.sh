#!/bin/bash
# 发门铃: 原子写入 outbox/new, webhook 只叮一声。
# 用法: send_doorbell.sh "任务brief" [webhook|slack|github]   (默认 webhook)
set -euo pipefail
BRIDGE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || echo python3)"
# shellcheck source=/dev/null
source "$BRIDGE/config.sh" 2>/dev/null || { echo "先建 $BRIDGE/config.sh (复制 config.sample.sh)"; exit 1; }
# 可选 --bot <role>: 按角色从 botreg 注册表寻址(解耦逻辑角色 ↔ 物理 webhook URL)。
ROLE=""
if [ "${1:-}" = "--bot" ]; then
  ROLE="${2:?--bot 后需跟角色名}"; shift 2
fi
BRIEF="${1:?用法: send_doorbell.sh [--bot <role>] \"brief\" [webhook|slack|github] (默认 webhook)}"
CH="${2:-webhook}"

# 角色寻址 + 握手硬门: 未验证的 bot 拒发正式任务(除非 GROK_HANDSHAKE=1 的握手探针)。
if [ -n "$ROLE" ]; then
  if ! "$PY" "$BRIDGE/botreg.py" is-verified "$ROLE" 2>/dev/null; then
    if [ "${GROK_HANDSHAKE:-}" != "1" ]; then
      echo "[gate] 角色 '$ROLE' 未经回声握手确认, 拒发正式任务。先跑: bash handshake.sh $ROLE"; exit 4
    fi
    echo "[gate] 握手模式(GROK_HANDSHAKE=1): 允许对未验证角色 '$ROLE' 发探针"
  fi
  GROK_WEBHOOK_URL="$("$PY" "$BRIDGE/botreg.py" geturl "$ROLE")"
  GROK_WEBHOOK_KEY="$("$PY" "$BRIDGE/botreg.py" getkey "$ROLE")"
  [ -n "$GROK_WEBHOOK_URL" ] || { echo "[gate] 角色 '$ROLE' 无 URL"; exit 4; }
fi

FROM="${FROM:-claude}"
ENV=$("$PY" -c '
import json, os, sys
from pathlib import Path
sys.path.insert(0, "'"$BRIDGE"'")
from envelope import make
brief = sys.argv[1]
frm = sys.argv[2]
payload = {"brief": brief, "cwd": os.environ.get("PWD") or os.getcwd()}
if frm == "codex":
    payload["reply_vendor"] = "codex"
    sid = os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
    if not sid:
        try:
            from register_codex_session import latest
            rec = latest(payload.get("cwd"))
            sid = (rec or {}).get("session_id")
        except Exception:
            sid = None
    if sid:
        payload["reply_session"] = sid
else:
    from register_claude_session import latest, record
    sid = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_SESSION")
    cwd = payload.get("cwd")
    if sid:
        record(sid, cwd, source="doorbell", force_lane=True)
    else:
        rec = latest(cwd) or latest()
        sid = (rec or {}).get("session_id")
    if sid:
        payload["reply_session"] = sid
print(json.dumps(make(frm, "grokbot", "task", "run", payload), ensure_ascii=False))
' "$BRIEF" "$FROM")
echo "信封: $ENV"
TASK_ID=$(printf '%s' "$ENV" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
printf '%s\n' "$ENV" | "$PY" -c '
import sys, json
from pathlib import Path
sys.path.insert(0, "'"$BRIDGE"'")
from maildirutil import atomic_put
env = json.load(sys.stdin)
atomic_put(Path("'"$BRIDGE"'"), "outbox", env["task_id"], env)
print("outbox/new:", env["task_id"]+".json")
'

case "$CH" in
  webhook)
    [ -n "${GROK_WEBHOOK_URL:-}" ] || { echo "[webhook] GROK_WEBHOOK_URL 未配置"; exit 1; }
    HDRS=(-H 'Content-Type: application/json')
    [ -n "${GROK_WEBHOOK_KEY:-}" ] && HDRS+=(-H "Authorization: Bearer ${GROK_WEBHOOK_KEY}")
    # M4: webhook 只叫醒, 正文走 outbox 文件。body 只带 task_id, 不把整封 brief+签名泄给 webhook 端点。
    DOORBELL_BODY=$(printf '{"task_id":"%s"}' "$TASK_ID")
    HTTP=$(curl -s -o /tmp/grok_doorbell_resp.txt -w '%{http_code}' -X POST "${HDRS[@]}" -d "$DOORBELL_BODY" "$GROK_WEBHOOK_URL" || echo "000")
    case "$HTTP" in
      2*) echo "[webhook] 已 POST (HTTP $HTTP)，Grok Bot 应已被叫醒";;
      *) echo "[webhook] POST 未成功 (HTTP $HTTP)，看 /tmp/grok_doorbell_resp.txt"; exit 1;;
    esac
    ;;
  slack)
    [ -n "${SLACK_WEBHOOK:-}" ] || { echo "[slack] SLACK_WEBHOOK 未配置"; exit 1; }
    BODY=$(printf '%s' "$ENV" | "$PY" -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))')
    curl -sf -X POST "$SLACK_WEBHOOK" -H 'Content-type: application/json' -d "$BODY" && echo "[slack] 已发(仅回执)" || { echo "[slack] 失败"; exit 1; }
    ;;
  github)
    [ -n "${GH_REPO:-}" ] || { echo "[github] GH_REPO 未配置"; exit 1; }
    gh issue create -R "$GH_REPO" -t "grokbot-task $(date +%H%M%S)" -b "$ENV" && echo "[github] 已建 issue" || { echo "[github] 失败"; exit 1; }
    ;;
  *) echo "未知通道: $CH (可选 webhook|slack|github)"; exit 1;;
esac
