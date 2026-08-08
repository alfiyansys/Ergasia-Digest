# Ergasia Digest

A small webservice that pulls activity from GitHub and GitLab — including
multiple accounts at once and multiple GitLab instances (gitlab.com +
self-hosted) — and merges it into a single daily digest. Account management
is CLI-only; digest data is available over HTTP or CLI. Sending the digest
to chat (Slack / OpenClaw) is **not** this service's job — that's handled by
an external agentic harness that reads the digest and sends its own
notification.

See `PLAN.md` for the full design, `AGENTS.md` for contribution rules if
you're working on this with an AI coding agent, and `CHANGELOG.md` for a
history of what's changed.

## Install

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and fill in a shared secret:

```
cp .env.example .env
```

```
API_KEY=<a long random string>
PORT=8000
HOST=127.0.0.1
```

`API_KEY` protects every HTTP endpoint via the `X-API-Key` header. It's
unrelated to the per-account tokens used below — those are entered
separately when you register an account and are never stored in `.env`.
If `API_KEY` is left blank/unset, auth is disabled entirely and every
endpoint is open with no header required — fine for local dev on
`127.0.0.1`, but set a real value for anything reachable beyond your own
machine.

## Managing accounts (CLI-only)

Accounts (and their tokens) are **not** managed over HTTP — only via `cli.py`,
run directly on the host. This keeps the one operation that handles raw
access tokens off the network entirely.

```
$ python cli.py accounts add --type github --username alice
Account API key (hidden input): ****************
Verifying account access... ok
Account added: github-github.com-alice

$ python cli.py accounts add --type gitlab --username bob \
    --base-url https://gitlab.acme.example.com --label "GitLab (work)"
Account API key (hidden input): ****************
Verifying account access... ok
Account added: gitlab-gitlab.acme.example.com-bob

$ python cli.py accounts list
ID                                  TYPE    USERNAME  BASE_URL                         LABEL
github-github.com-alice             github  alice     -                                -
gitlab-gitlab.acme.example.com-bob  gitlab  bob       https://gitlab.acme.example.com  GitLab (work)

$ python cli.py accounts delete gitlab-gitlab.acme.example.com-bob
Account 'gitlab-gitlab.acme.example.com-bob' deleted.
```

Notes:

- The API key/token is entered via a hidden prompt, never a command-line
  flag, so it doesn't end up in shell history.
- `add` performs a live check against the real GitHub/GitLab API before
  saving anything — if the token is invalid or lacks the scope needed to
  read events, the account is **not** added and the reason is printed.
- `--base-url` is only valid for `--type gitlab` (defaults to
  `https://gitlab.com` if omitted) — GitHub Enterprise isn't supported yet.
- `--label` sets the display name shown in digest output for a GitLab
  account. Set this for any self-hosted GitLab account — without it, digest
  output falls back to a generic `GitLab (self-hosted)` tag rather than ever
  printing the real hostname (digest text leaves the host via the harness,
  so the real hostname is treated as sensitive).

### Required token scopes

The per-account token needs to be able to read `GET /users/{username}/events`
(GitHub) or `GET /users/:id/events` (GitLab) — that's what `accounts add`'s
live verification actually checks, so a token that fails to add doesn't have
enough of the below.

**GitHub:**
- **Classic personal access token**: `repo` scope, if you want activity in
  private repos to show up (this is what makes private events visible on
  that endpoint at all). If you only care about public-repo activity, no
  scope is required — but most personal digests want private activity too,
  since that's usually most of the actual work.
- **Fine-grained personal access token**: read-only access to **Contents**,
  **Issues**, and **Pull requests** for whichever repos you want tracked
  (**Metadata** read is mandatory and gets included automatically). Scope
  it to "All repositories" if you don't want to keep updating it as you
  create new ones.

**GitLab** (gitlab.com or self-hosted): a personal access token with the
**`read_api`** scope (the broader `api` scope also works, since it's a
superset). `read_user` alone is *not* enough — it lets the token identify
itself but can't read events, which is exactly the failure
`accounts add` reports as `token is valid but missing 'read_api' scope`.

## Checking the digest (HTTP or CLI)

Both interfaces call the same underlying logic and produce the same output.
There's no persistent state anywhere — every call is a fresh live fetch
from GitHub/GitLab, defaulting to the last 24 hours (`DEFAULT_LOOKBACK_HOURS`
in `digest.py`), never based on when it was last called.

**CLI** (works without the HTTP service running at all — there's no shared
state to keep in sync):

```
$ python cli.py digest run                    # last 24 hours, every account
$ python cli.py digest run --hours 6          # override the window
$ python cli.py digest run --account <id> --days 3
```

**HTTP** (requires the service running, see below):

```
curl -H "X-API-Key: $API_KEY" -X POST http://127.0.0.1:8000/digest/run
curl -H "X-API-Key: $API_KEY" -X POST "http://127.0.0.1:8000/digest/run?hours=6"
```

