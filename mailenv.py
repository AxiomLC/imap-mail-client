r"""Credential + account resolution for the IMAP mail client.

Search order for the env file (first hit wins):
  1. $MAIL_ENV_FILE                (explicit override)
  2. <project>/.env                (local, gitignored)
  3. %USERPROFILE%/.mailclient/accounts.env

Accounts are declared with a prefix so several mailboxes can coexist:

    MC__DEV_HOST=imap.hostinger.com
    MC__DEV_PORT=993
    MC__DEV_USER=dev@w57th.agency
    MC__DEV_PASSWORD=...
    MC__DEV_SENT=INBOX.Sent

Every key after the MC__ prefix and before the final _FIELD segment is the
account name, lowercased. MC__OFFICE_SALES_USER -> account "office_sales".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PREFIX = "MC__"
FIELDS = (
    "host", "port", "user", "password", "tls",
    "sent", "junk", "bounces", "drafts",
    "smtp_host", "smtp_port", "smtp_tls",
)

DEFAULTS = {
    "port": "993",
    "sent": "INBOX.Sent",
    "junk": "INBOX.Junk",
    "bounces": "INBOX.Bounces",
    "drafts": "INBOX.Drafts",
    "tls": "implicit",
}


@dataclass
class Account:
    name: str
    host: str
    user: str
    password: str
    port: int = 993
    tls: str = "implicit"
    sent: str = "INBOX.Sent"
    junk: str = "INBOX.Junk"
    bounces: str = "INBOX.Bounces"
    drafts: str = "INBOX.Drafts"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_tls: bool = True

    @property
    def redacted(self) -> str:
        return f"{self.name} <{self.user}> @ {self.host}:{self.port}"


def find_env_file():
    explicit = os.environ.get("MAIL_ENV_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p

    # Project-local .env wins: the folder stays self-contained and nothing
    # outside it can silently shadow the credentials sitting next to the code.
    local = Path(__file__).resolve().parent / ".env"
    if local.is_file():
        return local

    home = Path.home() / ".mailclient" / "accounts.env"
    return home if home.is_file() else None


def parse_env_file(path) -> None:
    """Load KEY=VALUE lines into os.environ without clobbering real env vars."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)


def _account_from_env(name, keys):
    missing = [f for f in ("host", "user", "password") if not keys.get(f)]
    if missing:
        raise ValueError(
            f"account '{name}' is missing {', '.join(missing)} "
            f"(expected {PREFIX}{name.upper()}_{missing[0].upper()})"
        )
    try:
        port = int(keys.get("port", DEFAULTS["port"]))
    except ValueError:
        raise ValueError(f"account '{name}' has non-numeric port: {keys.get('port')!r}")

    try:
        smtp_port = int(keys.get("smtp_port", "465"))
    except ValueError:
        raise ValueError(
            f"account '{name}' has non-numeric smtp_port: {keys.get('smtp_port')!r}"
        )

    smtp_tls = keys.get("smtp_tls", "true").strip().lower() not in ("false", "0", "no")

    return Account(
        name=name,
        host=keys["host"],
        user=keys["user"],
        password=keys["password"],
        port=port,
        tls=keys.get("tls", DEFAULTS["tls"]).lower(),
        sent=keys.get("sent", DEFAULTS["sent"]),
        junk=keys.get("junk", DEFAULTS["junk"]),
        bounces=keys.get("bounces", DEFAULTS["bounces"]),
        drafts=keys.get("drafts", DEFAULTS["drafts"]),
        smtp_host=keys.get("smtp_host", ""),
        smtp_port=smtp_port,
        smtp_tls=smtp_tls,
    )


def load_accounts():
    env_path = find_env_file()
    if env_path:
        parse_env_file(env_path)

    found = {}
    # Field suffixes are matched longest-first so MC__DEV_SMTP_PORT resolves to
    # account 'dev' + field 'smtp_port' rather than account 'dev_smtp' + 'port'.
    # A plain rpartition cannot distinguish the two, and a greedy regex picks
    # the wrong one.
    suffixes = sorted(FIELDS, key=len, reverse=True)
    for key, val in os.environ.items():
        if not key.upper().startswith(PREFIX):
            continue
        body = key[len(PREFIX):]
        for fld in suffixes:
            tail = "_" + fld
            if body.lower().endswith(tail):
                acct = body[: -len(tail)]
                if acct:
                    found.setdefault(acct.lower(), {})[fld] = val
                break

    if not found:
        where = env_path or "nowhere (no env file found)"
        raise ValueError(
            "no accounts configured. Looked for MC__<NAME>_HOST / _USER / _PASSWORD "
            "in " + str(where) + ".\nSee .env.example for the format."
        )

    return {name: _account_from_env(name, keys) for name, keys in found.items()}


def get_account(name):
    accounts = load_accounts()
    if name:
        key = name.lower()
        if key not in accounts:
            raise ValueError(
                "unknown account '%s'. Available: %s"
                % (name, ", ".join(sorted(accounts)))
            )
        return accounts[key]
    if len(accounts) == 1:
        return next(iter(accounts.values()))
    raise ValueError(
        "multiple accounts configured (%s) - pass --account NAME or set MAIL_DEFAULT_ACCOUNT"
        % ", ".join(sorted(accounts))
    )
