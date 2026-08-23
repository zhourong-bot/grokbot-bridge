# 复制成 config.sh 再填。config.sh 不入库(含密钥)。全部可选——不设就用默认/自动探测。
# 也可全部走环境变量, 二选一。注释一律单独成行, 赋值行只放值。

# —— 门铃 webhook(Local→Bot 叫醒) ——
# 在你的常驻 Bot 对话里建一个「webhook 触发」routine, 把它给的 URL/密钥填这里。
# 更推荐用 botreg 多 bot 寻址(见 docs/BOT_HANDOFF.md), 这里作单 bot 默认。
GROK_WEBHOOK_URL=""
GROK_WEBHOOK_KEY=""

# —— 本机执行器(可选) ——
# 留空 = 自动探测 PATH(装了就启用); 填绝对路径 = 用它; 填 off = 显式禁用(即使装了也不用)。
# 只用 2 件套时, 把不想用的那个设成 off。
CLAUDE_BIN=""
CODEX_BIN=""

# —— 网络代理(可选, 默认直连) ——
# 仅当你的 CLI 必须走本地代理才出网(某些地区)才设, 例如 http://127.0.0.1:7897
GROKBRIDGE_PROXY=""

# —— 其它可选 ——
# 事件总线路径, 默认 bridge/bus/events.jsonl
GROKBRIDGE_BUS=""
# outbox 超时重叮门铃的秒数
GROKBRIDGE_STALE_S="90"
# 设 1 才允许桥用 codex exec resume 推到已开会话(默认关, thread-only 更稳)
GROKBRIDGE_CODEX_RESUME=""
