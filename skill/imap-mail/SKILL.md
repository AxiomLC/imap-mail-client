---
name: imap-mail
description: Read, triage, search, move, junk, draft, reply and send email over IMAP/SMTP using the local imap-mail-client CLI. Use when asked to check email, find messages, clean up or organise a mailbox, mark spam, reply to a message, or send a new email. Works with Hostinger, Gmail, Fastmail and any IMAP provider.
disable-model-invocation: false
---

# IMAP mail client

This skill drives `mailctl.py` from the **imap-mail-client** project. Every
command prints JSON on stdout; errors go to stderr with a non-zero exit code.

## Locate the project

The project is a folder named `imap-mail-client` containing `mailctl.py`.
Find it once, then use absolute paths for the rest of the session:

```bash
# adjust if it lives elsewhere
D="/path/to/imap-mail-client"
P="$D/.venv/Scripts/python.exe"      # Windows
# P="$D/.venv/bin/python"            # macOS/Linux
```

Verify before doing anything else:

```bash
"$P" "$D/mailctl.py" accounts
```

If that fails, the environment or `.env` is not set up — say so. Do not guess at
credentials, and never ask the user to paste a password into chat.

## Accounts

- `-a/--account` is optional when exactly one mailbox is configured.
- Multiple accounts → always pass `-a`, or set `MAIL_DEFAULT_ACCOUNT`.
- Run `accounts` if you are unsure of the name. Never invent one.

## Reading

```bash
"$P" "$D/mailctl.py" folders  -a dev
"$P" "$D/mailctl.py" search   -a dev --since 7d --limit 20
"$P" "$D/mailctl.py" search   -a dev --from alice@x.com --include-body
"$P" "$D/mailctl.py" search   -a dev --unread
"$P" "$D/mailctl.py" search   -a dev --subject invoice --since 30d
"$P" "$D/mailctl.py" read     -a dev --uid 4711
"$P" "$D/mailctl.py" bounces  -a dev --since 14d
```

Dates: `YYYY-MM-DD`, or relative `7d` / `2w` / `3m`.
Add `--compact` to `search` for single-line JSON when the list is long.

Reads never mark messages seen. Safe to run freely.

## Organising

```bash
"$P" "$D/mailctl.py" mkdir -a dev INBOX.Archive/2026
"$P" "$D/mailctl.py" move  -a dev --uids 4711,4712 --to INBOX.Archive
"$P" "$D/mailctl.py" move  -a dev --search-from spam@x.com --to INBOX.Archive
"$P" "$D/mailctl.py" junk  -a dev --uids 4711
"$P" "$D/mailctl.py" junk  -a dev --uids 4711 --not-junk
"$P" "$D/mailctl.py" flag  -a dev --uids 4711 --add '\Flagged'
"$P" "$D/mailctl.py" delete -a dev --uids 4711
```

**Critical — UIDs are unstable.** Some providers (Hostinger) reassign UIDs after
a move or re-delivery. Rules:

1. Never reuse a UID from an earlier tool call after a move.
2. Re-search by subject or sender to get fresh UIDs.
3. `move` reports `missing_uids` when a UID vanished — treat that as "nothing
   moved" and re-search; do not assume success.

**Destination must exist.** `move`/`junk` refuse unknown folders. Run `mkdir`
first.

**Junk folder name is auto-detected** (configured name, then `INBOX.Junk`,
`INBOX.Spam`, `Junk`, `Spam`). Do not hardcode a folder name.

**`delete` is soft.** It sets `\Deleted`; the message remains until an EXPUNGE.
Only pass `--purge` when the user explicitly asked for permanent removal, and
say clearly that it cannot be undone.

## Sending

```bash
# preview first — no network send
"$P" "$D/mailctl.py" send -a dev --to x@y.com --subject "S" --body "B" --dry-run

# real send
"$P" "$D/mailctl.py" send -a dev --to x@y.com --subject "S" --body "B"
"$P" "$D/mailctl.py" send -a dev --to x@y.com --subject "S" --body-file - < body.txt
"$P" "$D/mailctl.py" send -a dev --to x@y.com --subject "S" --body "B" --attach file.pdf
```

Replies:

```bash
"$P" "$D/mailctl.py" reply -a dev --uid 4711 --body "Thanks"            # -> Drafts
"$P" "$D/mailctl.py" reply -a dev --uid 4711 --body "Thanks" --dry-run  # -> preview
"$P" "$D/mailctl.py" reply -a dev --uid 4711 --body "Thanks" --send     # -> sent
"$P" "$D/mailctl.py" reply -a dev --uid 4711 --body "Ok" --reply-all --quote --send
```

## Rules for sending — read before acting

Sending is the one irreversible action available here. Do **not** send without
explicit approval from the user in the current conversation:

1. Compose with `--dry-run` and show the result (`to`, `subject`, body).
2. Get an explicit "yes / send it".
3. Re-run without `--dry-run` (or with `--send` for replies).

If the user says "reply to X", that is approval to **draft**, not necessarily to
send. Default to saving a draft, and say where it went. Send only when the user
clearly wants it sent.

Check `saved_to_sent` in the result. If `false`, the mail left but the Sent copy
failed — report that honestly rather than saying "done".

Never send to an address inferred from message content unless the user confirmed
it.

## Presenting results in chat

The user wants clean lists, not raw dumps. Convert JSON to a compact table:

```
UID   Date        From                  Subject
4711  2026-11-17  events@example.com    Dec 1 webinar: 20 lessons...
4712  2026-11-16  marketing@example.ai  When the Semantic Backbone...
```

Then summarise: count, date range, anything notable (bounces, unread).
Do not paste raw JSON unless asked or a UID is needed to act on.

## Common tasks

**"Check my email"** → `folders` for unread counts, then `search --unread`.

**"Clean up spam"** → search the sender/subject, list matches, confirm with the
user, then `junk --uids ...`.

**"Archive everything older than 6 months"** → `mkdir` the target, then `move
--since` with a date, reporting the count moved.

**"Reply to that"** → `read` it, `reply --dry-run`, show the draft, await
approval.

**"Did the email bounce?"** → `bounces --since 7d`.

## Known provider quirks

- Some providers reassign UIDs on move/re-delivery (see above).
- Move may set `\Seen` — re-set with `flag --remove '\Seen'` if unread state
  matters.
- `Junk`/`NonJunk` keywords may appear on arrival from the provider's spam
  classifier; a move does not cause them.

## Troubleshooting

| Symptom | Meaning |
|---|---|
| `no accounts configured` | `.env` missing or keys not `MC__<ACCT>_*` |
| `multiple accounts configured` | pass `-a NAME` |
| `unknown account 'x'` | run `accounts` for valid names |
| `AUTHENTICATIONFAILED` | wrong password in `.env`; ask the user to fix it |
| `destination folder ... does not exist` | run `mkdir` first |
| `none of these UIDs exist` | stale UID after a move — re-search |

Never fix an auth failure by editing `.env` yourself. Report it and let the user
update the credential.
