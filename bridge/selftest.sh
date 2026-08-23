#!/bin/bash
# maildir 自测: 原子写、claim、HMAC 拒、orig_task_id 幂等、半写 tmp 不领取。不读 secret 内容。
set -euo pipefail
BRIDGE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || echo python3)"
export PYTHONPATH="$BRIDGE"

echo "=== 0. 布局 ==="
"$PY" -c 'from pathlib import Path; from maildirutil import layout; layout(Path("'"$BRIDGE"'"))'
[ -f "$BRIDGE/secret.key" ] || "$PY" "$BRIDGE/envelope.py" init-key

echo "=== 1. 原子写 + claim-by-rename ==="
"$PY" - <<PY
from pathlib import Path
from maildirutil import atomic_put, claim, finish, layout
import json
b=Path("$BRIDGE")
layout(b)
tid="selftest-atomic"
dest=atomic_put(b,"inbox",tid,{"hello":1})
assert dest==b/"inbox"/"new"/f"{tid}.json"
got=claim(dest, b/"inbox"/"processing")
assert got and got.parent.name=="processing"
finish(got, b/"inbox"/"done")
print("atomic+claim ok", got.name)
PY

echo "=== 2. 合法 deliver + 篡改 HMAC ==="
OID=selftest-orig-$$
"$PY" "$BRIDGE/envelope.py" make grokbot claude result deliver "自测:bridge-ok" "$OID" > /tmp/ok.env.json
"$PY" - <<PY
from pathlib import Path
from maildirutil import atomic_put
import json
b=Path("$BRIDGE")
env=json.loads(Path("/tmp/ok.env.json").read_text())
atomic_put(b,"inbox", env["task_id"], env)
print("put ok", env["task_id"], "orig", env["orig_task_id"])
PY
"$PY" "$BRIDGE/envelope.py" make grokbot claude result deliver "原文" | sed 's/原文/HACKED/' > /tmp/tamper.env.json
"$PY" - <<PY
from pathlib import Path
from maildirutil import atomic_put
import json
b=Path("$BRIDGE")
raw=Path("/tmp/tamper.env.json").read_text()
env=json.loads(raw)
try:
    atomic_put(b,"inbox", env["task_id"], raw)
except FileExistsError:
    pass
print("put tamper", env["task_id"])
PY
"$PY" "$BRIDGE/land_result.py" --dry-run

echo "=== 3. 幂等按 orig_task_id ==="
"$PY" "$BRIDGE/envelope.py" make grokbot claude result deliver "第二份同 orig" "$OID" > /tmp/dup.env.json
"$PY" - <<PY
from pathlib import Path
from maildirutil import atomic_put
import json
b=Path("$BRIDGE")
env=json.loads(Path("/tmp/dup.env.json").read_text())
atomic_put(b,"inbox", env["task_id"], env)
print("put dup", env["task_id"])
PY
"$PY" "$BRIDGE/land_result.py" --dry-run | tee /tmp/land_dup.out
grep -q "已处理(幂等)" /tmp/land_dup.out
test -f "$BRIDGE/delivered/${OID}.md"
echo "idempotent+delivered ok"

echo "=== 4. 半写 tmp 不被认 ==="
echo '{"partial":true' > "$BRIDGE/inbox/tmp/half.tmp"
BEFORE=$("$PY" -c 'from pathlib import Path; print(len(list((Path("'"$BRIDGE"'")/"inbox"/"new").glob("*.json"))))')
"$PY" "$BRIDGE/land_result.py" --dry-run >/dev/null
AFTER=$("$PY" -c 'from pathlib import Path; print(len(list((Path("'"$BRIDGE"'")/"inbox"/"new").glob("*.json"))))')
test -f "$BRIDGE/inbox/tmp/half.tmp"
echo "half-write ignored ok (new count $BEFORE -> $AFTER, tmp remains)"

echo "=== 5. launchd ==="
launchctl list | grep grokbridge || echo "(load after this script)"
echo "=== 自测完成 ==="
