"""Tests for the mailctl CLI surface an agent drives.

Focus: exit codes, JSON shape, stdout encoding, and that mutations are gated.
Nothing here touches a network.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mailctl


def run(argv, monkeypatch, fake_accounts):
    """Invoke mailctl.main with a stubbed account loader."""
    monkeypatch.setattr(mailctl, "load_accounts", lambda: fake_accounts)
    monkeypatch.setattr(mailctl, "get_account", lambda name: next(iter(fake_accounts.values())))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    code = mailctl.main(argv)
    return code, out.getvalue()


@pytest.fixture
def fake_accounts():
    from mailenv import Account
    return {"dev": Account(name="dev", host="h", user="u@x.com", password="p")}


# ------------------------------------------------------------- arg parsing


def test_parser_has_all_commands():
    p = mailctl.build_parser()
    sub = next(a for a in p._actions if a.dest == "cmd")
    names = set(sub.choices or ())
    assert {"accounts", "folders", "search", "read", "move", "mkdir", "flag",
            "delete", "bounces", "junk", "send", "draft", "reply"} <= names


def test_account_flag_before_subcommand():
    args = mailctl.build_parser().parse_args(["-a", "dev", "folders"])
    assert args.account == "dev"


def test_account_flag_after_subcommand():
    args = mailctl.build_parser().parse_args(["folders", "-a", "dev"])
    assert args.account == "dev"


def test_folder_flag_defaults_to_inbox():
    args = mailctl.build_parser().parse_args(["folders"])
    assert args.folder in ("INBOX", None)


def test_missing_required_arg_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        mailctl.build_parser().parse_args(["send", "--body", "x"])
    assert exc.value.code == 2


# ------------------------------------------------------------ exit codes


def test_move_without_target_is_usage_error(monkeypatch, fake_accounts):
    # --to is required by argparse, so this exits 2 before reaching the handler.
    with pytest.raises(SystemExit) as exc:
        run(["move", "-a", "dev"], monkeypatch, fake_accounts)
    assert exc.value.code == 2


def test_move_without_uids_or_search_is_usage_error(monkeypatch, fake_accounts):
    code, _ = run(["move", "-a", "dev", "--to", "INBOX.X"], monkeypatch, fake_accounts)
    assert code == 2


def test_flag_without_action_is_usage_error(monkeypatch, fake_accounts):
    code, _ = run(["flag", "-a", "dev", "--uids", "1"], monkeypatch, fake_accounts)
    assert code == 2


def test_junk_without_target_is_usage_error(monkeypatch, fake_accounts):
    code, _ = run(["junk", "-a", "dev"], monkeypatch, fake_accounts)
    assert code == 2


def test_runtime_error_returns_1(monkeypatch, fake_accounts):
    def boom(*a, **k):
        raise ValueError("destination folder 'X' does not exist (create it first)")

    monkeypatch.setattr(mailctl.mail_ops, "move", boom)
    code, _ = run(["move", "-a", "dev", "--uids", "1", "--to", "X"],
                  monkeypatch, fake_accounts)
    assert code == 1


# ----------------------------------------------------------------- output


def test_accounts_emits_valid_json(monkeypatch, fake_accounts):
    code, out = run(["accounts"], monkeypatch, fake_accounts)
    assert code == 0
    data = json.loads(out)
    assert data[0]["name"] == "dev"
    assert "u@x.com" in data[0]["account"]


def test_accounts_never_leaks_password(monkeypatch, fake_accounts):
    _code, out = run(["accounts"], monkeypatch, fake_accounts)
    assert "password" not in out.lower()
    assert '"p"' not in out


def test_compact_output_is_single_line(monkeypatch, fake_accounts):
    monkeypatch.setattr(mailctl.mail_ops, "search", lambda *a, **k: [{"uid": "1"}])
    code, out = run(["search", "-a", "dev", "--compact"], monkeypatch, fake_accounts)
    assert code == 0
    assert out.strip().count("\n") == 0


def test_unicode_subjects_do_not_crash(monkeypatch, fake_accounts):
    monkeypatch.setattr(
        mailctl.mail_ops, "search",
        lambda *a, **k: [{"uid": "1", "subject": "En\u2013dash \u2014 Jos\u00e9"}],
    )
    code, out = run(["search", "-a", "dev"], monkeypatch, fake_accounts)
    assert code == 0
    assert "Jos\u00e9" in out


# --------------------------------------------------------------- body input


def test_body_file_read(tmp_path, monkeypatch, fake_accounts):
    f = tmp_path / "body.txt"
    f.write_text("from file", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(mailctl.mail_send, "send",
                        lambda acct, **kw: captured.update(kw) or {"status": "dry_run"})
    code, _ = run(["send", "-a", "dev", "--to", "a@b.com", "--subject", "S",
                   "--body-file", str(f), "--dry-run"], monkeypatch, fake_accounts)
    assert code == 0
    assert captured["body"] == "from file"


def test_body_defaults_to_empty(monkeypatch, fake_accounts):
    captured = {}
    monkeypatch.setattr(mailctl.mail_send, "send",
                        lambda acct, **kw: captured.update(kw) or {"status": "dry_run"})
    run(["send", "-a", "dev", "--to", "a@b.com", "--subject", "S", "--dry-run"],
        monkeypatch, fake_accounts)
    assert captured["body"] == ""


# ------------------------------------------------------------- send gating


def test_send_dry_run_does_not_call_smtp(monkeypatch, fake_accounts):
    called = []
    monkeypatch.setattr(mailctl.mail_send.smtplib, "SMTP_SSL",
                        lambda *a, **k: called.append(1))
    code, out = run(["send", "-a", "dev", "--to", "a@b.com", "--subject", "S",
                     "--body", "B", "--dry-run"], monkeypatch, fake_accounts)
    assert code == 0
    assert called == []
    assert json.loads(out)["status"] == "dry_run"


def test_reply_defaults_to_draft_not_send(monkeypatch, fake_accounts):
    """'reply' must not send unless --send is passed."""
    sent, drafted = [], []
    monkeypatch.setattr(mailctl.mail_send, "build_reply",
                        lambda *a, **k: {"to": ["x@y.com"], "subject": "Re: s",
                                         "body": "b", "in_reply_to": None,
                                         "references": None, "attachments": []})
    monkeypatch.setattr(mailctl.mail_send, "send",
                        lambda *a, **k: sent.append(1) or {"status": "sent"})
    monkeypatch.setattr(mailctl.mail_send, "save_draft",
                        lambda *a, **k: drafted.append(1) or {"status": "draft_saved"})
    code, out = run(["reply", "-a", "dev", "--uid", "1", "--body", "hi"],
                    monkeypatch, fake_accounts)
    assert code == 0
    assert sent == [] and drafted == [1]
    assert json.loads(out)["status"] == "draft_saved"
