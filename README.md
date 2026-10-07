# grokbot-bridge

**A local, file-based work bus that lets an always-on cloud agent supervise ephemeral CLI coding agents — so long tasks survive the conversation handoffs that would otherwise derail them.**

> Plain chat agents degrade as the conversation grows; you start a new conversation, and context handoff is lossy — long tasks drift. This bridge keeps the *supervisor / memory* in an **always-on agent that never needs a handoff** (e.g. a Grok Bot), and lets the executors (Claude Code, Codex) be **replaceable short-lived workers**. The supervisor holds the thread; the task stays on-rails.

Zero infrastructure: no server, no database, no inbound ports. Just atomic files, HMAC-signed envelopes, and a webhook doorbell.

---

## The problem it solves

- **Handoff rot.** A long task run inside one chat session loses coherence when you're forced to open a fresh conversation. The new session never quite inherits the old one's intent.
- **The fix.** Put the persistent part — the supervisor that remembers the goal and checks the work — in an agent that is *always on* and *never handed off*. Executors come and go; the supervisor is the through-line.
- **The hard part.** The cloud supervisor (Grok Bot) has **no official "summon" API**. This bridge solves that with a **doorbell** (a webhook that only *wakes* it) plus a **shared-file mailbox** (the actual task content), and proves delivery with an **echo handshake** — not an HTTP 200.

## How it works

```
 Local (your Mac)                          Cloud
 ┌───────────────┐   doorbell (webhook, wake-only)   ┌──────────────┐
 │ send_doorbell │ ────────────────────────────────▶ │  Grok Bot    │
 │   + outbox/   │   task body via shared file  ◀───▶ │ (always-on   │
 │               │                                    │  supervisor) │
 │  land_result  │ ◀── inbox/ (signed reply) ──────── │  reads/writes│
 │  (launchd)    │                                    │  local files │
 └──────┬────────┘                                    └──────────────┘
        │ routes by action:
        │  • deliver → delivered/<id>.md   (result for you)
        │  • run     → claude -p / codex exec   (put an executor to work)
```

- **Maildir mailbox** (`outbox/`, `inbox/`): atomic `tmp → fsync → rename` delivery, crash-safe.
- **HMAC-signed envelopes** with a closed action set (`run` / `echo` / `deliver`), `orig_task_id` idempotency, TTL.
- **Doorbell = wake only.** The webhook body carries just a task id; the real content travels as a file the cloud agent reads. (Webhooks can't reliably carry a payload to a routine — so we don't try.)
- **Echo handshake** (`handshake.sh`): the *only* proof you reached the right bot. Send a nonce, require it echoed back **plus a self-reported identity**. HTTP 200 means "the endpoint accepted it," not "the right bot got it."
- **Event-driven wait** (`wait_deliver.py`): native kqueue on `delivered/` (macOS/BSD), polling fallback elsewhere; exits the instant a result lands.
- **Self-healing** (`reconcile.py`, launchd): recovers crashed in-flight items, flags silently-failed executor runs, re-rings stalled doorbells.

## Quickstart

```bash
git clone <this repo> grokbot-bridge && cd grokbot-bridge
bash setup.sh          # creates config + key; installs launchd daemons on macOS; runs doctor
```

**Platforms.** macOS is the primary, tested target — it's where the Grok Bot desktop app and its local-file access live, and where `setup.sh` installs launchd jobs for you. The Python code itself is portable (the file-watch falls back to polling off macOS), so Linux works in principle with manual daemon setup ([systemd/grokbridge.md](systemd/grokbridge.md)) — but that path is **best-effort and unverified**. Windows is not supported.

Next, teach your bot how to participate: paste [docs/SUPERVISOR_BOT_PLAYBOOK.md](docs/SUPERVISOR_BOT_PLAYBOOK.md) into your always-on bot (it tells the bot where the mailbox is, the envelope format, how to sign, and how to answer a handshake). Without this, the doorbell just wakes a bot that doesn't know what to do.

Then, in that **always-on bot** conversation, create a *webhook-trigger routine* and copy its URL + secret. Register and **prove the binding**:

```bash
cd bridge
python3 botreg.py set mybot "<webhook_url>" "<secret>" "my supervisor bot"
bash handshake.sh mybot        # sends a nonce; only marks verified when it echoes back
```

Until a role is verified, `send_doorbell.sh --bot mybot` **refuses** to send real tasks (so you can never fire at the wrong bot). Then:

```bash
bash send_doorbell.sh --bot mybot "your task brief" webhook
python3 wait_deliver.py $(date +%s) 900 ""     # near-zero-latency wait for the reply
```

Check health any time:

