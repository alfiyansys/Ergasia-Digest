# PLAN.md — Ergasia Digest

A small webservice that pulls activity from GitHub and GitLab — including from multiple accounts at once and multiple GitLab instances (gitlab.com + self-hosted, e.g. GitLab CSRG) — and merges it into a single summary. Account registration (add/list/delete) is **CLI-only**, run directly on the host where the service runs. Digest stats can be accessed via HTTP **or** CLI. Sending to chat (Slack / OpenClaw) is **not** this service's responsibility — that part is deferred to an agentic harness that reads the digest output and sends its own notifications (see §4 and §6).

## 1. Project Structure

```
ergasia-digest/
├── app.py                 # FastAPI app + HTTP digest endpoints (HTTP entrypoint)
├── cli.py                 # CLI entrypoint: account management (add/list/delete) + digest stats access
├── digest.py              # Core logic: iterate accounts → fetch → aggregate → format (used by app.py & cli.py)
├── sources/
│   ├── github_source.py     # GitHub Events API client — takes (username, token) per account
│   └── gitlab_source.py     # GitLab Events API client — takes (base_url, username, token) per account
├── accounts_store.py       # Account CRUD: load/save/add/list/delete (reads/writes accounts.json) — called by cli.py, read-only from digest.py
├── accounts.json            # (git-ignored, chmod 600) Active account data — managed via cli.py, never edited manually or over HTTP
├── accounts.example.json    # Example account schema (no real tokens) — for documentation
├── state.py                # Tracks last-run timestamp per account (no longer per-source)
├── notify.py                # (deferred, not wired up yet — see §4) Sends digest output (Slack / generic webhook / stdout)
├── requirements.txt
├── .env.example
├── .gitignore                # Must exclude accounts.json (contains raw tokens)
└── README.md                 # Install, config, deploy, cron instructions
```

**Current status:** `sources/`, `state.py`, and `notify.py` already exist and have passed a smoke test (the `build_digest` logic runs), but are still the old single-account/single-endpoint version — they need refactoring to accept `(base_url, username, token)` as parameters instead of reading straight from env vars, so they can be reused per account. `digest.py` is still the old CLI version and needs to be stripped down to pure functions plus a multi-account loop. `app.py`, `cli.py`, and `accounts_store.py` don't exist yet.

## 2. Account Management (CLI-only) & Multi-GitLab-Endpoint

Accounts are **not edited manually via a config file, and not over HTTP at all** — only through the `accounts` subcommand in `cli.py` (add/list/delete), run directly on the host. `accounts_store.py` reads/writes `accounts.json` on disk as the runtime source of truth; both `cli.py` and `digest.py` import this module.

**Why CLI-only, not HTTP:** adding an account means accepting a raw personal access token. An HTTP endpoint for that — even gated by `X-API-Key` — still adds attack surface for the most sensitive operation in this service (it could be used to inject/enumerate tokens if `API_KEY` ever leaks). CLI-only means only someone with direct shell/SSH access to the host can manage accounts — the gate becomes OS access, not the network. `cli.py accounts add` also asks for `api_key` via a hidden prompt (`getpass`, not a command-line flag) so the token doesn't end up in shell history or show up in `ps aux`.

**Schema for each account** (`accounts.example.json` documents this shape, without real token values):

```json
{
  "id": "gitlab-csrg-alfiyan",
  "type": "gitlab",
  "username": "alfiyan",
  "base_url": "https://gitlab.csrg.example.com",
  "api_key": "glpat-xxxxxxxxxxxxxxxx"
}
```

Fields:
- `id` — unique, used as the `last_run` tracking key in `state.py` and as the grouping label in digest output (see §3). Optional on `accounts add`; if omitted, it's auto-generated from `{type}-{host}-{username}` (`host` = `github.com` for GitHub, or the hostname from `base_url` for GitLab).
- `type` — `github` or `gitlab`.
- `username` — username on the relevant platform.
- `api_key` — personal access token for accessing the GitHub/GitLab Events API on behalf of this account (entered via hidden prompt, see above). **This is different from the service's `API_KEY`** (see §4 Auth) — the term "API key" here refers to the tracked account's own token, not the shared secret that protects Ergasia Digest's HTTP endpoints.
- `base_url` — **only applies to `type: gitlab`**, optional, defaults to `https://gitlab.com` if left blank (supports a custom/self-hosted GitLab endpoint, e.g. GitLab CSRG). For `type: github`, this field must be left blank/is rejected — GitHub Enterprise custom endpoints aren't supported yet, could be a future step if needed.

