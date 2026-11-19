# imap-mail-client

A multi-account IMAP + SMTP client built for **agents first, humans second**.

Every command prints JSON on stdout, takes flags an agent can compose without
guessing, and fails loudly instead of silently doing nothing. Works against any
IMAP provider — Hostinger, Gmail, Fastmail, your own Dovecot.

```
search ─┐
read   ─┤
folders ─┼─→ triage ─→ junk / move / flag ─→ archive
bounces ─┘

send  ─→ new outbound mail (always copied to Sent)
draft ─→ compose without sending
reply ─→ thread-aware reply, send or draft
```

---

## Why this exists

Most IMAP tooling is either a raw protocol client — you hand-write `\Seen \Deleted`
strings and get burned by provider quirks — or an MCP server that has to be
spawned with a static environment block, which makes credential handling awkward.

This is a **CLI**. It reads credentials at runtime from a file, so:

- secrets never enter editor settings
- the project folder is self-contained and copies between machines
- an agent drives it through the normal terminal tool, no MCP plumbing

It also handles the sharp edges that only show up in real use — see
[Behaviour worth knowing](#behaviour-worth-knowing).

---

## Requirements

- Python 3.8+ (tested on 3.13)
- One dependency: `imap-tools`
- Network access to your mail host

---

## Install

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt    # Windows
# .venv/bin/python -m pip install -r requirements.txt          # macOS/Linux
```

Verify:

```bash
.venv/Scripts/python.exe mailctl.py accounts
```

---

## Credentials

Create `.env` next to the code (gitignored):

```ini
MC__DEV_HOST=imap.hostinger.com
MC__DEV_PORT=993
MC__DEV_USER=dev@yourdomain.com
MC__DEV_PASSWORD=your-password
```

Optional keys, with their defaults:

```ini
# MC__DEV_SENT=INBOX.Sent
# MC__DEV_JUNK=INBOX.Junk
# MC__DEV_DRAFTS=INBOX.Drafts
# MC__DEV_BOUNCES=INBOX.Bounces
# MC__DEV_TLS=implicit          # or starttls
# MC__DEV_SMTP_HOST=smtp.hostinger.com   # derived from HOST if omitted
# MC__DEV_SMTP_PORT=465
# MC__DEV_SMTP_TLS=true
```

**Naming:** `MC__<ACCOUNT>_<FIELD>`. The account part is arbitrary and lowercased
for `-a`. `MC__OFFICE_SALES_USER` → `--account office_sales`.

**Multiple accounts:** add more blocks. Set `MAIL_DEFAULT_ACCOUNT=dev` to skip
`-a` when only one should be the default.

### Where the env file is looked up

1. `$MAIL_ENV_FILE` — explicit override
2. `<project>/.env` — **recommended**; gitignored
3. `%USERPROFILE%/.mailclient/accounts.env` — fallback for shared secrets

---

## Commands

Global flags `-a/--account` and `-f/--folder` work **before or after** the
subcommand.

### Reading

| Command | What it does |
|---|---|
| `accounts` | list configured mailboxes (passwords never shown) |
| `folders` | every folder with message / unseen counts |
| `search` | filter by sender, recipient, subject, body, date, flag |
| `read` | full message by UID |
| `bounces` | MAILER-DAEMON / postmaster notifications |

```bash
P=".venv/Scripts/python.exe"

$P mailctl.py accounts
$P mailctl.py folders -a dev
$P mailctl.py search -a dev --since 7d --limit 20
$P mailctl.py search -a dev --from boss@x.com --include-body
$P mailctl.py search -a dev --unread
$P mailctl.py search -a dev --subject invoice --since 30d
$P mailctl.py read -a dev --uid 4711
$P mailctl.py bounces -a dev --since 14d
```

Dates accept `YYYY-MM-DD` or relative `7d` / `2w` / `3m`.

`search --compact` emits single-line JSON — cheaper on tokens when an agent is
scanning a long list.

### Organising

| Command | What it does |
|---|---|
| `move` | move by UID list, or by search criteria |
| `junk` | move into the junk folder, or back out (`--not-junk`) |
| `mkdir` | create a folder (idempotent) |
| `flag` | add / remove IMAP flags |
| `delete` | soft-delete by default; `--purge` to expunge |

```bash
$P mailctl.py mkdir -a dev INBOX.Archive/2026
$P mailctl.py move -a dev --uids 4711,4712 --to INBOX.Archive
$P mailctl.py move -a dev --search-from spam@x.com --to INBOX.Archive
$P mailctl.py junk -a dev --uids 4711
$P mailctl.py junk -a dev --uids 4711 --not-junk
$P mailctl.py flag -a dev --uids 4711 --add '\Flagged'
$P mailctl.py flag -a dev --uids 4711 --remove '\Seen'
$P mailctl.py delete -a dev --uids 4711
$P mailctl.py delete -a dev --uids 4711 --purge
```

### Sending

```bash
# new mail
$P mailctl.py send -a dev --to x@y.com --subject "Hello" --body "Text"
$P mailctl.py send -a dev --to x@y.com --to z@y.com --subject "S" --body-file -  < body.txt
$P mailctl.py send -a dev --to x@y.com --subject "S" --body "B" --attach report.pdf

# compose without sending
$P mailctl.py send -a dev --to x@y.com --subject "S" --body "B" --dry-run

# save instead of send
$P mailctl.py draft -a dev --to x@y.com --subject "S" --body "B"

# reply
$P mailctl.py reply -a dev --uid 4711 --body "Thanks"              # -> Drafts
$P mailctl.py reply -a dev --uid 4711 --body "Thanks" --send       # -> sent
$P mailctl.py reply -a dev --uid 4711 --body "Thanks" --dry-run    # -> preview only
$P mailctl.py reply -a dev --uid 4711 --body "Ok" --reply-all --quote --send
```

**`send` always appends a copy to `INBOX.Sent`.** Without that, Hostinger webmail
shows an empty Sent folder. The result reports `saved_to_sent` and, when it
fails, `sent_detail` — the failure is never swallowed.

Replies set `In-Reply-To` and `References` so they thread correctly.
`--body-file -` reads from stdin, which avoids shell-quoting issues on long or
multi-line bodies.

---

## Behaviour worth knowing

These are the sharp edges, all found in real use rather than documentation.

**Reads never mark messages as seen.** Every fetch uses `BODY.PEEK` semantics,
so searching an inbox does not destroy unread state.

**UIDs are not stable.** Hostinger reassigns UIDs when a message is moved or
re-delivered. Never cache a UID across a move — re-search by subject or sender.
`move` verifies UIDs exist first and reports `missing_uids` rather than claiming
a false success:

```
error: none of these UIDs exist in INBOX: 235 (UIDs change after a move - re-search)
```

**Junk folder names vary.** Your configured `JUNK` is tried first, then
`INBOX.Junk`, `INBOX.Spam`, `Junk`, `Spam`. No need to know the exact spelling.

**Moves may imply `\Seen`.** Per RFC 3501 some servers mark moved messages as
seen. If unread state matters, set it explicitly after the move with `flag`.

**`delete` is soft by default.** It sets `\Deleted`; the message stays until an
EXPUNGE. Only `--purge` removes it.

**Spam keywords are server-side.** `Junk`/`NonJunk` keywords may already be
present on arrival — they come from the provider's classifier, not from this
tool.

---

## Output

Everything is JSON. Errors go to stderr with a non-zero exit:

```
error: destination folder 'INBOX.Nope' does not exist (create it first)
```

Exit codes: `0` success, `1` runtime error, `2` bad arguments.

---

## Tests

72 tests, no network, no real mailbox. Provider behaviours (UID reassignment,
missing folders) are modelled by in-memory fakes, so regressions fail here
instead of corrupting mail.

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest
```

| File | What it protects |
|---|---|
| `test_mailenv.py` | account parsing, defaults, multi-account, error messages |
| `test_mail_ops.py` | the stale-UID guard, junk fallback, flags, soft delete |
| `test_mail_send.py` | MIME building, Sent-append reporting, drafts, reply threading |
| `test_cli.py` | exit codes, JSON shape, UTF-8 output, send gating |

Two tests encode bugs found in live testing and must never regress:

- a move with an unknown UID raises instead of silently doing nothing
- `reply` saves a draft unless `--send` is passed

### Integration testing (optional)

To exercise a real server, copy `.env.example` to `.env` with real credentials
and run any command against your own account — start with `accounts`, then
`folders`, then a read-only `search`. Mutating commands are safe to try in a
scratch folder:

```bash
$P mailctl.py mkdir -a dev INBOX.ZZTest
$P mailctl.py move  -a dev --uids <uid> --to INBOX.ZZTest
$P mailctl.py move  -a dev -f INBOX.ZZTest --uids <uid> --to INBOX
```

---

## Using it with an AI agent

The JSON output and explicit error messages exist so an agent can drive this
without guessing. Two things make that safe:

1. **`--dry-run`** on `send` and `reply` composes and reports without sending.
2. **`reply` drafts by default**; `--send` is required to actually send.

A skill file for Zed lives in `skill/imap-mail/SKILL.md` — copy it to
`~/.agents/skills/imap-mail/` to have the agent pick it up automatically.

---

## Layout

```
mailctl.py        CLI entry point and dispatch
mail_ops.py       IMAP: search, read, folders, move, junk, flag, delete, bounces
mail_send.py      SMTP send, drafts, reply composition
mailenv.py        credential + account resolution
tests/            offline test suite + IMAP fakes
skill/            agent skill for Zed
requirements.txt  runtime dependency
requirements-dev.txt  adds pytest
.env.example      template with no secrets
```

---

## License

MIT — see [LICENSE](LICENSE).
