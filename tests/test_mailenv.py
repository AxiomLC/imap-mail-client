"""Tests for credential and account resolution (mailenv)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mailenv


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Isolate every test from the real environment and real .env files."""
    for key in list(os.environ):
        if key.startswith("MC__") or key in ("MAIL_ENV_FILE", "MAIL_DEFAULT_ACCOUNT"):
            monkeypatch.delenv(key, raising=False)
    # Point the loader at a scratch dir so no project .env leaks in.
    monkeypatch.setattr(mailenv, "Path", Path)
    monkeypatch.setenv("MAIL_ENV_FILE", str(tmp_path / "missing.env"))
    yield


def write_env(tmp_path, body):
    p = tmp_path / "accounts.env"
    p.write_text(body, encoding="utf-8")
    return p


def test_single_account_parsed(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "MC__DEV_HOST=imap.example.com\n"
        "MC__DEV_USER=me@example.com\n"
        "MC__DEV_PASSWORD=secret\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    accts = mailenv.load_accounts()
    assert list(accts) == ["dev"]
    a = accts["dev"]
    assert a.host == "imap.example.com"
    assert a.user == "me@example.com"
    assert a.port == 993
    assert a.sent == "INBOX.Sent"


def test_multiword_account_name_lowercased(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "MC__OFFICE_SALES_HOST=imap.example.com\n"
        "MC__OFFICE_SALES_USER=a@b.com\n"
        "MC__OFFICE_SALES_PASSWORD=x\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    assert list(mailenv.load_accounts()) == ["office_sales"]


def test_multiple_accounts(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "MC__A_HOST=h\nMC__A_USER=u\nMC__A_PASSWORD=p\n"
        "MC__B_HOST=h2\nMC__B_USER=u2\nMC__B_PASSWORD=p2\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    assert sorted(mailenv.load_accounts()) == ["a", "b"]


def test_optional_fields_override_defaults(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "MC__DEV_HOST=h\nMC__DEV_USER=u\nMC__DEV_PASSWORD=p\n"
        "MC__DEV_JUNK=INBOX.Spam\n"
        "MC__DEV_PORT=143\n"
        "MC__DEV_TLS=starttls\n"
        "MC__DEV_SMTP_PORT=587\n"
        "MC__DEV_SMTP_TLS=false\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    a = mailenv.load_accounts()["dev"]
    assert a.junk == "INBOX.Spam"
    assert a.port == 143
    assert a.tls == "starttls"
    assert a.smtp_port == 587
    assert a.smtp_tls is False


def test_missing_password_raises_actionable(tmp_path, monkeypatch):
    p = write_env(tmp_path, "MC__DEV_HOST=h\nMC__DEV_USER=u\n")
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    with pytest.raises(ValueError) as exc:
        mailenv.load_accounts()
    assert "password" in str(exc.value)
    assert "MC__DEV_PASSWORD" in str(exc.value)


def test_non_numeric_port_raises(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "MC__DEV_HOST=h\nMC__DEV_USER=u\nMC__DEV_PASSWORD=p\nMC__DEV_PORT=abc\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    with pytest.raises(ValueError, match="non-numeric port"):
        mailenv.load_accounts()


def test_no_accounts_raises(tmp_path, monkeypatch):
    p = write_env(tmp_path, "# nothing here\n")
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    with pytest.raises(ValueError, match="no accounts configured"):
        mailenv.load_accounts()


def test_comments_blank_lines_and_quotes_ignored(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "# leading comment\n"
        "\n"
        'MC__DEV_HOST="imap.example.com"\n'
        "MC__DEV_USER='u@x.com'\n"
        "MC__DEV_PASSWORD=p\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    a = mailenv.load_accounts()["dev"]
    assert a.host == "imap.example.com"
    assert a.user == "u@x.com"


def test_get_account_by_name(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "MC__DEV_HOST=h\nMC__DEV_USER=u\nMC__DEV_PASSWORD=p\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    assert mailenv.get_account("dev").name == "dev"
    assert mailenv.get_account("DEV").name == "dev"


def test_get_account_unknown_lists_available(tmp_path, monkeypatch):
    p = write_env(tmp_path, "MC__DEV_HOST=h\nMC__DEV_USER=u\nMC__DEV_PASSWORD=p\n")
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    with pytest.raises(ValueError) as exc:
        mailenv.get_account("nope")
    assert "dev" in str(exc.value)


def test_get_account_ambiguous_without_name(tmp_path, monkeypatch):
    p = write_env(tmp_path, (
        "MC__A_HOST=h\nMC__A_USER=u\nMC__A_PASSWORD=p\n"
        "MC__B_HOST=h\nMC__B_USER=u\nMC__B_PASSWORD=p\n"
    ))
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    with pytest.raises(ValueError, match="multiple accounts"):
        mailenv.get_account(None)


def test_get_account_single_is_implicit(tmp_path, monkeypatch):
    p = write_env(tmp_path, "MC__ONLY_HOST=h\nMC__ONLY_USER=u\nMC__ONLY_PASSWORD=p\n")
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    assert mailenv.get_account(None).name == "only"


def test_redacted_never_contains_password(tmp_path, monkeypatch):
    p = write_env(tmp_path, "MC__DEV_HOST=h\nMC__DEV_USER=u\nMC__DEV_PASSWORD=topsecret\n")
    monkeypatch.setenv("MAIL_ENV_FILE", str(p))
    assert "topsecret" not in mailenv.load_accounts()["dev"].redacted
