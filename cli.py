"""CLI entrypoint: account management (add/list/delete) + digest stats access.

Account management is CLI-only by design — see PLAN.md §2 / AGENTS.md.
Digest commands call digest.py/state.py directly (no HTTP round-trip), so
they don't require app.py's uvicorn service to be running.
"""

from __future__ import annotations

import argparse
import sys
from getpass import getpass

import accounts_store
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
