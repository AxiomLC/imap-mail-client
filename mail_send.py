r"""SMTP send, draft persistence and reply composition.

Design notes:
  - Sending uses smtplib directly so the from-address is explicit.
  - After every successful send the message is IMAP-APPENDed to the account's
    Sent folder. Without that append, Hostinger webmail shows an empty Sent.
  - The append is reported, never silently swallowed: callers get
    saved_to_sent=True/False plus a reason, so a half-finished send is visible.
  - Drafts are appended to the Drafts folder with the Draft flag set.
"""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path

from imap_tools import A, MailBox, MailBoxStartTls, MailMessageFlags

from mailenv import Account


def _open(acct, folder=None):
    if acct.tls == "starttls":
        return MailBoxStartTls(acct.host, acct.port)
    return MailBox(acct.host, acct.port)


def _smtp_config(acct):
    """Resolve SMTP host/port/tls, defaulting from the IMAP host convention."""
    host = acct.smtp_host or acct.host.replace("imap.", "smtp.", 1)
    return host, acct.smtp_port, acct.smtp_tls


def _imap_append(acct, mailbox, raw, flags):
    """Append a raw message to a folder. Returns (ok, detail)."""
    try:
        with _open(acct).login(acct.user, acct.password, initial_folder=None) as mb:
            if not mb.folder.exists(mailbox):
                mb.folder.create(mailbox)
            mb.append(raw, mailbox, flag_set=flags, dt=None)
        return True, "appended to " + mailbox
    except Exception as exc:  # noqa: BLE001 - surfaced, not raised
        return False, "%s: %s" % (type(exc).__name__, exc)


def _build_message(
    acct,
    to,
    subject,
    body,
    cc=None,
    bcc=None,
    html=None,
    attachments=None,
    in_reply_to=None,
    references=None,
    from_name=None,
):
    msg = MIMEMultipart("alternative")
    msg["From"] = ("%s <%s>" % (from_name, acct.user)) if from_name else acct.user
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=acct.user.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    msg.attach(MIMEText(body, "plain", "utf-8"))
    if html:
        msg.attach(MIMEText(html, "html", "utf-8"))

    for path in attachments or []:
        p = Path(path).expanduser()
        if not p.is_file():
            raise ValueError("attachment not found: %s" % path)
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        part = MIMEBase(maintype or "application", subtype or "octet-stream")
        part.set_payload(p.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=p.name)
        msg.attach(part)

    return msg


def send(
    acct,
    to,
    subject,
    body,
    cc=None,
    bcc=None,
    html=None,
    attachments=None,
    in_reply_to=None,
    references=None,
    from_name=None,
    dry_run=False,
):
    msg = _build_message(
        acct, to, subject, body, cc, bcc, html, attachments,
        in_reply_to, references, from_name,
    )
    raw = msg.as_bytes()
    recipients = list(to) + list(cc or []) + list(bcc or [])
    host, port, use_ssl = _smtp_config(acct)

    if dry_run:
        return {
            "status": "dry_run",
            "from": acct.user,
            "smtp": "%s:%s ssl=%s" % (host, port, use_ssl),
            "to": recipients,
            "subject": subject,
            "bytes": len(raw),
            "attachments": [Path(a).name for a in (attachments or [])],
        }

    context = ssl.create_default_context()
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=45)
    else:
        server = smtplib.SMTP(host, port, timeout=45)
    try:
        server.ehlo()
        if not use_ssl:
            server.starttls(context=context)
            server.ehlo()
        server.login(acct.user, acct.password)
        server.sendmail(acct.user, recipients, raw)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass

    ok, detail = _imap_append(acct, acct.sent, raw, [MailMessageFlags.SEEN])
    return {
        "status": "sent",
        "from": acct.user,
        "to": recipients,
        "subject": subject,
        "saved_to_sent": ok,
        "sent_detail": detail,
        "bytes": len(raw),
    }


def save_draft(
    acct,
    to,
    subject,
    body,
    cc=None,
    bcc=None,
    attachments=None,
    in_reply_to=None,
    references=None,
    folder=None,
):
    msg = _build_message(
        acct, list(to) or [""], subject, body, cc, bcc, None, attachments,
        in_reply_to, references,
    )
    raw = msg.as_bytes()
    target = folder or acct.drafts
    ok, detail = _imap_append(
        acct, target, raw, [MailMessageFlags.DRAFT, MailMessageFlags.SEEN]
    )
    if not ok:
        raise RuntimeError("could not save draft: " + detail)
    return {
        "status": "draft_saved",
        "folder": target,
        "subject": subject,
        "to": list(to or []),
        "bytes": len(raw),
    }


def build_reply(
    acct,
    uid,
    body,
    folder="INBOX",
    reply_all=False,
    attachments=None,
    quote_original=False,
):
    """Compose a reply payload from an existing message. Does not send."""
    with _open(acct).login(acct.user, acct.password, initial_folder=folder) as mb:
        target = None
        for m in mb.fetch(A(uid=uid), headers_only=False, limit=1, mark_seen=False):
            target = m
            break
    if target is None:
        raise ValueError("uid %s not found in %s" % (uid, folder))

    subject = target.subject or ""
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    to = [target.from_] if target.from_ else []
    if reply_all:
        seen = {x.lower() for x in to}
        for addr in list(target.to or ()) + list(target.cc or ()):
            low = (addr or "").lower()
            if low and low not in seen and acct.user.lower() not in low:
                seen.add(low)
                to.append(addr)

    headers = target.headers or {}
    msg_id = (headers.get("message-id") or [None])[0]
    refs = (headers.get("references") or [None])[0]

    text = body
    if quote_original:
        orig = (target.text or "").strip()
        if orig:
            quoted = "\n".join("> " + line for line in orig.splitlines())
            text = "%s\n\nOn %s, %s wrote:\n%s" % (
                body,
                target.date.isoformat() if target.date else "an earlier date",
                target.from_,
                quoted,
            )

    reference_chain = " ".join([x for x in (refs, msg_id) if x]) or None

    return {
        "to": to,
        "subject": subject,
        "body": text,
        "in_reply_to": msg_id,
        "references": reference_chain,
        "attachments": attachments or [],
        "original_from": target.from_,
        "original_subject": target.subject,
    }
