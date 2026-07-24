# PLAN.md — Ergasia Digest

A small webservice that pulls activity from GitHub and GitLab — including from multiple accounts at once and multiple GitLab instances (gitlab.com + self-hosted, e.g. an internal company GitLab) — and merges it into a single summary. Account registration (add/list/delete) is **CLI-only**, run directly on the host where the service runs. Digest stats can be accessed via HTTP **or** CLI. Sending to chat (Slack / OpenClaw) is **not** this service's responsibility — that part is deferred to an agentic harness that reads the digest output and sends its own notifications (see §4 and §6).

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

**Current status (as of Phase 1):** `.gitignore`, `.env.example`, `accounts.example.json`, and `requirements.txt` are done. Everything else — `accounts_store.py`, `state.py`, `sources/github_source.py`, `sources/gitlab_source.py`, `digest.py`, `cli.py`, `app.py`, `README.md` — is being written from scratch; none of it pre-exists anywhere. (An earlier draft of this plan assumed `sources/`, `state.py`, and `notify.py` already existed from prior work — that turned out to be wrong, no such files were found on disk, so §8 below is written as greenfield implementation, not a refactor of existing code. `notify.py` is deferred per §4 and isn't being written in any phase below; it can be added later if the harness ever needs a direct-send fallback.)

## 2. Account Management (CLI-only) & Multi-GitLab-Endpoint

Accounts are **not edited manually via a config file, and not over HTTP at all** — only through the `accounts` subcommand in `cli.py` (add/list/delete), run directly on the host. `accounts_store.py` reads/writes `accounts.json` on disk as the runtime source of truth; both `cli.py` and `digest.py` import this module.

**Why CLI-only, not HTTP:** adding an account means accepting a raw personal access token. An HTTP endpoint for that — even gated by `X-API-Key` — still adds attack surface for the most sensitive operation in this service (it could be used to inject/enumerate tokens if `API_KEY` ever leaks). CLI-only means only someone with direct shell/SSH access to the host can manage accounts — the gate becomes OS access, not the network. `cli.py accounts add` also asks for `api_key` via a hidden prompt (`getpass`, not a command-line flag) so the token doesn't end up in shell history or show up in `ps aux`.

**Verification on add:** before an account is persisted to `accounts.json`, `cli.py accounts add` makes a live call to that account's platform (GitHub or GitLab Events API, at `base_url` for GitLab) using the exact credentials just entered, to confirm the account is actually usable for digesting — not just that the fields are well-formed. Concretely, it checks:
- The token authenticates successfully (not invalid/expired/revoked).
- The token can read that `username`'s events (right scope/permissions — e.g. a GitLab token needs at least `read_api`; a GitHub token needs read access to the events it needs).
- The response is a well-formed events payload `digest.py` can actually parse for the §3 metrics (commits, PR/MR, issues) — **not** that the account has non-zero recent activity. A brand-new or quiet account with zero events in range is a valid, successful response and must pass; the check is about API/permission access, not activity volume.

If verification fails, the account is **not** written to `accounts.json` — `accounts add` exits non-zero with the specific reason (auth failed / insufficient scope / username not found / network error reaching `base_url`), so a broken account never sits silently in the store until a digest run fails on it days later. This reuses the same fetch path `sources/github_source.py`/`sources/gitlab_source.py` already implement for `digest.py` (a `verify_access(...)` function alongside `fetch_events(...)`), rather than a separate check that could drift from what the digest actually needs. There is deliberately no `--skip-verify` flag — an account that can't be verified shouldn't be added, full stop.

**Schema for each account** (`accounts.example.json` documents this shape, without real token values):

```json
{
  "id": "gitlab-gitlab.acme.example.com-bob",
  "type": "gitlab",
  "username": "bob",
  "base_url": "https://gitlab.acme.example.com",
  "label": "GitLab (work)",
  "api_key": "glpat-xxxxxxxxxxxxxxxx"
}
```

Fields:
- `id` — unique, used as the `last_run` tracking key in `state.py` and for internal lookups (`?account=<id>` / `--account`). Optional on `accounts add`; if omitted, it's auto-generated from `{type}-{host}-{username}` (`host` = `github.com` for GitHub, or the hostname from `base_url` for GitLab). This `id` is **internal only** — it's used in `cli.py`/local API calls, never printed as the platform tag in digest output (that's `label`, below), so it embedding the real self-hosted hostname is not itself an external leak.
- `type` — `github` or `gitlab`.
- `username` — username on the relevant platform.
- `api_key` — personal access token for accessing the GitHub/GitLab Events API on behalf of this account (entered via hidden prompt, see above). **This is different from the service's `API_KEY`** (see §4 Auth) — the term "API key" here refers to the tracked account's own token, not the shared secret that protects Ergasia Digest's HTTP endpoints.
- `base_url` — **only applies to `type: gitlab`**, optional, defaults to `https://gitlab.com` if left blank (supports a custom/self-hosted GitLab endpoint, e.g. an internal company instance). For `type: github`, this field must be left blank/is rejected — GitHub Enterprise custom endpoints aren't supported yet, could be a future step if needed.
- `label` — optional, free-text display name used as the platform tag in digest output instead of anything derived from `base_url` (see §3). **Recommended when adding a self-hosted GitLab account** — pick something that doesn't reveal internal infra naming (e.g. `GitLab (work)` rather than the real hostname). If omitted for a self-hosted account, digest output falls back to a generic `GitLab (self-hosted)` tag (see §3) rather than ever printing the real hostname.

**CLI commands:**

```
$ python cli.py accounts add --type github --username alice
Account API key (hidden input): ****************
Verifying account access... ok
Account added: github-github.com-alice

$ python cli.py accounts add --type gitlab --username bob --base-url https://gitlab.acme.example.com --label "GitLab (work)"
Account API key (hidden input): ****************
Verifying account access... ok
Account added: gitlab-gitlab.acme.example.com-bob

$ python cli.py accounts add --type gitlab --username carol --base-url https://gitlab.acme.example.com --label "GitLab (work)"
Account API key (hidden input): ****************
Verifying account access... failed: token is valid but missing 'read_api' scope (needed to read events)
Account not added.

$ python cli.py accounts list
ID                                   TYPE    USERNAME  BASE_URL                   LABEL           LAST_RUN
github-github.com-alice              github  alice     github.com                 -               2026-07-24T21:00:00+07:00
gitlab-gitlab.acme.example.com-bob   gitlab  bob       gitlab.acme.example.com    GitLab (work)   -

$ python cli.py accounts delete gitlab-gitlab.acme.example.com-bob
Account 'gitlab-gitlab.acme.example.com-bob' deleted.
```

`accounts list` still shows the real `base_url` — this command only runs locally on the trusted host (§2/§7), so there's no external-exposure concern there. The masking below only applies to what gets rendered into digest output, since that text is what leaves the host (sent to chat by the harness).

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
Username: alice
Activities: 8 commits created, 2 pull requests opened, 1 pull request merged, 3 issues closed

Platform: GitLab (work)
Username: bob
Activities: 5 commits created, 1 merge request opened
```

Rules:
- **Platform label** — `GitHub` for `type: github` (no host ever needed, only github.com is supported); `GitLab` if `base_url` is the default (gitlab.com — a public, well-known host, not sensitive); the account's `label` verbatim (§2) if one is set — this is the normal way to distinguish self-hosted GitLab accounts, since `label` is a value the user chooses, not derived from the real host; falls back to a generic `GitLab (self-hosted)` if `base_url` is custom/self-hosted **and no `label` was set**.
  **The real hostname from `base_url` must never be rendered here.** Digest text leaves the host and gets sent to chat by an external harness (§6), so printing internal infra naming there is a real exposure — unlike `cli.py accounts list` (§2), which stays local and can show it.
  If two or more self-hosted GitLab accounts are both left without a `label`, they'll render with the identical generic `GitLab (self-hosted)` tag and only be distinguishable by `Username` — set distinct `label`s when adding accounts if that ambiguity matters.
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
- **Caution for the harness:** `account_id` in that JSON payload can still be derived from the real self-hosted hostname (§2, e.g. `gitlab-gitlab.acme.example.com-bob`), since it's only ever used internally (state tracking, `?account=` filtering) and was never in scope for the masking above. The raw JSON is HTTP/CLI output gated by `API_KEY`/host access (§4) — the harness should only forward the rendered `platform`/text fields to chat, never paste the raw payload (including `account_id`) into an external channel.

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
$ python cli.py digest preview --account gitlab-gitlab.acme.example.com-bob --days 3
$ python cli.py digest run       # equivalent to POST /digest/run: updates state + cache
$ python cli.py digest latest    # equivalent to GET /digest/latest
```

`cli.py digest ...` calls `digest.py`/`state.py` directly (not over HTTP), so it **doesn't require the `uvicorn` service to be running** for `preview`/`run` (it reads/writes the same files on disk directly). Because of this, don't run `cli.py digest run` at the same time as `POST /digest/run` from the harness/cron — both write to the same `state.py`/cache with no file-locking (out of scope for a tool this small); use one or the other at a time.

**Auth:** the HTTP endpoints above are protected by a simple shared secret (`X-API-Key` header), checked against the `API_KEY` env var. This is different from the per-account `api_key` in §2 — `API_KEY` here is a secret belonging to the Ergasia Digest service itself, gating who's allowed to call its HTTP endpoints at all. Good enough for an internal tool — no need for OAuth. The CLI (`cli.py`) doesn't go through this gate, since its access is already restricted to shell/SSH access on the host (see §2 and §7).

**Window parameter (`/digest/preview` & `cli.py digest preview`):** besides `?account=<id>` (or `--account`), both also accept `hours=<N>` or `days=<N>` (pick one — an error if both are set at once) to override the fetch window to "the last N hours/days from now," regardless of each account's `last_run`. Useful for ad-hoc checks, e.g. "what happened in the last 3 days" without waiting on/caring about incremental state. If this parameter isn't provided, the default behavior applies: fetch since each account's `last_run`, or `DEFAULT_LOOKBACK_HOURS = 24` if the account is newly added and has never had a `last_run`. This is also the effective window for the harness's daily call (see §6) — since it's called once every 24 hours, `last_run` is always ~24 hours back, consistent with the default 24-hour window when no parameter is given. This parameter is deliberately **not supported** on `/digest/run`/`cli.py digest run` — both must stay consistent with incremental `last_run` so a custom window never creates a gap or overlap when used to update state.

**Note on notify.py:** this module is **not** part of any phase in §8 — sending notifications is deferred to the agentic harness so it can be more flexible (e.g. LLM-based filtering/formatting before sending), so there's nothing to build here for now. If the harness ever needs a direct-send fallback from the service, add `notify.py` and wire it into `/digest/run` as its own future phase, revisited explicitly rather than assumed.

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

**Bare-metal (default):**
- Run via `uvicorn app:app --host 127.0.0.1 --port 8000` behind a systemd service, following the same pattern as other services on the production host.
- No need to expose it externally — localhost is enough, with an optional Nginx reverse proxy if it needs to be reachable from outside the host.
- `cli.py` can/should only be run directly on the host where `accounts.json` lives (local filesystem access) — not exposed over the network. If accounts need to be managed remotely, that access should go through SSH to the host, not a new HTTP endpoint built for it.
- `accounts.json` contains raw tokens for every account — make sure its file permissions are `600`, it's in `.gitignore`, and it's included in whatever backup mechanism the host has, so account tokens aren't lost if the file gets corrupted or deleted.

**Docker (optional, see Phase 7 in §8):** the container binds `0.0.0.0:8000` internally (required for Docker's port mapping to reach it — `127.0.0.1` inside the container isn't reachable from the host), but the port is only published to the host's loopback (`127.0.0.1:8000:8000`), preserving the same "not exposed externally" posture as bare-metal. `accounts.json`/`state.json` move to a mounted `/data` volume (via `ACCOUNTS_FILE`/`STATE_FILE` env vars) rather than living next to the source inside the image, so they persist across container rebuilds. The CLI-only account-management model still holds — `cli.py accounts add` runs via `docker exec` into the running container instead of directly on host shell, which requires Docker daemon access (root/`docker` group), an equivalent-or-stronger gate than plain SSH.

## 8. Next Steps

Work is organized into phases. Each phase gets its own `feature/phase-<n>-<slug>` branch, cut from an up-to-date `dev` (never from `main`) — not a branch per individual step. All steps inside a phase are done, checked off, and smoke-tested together on that branch before it's merged back into `dev`; only after that does `dev` ever get promoted to `main` (see `AGENTS.md` Git Workflow). Within a phase, commits still stay granular per lettered sub-step — a phase branch holds several commits, not one.

### Phase 1 — Foundation & Safety Net (`feature/phase-1-foundation`)

- [x] **1.1. Project scaffolding & safety net** — do this *before* anything can create a real `accounts.json` or needs `fastapi`/`uvicorn` installed:
  - [x] a. `.gitignore` (exclude `accounts.json`, `.env`, `__pycache__/`, `.venv/`, etc.)
  - [x] b. `.env.example` (`API_KEY=`, `PORT=8000`)
  - [x] c. `accounts.example.json` documenting the §2 schema (one github + one gitlab example entry, fake tokens)
  - [x] d. `requirements.txt` (+`fastapi`, +`uvicorn`)

  > This exists specifically so `.gitignore` is committed before step 2.1 can ever write a real `accounts.json` into the working tree, and so `fastapi`/`uvicorn` are already available by the time `app.py` (step 5.2) needs them.

### Phase 2 — Storage Layer (`feature/phase-2-storage-layer`)

- [x] **2.1. `accounts_store.py`** — account CRUD:
  - [x] a. Schema (dataclass/typed dict, including optional `label`) + `load_accounts()`/`save_accounts()` (create an empty store if missing)
  - [x] b. `generate_id(type, username, base_url)` → `{type}-{host}-{username}` (internal `id` only — never rendered in digest output, see §3)
  - [x] c. `add_account(...)` with field-level validation only (type in {github, gitlab}; `base_url` only for gitlab; `label` accepted for either type; reject duplicate id) — no network calls here; live verification against the actual platform is `cli.py`'s job (Phase 5), calling `sources.*.verify_access(...)` before it ever calls this
  - [x] d. `list_accounts()` with `api_key` masked (last 4 chars) — `base_url`/`label` shown as-is, this output stays local (§2)
  - [x] e. `delete_account(id)`

- [x] **2.2. `state.py`** — write per-account tracking and the digest cache together in the same step, since both are needed before steps 4.1–5.2 build anything that reads "latest":
  - [x] a. Change the tracking key from per-source to per-`account_id`: `get_last_run(account_id)` / `set_last_run(account_id, ts)`
  - [x] b. `delete_account_state(account_id)` — called from `accounts_store.delete_account` / `cli.py accounts delete`
  - [x] c. `save_latest_digest(text, data)` / `get_latest_digest()` — the backing store for `/digest/latest` and `cli.py digest latest`

### Phase 3 — Source Clients (`feature/phase-3-source-clients`)

- [x] **3.1. Write `sources/github_source.py` and `sources/gitlab_source.py`**:
  - [x] a. Accept `(username, token, since)` / `(base_url, username, token, since)` as parameters — no direct env var reads
  - [x] b. Normalize both clients' output into one shared event shape, so `digest.py` doesn't need platform-specific branching to compute metrics
  - [x] c. Add `verify_access(username, token)` / `verify_access(base_url, username, token)` — a live, minimal call to confirm auth succeeds and the token can read that account's events (used by `cli.py accounts add`, see Phase 5); returns ok/reason, doesn't require any events to actually exist

### Phase 4 — Digest Core (`feature/phase-4-digest-core`)

- [x] **4.1. Write `digest.py`**:
  - [x] a. `fetch_all_events(since_override=None)` — resolve per-account `since` (override > `last_run` > `DEFAULT_LOOKBACK_HOURS = 24`), call the matching source client, catch per-account fetch failures without stopping the loop
  - [x] b. Compute per-account metrics (commits created; PR/MR opened & merged; issues opened & closed) from the normalized events
  - [x] c. `platform_label(account)` → `GitHub` / `GitLab` / account's `label` if set / generic `GitLab (self-hosted)` fallback — **never** the real `base_url` hostname (see §3)
  - [x] d. `build_digest(...)` — render the §3 text format + structured data, including the "fetch failed" block variant

### Phase 5 — Interfaces (`feature/phase-5-interfaces`)

- [x] **5.1. `cli.py`**:
  - [x] a. `accounts add` (`--label` flag for the digest-safe display name; `getpass` prompt for `api_key`; calls `sources.*.verify_access(...)` first and only calls `accounts_store.add_account` if it passes — print the failure reason and exit non-zero without writing anything if it doesn't)
  - [x] b. `accounts list` / `accounts delete` (join `last_run` from `state.py` for `list`; call `state.py` cleanup on `delete`)
  - [x] c. `digest preview` (`--account`/`--hours`/`--days`, mutually-exclusive validation)
  - [x] d. `digest run` / `digest latest`

- [x] **5.2. `app.py`**:
  - [x] a. FastAPI skeleton + `X-API-Key` auth dependency (`secrets.compare_digest`)
  - [x] b. `GET /health`
  - [x] c. `GET /digest/preview` (query params + mutual-exclusivity validation → `400`)
  - [x] d. `POST /digest/run` (no `notify.py` call)
  - [x] e. `GET /digest/latest`

### Phase 6 — Documentation (`feature/phase-6-docs`)

- [x] **6.1. `README.md`** — install, `.env` setup, `cli.py` usage (accounts + digest examples), running locally, systemd unit + cron/harness note.

### Phase 7 — Containerization (`feature/phase-7-docker`)

Added after the original 6-phase plan was completed, per a follow-up request to make the service runnable via Docker.

- [x] **7.1. Env-var overrides for data file paths**:
  - [x] a. `accounts_store.py`: `ACCOUNTS_FILE = os.environ.get("ACCOUNTS_FILE", <default next-to-source path>)` — same default as before if unset, so bare-metal/CLI usage is unaffected
  - [x] b. `state.py`: same pattern for `STATE_FILE`
  - [x] c. Rationale: bind-mounting *individual files* that don't yet exist is a known Docker footgun (Docker can silently create a directory instead of a file); mounting a whole `/data` directory and pointing both files at it via env var avoids that entirely, without changing bare-metal behavior at all
- [x] **7.2. `Dockerfile` + `.dockerignore`**:
  - [x] a. `Dockerfile`: `python:3.12-slim` base, install `requirements.txt`, copy source, `EXPOSE 8000`, `CMD uvicorn app:app --host 0.0.0.0 --port 8000` (must be `0.0.0.0` inside the container — see §7 note on why this doesn't weaken the "not exposed externally" posture)
  - [x] b. `.dockerignore`: exclude `.venv/`, `__pycache__/`, `.git/`, `accounts.json`, `state.json`, `.env` — none of those belong baked into an image
- [x] **7.3. `docker-compose.yml`**:
  - [x] a. `ports: ["127.0.0.1:8000:8000"]` — published to host loopback only, not `0.0.0.0`, so the container isn't reachable from outside the host despite binding `0.0.0.0` internally
  - [x] b. `env_file: .env`, plus `ACCOUNTS_FILE=/data/accounts.json` / `STATE_FILE=/data/state.json`
  - [x] c. `volumes: ["./data:/data"]` — one directory mount, not per-file, so Docker creates it cleanly if missing
- [ ] **7.4. Update `README.md`** with a Docker section: `docker compose up -d`, and `docker compose exec ergasia-digest python cli.py accounts add ...` as the Docker-mode equivalent of running `cli.py` directly on host shell

Once this plan is approved, proceed phase by phase in order, checking off each box as it's completed and merging each phase branch into `dev` before starting the next.
