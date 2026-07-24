"""CLI entrypoint: account management (add/list/delete) + digest stats access.

Account management is CLI-only by design — see PLAN.md §2 / AGENTS.md.
Digest commands call digest.py directly (no HTTP round-trip), so they
don't require app.py's uvicorn service to be running. There is no
persistent state anywhere in this project — every call is a fresh live
fetch from GitHub/GitLab (see AGENTS.md for why).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from getpass import getpass

import accounts_store
import digest
from sources import github_source, gitlab_source


def cmd_accounts_add(args: argparse.Namespace) -> int:
    if args.type not in accounts_store.VALID_TYPES:
        print(f"Error: invalid type '{args.type}': must be one of {sorted(accounts_store.VALID_TYPES)}", file=sys.stderr)
        return 1
    if args.type == "github" and args.base_url:
        print("Error: base_url is only valid for type 'gitlab'", file=sys.stderr)
        return 1

    base_url = None
    if args.type == "gitlab":
        base_url = args.base_url or accounts_store.DEFAULT_GITLAB_BASE_URL

    api_key = getpass("Account API key (hidden input): ")

    print("Verifying account access...", end=" ")
    if args.type == "github":
        ok, reason = github_source.verify_access(args.username, api_key)
    else:
        ok, reason = gitlab_source.verify_access(base_url, args.username, api_key)

    if not ok:
        print(f"failed: {reason}")
        print("Account not added.")
        return 1
    print("ok")

    try:
        account = accounts_store.add_account(
            type_=args.type,
            username=args.username,
            api_key=api_key,
            base_url=base_url,
            label=args.label,
            id_=args.id,
        )
    except accounts_store.AccountError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Account added: {account['id']}")
    return 0


def cmd_accounts_list(args: argparse.Namespace) -> int:
    accounts = accounts_store.list_accounts()
    if not accounts:
        print("No accounts registered.")
        return 0

    columns = ["id", "type", "username", "base_url", "label"]
    headers = ["ID", "TYPE", "USERNAME", "BASE_URL", "LABEL"]

    rows = []
    for account in accounts:
        rows.append({
            "id": account["id"],
            "type": account["type"],
            "username": account["username"],
            "base_url": account.get("base_url") or "-",
            "label": account.get("label") or "-",
        })

    widths = {c: max(len(h), *(len(r[c]) for r in rows)) for c, h in zip(columns, headers)}
    print("  ".join(h.ljust(widths[c]) for c, h in zip(columns, headers)))
    for row in rows:
        print("  ".join(row[c].ljust(widths[c]) for c in columns))
    return 0


def cmd_accounts_delete(args: argparse.Namespace) -> int:
    if not accounts_store.delete_account(args.id):
        print(f"Error: account '{args.id}' not found", file=sys.stderr)
        return 1
    print(f"Account '{args.id}' deleted.")
    return 0


def _resolve_accounts(account_id: str | None) -> list[dict] | None:
    """Returns None (error already printed) if account_id was given but not found."""
    if account_id is None:
        return accounts_store.load_accounts()
    accounts = [a for a in accounts_store.load_accounts() if a["id"] == account_id]
    if not accounts:
        print(f"Error: account '{account_id}' not found", file=sys.stderr)
        return None
    return accounts


def cmd_digest_run(args: argparse.Namespace) -> int:
    """Fetch + build the digest, always live — equivalent to POST /digest/run.
    Defaults to the last DEFAULT_LOOKBACK_HOURS; --hours/--days override that
    window, --account filters to one account. Nothing is persisted anywhere."""
    if args.hours is not None and args.days is not None:
        print("Error: --hours and --days are mutually exclusive", file=sys.stderr)
        return 1

    since_override = None
    if args.hours is not None:
        since_override = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    elif args.days is not None:
        since_override = datetime.now(timezone.utc) - timedelta(days=args.days)

    accounts = _resolve_accounts(args.account)
    if accounts is None:
        return 1

    results = digest.fetch_all_events(since_override=since_override, accounts=accounts)
    rendered = digest.build_digest(results)
    print(rendered["text"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py", description="Ergasia Digest CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    accounts_parser = subparsers.add_parser("accounts", help="Manage tracked accounts (CLI-only, see PLAN.md §2)")
    accounts_sub = accounts_parser.add_subparsers(dest="accounts_command", required=True)

    add_parser = accounts_sub.add_parser("add", help="Add a new account (verifies access before saving)")
    add_parser.add_argument("--type", required=True, choices=sorted(accounts_store.VALID_TYPES))
    add_parser.add_argument("--username", required=True)
    add_parser.add_argument("--base-url", dest="base_url", default=None, help="gitlab only; defaults to gitlab.com")
    add_parser.add_argument("--label", default=None, help="display name for digest output (see PLAN.md §2/§3)")
    add_parser.add_argument("--id", dest="id", default=None, help="override the auto-generated id")
    add_parser.set_defaults(func=cmd_accounts_add)

    list_parser = accounts_sub.add_parser("list", help="List tracked accounts")
    list_parser.set_defaults(func=cmd_accounts_list)

    delete_parser = accounts_sub.add_parser("delete", help="Delete a tracked account")
    delete_parser.add_argument("id")
    delete_parser.set_defaults(func=cmd_accounts_delete)

    digest_parser = subparsers.add_parser("digest", help="Digest operations")
    digest_sub = digest_parser.add_subparsers(dest="digest_command", required=True)

    run_parser = digest_sub.add_parser("run", help="Fetch + build the digest (always live, nothing persisted)")
    run_parser.add_argument("--account", default=None)
    run_parser.add_argument("--hours", type=int, default=None)
    run_parser.add_argument("--days", type=int, default=None)
    run_parser.set_defaults(func=cmd_digest_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
