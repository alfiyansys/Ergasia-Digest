# AGENTS.md — Ergasia Digest

A small FastAPI webservice that pulls activity from GitHub + GitLab (multi-account, multi-endpoint GitLab), merges it into a single digest, and exposes it over HTTP + CLI. Sending notifications to chat is deliberately **not** this service's responsibility — that's an external agentic harness's job.

**Read `PLAN.md` first before starting work.** This file is just a summary of rules that are easy to violate if you only read the code; `PLAN.md` is the source of truth for the schema, endpoints, output format, and implementation step order (§8).

## Status

No code yet — just `PLAN.md`. If asked to "continue the implementation" without further detail, follow the step order in `PLAN.md` §8 as-is — don't reorder or skip steps without asking.

**Check off the corresponding `- [ ]` box in `PLAN.md` §8 (`- [ ]` → `- [x]`) as soon as a step or lettered sub-step is actually done** — include that edit in the same commit as the step itself, not as a separate batch-update pass later. `PLAN.md` §8 is the one place progress is tracked across sessions, so an unchecked box must mean "not done yet," not "done but forgot to mark it."

## Rules not to break without discussing first

- **Account management (`add`/`list`/`delete`) is CLI-only, via `cli.py`.** Don't build an HTTP `/accounts` endpoint in any form — this is a deliberate decision (§2), not something that just hasn't been gotten to yet. Reason: accepting raw tokens over the network adds attack surface for the most sensitive operation in this service.
- **`accounts.json` stores raw per-account tokens.** It must be in `.gitignore`, and must be `chmod 600`. Never log its contents or echo back a raw `api_key` from `cli.py accounts list` (mask it, e.g. show only the last 4 characters).
- **Two different meanings of "API key" — don't conflate them:** the per-account `api_key` (the GitHub/GitLab token belonging to a tracked account, stored in `accounts.json`) vs. the service's `API_KEY` (an env var, checked from the `X-API-Key` header to protect HTTP endpoints). When naming variables/parameters, use clearly distinct names (e.g. `account_api_key` vs. `service_api_key`) — don't just call everything `api_key`.
- **`notify.py` is deliberately not wired into any endpoint or command.** This is deferred by design (an external harness sends notifications instead), not a TODO that needs finishing urgently. Don't call it from `/digest/run` or `cli.py digest run` without an explicit discussion first.
- **`?hours=`/`?days=` only exist on `/digest/preview` and `cli.py digest preview`.** `/digest/run` and `cli.py digest run` deliberately don't accept this override — both must stay consistent with incremental `last_run` so state never has a gap/overlap.
- **`base_url` is only valid for `type: gitlab`.** Reject it (validation error) if this field is set for `type: github` — GitHub Enterprise isn't supported yet.
- **The digest output format must exactly follow `PLAN.md` §3:** a `Platform:`/`Username:`/`Activities:` block per account, `commits created` always shown (including 0), other metrics dropped when 0, and accounts that failed to fetch still appear as a block (with a short error message) — never silently skipped.
- **Never render the real self-hosted GitLab hostname (`base_url`) in digest output.** That text leaves the host via the harness (§6), so it's an external-exposure risk, not just an internal detail. Use the account's `label` if set, otherwise a generic `GitLab (self-hosted)` fallback (`PLAN.md` §2/§3). The real hostname is fine in `cli.py accounts list` (local-only) and internally in `account_id` — just don't let `account_id` or `base_url` leak into anything sent to chat.
- **`cli.py digest run` and `POST /digest/run` read/write the same state/cache files with no locking.** Don't run both at the same time; this is a deliberate choice to avoid over-engineering file-locking into a tool this small internally — don't "fix" it by adding locking unless asked.

## Code conventions (planned — follow once `app.py`/`cli.py` are built)

- `digest.py` (`fetch_all_events()`, `build_digest()`) must stay pure functions — shared by both `app.py` (HTTP) and `cli.py` (CLI) with no logic duplication. If adding a digest feature, put the logic here, not in `app.py`/`cli.py`.
- `sources/github_source.py` and `sources/gitlab_source.py` take credentials/parameters (`base_url`, `username`, `token`, `since`) as function arguments — don't read env vars directly inside these modules (that's what made multi-account support hard in the first place).
- `state.py` tracks the `last_run` key per `account_id`, not per-source.

## Git Workflow

- **Gitflow with `dev` as staging, one branch per phase:** `main` = always stable/production (what runs on the production host), `dev` = staging/integration — where phase branches get merged and validated before going up to `main`. `PLAN.md` §8 groups the implementation steps into phases (Foundation, Storage Layer, Source Clients, Digest Core, Interfaces, Documentation); each phase gets its own `feature/phase-<n>-<slug>` branch (e.g. `feature/phase-1-foundation`, `feature/phase-2-storage-layer`), branched from an up-to-date `dev` (not from `main`) — **not** a branch per individual step. A phase branch is merged back to `dev` only once every step inside that phase is checked off and smoke-tested together. `dev` only gets merged/promoted to `main` once a few phases have been tried out together in staging and are considered deploy-ready — don't commit directly to `main` or to `dev` for work that isn't a fully finished phase.
- **Granular commits per logical step within a phase branch** — a phase branch holds several commits, not one giant commit for the whole phase:
  - One commit = one self-contained logical change (e.g. "add `accounts_store.py` CRUD" separate from "add `cli.py accounts` subcommand", even though both are part of the same phase).
  - Don't mix unrelated concerns in one commit (a new feature + an unrelated refactor + a config change all at once).
  - Commit messages should be short, imperative ("add ...", "fix ...", not "adding..."), describing **what** changed — a long rationale/why belongs in the PR/commit body if needed, not the title.
  - Each commit should ideally leave things in a runnable state (don't leave a half-finished state that makes the branch un-runnable/un-testable at that commit).
- **Commit messages must not include any Claude/AI-assistant attribution** — no `Co-Authored-By: Claude`, no session link, no similar trailer. That information only needs to be mentioned once in `README.md` (e.g. a "built with help from Claude Code" section), not repeated in every commit.
- Make sure `.gitignore` (excluding `accounts.json`, `.env`) is committed **before** those secret files exist in the working tree — don't let one slip into a commit and only `.gitignore` it afterward.

## Running & Testing (once built)

```
uvicorn app:app --host 127.0.0.1 --port 8000
python cli.py accounts add --type github --username <user>
python cli.py accounts list
python cli.py accounts delete <id>
python cli.py digest preview [--account <id>] [--hours N | --days N]
python cli.py digest run
python cli.py digest latest
```

No test suite yet. If adding one, prioritize unit tests for `digest.py` (`fetch_all_events`/`build_digest` with mocked GitHub/GitLab events) and `accounts_store.py` (field validation, id auto-generation, masking) — these two modules are the easiest to get silently wrong.
