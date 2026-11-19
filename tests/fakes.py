"""In-memory IMAP doubles so tests never touch a real mailbox.

Models the provider behaviours that caused real bugs during development:
  - UIDs get reassigned when a message moves (Hostinger)
  - a folder may not exist under the name the account config expects
"""

from __future__ import annotations


class FakeMessage:
    def __init__(self, uid, subject="", from_="a@b.com", to=("me@x.com",),
                 cc=(), bcc=(), flags=()):
        self.uid = str(uid)
        self.subject = subject
        self.from_ = from_
        self.to = tuple(to)
        self.cc = tuple(cc)
        self.bcc = tuple(bcc)
        self.flags = tuple(flags)
        self.date = None
        self.size = 100
        self.attachments = []
        self.text = "body text"
        self.html = None
        self.headers = {"message-id": ["<%s@fake>" % uid]}


class FakeFolderManager:
    def __init__(self, box):
        self._box = box

    def exists(self, name):
        return name in self._box.folders

    def create(self, name):
        self._box.folders.setdefault(name, [])

    def list(self, *a, **k):
        return [FakeFolderInfo(n) for n in self._box.folders]

    def status(self, name):
        return {"MESSAGES": len(self._box.folders.get(name, [])), "UNSEEN": 0}

    def delete(self, name):
        self._box.folders.pop(name, None)


class FakeFolderInfo:
    def __init__(self, name):
        self.name = name
        self.delim = "."
        self.flags = ()


class FakeMailbox:
    """Minimal in-memory mailbox collection keyed by folder name."""

    def __init__(self, messages=None, folders=None, current="INBOX"):
        self.folders = {n: [] for n in (folders or [])}
        for name in ("INBOX", "INBOX.Sent", "INBOX.Junk", "INBOX.Drafts", "INBOX.Bounces"):
            self.folders.setdefault(name, [])
        for msg in messages or []:
            self.folders["INBOX"].append(msg)
        self.current = current
        self.appended = []

    @property
    def folder(self):
        return FakeFolderManager(self)

    def messages(self, folder=None):
        return self.folders.get(folder or self.current, [])

    def uids(self, criteria=None, limit=None):
        msgs = self.messages()
        # Honour a UID restriction so the stale-UID guard can be tested.
        # imap_tools collapses A(uid=[...]) into a query string like "(UID 1,2)".
        text = str(criteria or "")
        if "UID " in text.upper():
            import re as _re
            m = _re.search(r"UID ([0-9,:*]+)", text, _re.IGNORECASE)
            if m:
                wanted = set()
                for part in m.group(1).split(","):
                    part = part.strip()
                    if part.isdigit():
                        wanted.add(part)
                    elif ":" in part:
                        lo, _, hi = part.partition(":")
                        if lo.isdigit() and hi.isdigit():
                            wanted.update(str(n) for n in range(int(lo), int(hi) + 1))
                msgs = [msg for msg in msgs if msg.uid in wanted]
        return [m.uid for m in msgs]

    def fetch(self, criteria=None, headers_only=False, limit=None, reverse=False,
              mark_seen=False, uid_list=None):
        msgs = list(self.messages())
        # imap_tools collapses A(uid=..) to a query string; honour it so
        # build_reply selects the message it actually asked for.
        text = str(criteria or "")
        if "UID " in text.upper():
            import re as _re
            m = _re.search(r"UID ([0-9,:*]+)", text, _re.IGNORECASE)
            if m:
                wanted = set()
                for part in m.group(1).split(","):
                    part = part.strip()
                    if part.isdigit():
                        wanted.add(part)
                    elif ":" in part:
                        lo, _, hi = part.partition(":")
                        if lo.isdigit() and hi.isdigit():
                            wanted.update(str(n) for n in range(int(lo), int(hi) + 1))
                msgs = [msg for msg in msgs if msg.uid in wanted]
        if reverse:
            msgs.reverse()
        if limit:
            msgs = msgs[:limit]
        return iter(msgs)

    def move(self, uids, dest, chunks=50):
        if dest not in self.folders:
            raise ValueError("no such folder %r" % dest)
        ids = {str(u) for u in uids}
        src = self.messages()
        moving = [m for m in src if m.uid in ids]
        self.folders[self.current] = [m for m in src if m.uid not in ids]
        self.folders[dest].extend(moving)

    def flag(self, uids, flags, value):
        ids = {str(u) for u in uids}
        for m in self.messages():
            if m.uid in ids:
                m.flags = tuple(set(m.flags) | set(flags)) if value else tuple(
                    f for f in m.flags if f not in flags
                )

    def delete(self, uids):
        ids = {str(u) for u in uids}
        self.folders[self.current] = [m for m in self.messages() if m.uid not in ids]

    def append(self, raw, folder, flag_set=None, dt=None):
        self.appended.append({"folder": folder, "raw": raw, "flags": flag_set})
        self.folders.setdefault(folder, [])

    def select(self, folder):
        self.current = folder


class FakeContext:
    """Stands in for the object returned by MailBox.login()."""

    def __init__(self, box):
        self.box = box

    def __enter__(self):
        return self.box

    def __exit__(self, *exc):
        return False


def fake_login(box):
    def _login(*args, **kwargs):
        initial = kwargs.get("initial_folder")
        if initial:
            box.current = initial
        return FakeContext(box)

    return _login
