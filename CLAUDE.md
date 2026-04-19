# Claude-Code project memory — AlKazBot

This file is read automatically at the start of every Claude Code session
in this repo. Keep it terse; details live in `инструкции/` and `DECISIONS.md`.

---

## Bootstrap — run this before doing anything that needs remote access

```bash
source scripts/load-secrets.sh
```

That pulls secrets out of the user's **macOS Keychain** (managed by
`scripts/secrets.sh`) and exports:

- `RAILWAY_API_TOKEN` / `RAILWAY_TOKEN` — both point to the same
  Railway account token. Use `Authorization: Bearer $RAILWAY_API_TOKEN`
  on `https://backboard.railway.com/graphql/v2`.

Secrets were stored with `-T /usr/bin/security` ACL, so reads from the
`security` CLI are silent — no login-password prompt unless the user
specifically deny-listed access. If the Keychain does prompt, tell the
user to click **"Always Allow"** — it's a one-time thing.

If you need a secret that isn't stored yet, prompt the user to run:

```bash
./scripts/secrets.sh set-prompt <name>
```

and add a mapping in `scripts/load-secrets.sh`'s `MAPPING` array.

## Railway — key IDs (so you don't have to re-discover them)

- Project `peaceful-eagerness` → id `befcb51e-c4b4-4c3b-a6d4-7eeba2204d81`
- Service `AlKazBot` → id `3a79891c-3ddc-4e20-9a28-cfc65ed0c60d`
- Service `Postgres` → id `4eecb00e-ad0b-4f11-9a22-ecd4e531c630`
- Environment `production` → id `4ce7e1fd-5414-4c30-8505-46ce1ff0c5b7`

Common GraphQL queries — see `инструкции/СЕССИЯ_2026-04-19.md` for
worked examples (deployments list, logs fetch, env vars read).

## DB — direct access from your machine

The production DB is reachable via a public TCP proxy; connection string
lives in the Railway env var `DATABASE_URL`. You can get it with:

```bash
source scripts/load-secrets.sh  # if you've stashed it, else pull from Railway env
```

…or pull from Railway via GraphQL `variables(projectId,serviceId,environmentId)`.

## Conventions

- Python 3.12, `uv` for deps, `pytest` with `-q`, `ruff` for lint.
- Every schema change ships an Alembic migration (even for data-only
  seeds — use `ON CONFLICT` to keep it idempotent).
- Commits: conventional-ish prefix (`fix:`, `feat:`, `docs:`), signed
  off with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- Don't create docs files unless the user asks.

## Where to read up when you're new

- `sber26-bot-SPEC.md` — product spec.
- `DECISIONS.md` — architectural decisions log.
- `CHANGELOG.md` — release notes.
- `SESSION_HANDOFF.md` — last-session snapshot (may be stale).
- `инструкции/` — accumulated session dumps & how-tos.
