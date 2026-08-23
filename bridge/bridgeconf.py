#!/usr/bin/env python3
"""中央配置: 消除硬编码, 让任何人 clone 即用。
优先级: 环境变量 > bridge/config.sh > 自动探测 > 默认值。
codex / claude 均为可选——只装其一也能跑(2 件套)。"""
import os, shutil, pathlib

BRIDGE = pathlib.Path(__file__).resolve().parent


def _parse_value(raw):
    """按 shell 语义取值: 引号内原样(忽略引号后的行内注释); 无引号则在 ' #'/制表#处截断注释。"""
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in ('"', "'"):
        q = raw[0]
        end = raw.find(q, 1)
        return raw[1:end] if end != -1 else raw[1:]
    for sep in (" #", "\t#"):
        i = raw.find(sep)
        if i != -1:
            raw = raw[:i]
    return raw.strip()


def _load_config_sh():
    cfg = {}
    p = BRIDGE / "config.sh"
    if not p.exists():
        return cfg
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        cfg[k.strip()] = _parse_value(v)
    return cfg


# reconcile 等复用同一解析器, 别再各写一份朴素解析。
def load_config():
    return _load_config_sh()


_CFG = _load_config_sh()


def get(key, default=""):
    """环境变量优先, 其次 config.sh, 最后 default。"""
    return os.environ.get(key) or _CFG.get(key) or default


def _resolve_bin(key, candidates):
    """显式路径 > 'off' 显式禁用 > 空则 PATH/常见位自动探测 > None(视为未装/禁用)。"""
    v = get(key)
    if v:
        if v.strip().lower() in ("off", "none", "disabled", "disable"):
            return None  # 显式禁用: 即使装了也不启用(2 件套用这个关掉不想用的那个)
        return v if pathlib.Path(v).exists() else None
    name = candidates[0]
    found = shutil.which(name)
    if found:
        return found
    for c in candidates[1:]:
        if pathlib.Path(c).exists():
            return c
    return None


# ---- 路径 ----
# 事件总线(可选, 用于可观测). 默认落 bridge 自己目录, 不依赖外部项目。
BUS_PATH = pathlib.Path(get("GROKBRIDGE_BUS", str(BRIDGE / "bus" / "events.jsonl")))

# ---- 执行器(可选) ----
CLAUDE_BIN = _resolve_bin("CLAUDE_BIN", ["claude", "/usr/local/bin/claude",
                                         str(pathlib.Path.home() / ".local/node/bin/claude")])
CODEX_BIN = _resolve_bin("CODEX_BIN", ["codex", "/Applications/Codex.app/Contents/Resources/codex",
                                       "/Applications/ChatGPT.app/Contents/Resources/codex"])

# ---- 网络代理(可选, 默认无) ----
# 只有当你的 CLI 必须走本地代理才出网时才设(例如某些地区的 Claude/Codex)。默认空 = 直连。
PROXY = get("GROKBRIDGE_PROXY", "")


def enabled_executors():
    e = []
    if CLAUDE_BIN:
        e.append("claude")
    if CODEX_BIN:
        e.append("codex")
    return e


if __name__ == "__main__":
    print("BRIDGE     :", BRIDGE)
    print("BUS_PATH   :", BUS_PATH)
    print("CLAUDE_BIN :", CLAUDE_BIN or "(未装/未配 — claude 路由禁用)")
    print("CODEX_BIN  :", CODEX_BIN or "(未装/未配 — codex 路由禁用)")
    print("PROXY      :", PROXY or "(直连)")
    print("启用执行器  :", enabled_executors() or "(仅 deliver 回流, 无本机执行)")