**CLI commands:**

```
$ python cli.py accounts add --type github --username alfiyansys
Account API key (hidden input): ****************
Account added: github-github.com-alfiyansys

$ python cli.py accounts add --type gitlab --username alfiyan --base-url https://gitlab.csrg.example.com
Account API key (hidden input): ****************
Account added: gitlab-csrg.example.com-alfiyan

$ python cli.py accounts list
ID                                  TYPE    USERNAME     BASE_URL                    LAST_RUN
github-github.com-alfiyansys        github  alfiyansys   github.com                  2026-07-24T21:00:00+07:00
gitlab-csrg.example.com-alfiyan     gitlab  alfiyan      gitlab.csrg.example.com     -

$ python cli.py accounts delete gitlab-csrg.example.com-alfiyan
Account 'gitlab-csrg.example.com-alfiyan' deleted.
```

Rules:
- `type: gitlab` can be registered multiple times with different `base_url`s (gitlab.com, other self-hosted GitLab instances) — no separate instance needed, just a new `accounts add`.
- `type: github` can also be registered multiple times, for several GitHub accounts/organizations at once.
- `digest.py` (`fetch_all_events()`) iterates over every account in `accounts.json` (read via `accounts_store.py`), calling `github_source.py`/`gitlab_source.py` per `type` with that account's parameters, then tags each event with `account_id` so `build_digest()` can group results per account in the final summary (exact format in §3).
- If one account fails to fetch (rate limit / network error), the other accounts still get processed; the failed account's `last_run` is **not** updated, so its window isn't lost on the next run.
- `accounts delete` also cleans up the associated `last_run` entry in `state.py`, so no stale state is left hanging around.

**Security:** since each account's `api_key` is stored directly in `accounts.json`, this file **must** be in `.gitignore` and have restricted permissions (`chmod 600`). `accounts list` never shows the raw `api_key` — only a masked version (e.g. last 4 characters).

## 3. Digest Output Format

Each account is rendered as one block, looped in order through `accounts.json`. Required format per block — used by both HTTP output (§4) and CLI output (`cli.py digest ...`):

```
Platform: <platform label>
Username: <username>
Activities: <N> commits created, <other metrics>
```

Example (two accounts):

```
Platform: GitHub
Username: alfiyansys
Activities: 8 commits created, 2 pull requests opened, 1 pull request merged, 3 issues closed

Platform: GitLab (gitlab.csrg.example.com)
Username: alfiyan
Activities: 5 commits created, 1 merge request opened
```

