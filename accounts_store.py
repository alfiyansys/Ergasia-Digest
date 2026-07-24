"""Account storage: CRUD over accounts.json.

Field-level validation only — no network calls here. Live verification
against the real GitHub/GitLab API is cli.py's job (see PLAN.md §2).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import urlparse

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")
DEFAULT_GITLAB_BASE_URL = "https://gitlab.com"
VALID_TYPES = {"github", "gitlab"}


class AccountError(ValueError):
    """Raised for invalid account fields or a duplicate id."""


@dataclass
class Account:
    id: str
    type: str
    username: str
    api_key: str
    base_url: Optional[str] = None
    label: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def generate_id(type_: str, username: str, base_url: Optional[str] = None) -> str:
    """{type}-{host}-{username}. This id is internal-only — never rendered in digest output."""
    if type_ == "github":
        host = "github.com"
    else:
        host = urlparse(base_url or DEFAULT_GITLAB_BASE_URL).hostname or (base_url or DEFAULT_GITLAB_BASE_URL)
    return f"{type_}-{host}-{username}"


def load_accounts() -> list[dict]:
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, "r") as f:
        content = f.read().strip()
    return json.loads(content) if content else []


def save_accounts(accounts: list[dict]) -> None:
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)
        f.write("\n")
    os.chmod(ACCOUNTS_FILE, 0o600)


def add_account(
    type_: str,
    username: str,
    api_key: str,
    base_url: Optional[str] = None,
    label: Optional[str] = None,
    id_: Optional[str] = None,
) -> dict:
    if type_ not in VALID_TYPES:
        raise AccountError(f"invalid type '{type_}': must be one of {sorted(VALID_TYPES)}")
    if not username:
        raise AccountError("username is required")
    if not api_key:
        raise AccountError("api_key is required")
    if type_ == "github" and base_url:
        raise AccountError("base_url is only valid for type 'gitlab'")

    if type_ == "gitlab":
        base_url = base_url or DEFAULT_GITLAB_BASE_URL
    else:
        base_url = None

    account_id = id_ or generate_id(type_, username, base_url)

    accounts = load_accounts()
    if any(a["id"] == account_id for a in accounts):
        raise AccountError(f"account id '{account_id}' already exists")

    account = Account(
        id=account_id,
        type=type_,
        username=username,
        api_key=api_key,
        base_url=base_url,
        label=label,
    )
    accounts.append(account.to_dict())
    save_accounts(accounts)
    return account.to_dict()


def _mask_api_key(api_key: str) -> str:
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return "*" * (len(api_key) - 4) + api_key[-4:]


def list_accounts() -> list[dict]:
    """Every stored account with api_key masked. base_url/label are shown as-is —
    this output stays local (cli.py only), see PLAN.md §2."""
    result = []
    for account in load_accounts():
        entry = dict(account)
        entry["api_key"] = _mask_api_key(entry["api_key"])
        result.append(entry)
    return result
