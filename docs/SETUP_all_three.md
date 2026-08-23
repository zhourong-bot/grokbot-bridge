# Setup: all three (Grok Bot + Claude Code + Codex)

Supervisor = Grok Bot. Executors = Claude Code **and** Codex. Full mesh (6 edges).

1. `bash setup.sh` — both `CLAUDE_BIN` and `CODEX_BIN` auto-detect; set `GROKBRIDGE_PROXY` if your CLIs need one.
2. **Teach the bot**: paste [SUPERVISOR_BOT_PLAYBOOK.md](SUPERVISOR_BOT_PLAYBOOK.md) into your supervisor bot. Then Doorbell + `botreg.py set` + `handshake.sh`.
3. Roles:
   - **Grok Bot** — always-on supervisor; operates no-API backends; can delegate to either executor.
   - **Codex** — local machine access, debugging, verification (the one that can read logged-in apps / system).
   - **Claude Code** — orchestration, judgement, synthesis.
4. A relayed task can hop: `Claude → GrokBot → Codex → GrokBot → Claude`. The supervisor self-signs a `to=codex, action=run`, the bridge runs Codex, Codex writes a result file, the supervisor signs a `to=claude, action=deliver`.
5. `python3 doctor.py` should show both executors enabled and a verified bot.

Maintenance tip: for bug fixes, drive the supervisor bot directly and let it run `doctor.py` + `selftest.sh` as the pass/fail gate. For changes touching the exec/security path, verify via Codex (local) or review — don't let the cloud agent self-certify changes to its own guardrails. Security: [SECURITY.md](SECURITY.md).
