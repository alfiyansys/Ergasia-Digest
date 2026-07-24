# Ergasia Digest

A small webservice that pulls activity from GitHub and GitLab — including
multiple accounts at once and multiple GitLab instances (gitlab.com +
self-hosted) — and merges it into a single daily digest. Account management
is CLI-only; digest data is available over HTTP or CLI. Sending the digest
to chat (Slack / OpenClaw) is **not** this service's job — that's handled by
an external agentic harness that reads the digest and sends its own
notification.

See `PLAN.md` for the full design and `AGENTS.md` for contribution rules if
you're working on this with an AI coding agent.

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
```

`API_KEY` protects every HTTP endpoint via the `X-API-Key` header. It's
unrelated to the per-account tokens used below — those are entered
separately when you register an account and are never stored in `.env`.

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
ID                                  TYPE    USERNAME  BASE_URL                         LABEL          LAST_RUN
github-github.com-alice             github  alice     -                                -              2026-07-24T21:00:00+00:00
gitlab-gitlab.acme.example.com-bob  gitlab  bob       https://gitlab.acme.example.com  GitLab (work)  -

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

## Checking the digest (HTTP or CLI)

Both interfaces call the same underlying logic and produce the same output.

**CLI** (works without the HTTP service running — reads/writes state files
directly):

```
$ python cli.py digest preview                       # since each account's last run, or last 24h if new
$ python cli.py digest preview --hours 6              # ad-hoc window override, no state touched
$ python cli.py digest preview --account <id> --days 3
$ python cli.py digest run                            # updates state + cache, same as POST /digest/run
$ python cli.py digest latest                         # last cached digest
```

**HTTP** (requires the service running, see below):

```
curl -H "X-API-Key: $API_KEY" http://127.0.0.1:8000/digest/preview
curl -H "X-API-Key: $API_KEY" -X POST http://127.0.0.1:8000/digest/run
curl -H "X-API-Key: $API_KEY" http://127.0.0.1:8000/digest/latest
```

Don't run `cli.py digest run` and `POST /digest/run` at the same time —
both write to the same state/cache files with no locking.

## Running locally

```
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

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
ExecStart=/path/to/ergasia-digest/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`accounts.json` holds raw tokens for every registered account — make sure
its permissions stay `600` (the CLI sets this automatically) and that it's
included in whatever backup mechanism the host has.

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