`--hours`/`--days` (or `?hours=`/`?days=`) are mutually exclusive. Calling
`digest run` repeatedly is completely safe — nothing is written anywhere,
so there's no risk of state drift, but also no memory of previous calls:
two runs close together can show overlapping activity, and a long gap
between runs (e.g. the harness being down for a few days) means whatever
happened before the last 24h window is simply not seen. That tradeoff was
chosen deliberately in exchange for a much simpler, fully stateless design.

## Running locally

```
.venv/bin/python run.py
```

Reads `HOST`/`PORT` from `.env` (defaults to `127.0.0.1:8000` if unset).
Running `uvicorn app:app` directly instead works too, but then `--host`/
`--port` have to be passed on the command line by hand — `.env`'s
`HOST`/`PORT` are only read by `run.py`.

## Deployment

Run continuously behind a systemd service, localhost-only (add an Nginx
reverse proxy only if it needs to be reachable from outside the host):

```ini
[Unit]
Description=Ergasia Digest
After=network.target

[Service]
WorkingDirectory=/path/to/ergasia-digest
EnvironmentFile=/path/to/ergasia-digest/.env
ExecStart=/path/to/ergasia-digest/.venv/bin/python run.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`accounts.json` holds raw tokens for every registered account — make sure
its permissions stay `600` (the CLI sets this automatically) and that it's
included in whatever backup mechanism the host has.

### Docker (alternative to systemd)

```
cp .env.example .env   # fill in API_KEY
docker compose up -d --build
```

The port is published to `127.0.0.1` only (not `0.0.0.0`), so the container
is unreachable from outside the host — same posture as the bare-metal
setup above. `accounts.json` lives in `./data` on the host (bind-mounted),
so it survives container rebuilds/restarts (there's no `state.json` — see
"Checking the digest" above, this project keeps no persistent state).

Note: `HOST` in `.env` has **no effect** here — the container always binds
`0.0.0.0` internally (hardcoded in the `Dockerfile`), which is required for
Docker's port mapping to reach it at all. The loopback-only restriction
above comes from the `docker-compose.yml` port publish, not from `HOST`.

Account management is still CLI-only — with Docker, that means running
`cli.py` inside the running container instead of directly on host shell:

```
docker compose exec ergasia-digest python cli.py accounts add --type github --username alice
docker compose exec ergasia-digest python cli.py accounts list
docker compose exec ergasia-digest python cli.py digest run
```

This isn't a workaround — it's the same security model as bare-metal, just
with the gate being Docker daemon access (root/`docker` group) instead of
SSH. There's no HTTP endpoint for account management in either mode.

Docker Swarm is not recommended for this project: it's a single stateful
instance by design (one `accounts.json`), and a plain host-path bind mount
like `./data` only stays consistent if the container never gets rescheduled
to a different node — which Swarm's scheduler does by default. If you need
to run this under Swarm anyway, pin it with `deploy.replicas: 1` and a
`placement.constraints` entry for a specific node; otherwise a rescheduled
container will start with an empty `accounts.json` (the original data isn't
deleted, just inaccessible from wherever the container landed).

### Pyker (lightweight alternative to systemd/Docker)

[Pyker](https://github.com/mrvi0/pyker) is a small, no-root, single-file
Python process manager — `start`/`stop`/`restart`/`list`/`logs`, nothing
container-related. Good fit if systemd/Docker feel like too much for a
personal setup.

```
curl -sSL https://raw.githubusercontent.com/mrvi0/pyker/main/install.sh | bash

cp .env.example .env   # fill in API_KEY, set PORT if you don't want the default
pyker start ergasia-digest run.py --venv ./.venv
pyker list
pyker logs ergasia-digest -f
pyker restart ergasia-digest
pyker stop ergasia-digest
```

Run via `run.py`, not `uvicorn app:app` directly — Pyker's CLI only knows
how to launch a plain `.py` script, and `run.py` loads `.env` itself since
Pyker has no env-file mechanism of its own (unlike systemd's
`EnvironmentFile=` or docker-compose's `env_file:`).

**Caveat, found by reading Pyker's actual source rather than trusting its
docs:** the `--auto-restart` flag exists in its CLI and gets recorded, but
nothing in the current codebase actually watches and restarts a crashed
process — there's no background monitor loop. Don't rely on Pyker alone
for crash recovery; use systemd (`Restart=on-failure`) or Docker
(`restart: unless-stopped`) if that matters to you.

Account management stays CLI-only exactly like bare-metal — Pyker doesn't
containerize anything, so `cli.py` just runs directly on the same host.

## Triggering the daily digest

In production, an agentic harness — not a raw system crontab — is
scheduled to call `POST /digest/run` once a day (21:00 WIB / 14:00 UTC by
default), then decides on its own when and how to send the result to chat.

If you need an OS cron fallback instead (e.g. the harness is down), adjust
the hour to your server's timezone:

```
0 21 * * * curl -s -X POST -H "X-API-Key: $API_KEY" http://127.0.0.1:8000/digest/run
```

---

Built with help from [Claude Code](https://claude.com/claude-code).
