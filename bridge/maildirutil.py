#!/usr/bin/env python3
"""maildir helpers: 布局、原子写(tmp+fsync+rename)、claim-by-rename。不碰 secret.key。"""
import json, os, uuid, time, pathlib

SUBS = ("tmp", "new", "processing", "done", "failed")

def layout(bridge: pathlib.Path):
    for box in ("outbox", "inbox"):
        for s in SUBS:
            (bridge / box / s).mkdir(parents=True, exist_ok=True)
    for extra in ("delivered", "state", "logs"):
        (bridge / extra).mkdir(parents=True, exist_ok=True)
    return bridge

def atomic_put(bridge, box, task_id, data):
    """box is 'outbox' or 'inbox'. data: bytes or str or dict. dest = box/new/<task_id>.json"""
    layout(bridge)
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False).encode()
    elif isinstance(data, str):
        data = data.encode()
    tmp = bridge / box / "tmp" / (uuid.uuid4().hex + ".tmp")
    dest = bridge / box / "new" / (task_id + ".json")
    if dest.exists():
        raise FileExistsError(str(dest))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(str(tmp), str(dest))
    return dest

def claim(src: pathlib.Path, processing_dir: pathlib.Path):
    dest = processing_dir / src.name
    try:
        os.rename(str(src), str(dest))
        return dest
    except FileNotFoundError:
        return None
    except OSError:
        return None

def finish(src: pathlib.Path, dest_dir: pathlib.Path):
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    os.rename(str(src), str(dest))
    return dest

def orig_id(env: dict) -> str:
    if env.get("orig_task_id"):
        return env["orig_task_id"]
    p = env.get("payload") or {}
    if isinstance(p, dict) and p.get("orig_task_id"):
        return p["orig_task_id"]
    return env.get("task_id", "")
