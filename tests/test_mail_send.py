"""Tests for mail_send: message composition, Sent-append, drafts, replies."""

from __future__ import annotations

import sys
from email import message_from_bytes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import mail_send
from fakes import FakeContext, FakeMailbox, FakeMessage
from mailenv import Account


@pytest.fixture
def acct():
    return Account(
        name="dev",
        host="imap.example.com",
        user="me@example.com",
        password="pw",
        smtp_host="smtp.example.com",
    )


@pytest.fixture
def box(monkeypatch):
    b = FakeMailbox(messages=[FakeMessage(5, "Original subject", from_="them@x.com")])
    monkeypatch.setattr(mail_send, "_open", lambda a, folder=None: _Box(b))
    return b


class _Box:
    """Adapter giving mail_send._open(...).login(...) the shape it expects."""

    def __init__(self, box):
        self._box = box

    def login(self, user, password, initial_folder=None):
        if initial_folder:
            self._box.select(initial_folder)
        return FakeContext(self._box)

    def __getattr__(self, name):
        return getattr(self._box, name)


# ------------------------------------------------------------ composition


def test_build_message_headers(acct):
    msg = mail_send._build_message(
        acct, ["a@b.com"], "Subject", "Body",
        cc=["c@d.com"], bcc=["e@f.com"],
    )
    assert msg["From"] == "me@example.com"
    assert msg["To"] == "a@b.com"
    assert msg["Cc"] == "c@d.com"
    assert msg["Bcc"] == "e@f.com"
    assert msg["Subject"] == "Subject"
    assert msg["Message-ID"]


def test_build_message_from_name(acct):
    msg = mail_send._build_message(acct, ["a@b.com"], "S", "B", from_name="Dev Team")
    assert msg["From"] == "Dev Team <me@example.com>"


def test_build_message_sets_threading_headers(acct):
    msg = mail_send._build_message(
        acct, ["a@b.com"], "S", "B",
        in_reply_to="<x@y>", references="<a@b> <x@y>",
    )
    assert msg["In-Reply-To"] == "<x@y>"
    assert msg["References"] == "<a@b> <x@y>"


def test_attachment_missing_file_raises(acct):
    with pytest.raises(ValueError, match="attachment not found"):
        mail_send._build_message(acct, ["a@b.com"], "S", "B",
                                 attachments=["/no/such/file.pdf"])


def test_attachment_included(acct, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello", encoding="utf-8")
    msg = mail_send._build_message(acct, ["a@b.com"], "S", "B", attachments=[str(f)])
    parsed = message_from_bytes(msg.as_bytes())
    names = [p.get_filename() for p in parsed.walk() if p.get_filename()]
    assert "note.txt" in names


def test_unicode_survives_roundtrip(acct):
    body = "En-dash \u2013 em-dash \u2014 accent Jos\u00e9"
    msg = mail_send._build_message(acct, ["a@b.com"], "Sujet \u2014 test", body)
    raw = msg.as_bytes()
    parsed = message_from_bytes(raw)
    assert parsed["Subject"]  # header encoded, not lost


# ------------------------------------------------------------------ smtp


def test_smtp_config_derives_from_imap_host():
    a = Account(name="x", host="imap.hostinger.com", user="u", password="p")
    host, port, tls = mail_send._smtp_config(a)
    assert host == "smtp.hostinger.com"
    assert port == 465
    assert tls is True


def test_smtp_config_explicit_override():
    a = Account(name="x", host="imap.x.com", user="u", password="p",
                smtp_host="mail.x.com", smtp_port=587, smtp_tls=False)
    assert mail_send._smtp_config(a) == ("mail.x.com", 587, False)


def test_dry_run_sends_nothing(monkeypatch, acct):
    called = []
    monkeypatch.setattr(mail_send.smtplib, "SMTP_SSL",
                        lambda *a, **k: called.append("smtp"))
    result = mail_send.send(acct, ["a@b.com"], "S", "B", dry_run=True)
    assert result["status"] == "dry_run"
    assert called == []


def test_send_reports_sent_append_success(acct, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def ehlo(self): pass
        def login(self, u, p): pass
        def sendmail(self, f, t, m): sent.append((f, t))
        def quit(self): pass

    monkeypatch.setattr(mail_send.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mail_send, "_imap_append",
                        lambda *a, **k: (True, "appended to INBOX.Sent"))
    result = mail_send.send(acct, ["a@b.com"], "S", "B")
    assert result["status"] == "sent"
    assert result["saved_to_sent"] is True
    assert sent == [("me@example.com", ["a@b.com"])]


def test_send_surfaces_sent_append_failure(acct, monkeypatch):
    """A failed Sent append must be visible, never swallowed."""

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def ehlo(self): pass
        def login(self, u, p): pass
        def sendmail(self, f, t, m): pass
        def quit(self): pass

    monkeypatch.setattr(mail_send.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mail_send, "_imap_append",
                        lambda *a, **k: (False, "IMAP4.error: nope"))
    result = mail_send.send(acct, ["a@b.com"], "S", "B")
    assert result["status"] == "sent"
    assert result["saved_to_sent"] is False
    assert "nope" in result["sent_detail"]


# ---------------------------------------------------------------- drafts


def test_save_draft_appends_to_drafts(acct, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        mail_send, "_imap_append",
        lambda a, mailbox, raw, flags: (captured.update(mailbox=mailbox, flags=flags)
                                        or (True, "ok")),
    )
    result = mail_send.save_draft(acct, ["a@b.com"], "Draft subj", "text")
    assert result["status"] == "draft_saved"
    assert captured["mailbox"] == "INBOX.Drafts"


def test_save_draft_raises_when_append_fails(acct, monkeypatch):
    monkeypatch.setattr(mail_send, "_imap_append", lambda *a, **k: (False, "boom"))
    with pytest.raises(RuntimeError, match="could not save draft"):
        mail_send.save_draft(acct, ["a@b.com"], "S", "B")


# ---------------------------------------------------------------- reply


def test_build_reply_addresses_sender(acct, box):
    r = mail_send.build_reply(acct, uid="5", body="Thanks")
    assert r["to"] == ["them@x.com"]
    assert r["subject"] == "Re: Original subject"
    assert r["in_reply_to"] == "<5@fake>"


def test_build_reply_does_not_double_prefix(acct, box):
    box.folders["INBOX"].append(FakeMessage(6, "Re: already", from_="x@y.com"))
    r = mail_send.build_reply(acct, uid="6", body="ok")
    assert r["subject"] == "Re: already"


def test_build_reply_unknown_uid_raises(acct, box):
    with pytest.raises(ValueError, match="not found"):
        mail_send.build_reply(acct, uid="999", body="x")


def test_build_reply_quotes_original(acct, box):
    r = mail_send.build_reply(acct, uid="5", body="My reply", quote_original=True)
    assert "My reply" in r["body"]
    assert "> body text" in r["body"]


def test_build_reply_all_excludes_self(acct, box):
    box.folders["INBOX"].append(
        FakeMessage(8, "thread", from_="a@x.com", to=("b@x.com", "me@example.com"))
    )
    r = mail_send.build_reply(acct, uid="8", body="hi", reply_all=True)
    assert "a@x.com" in r["to"]
    assert "b@x.com" in r["to"]
    assert not any("me@example.com" in addr for addr in r["to"])