```bash
python3 bridge/doctor.py     # PASS/WARN/FAIL on key, layout, daemons, verified bots, orphans, DLQ
python3 bridge/dlq.py list   # see anything that failed
```

## Pick your combination — 2 tools or 3

The bridge routes between `claude`, `codex`, and `grokbot`, but **needs none of them all**. Executors are optional and auto-detected.

| You have | Setup |
|---|---|
| Grok Bot **+ Claude Code** | [docs/SETUP_grokbot_claude.md](docs/SETUP_grokbot_claude.md) |
| Grok Bot **+ Codex** | [docs/SETUP_grokbot_codex.md](docs/SETUP_grokbot_codex.md) |
| All three | [docs/SETUP_all_three.md](docs/SETUP_all_three.md) |

If an executor isn't installed its route is cleanly disabled; to disable one you *do* have installed, set `CLAUDE_BIN=off` / `CODEX_BIN=off` in `config.sh`. Either way you still get the supervisor + `deliver` results.

## ⚠️ Security model — read before you deploy

This is built for a **single trusted operator running trusted agents on their own machine.** It is **not** a multi-tenant or hardened service. Specifically:

- **`action=run` executes the envelope's brief** as `claude -p <brief>` / `codex exec <brief>`. The signature proves "holds the shared key," and in this design **the key is shared with the cloud agent**. So a signed `run` is, by design, **local code execution driven by the supervisor.**
- **Indirect prompt-injection is the real threat.** If a task brief contains external content (a web page, a scraped post), that content could steer the legitimate supervisor into signing a malicious `run`. The bridge ships a **dangerous-pattern scanner** and a **write-path allowlist** as defense-in-depth — these reduce risk, they are **not** a guarantee. Do not feed untrusted content into briefs you wouldn't run yourself.
- **Keep the doorbell URL and `secret.key` private.** Anyone who can post to your doorbell can wake the bot; anyone with the key can sign envelopes.
- **Delivery is at-least-once + idempotent**, not exactly-once. Duplicate delivery is possible; design briefs to be safe to re-run.

Full detail and the threat model: [docs/SECURITY.md](docs/SECURITY.md).

## What's inside

| File | Role |
|---|---|
| `bridge/envelope.py` | HMAC envelope: sign / verify / closed field set |
| `bridge/maildirutil.py` | atomic put, claim-by-rename, finish |
| `bridge/land_result.py` | router (launchd): verify, idempotency, deliver / trigger executor, guardrails |
| `bridge/reconcile.py` | self-heal: orphan recovery, run-timeout alerts, stale doorbell re-ring |
| `bridge/notify_reply.py` | append thread logs, resume the bound session (only) |
| `bridge/send_doorbell.sh` | write outbox + ring doorbell, role-addressed, verified-gate |
| `bridge/botreg.py` | bot registry: role ↔ webhook URL, verification state |
| `bridge/handshake.sh` | echo-handshake binding — the only proof of "right bot" |
| `bridge/wait_deliver.py` | kqueue event-driven wait for results |
| `bridge/doctor.py` / `dlq.py` | health check / dead-letter queue |
| `bridge/bridgeconf.py` | central config: paths, optional executors, proxy |

## Prior art

The messaging substrate is textbook: a local **transactional outbox** + **Maildir** atomic delivery + **at-least-once with idempotency** — the same shape as [agent-message-queue](https://github.com/avivsinai/agent-message-queue). What's distinct here is the **non-handoff-supervisor** framing and the **no-API cloud-agent doorbell + echo-handshake identity binding** — waking and trusting an agent that exposes no endpoint of its own.

## License

MIT — see [LICENSE](LICENSE).
# 有界门铃重试

`reconcile.py` 对未回流的 stale outbox 任务按文件名 task id 持久化
`state/nudge_attempts.json`：首次 stale 时间、重叮次数、上次重叮时间和耗尽状态。
每个任务最多重叮 3 次，指数退避基础 60 秒、上限 300 秒；第三次后仍等待
240 秒回流，首次 stale 起绝对 TTL 为 900 秒。网络失败也消耗次数；没有 webhook
仍受 TTL 限制。正常的 delivered/seen 归档优先于耗尽检查。

耗尽的原始信封保留在 `outbox/failed`，附 `.reason`，并写一次 bus error。
使用 `python3 bridge/dlq.py --outbox list` 查看；人工核查后如需重新发起，使用新的
task id，避免无意重置预算。终态记录保留以阻止同一 task id 再次无限重叮。
重试状态损坏或写入失败时停止本轮，避免重置预算；重叮状态与 POST 由独立文件锁串行化。
错误事件沿用现有 best-effort bus；异常中断时优先保证不重复发送，不能保证事件一定写入。

回归测试：`python3 -m unittest discover -s tests -v`（标准库，无真实 webhook 调用）。

