# Security model & threat model

**One sentence:** this is a coordination bus for a *single trusted operator* running *trusted agents* on *their own machine*. It deliberately trades hardening for zero-friction local automation. Read this before deploying, and never expose it to untrusted input or networks.

## Trust assumptions (what it relies on being true)

1. **One operator.** You control all three sides (the local machine, the executors, the cloud supervisor account).
2. **Shared symmetric key.** `secret.key` is shared by all parties, including the cloud agent, so the agent can sign its own replies. This means the key — and the ability to mint valid envelopes — has, by design, left the local machine.
3. **Private doorbell.** The webhook URL and its bearer secret are not public.

If any of these is false for your setup, do not use this as-is.

## The main risk: indirect prompt injection → local execution

`land_result.py` routes an `action=run` envelope to `claude -p <brief>` / `codex exec <brief>`. A valid HMAC only proves *the sender holds the shared key* — it does **not** prove the brief is benign.

The realistic attack is **not** key theft. It is **indirect prompt injection**: if a task brief contains attacker-controlled external text (a web page, a scraped post, a document), that text can steer the *legitimate* supervisor into signing a perfectly valid `run` that does something you didn't intend. The bridge cannot distinguish "a real delegation" from "an injected delegation" — both signatures are valid.

### Mitigations shipped (defense-in-depth, not guarantees)

- **Dangerous-pattern scan** on every `run` brief (`land_result.py: scan_danger`): refuses briefs matching destructive/persistence patterns (`rm -rf`, pipe-to-shell, writes to `LaunchAgents`, `secret.key`, shell rc files, `sudo`, `dd`, `mkfs`, …). A refused brief goes to the dead-letter queue with a reason.
- **Write-path allowlist** (`deliver_brief`): a `deliver` may only write under `delivered/`. It cannot overwrite `~/.zshrc`, a LaunchAgent, or `secret.key`.
- **Closed action set** (`run` / `deliver` / `echo`) — no arbitrary verbs.

These raise the bar; they do not close it. A cleverly-worded non-matching command can still run. **Do not feed briefs built from untrusted content into a bridge with executors enabled.**

### If you want it tighter

- Run with **no executors** — set `CLAUDE_BIN=off` and `CODEX_BIN=off` in `config.sh`. (Leaving them *empty* auto-detects and **enables** whatever is on `PATH`; `off` is the explicit disable.) You keep the supervisor + `deliver` results, and nothing auto-executes.
- **Sandbox asymmetry:** a triggered `codex exec` runs under `--sandbox workspace-write` scoped to the bridge dir, but a triggered `claude -p` runs with **no sandbox** — treat the Claude route as the more powerful one and enable it only when you trust the brief source. Add your own `--allowedTools`/sandbox flags to the `claude` command in `land_result.py: trigger()` if you want it tighter.
- Gate `run` behind manual approval: change `trigger()` to drop `run` into a `pending_approval/` box you release by hand.
- Add per-executor `--allowedTools` / sandbox flags to the trigger command.

## What is NOT protected

- **Source attribution.** Because the key is shared, any party can sign an envelope claiming any `from`. The bus cannot cryptographically tell Claude's message from Codex's. (Fix if you need it: give each party its own key and select the verifying key by `from`.)
- **Replay.** `ts`/`ttl_s` are signed but not enforced as a hard reject (a stale reply after a laptop sleep is a legitimate case we didn't want to drop). Replay within the trust boundary is possible; `orig_task_id` idempotency limits its effect. `reconcile.py` surfaces over-age items to the bus as a warning.
- **Multi-tenant isolation.** There is none. This is single-operator by design.

## Delivery semantics

- **At-least-once + idempotent**, not exactly-once. A crash between "delivered" and "marked seen" can re-deliver. Design briefs to be safe to re-run.
- **Crash recovery:** `reconcile.py` recovers items orphaned in `processing/` (retried up to 3× then dead-lettered) and reports executor runs that exited non-zero or never exited.

## Deployment checklist

- [ ] `secret.key` is `chmod 600` and never committed (`.gitignore` covers it).
- [ ] `config.sh` (with your webhook secret) is never committed.
- [ ] The doorbell URL is private; rotate its secret if it ever leaked (it can appear in chat logs).
- [ ] You ran `handshake.sh <role>` and saw the nonce echo back **with the identity you expected** before sending real tasks.
- [ ] You understand that `run` executes; you do not build briefs from untrusted content.

## Reporting

This is a personal reference implementation. If you find a sharp edge, open an issue — but assume the trust model above is intentional, not an oversight.
