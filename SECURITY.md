# Security

This project has an intentional, non-default trust model — **single trusted operator, trusted agents, local machine only** — and a real local-code-execution surface via `action=run`.

**Read [docs/SECURITY.md](docs/SECURITY.md) before deploying.** It covers the threat model (indirect prompt injection), what is and isn't protected, and the deployment checklist.

Found a sharp edge? Open an issue — but assume the trust model in `docs/SECURITY.md` is intentional, not an oversight.