Rules:
- **Platform label** — `GitHub` for `type: github`; `GitLab` if `base_url` is the default (gitlab.com); `GitLab (<hostname>)` if `base_url` is custom/self-hosted. Using the hostname (not just a generic "GitLab") keeps things distinguishable when there's more than one GitLab account (gitlab.com + self-hosted, or several self-hosted at once).
- **Minimum metric:** `commits created` (from push events) — must always be shown, including when the value is 0.
- **Other metrics (for personal esteem)** — computed from whatever events are available in each platform's Events API, using platform-correct terminology (GitHub: "pull request"; GitLab: "merge request"):
  - pull/merge requests opened
  - pull/merge requests merged
  - issues opened
  - issues closed
  - (optional, future step if there's time: code review comments given, new repos created)
- Metrics with a value of 0 **other than** commits may be dropped from the `Activities` sentence, so it doesn't get noisy/long on quiet days (e.g. no MRs merged → don't write "0 merge requests merged").
- If an account fails to fetch during this window (see the failure rules in §2), its block **still appears** (not silently skipped) with `Activities: fetch failed (<short reason, e.g. rate limit>)` — so it's visible that an account needs attention (expired token, etc.) instead of just vanishing from the digest without a trace.
- This is the human-readable rendering generated from the underlying per-account data structure: `{ "account_id", "platform", "username", "metrics": { "commits_created", "prs_or_mrs_opened", "prs_or_mrs_merged", "issues_opened", "issues_closed" }, "error"? }`. HTTP (`/digest/preview`, `/digest/run`'s response, `/digest/latest`) returns this text (ready to send to chat by the harness) combined with the raw data structure in a single JSON payload; the CLI (`cli.py digest ...`) prints the text version directly to stdout.

## 4. HTTP Endpoints (Planned) & CLI Access

HTTP endpoints are **read-only for digest data** — there is no endpoint for managing accounts (see §2, that's CLI-only):

| Method | Path               | Purpose |
|--------|--------------------|---------|
| GET    | `/health`          | Check that the service is alive. |
| GET    | `/digest/preview`  | Fetch + build digest (format in §3) from every account in `accounts.json`, **returned as JSON/text** — no state update, no push to the notify target. For manual checks. Takes an optional `?account=<id>` query param to check a single account, and `?hours=<N>` / `?days=<N>` to override the window (see note below the table). |
| POST   | `/digest/run`      | Fetch + build (format in §3) + update per-account state (`last_run`) + cache the result for `/digest/latest`. **No longer pushes to a notify target** — sending to Slack/OpenClaw is deferred, now the agentic harness's responsibility, reading this endpoint's response (or `/digest/latest`) and sending its own notification. This is the endpoint called by cron/the harness. |
| GET    | `/digest/latest`   | Return the last successfully generated digest (cached in memory/file) — the primary source the agentic harness reads to send notifications. |

**Equivalent CLI commands** (called directly, no `curl` needed, but only requires `app.py`/the service to be running if you want state & cache to stay in sync with HTTP — see note below):

```
$ python cli.py digest preview --hours 6
$ python cli.py digest preview --account gitlab-csrg.example.com-alfiyan --days 3
$ python cli.py digest run       # equivalent to POST /digest/run: updates state + cache
$ python cli.py digest latest    # equivalent to GET /digest/latest
```

`cli.py digest ...` calls `digest.py`/`state.py` directly (not over HTTP), so it **doesn't require the `uvicorn` service to be running** for `preview`/`run` (it reads/writes the same files on disk directly). Because of this, don't run `cli.py digest run` at the same time as `POST /digest/run` from the harness/cron — both write to the same `state.py`/cache with no file-locking (out of scope for a tool this small); use one or the other at a time.

**Auth:** the HTTP endpoints above are protected by a simple shared secret (`X-API-Key` header), checked against the `API_KEY` env var. This is different from the per-account `api_key` in §2 — `API_KEY` here is a secret belonging to the Ergasia Digest service itself, gating who's allowed to call its HTTP endpoints at all. Good enough for an internal tool — no need for OAuth. The CLI (`cli.py`) doesn't go through this gate, since its access is already restricted to shell/SSH access on the host (see §2 and §7).

**Window parameter (`/digest/preview` & `cli.py digest preview`):** besides `?account=<id>` (or `--account`), both also accept `hours=<N>` or `days=<N>` (pick one — an error if both are set at once) to override the fetch window to "the last N hours/days from now," regardless of each account's `last_run`. Useful for ad-hoc checks, e.g. "what happened in the last 3 days" without waiting on/caring about incremental state. If this parameter isn't provided, the default behavior applies: fetch since each account's `last_run`, or `DEFAULT_LOOKBACK_HOURS = 24` if the account is newly added and has never had a `last_run`. This is also the effective window for the harness's daily call (see §6) — since it's called once every 24 hours, `last_run` is always ~24 hours back, consistent with the default 24-hour window when no parameter is given. This parameter is deliberately **not supported** on `/digest/run`/`cli.py digest run` — both must stay consistent with incremental `last_run` so a custom window never creates a gap or overlap when used to update state.

**Note on notify.py:** this module already exists and has been smoke-tested, but for now it isn't called from any endpoint or command. Sending notifications is deferred to the agentic harness so it can be more flexible (e.g. LLM-based filtering/formatting before sending). Wiring `notify.py` into `/digest/run` can be revisited later if the harness ever needs a direct-send fallback from the service.

## 5. Configuration (`.env`)

Accounts (and their tokens) are managed via `cli.py` (§2), not `.env`. `.env` only needs to contain:
- `API_KEY` — shared secret protecting the HTTP endpoints (different from the per-account `api_key`, see §2/§4).
- `PORT` — defaults to `8000`.

## 6. Trigger Flow

The actual trigger in production is **not** a raw system crontab, but the agentic harness itself: the harness is scheduled (via its own scheduler) to call `POST /digest/run` once a day at **21:00 WIB (14:00 UTC)**, with no extra parameters — so it automatically uses the default 24-hour window (see §4). After getting the response, the harness decides when and how to send its notification to Slack/OpenClaw — not this service's responsibility (see the notify.py note in §4).

If OS cron is ever needed as an alternative/fallback (e.g. if the harness is down), here's an equivalent `curl` example — adjust the time to the server's timezone (`0 21 * * *` if the server's TZ is Asia/Jakarta/WIB, `0 14 * * *` if the server's TZ is UTC):

```
0 21 * * * curl -s -X POST -H "X-API-Key: $ERGASIA_KEY" http://127.0.0.1:8000/digest/run
```

The service still runs continuously (systemd unit / uvicorn) so it can be called anytime — by the scheduled harness, a manual `curl`, or `/digest/preview` for an ad-hoc check (with or without the `?hours=`/`?days=` override) without waiting for the daily schedule. Accounts are still managed via `cli.py` (§2) on the same host — not over HTTP.

## 7. Deployment

- Run via `uvicorn app:app --host 127.0.0.1 --port 8000` behind a systemd service, following the same pattern as other services on `invis`.
- No need to expose it externally — localhost is enough, with an optional Nginx reverse proxy if it needs to be reachable from outside `invis`.
- `cli.py` can/should only be run directly on the host where `accounts.json` lives (local filesystem access) — not exposed over the network. If accounts need to be managed from outside `invis`, that access should go through SSH to the host, not a new HTTP endpoint built for it.
- `accounts.json` contains raw tokens for every account — make sure its file permissions are `600`, it's in `.gitignore`, and it's included in backups (if `invis` has a backup mechanism) so account tokens aren't lost if the file gets corrupted or deleted.

## 8. Next Steps

1. Write `accounts_store.py` — account CRUD (`add`/`list`/`delete`, reads/writes `accounts.json`), including field validation per §2 (id auto-generation + uniqueness, `base_url` only for gitlab) and masking `api_key` when listing.
2. Refactor `sources/github_source.py` and `sources/gitlab_source.py` to accept `(base_url, username, token, since)` as function parameters instead of reading env vars directly — `since` is a prerequisite for multi-account support and for the `hours`/`days` window feature.
3. Refactor `digest.py`: `fetch_all_events(since_override=None)` iterates over accounts from `accounts_store.py` and tags each event with `account_id`. If `since_override` is set (from `hours`/`days`), use it for every account being fetched; otherwise use each account's `last_run` from `state.py`, or `DEFAULT_LOOKBACK_HOURS = 24` if the account has never had a `last_run`. `build_digest()` computes per-account metrics (commits created, PR/MR opened & merged, issues opened & closed) and renders each account per the §3 format, including the platform label and handling of accounts that failed to fetch (basic logic already exists, just needs cleaned-up imports and removal of the `if __name__ == "__main__"` section).
4. Update `state.py`: change the tracking key from per-source to per-`account_id`, and add a function to clean up an entry when an account is deleted.
5. Write `cli.py` — the `accounts add/list/delete` subcommand (using `getpass` for `api_key`, calling `accounts_store.py` directly) and `digest preview/run/latest` (calling `digest.py`/`state.py` directly, printing the §3 format to stdout).
6. Write `app.py` — a FastAPI app with the read-only HTTP endpoints in §4 (`/health`, `/digest/preview` with `?account=`/`?hours=`/`?days=` filters, `/digest/run`, `/digest/latest`) plus API key middleware. **No** `/accounts` endpoint over HTTP. `/digest/run` **does not** call `notify.py` — just fetch + build + update state + cache for `/digest/latest`.
7. Update `state.py` (continued) if it also needs to store a "last digest text" cache for the `/digest/latest` endpoint.
8. Update `.gitignore` (+`accounts.json`) and `requirements.txt` (+`fastapi`, +`uvicorn`).
9. Write `README.md` — how to install, what goes in `.env`, how to add/list/delete accounts via `cli.py`, how to check the digest via HTTP or CLI, running locally, systemd + cron setup.

Once this plan is approved, proceed with steps 1–9.
