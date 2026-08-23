# Linux 常驻(systemd user units)

`~/.config/systemd/user/grokbridge-land.service`:
```ini
[Unit]
Description=grokbot-bridge land
[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 %h/grokbot-bridge/bridge/land_result.py
```
`~/.config/systemd/user/grokbridge-land.timer`:
```ini
[Unit]
Description=run grokbridge land every 60s
[Timer]
OnBootSec=30
OnUnitActiveSec=60
[Install]
WantedBy=timers.target
```
reconcile 同理(换 reconcile.py)。启用:
```bash
systemctl --user enable --now grokbridge-land.timer grokbridge-reconcile.timer
```
> systemd timer 无 WatchPaths, 靠 60s 轮询; 想要文件事件即时唤醒可另跑 `inotifywait` 触发 land_result.py。
最省事的跨平台兜底(不装常驻):
```bash
while true; do python3 bridge/land_result.py; python3 bridge/reconcile.py; sleep 60; done
```
