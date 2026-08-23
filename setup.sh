#!/bin/bash
# 一条命令装机: 建密钥/配置 → 渲染安装常驻(launchd/systemd)→ 体检。
# 用法: bash setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BRIDGE="$ROOT/bridge"
PY="$(command -v python3 || echo /usr/bin/python3)"

echo "== grokbot-bridge 装机 =="
echo "bridge: $BRIDGE"
echo "python: $PY"

# 1. 配置
if [ ! -f "$BRIDGE/config.sh" ]; then
  cp "$BRIDGE/config.sample.sh" "$BRIDGE/config.sh"
  echo "[配置] 已从 sample 生成 config.sh — 记得填 GROK_WEBHOOK_URL/KEY(或改用 botreg)"
fi

# 2. 密钥
[ -f "$BRIDGE/secret.key" ] || "$PY" "$BRIDGE/envelope.py" init-key

# 2b. 建邮箱目录(否则 fresh clone 的 doctor 会因缺目录报 FAIL)
"$PY" -c "import sys,pathlib; sys.path.insert(0,'$BRIDGE'); from maildirutil import layout; layout(pathlib.Path('$BRIDGE'))"

# 3. 探测执行器
echo "[执行器] $("$PY" "$BRIDGE/bridgeconf.py" | grep 启用)"

# 4. 常驻
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
  for job in land reconcile; do
    script="land_result.py"; [ "$job" = "reconcile" ] && script="reconcile.py"
    sed -e "s|__PY__|$PY|g" -e "s|__BRIDGE__|$BRIDGE|g" -e "s|__SCRIPT__|$script|g" -e "s|__JOB__|$job|g" \
      "$ROOT/launchd/com.grokbridge.template.plist" > "$LA/com.grokbridge.$job.plist"
    launchctl bootout "gui/$(id -u)/com.grokbridge.$job" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$LA/com.grokbridge.$job.plist"
    echo "[launchd] 已装 com.grokbridge.$job"
  done
elif [ "$OS" = "Linux" ]; then
  echo "[Linux] 未自动安装常驻(未实测)。二选一: (a) 按 systemd/grokbridge.md 配 user timer; (b) 直接轮询兜底:"
  echo "        while true; do python3 $BRIDGE/land_result.py; python3 $BRIDGE/reconcile.py; sleep 60; done"
else
  echo "[常驻] 未识别 OS, 请手动定时跑 land_result.py + reconcile.py"
fi

# 5. 体检
echo; echo "== 体检 =="
"$PY" "$BRIDGE/doctor.py" || true
echo; echo "下一步(见 README.md): 1) 把 docs/SUPERVISOR_BOT_PLAYBOOK.md 粘进你的常驻 bot(替换 <BRIDGE>) → 2) 建 webhook routine → 3) botreg 注册 → 4) handshake 绑定 → 开跑。"
