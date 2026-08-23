# Setup: Grok Bot + Codex (2-tool)

Supervisor = Grok Bot. Executor = Codex CLI. No Claude Code.

1. **Install & config**: `bash setup.sh`. In `config.sh` set `CLAUDE_BIN=off` (explicit disable); `CODEX_BIN` auto-detects (or set an absolute path).
   - If your Codex CLI must reach its model through a local proxy, set `GROKBRIDGE_PROXY=http://127.0.0.1:<port>` (launchd/systemd don't inherit your shell proxy).
2. **Teach the bot**: paste [SUPERVISOR_BOT_PLAYBOOK.md](SUPERVISOR_BOT_PLAYBOOK.md) into it (replace `<BRIDGE>`).
3. **Doorbell + bind** (same as the Claude recipe): `botreg.py set` then `handshake.sh`.
4. **Use it**: supervisor writes `to=codex, action=run`; the bridge runs `codex exec -C <bridge> <brief>`. Codex writes its result to a file the supervisor reads, or a `deliver` reply.
5. **Health**: `python3 doctor.py` (lists enabled executors as `['codex']` — expected).

Note: a triggered `codex exec` runs with `--sandbox workspace-write` scoped to the bridge dir. Security: [SECURITY.md](SECURITY.md).
