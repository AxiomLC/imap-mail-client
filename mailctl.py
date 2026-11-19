#!/usr/bin/env python3
r"""mailctl - multi-account IMAP client for agents and humans.

Read/triage/move/folders/flags. Sending is out of scope here; keep the SMTP
path (with its explicit Sent-folder append) separate.

All commands emit JSON on stdout.

Examples:
    python mailctl.py accounts
    python mailctl.py folders -a dev
    python mailctl.py search -a dev --since 7d --limit 20
    python mailctl.py search -a dev --from boss@x.com --include-body
    python mailctl.py read -a dev --uid 4711
    python mailctl.py move -a dev --uids 4711,4712 --to INBOX.Archive
    python mailctl.py move -a dev --search-from spam@x.com --to INBOX.Junk
    python mailctl.py mkdir -a dev INBOX.Archive/2026
    python mailctl.py flag -a dev --uids 4711 --add \Seen
    python mailctl.py delete -a dev --uids 4711
    python mailctl.py bounces -a dev --since 7d
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Windows consoles default to cp1252; mail routinely contains en-dashes, curly
# quotes and accented names. Force UTF-8 so output never dies on a codepoint.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

import mail_ops
import mail_send
from mailenv import get_account, load_accounts


def emit(data, compact=False):
    if compact:
        json.dump(data, sys.stdout, ensure_ascii=True, default=str, separators=(",", ":"))
    else:
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def resolve_account(args):
    name = args.account or os.environ.get("MAIL_DEFAULT_ACCOUNT")
    return get_account(name)


def _read_body(args):
    """Body from --body, --body-file, or stdin ("-")."""
    if getattr(args, "body_file", None):
        if args.body_file == "-":
            return sys.stdin.read()
        with open(args.body_file, encoding="utf-8") as fh:
            return fh.read()
    return getattr(args, "body", None) or ""


def build_parser():
    p = argparse.ArgumentParser(
        prog="mailctl",
        description="Multi-account IMAP client (JSON output).",
    )
    # Global flags are attached to the main parser AND to every subparser so
    # they may be given before or after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--account", "-a", default=argparse.SUPPRESS,
                        help="account name (omit if only one configured)")
    common.add_argument("--folder", "-f", default=argparse.SUPPRESS, help="mailbox (default INBOX)")

    p.add_argument("--account", "-a", default=None,
                   help="account name (omit if only one configured)")
    p.add_argument("--folder", "-f", default="INBOX", help="mailbox (default INBOX)")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("accounts", help="list configured accounts", parents=[common])
    sub.add_parser("folders", help="list folders with message/unseen counts", parents=[common])

    s = sub.add_parser("search", help="search messages", parents=[common])
    s.add_argument("--from", dest="from_")
    s.add_argument("--to")
    s.add_argument("--subject")
    s.add_argument("--body")
    s.add_argument("--text")
    s.add_argument("--since", help="YYYY-MM-DD or 7d / 2w / 3m")
    s.add_argument("--before", help="YYYY-MM-DD or 7d / 2w / 3m")
    s.add_argument("--unread", action="store_true")
    s.add_argument("--flagged", action="store_true")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--include-body", action="store_true")
    s.add_argument("--oldest-first", action="store_true")
    s.add_argument("--compact", action="store_true",
                   help="single-line JSON array (token-efficient for agents)")

    r = sub.add_parser("read", help="read one message by uid", parents=[common])
    r.add_argument("--uid", required=True)
    r.add_argument("--max-body", type=int, default=20000)

    m = sub.add_parser("move", help="move by uid list or by search", parents=[common])
    m.add_argument("--uids")
    m.add_argument("--search-from", dest="search_from")
    m.add_argument("--search-subject", dest="search_subject")
    m.add_argument("--since")
    m.add_argument("--to", required=True, dest="to")
    m.add_argument("--limit", type=int, default=500)

    mk = sub.add_parser("mkdir", help="create a folder", parents=[common])
    mk.add_argument("name")

    fl = sub.add_parser("flag", help="add/remove flags", parents=[common])
    fl.add_argument("--uids", required=True)
    fl.add_argument("--add", action="append", default=[])
    fl.add_argument("--remove", action="append", default=[])

    d = sub.add_parser("delete", help="delete messages (soft by default)", parents=[common])
    d.add_argument("--uids", required=True)
    d.add_argument("--purge", action="store_true", help="also EXPUNGE (irreversible)")

    b = sub.add_parser("bounces", help="list bounce notifications", parents=[common])
    b.add_argument("--since")
    b.add_argument("--limit", type=int, default=100)

    # ---- junk / spam ----
    j = sub.add_parser(
        "junk", help="move messages to the Junk folder (or out of it)", parents=[common]
    )
    j.add_argument("--uids")
    j.add_argument("--search-from", dest="search_from")
    j.add_argument("--search-subject", dest="search_subject")
    j.add_argument("--since")
    j.add_argument("--limit", type=int, default=500)
    j.add_argument("--not-junk", action="store_true",
                   help="move back to INBOX instead of into Junk")

    # ---- send ----
    sd = sub.add_parser("send", help="send a new email", parents=[common])
    sd.add_argument("--to", required=True, action="append",
                    help="recipient (repeatable)")
    sd.add_argument("--cc", action="append", default=[])
    sd.add_argument("--bcc", action="append", default=[])
    sd.add_argument("--subject", required=True)
    sd.add_argument("--body", help="plain-text body")
    sd.add_argument("--body-file", help="read body from a UTF-8 file")
    sd.add_argument("--html")
    sd.add_argument("--attach", action="append", default=[],
                    help="file path (repeatable)")
    sd.add_argument("--from-name")
    sd.add_argument("--dry-run", action="store_true",
                    help="compose and report, do not send")

    # ---- draft ----
    dr = sub.add_parser("draft", help="save a draft (does not send)", parents=[common])
    dr.add_argument("--to", action="append", default=[])
    dr.add_argument("--cc", action="append", default=[])
    dr.add_argument("--bcc", action="append", default=[])
    dr.add_argument("--subject", required=True)
    dr.add_argument("--body")
    dr.add_argument("--body-file")
    dr.add_argument("--attach", action="append", default=[])
    dr.add_argument("--draft-folder", dest="draft_folder",
                    help="target Drafts folder (default from account)")

    # ---- reply ----
    rp = sub.add_parser(
        "reply", help="reply to a message (compose, then send or draft)", parents=[common]
    )
    rp.add_argument("--uid", required=True)
    rp.add_argument("--body", help="reply text")
    rp.add_argument("--body-file")
    rp.add_argument("--reply-all", action="store_true")
    rp.add_argument("--quote", action="store_true", help="quote the original")
    rp.add_argument("--attach", action="append", default=[])
    rp.add_argument("--from-name")
    rp.add_argument("--send", action="store_true", help="send instead of drafting")
    rp.add_argument("--dry-run", action="store_true",
                    help="show the composed reply, send nothing")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not hasattr(args, "account"):
        args.account = None
    if not hasattr(args, "folder") or args.folder is None:
        args.folder = "INBOX"

    try:
        if args.cmd == "accounts":
            emit([{"name": a.name, "account": a.redacted} for a in load_accounts().values()])
            return 0

        acct = resolve_account(args)

        if args.cmd == "folders":
            emit(mail_ops.list_folders(acct))

        elif args.cmd == "search":
            crit = mail_ops.build_criteria(
                from_=args.from_,
                to=args.to,
                subject=args.subject,
                body=args.body,
                text=args.text,
                since=args.since,
                before=args.before,
                unread_only=args.unread,
                flagged=True if args.flagged else None,
            )
            emit(
                mail_ops.search(
                    acct,
                    folder=args.folder,
                    criteria=crit,
                    limit=args.limit,
                    include_body=args.include_body,
                    reverse=not args.oldest_first,
                ),
                compact=args.compact,
            )

        elif args.cmd == "read":
            emit(mail_ops.read_one(acct, args.uid, folder=args.folder, max_body=args.max_body))

        elif args.cmd == "move":
            if args.uids:
                emit(mail_ops.move(acct, uids=args.uids, dest=args.to, source=args.folder))
            elif args.search_from or args.search_subject or args.since:
                crit = mail_ops.build_criteria(
                    from_=args.search_from, subject=args.search_subject, since=args.since
                )
                emit(
                    mail_ops.move_by_search(
                        acct, criteria=crit, dest=args.to, source=args.folder, limit=args.limit
                    )
                )
            else:
                print("move needs --uids or --search-*/--since", file=sys.stderr)
                return 2

        elif args.cmd == "mkdir":
            emit({"result": mail_ops.make_folder(acct, args.name)})

        elif args.cmd == "flag":
            if not args.add and not args.remove:
                print("flag needs --add and/or --remove", file=sys.stderr)
                return 2
            emit(
                mail_ops.set_flags(
                    acct, uids=args.uids, folder=args.folder, add=args.add, remove=args.remove
                )
            )

        elif args.cmd == "delete":
            emit(mail_ops.delete(acct, uids=args.uids, folder=args.folder, purge=args.purge))

        elif args.cmd == "bounces":
            emit(mail_ops.find_bounces(acct, since=args.since, limit=args.limit))

        elif args.cmd == "junk":
            if args.not_junk:
                dest = "INBOX"
                source = None
            else:
                dest = None
                source = args.folder
            if args.uids or args.search_from or args.search_subject or args.since:
                emit(
                    mail_ops.junk_move(
                        acct,
                        uids=args.uids,
                        criteria=mail_ops.build_criteria(
                            from_=args.search_from,
                            subject=args.search_subject,
                            since=args.since,
                        )
                        if not args.uids
                        else None,
                        source=source,
                        dest=dest,
                        limit=args.limit,
                    )
                )
            else:
                print("junk needs --uids or at least one --search-*/--since", file=sys.stderr)
                return 2

        elif args.cmd == "send":
            body = _read_body(args)
            emit(
                mail_send.send(
                    acct,
                    to=args.to,
                    subject=args.subject,
                    body=body,
                    cc=args.cc or None,
                    bcc=args.bcc or None,
                    html=args.html,
                    attachments=args.attach or None,
                    from_name=args.from_name,
                    dry_run=args.dry_run,
                )
            )

        elif args.cmd == "draft":
            body = _read_body(args)
            emit(
                mail_send.save_draft(
                    acct,
                    to=args.to,
                    subject=args.subject,
                    body=body,
                    cc=args.cc or None,
                    bcc=args.bcc or None,
                    attachments=args.attach or None,
                    folder=args.draft_folder,
                )
            )

        elif args.cmd == "reply":
            body = _read_body(args)
            composed = mail_send.build_reply(
                acct,
                uid=args.uid,
                body=body,
                folder=args.folder,
                reply_all=args.reply_all,
                attachments=args.attach or None,
                quote_original=args.quote,
            )
            if args.dry_run:
                composed["status"] = "dry_run"
                emit(composed)
            elif args.send:
                result = mail_send.send(
                    acct,
                    to=composed["to"],
                    subject=composed["subject"],
                    body=composed["body"],
                    attachments=composed["attachments"] or None,
                    in_reply_to=composed["in_reply_to"],
                    references=composed["references"],
                    from_name=args.from_name,
                )
                result["reply_to_uid"] = args.uid
                emit(result)
            else:
                result = mail_send.save_draft(
                    acct,
                    to=composed["to"],
                    subject=composed["subject"],
                    body=composed["body"],
                    attachments=composed["attachments"] or None,
                    in_reply_to=composed["in_reply_to"],
                    references=composed["references"],
                )
                result["reply_to_uid"] = args.uid
                emit(result)

        else:
            return 2

    except Exception as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
