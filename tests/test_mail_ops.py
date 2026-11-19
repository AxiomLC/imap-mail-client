"""Tests for mail_ops: UID validation, junk detection, flags, date parsing.

These target the failures found during live testing, so a regression in any of
them fails the suite rather than silently corrupting a mailbox.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import mail_ops
from fakes import FakeMailbox, FakeMessage
from mailenv import Account


@pytest.fixture
def acct():
    return Account(
        name="dev",
        host="imap.example.com",
        user="me@example.com",
        password="pw",
        junk="INBOX.Spam",  # deliberately wrong; real folder is INBOX.Junk
    )


@pytest.fixture
def box():
    return FakeMailbox(messages=[
        FakeMessage(1, "first"),
        FakeMessage(2, "second"),
        FakeMessage(3, "third"),
    ])


@pytest.fixture(autouse=True)
def patch_connect(monkeypatch, box):
    """Route mail_ops.connect at the fake mailbox."""

    @mail_ops.contextmanager
    def fake_connect(acct, folder=None):
        if folder:
            box.select(folder)
        yield box

    monkeypatch.setattr(mail_ops, "connect", fake_connect)
    return box


# ---------------------------------------------------------------- dates


@pytest.mark.parametrize("value,expected_days", [
    ("7d", 7), ("2w", 14), ("3m", 90), ("1d", 1),
])
def test_parse_relative_dates(value, expected_days):
    got = mail_ops.parse_date(value)
    assert got == dt.date.today() - dt.timedelta(days=expected_days)


def test_parse_absolute_date():
    assert mail_ops.parse_date("2026-01-15") == dt.date(2026, 1, 15)


def test_parse_date_rejects_garbage():
    with pytest.raises(ValueError, match="unrecognised date"):
        mail_ops.parse_date("last tuesday")


# ---------------------------------------------------------------- criteria


def test_build_criteria_empty_returns_all():
    assert mail_ops.build_criteria() == "ALL"


def test_build_criteria_unread_maps_to_unseen():
    crit = mail_ops.build_criteria(unread_only=True)
    assert "UNSEEN" in str(crit).upper()


def test_build_criteria_combines_fields():
    crit = mail_ops.build_criteria(from_="a@b.com", subject="hi")
    text = str(crit).upper()
    assert "FROM" in text and "SUBJECT" in text


# ------------------------------------------------------------- UID guard


def test_move_rejects_unknown_uids_loudly(acct):
    """The silent-no-op bug: a stale UID must raise, not pretend to succeed."""
    with pytest.raises(ValueError) as exc:
        mail_ops.move(acct, uids="999", dest="INBOX.Bounces", source="INBOX")
    msg = str(exc.value)
    assert "none of these UIDs exist" in msg
    assert "re-search" in msg


def test_move_reports_missing_uids_when_partial(acct):
    result = mail_ops.move(acct, uids="1,999", dest="INBOX.Bounces", source="INBOX")
    assert result["moved"] == 1
    assert result["missing_uids"] == ["999"]
    assert "note" in result


def test_move_rejects_missing_destination(acct):
    with pytest.raises(ValueError, match="does not exist"):
        mail_ops.move(acct, uids="1", dest="INBOX.Nope", source="INBOX")


# ------------------------------------------------------- junk resolution


def test_junk_folder_falls_back_past_wrong_config(acct):
    """Config says INBOX.Spam; the real folder is INBOX.Junk."""
    with mail_ops.connect(acct, "INBOX") as mb:
        resolved = mail_ops.junk_folder(mb, acct)
    assert resolved == "INBOX.Junk"


def test_junk_folder_respects_correct_config(box):
    box.folders["INBOX.Spam"] = []
    a = Account(name="x", host="h", user="u", password="p", junk="INBOX.Spam")
    with mail_ops.connect(a, "INBOX") as mb:
        assert mail_ops.junk_folder(mb, a) == "INBOX.Spam"


def test_junk_folder_raises_when_none_exist(box):
    del box.folders["INBOX.Junk"]
    a = Account(name="x", host="h", user="u", password="p", junk="INBOX.Spam")
    with mail_ops.connect(a, "INBOX") as mb, pytest.raises(
        ValueError, match="none of these folders exist"
    ):
        mail_ops.junk_folder(mb, a)


def test_junk_move_into_junk_uses_detected_folder(acct, box):
    mail_ops.junk_move(acct, uids="1")
    assert any(m.uid == "1" for m in box.folders["INBOX.Junk"])


def test_junk_move_back_to_inbox(acct, box):
    box.folders["INBOX.Junk"].append(FakeMessage(7, "jailed"))
    result = mail_ops.junk_move(acct, uids="7", dest="INBOX")
    assert result["to"] == "INBOX"
    assert any(m.uid == "7" for m in box.folders["INBOX"])


# ------------------------------------------------------------- behaviour


def test_make_folder_is_idempotent(acct, box):
    assert mail_ops.make_folder(acct, "INBOX.New").startswith("created")
    assert mail_ops.make_folder(acct, "INBOX.New").startswith("exists")


def test_list_folders_returns_counts(acct, box):
    rows = mail_ops.list_folders(acct)
    names = {r["name"] for r in rows}
    assert "INBOX" in names
    inbox = next(r for r in rows if r["name"] == "INBOX")
    assert inbox["messages"] == 3


def test_set_flags_adds_and_removes(acct, box):
    mail_ops.set_flags(acct, uids="1", add=["\\Flagged"])
    assert "\\Flagged" in box.folders["INBOX"][0].flags
    mail_ops.set_flags(acct, uids="1", remove=["\\Flagged"])
    assert "\\Flagged" not in box.folders["INBOX"][0].flags


def test_delete_soft_by_default(acct, box):
    result = mail_ops.delete(acct, uids="1")
    assert result["purged"] is False
    assert len(box.folders["INBOX"]) == 3  # nothing actually removed


def test_delete_purge_removes(acct, box):
    result = mail_ops.delete(acct, uids="1", purge=True)
    assert result["purged"] is True
    assert len(box.folders["INBOX"]) == 2


def test_search_respects_limit_and_order(acct, box):
    rows = mail_ops.search(acct, limit=2)
    assert len(rows) == 2
    assert rows[0]["uid"] == "3"  # newest-first
