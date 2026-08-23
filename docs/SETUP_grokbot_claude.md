# Setup: Grok Bot + Claude Code (2-tool)

Supervisor = Grok Bot (always-on). Executor = Claude Code CLI. No Codex.

1. **Install & config**
   ```bash
   bash setup.sh
   cd bridge && cp config.sample.sh config.sh   # if setup didn't
   ```
   In `config.sh`: set `CODEX_BIN=off` (explicitly disables the Codex route). `CLAUDE_BIN` auto-detects `which claude`, or set an absolute path.
2. **Doorbell**: in your Grok Bot conversation, create a webhook-trigger routine; copy its URL + secret.
   **Before binding, teach the bot:** paste [SUPERVISOR_BOT_PLAYBOOK.md](SUPERVISOR_BOT_PLAYBOOK.md) into your bot (replace `<BRIDGE>` with this repo's `bridge/` path). Without it the bot won't know the mailbox or how to sign.

   ```bash
   python3 botreg.py set super "<url>" "<secret>" "supervisor"
   bash handshake.sh super          # must echo the nonce back before it's usable
   ```
3. **Use it**: the supervisor delegates local work to Claude by writing an inbox envelope `to=claude, action=run`; results come back to `delivered/`.
   ```bash
   bash send_doorbell.sh --bot super "your task" webhook
   python3 wait_deliver.py $(date +%s) 900 ""
   ```
4. **Health**: `python3 doctor.py`. `doctor` will list enabled executors as `['claude']` — expected.

Security: read [SECURITY.md](SECURITY.md). `run` executes as `claude -p`; don't build briefs from untrusted content.
