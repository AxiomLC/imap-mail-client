r"""IMAP operations built on imap-tools.

Read/triage/move/folder/flag only - sending lives in the SMTP path so the
Sent-folder append stays under explicit control.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

from imap_tools import A, AND, MailBox, MailBoxStartTls, MailMessageFlags, OR


@contextmanager
def connect(acct, folder=None):
    """Open a mailbox. folder=None connects without selecting one."""
    if acct.tls == "starttls":
        box = MailBoxStartTls(acct.host, acct.port)
    else:
        box = MailBox(acct.host, acct.port)
    with box.login(acct.user, acct.password, initial_folder=folder) as mb:
        yield mb


def parse_date(value):
    """Accept YYYY-MM-DD, or relative forms: 7d, 2w, 3m."""
    value = value.strip().lower()
    if value.endswith(("d", "w", "m")) and value[:-1].isdigit():
        n = int(value[:-1])
        days = {"d": 1, "w": 7, "m": 30}[value[-1]] * n
        return dt.date.today() - dt.timedelta(days=days)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError("unrecognised date %r (use YYYY-MM-DD or 7d/2w/3m)" % value)


def build_criteria(
    from_=None,
    to=None,
    subject=None,
    body=None,
    text=None,
    since=None,
    before=None,
    seen=None,
    flagged=None,
    unread_only=False,
    uid=None,
):
    """Assemble an imap-tools query from flat CLI flags."""
    kwargs = {}
    if from_:
        kwargs["from_"] = from_
    if to:
        kwargs["to"] = to
    if subject:
        kwargs["subject"] = subject
    if body:
        kwargs["body"] = body
    if text:
        kwargs["text"] = text
    if since:
        kwargs["date_gte"] = parse_date(since)
    if before:
        kwargs["date_lt"] = parse_date(before)
    if seen is not None:
        kwargs["seen"] = seen
    if unread_only:
        kwargs["seen"] = False
    if flagged is not None:
        kwargs["flagged"] = flagged
    if uid:
        kwargs["uid"] = uid

    if not kwargs:
        return "ALL"
    return AND(**kwargs)


def msg_to_dict(msg, include_body=False, max_body=4000):
    out = {
        "uid": msg.uid,
        "date": msg.date.isoformat() if msg.date else None,
        "from": msg.from_,
        "to": list(msg.to or ()),
        "subject": msg.subject,
        "flags": list(msg.flags or ()),
        "size": msg.size,
        "attachments": [a.filename for a in msg.attachments],
    }
    if include_body:
        body = msg.text or msg.html or ""
        out["body"] = body[:max_body]
        if len(body) > max_body:
            out["body_truncated"] = True
    return out


def search(acct, folder="INBOX", criteria=None, limit=25, include_body=False, reverse=True):
    with connect(acct, folder) as mb:
        msgs = mb.fetch(
            criteria=criteria or "ALL",
            headers_only=not include_body,
            limit=limit,
            reverse=reverse,
            mark_seen=False,
        )
        return [msg_to_dict(m, include_body=include_body) for m in msgs]


def read_one(acct, uid, folder="INBOX", max_body=20000):
    with connect(acct, folder) as mb:
        for msg in mb.fetch(A(uid=uid), headers_only=False, limit=1, mark_seen=False):
            return msg_to_dict(msg, include_body=True, max_body=max_body)
    raise ValueError("uid %s not found in %s" % (uid, folder))


def list_folders(acct):
    with connect(acct, "INBOX") as mb:
        rows = []
        for f in mb.folder.list():
            stat = {}
            try:
                stat = mb.folder.status(f.name)
            except Exception:
                pass
            rows.append(
                {
                    "name": f.name,
                    "delimiter": f.delim,
                    "flags": list(f.flags or ()),
                    "messages": stat.get("MESSAGES"),
                    "unseen": stat.get("UNSEEN"),
                }
            )
        return rows


def make_folder(acct, name):
    with connect(acct, None) as mb:
        if mb.folder.exists(name):
            return "exists: " + name
        mb.folder.create(name)
        return "created: " + name


def move(acct, uids, dest, source="INBOX", chunks=50):
    with connect(acct, source) as mb:
        if not mb.folder.exists(dest):
            raise ValueError("destination folder %r does not exist (create it first)" % dest)
        ids = [u.strip() for u in uids.split(",") if u.strip()]
        # UIDs are not stable across moves on some providers (Hostinger
        # reassigns them), so verify before claiming success. A silent no-op
        # is the worst possible outcome for an agent caller.
        present = set(mb.uids(A(uid=ids)))
        missing = [u for u in ids if u not in present]
        if not present:
            raise ValueError(
                "none of these UIDs exist in %s: %s (UIDs change after a move - re-search)"
                % (source, ",".join(ids))
            )
        present_ids = [u for u in ids if u in present]
        mb.move(present_ids, dest, chunks=chunks)
        result = {
            "moved": len(present_ids),
            "from": source,
            "to": dest,
            "uids": present_ids,
        }
        if missing:
            result["missing_uids"] = missing
            result["note"] = (
                "some UIDs were not found and were skipped; UIDs change after a "
                "move on providers like Hostinger"
            )
        return result


def move_by_search(acct, criteria, dest, source="INBOX", limit=500):
    with connect(acct, source) as mb:
        if not mb.folder.exists(dest):
            raise ValueError("destination folder %r does not exist (create it first)" % dest)
        uids = mb.uids(criteria, limit=limit)
        if not uids:
            return {"moved": 0, "from": source, "to": dest, "uids": []}
        mb.move(uids, dest, chunks=50)
        return {"moved": len(uids), "from": source, "to": dest, "uids": uids}


def set_flags(acct, uids, folder="INBOX", add=(), remove=()):
    ids = [u.strip() for u in uids.split(",") if u.strip()]
    with connect(acct, folder) as mb:
        if add:
            mb.flag(ids, add, True)
        if remove:
            mb.flag(ids, remove, False)
    return {"uids": ids, "added": list(add), "removed": list(remove), "folder": folder}


def resolve_folder(mb, configured, *fallbacks):
    """Return the first of configured/*fallbacks that exists on the server.

    Lets accounts stay forgiving about folder spelling (INBOX.Spam vs INBOX.Junk)
    without the caller having to know the provider's layout.
    """
    for cand in (configured,) + fallbacks:
        if cand and mb.folder.exists(cand):
            return cand
    raise ValueError(
        "none of these folders exist: %s"
        % ", ".join(str(c) for c in (configured,) + fallbacks if c)
    )


def junk_folder(mb, acct):
    return resolve_folder(mb, acct.junk, "INBOX.Junk", "INBOX.Spam", "Junk", "Spam")


def delete(acct, uids, folder="INBOX", purge=False):
    r"""Soft-delete by default (\Deleted flag only). purge=True also expunges."""
    ids = [u.strip() for u in uids.split(",") if u.strip()]
    with connect(acct, folder) as mb:
        if purge:
            mb.delete(ids)
            return {"deleted": ids, "folder": folder, "purged": True}
        mb.flag(ids, [MailMessageFlags.DELETED], True)
        return {
            "marked_deleted": ids,
            "folder": folder,
            "purged": False,
            "note": "still present until EXPUNGE; pass --purge to remove",
        }


def junk_move(acct, uids=None, criteria=None, source=None, dest=None, limit=500):
    """Move messages into or out of the junk folder, resolving its real name.

    dest=None  -> move into the junk folder (detected by name)
    dest='INBOX' -> move back out of the junk folder
    """
    if dest is None:
        with connect(acct, "INBOX") as mb:
            target = junk_folder(mb, acct)
        src = source or "INBOX"
    else:
        with connect(acct, "INBOX") as mb:
            src = junk_folder(mb, acct)
        target = dest

    if uids:
        return move(acct, uids=uids, dest=target, source=src)
    return move_by_search(acct, criteria=criteria, dest=target, source=src, limit=limit)


def find_bounces(acct, since=None, limit=100, folder=None):
    """Locate bounce notifications.

    Scans the configured bounce folder by default (bounces are normally
    filed there by a rule); pass folder= to override.
    """
    crit = OR(
        A(from_="MAILER-DAEMON"),
        A(from_="postmaster"),
        A(subject="Undelivered Mail"),
        A(subject="Delivery Status Notification"),
        A(subject="Mail delivery failed"),
    )
    if since:
        crit = AND(crit, A(date_gte=parse_date(since)))
    with connect(acct, folder or acct.bounces) as mb:
        return [
            msg_to_dict(m)
            for m in mb.fetch(crit, headers_only=True, limit=limit, mark_seen=False)
        ]
