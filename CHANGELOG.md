# Changelog

All notable changes to Ergasia Digest are documented in this file.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/);
this project has no version tags, so entries are grouped by date instead.

## 2026-08-08

Bugs below were found and patched by the INVIS agent **Garbis**, whose
patch (`ergasia-fix-garbis.patch`) was reviewed and implemented here —
its `docker-compose.yml` hunk was reverted as redundant (see that
section's note in the repo history), the other two were kept as-is.

### Added
- `POST /digest/run` now also accepts `hours`/`days` as JSON body fields,
  not just query params — a body-style caller (`curl -d '{"days":7}'`)
  is no longer silently ignored and defaulted to the 24h window. Body
  values take precedence over query params when both are present.

### Fixed
- GitHub push commit counts are capped at a spike threshold (500 commits
  in a single push): a rebase/force-push/fork-sync can make the Compare
  API report an implausible commit distance for one `PushEvent`, which
  isn't the user's actual work for the day. Above the threshold, the
  push is counted as 1 commit instead of the raw (inflated) distance.
  The cap applies per push event, not to the digest's daily total.

## 2026-07-25 — Optional X-API-Key

### Changed
- `API_KEY` is now optional. If unset, `require_api_key` fails **open**
  — every HTTP endpoint accepts requests with no `X-API-Key` header at
  all, instead of rejecting them. Deliberate for local-dev convenience
  on a tool that's localhost-only by default; set a real `API_KEY` for
  anything reachable beyond the host itself.

## 2026-07-25 — Initial release (Phases 1–8)

First complete implementation, built and smoke-tested against real
GitHub/GitLab accounts.

### Added
- Multi-account, multi-platform digest: GitHub and GitLab (gitlab.com
  and self-hosted instances), any number of accounts of either type.
- `accounts_store.py` — CRUD over `accounts.json` (`chmod 600`,
  git-ignored), masking the raw token everywhere except live use.
- `sources/github_source.py` / `sources/gitlab_source.py` — per-account
  Events API clients, each with `fetch_events()` + `verify_access()`.
- `digest.py` — pure `fetch_all_events()` / `build_digest()` core,
  shared by both interfaces, no logic duplicated between them.
- `cli.py` — `accounts add/list/delete` (CLI-only; `add` live-verifies
  access before persisting anything, hidden-prompt token entry via
  `getpass`) and `digest run` (`--account`/`--hours`/`--days`).
- `app.py` — FastAPI service: `GET /health`, `POST /digest/run`, both
  gated by an `X-API-Key` header checked against `API_KEY`.
- Fully stateless design: every digest call is a fresh live fetch over
  a rolling `DEFAULT_LOOKBACK_HOURS = 24` window (or an explicit
  `hours`/`days` override) — no `last_run` tracking, no cache, no
  `state.json`. `digest run`/`digest preview` were merged into one
  `digest run` command/endpoint once neither touched state.
  `/digest/latest` (cached-result endpoint) was removed for the same
  reason — no consumer needed a previous result instead of re-running.
- Self-hosted GitLab hostnames are never rendered in digest output —
  only the account's `label`, or a generic `GitLab (self-hosted)`
  fallback — since digest text leaves the host via an external harness.
- `run.py` entrypoint (loads `.env`, canonical for bare-metal/systemd
  and for process managers like Pyker that run a plain `.py` script).
- Docker support: `Dockerfile`, `docker-compose.yml` (published to
  `127.0.0.1` only), `ACCOUNTS_FILE` env var override so `accounts.json`
  can live on a mounted `/data` volume.
- `README.md` / `AGENTS.md` / `PLAN.md` documenting install, config,
  deployment (systemd, Docker, Pyker), and the design rules above.

### Fixed
- GitHub commit counting: the Events API no longer includes a `commits`
  list or count in `PushEvent` payloads — switched to the Compare API's
  `total_commits` (previously silently under-counted to 0 for every
  push, found against a real account rather than mocked payloads).
- GitLab commit counting: push events always have `target_type ==
  "Project"`, never `None` as originally assumed — the `None` check
  matched zero real push events and reported 0 commits for everyone.
