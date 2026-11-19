# Contributions are welcome. Keep the suite green.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Windows
# .venv/bin/python -m pip install -r requirements-dev.txt         # macOS/Linux
```

## Running tests

```bash
.venv/Scripts/python.exe -m pytest
```

The suite is fully offline. `tests/fakes.py` provides in-memory IMAP doubles;
no credentials or network are needed.

**Do not** add tests that hit a real mail server to the default run. If you want
to exercise a live account, do it manually per the README's integration section.

## Ground rules

- **Never swallow errors.** If a send succeeds but the Sent append fails, report
  it (`saved_to_sent: false`). Silent partial success is the worst outcome for
  an agent caller.
- **Never silently no-op.** A move with unknown UIDs must raise, not pretend.
- **Never mark messages seen on read.** Use `mark_seen=False`.
- **Destructive commands are opt-in.** `delete` is soft unless `--purge`.
- **No new dependencies** without a strong reason. The single runtime dependency
  is deliberate.

## Style

- stdlib-first, `%`-style formatting in error paths for py3.8 compatibility
- 4-space indent, LF line endings (see `.editorconfig`)
- type hints on public functions
- tests one behaviour per function, named for the behaviour not the method

## Reporting issues

Include:

- provider (Hostinger, Gmail, ...)
- the command you ran, with secrets redacted
- the exact stdout/stderr and exit code

Never paste real credentials or `.env` contents into an issue.
