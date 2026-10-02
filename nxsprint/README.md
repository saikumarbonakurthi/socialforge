# NxSprint

AI scrum master for SRIA Infotech. Reads GitHub Projects v2, detects problems with deterministic rules, nudges the right person, prepares ceremonies. Dry-run by default. Humans stay accountable.

Status: **Phase 1** (GitHub sync, snapshots, demo). No rules, LLM or Teams yet.

## Layout
`core/` FastAPI service (`app/{api,domain,integrations,llm,jobs}`, `alembic/`, `tests/`), `n8n/workflows/`, `config/`, `docs/`.
Lives in `nxsprint/` because this repo root belongs to SocialForge.

## Run
```
make env     # creates .env (generated secrets) and config/projects.yaml from SAMPLE values
make up      # postgres + core (runs migrations) + n8n
curl localhost:8000/health
```
See it with zero integrations: `make demo` seeds a fake project through the real sync path and prints the board.

Sync: `POST /jobs/sync` (bearer secret) pulls every configured project from GitHub (needs `NXSPRINT_GITHUB_TOKEN`, read only) and writes a snapshot only when an item changed. `POST /webhooks/github` verifies the HMAC signature and records the event; it does not trigger a sync yet.

Dev without Docker (Python 3.12): `make install && make migrate && make test && make lint`.

## Config
`config/projects.yaml` (see `projects.example.yaml`). Validated at startup, bad config stops the service.
Nothing that messages a human has a default: holidays, quiet hours, thresholds, cooldowns, escalation hours and channels are all required.
The example is marked `placeholder: true`, which the service refuses in `live` mode.

## Modes
`NXSPRINT_MODE=dry_run` (default) or `live`. See `.env.example` for all variables.
