# File-boundary encoding policy

crm reads and writes files, and shells out to external tools, on Linux, WSL, and
Windows. Text that crosses that boundary must behave identically everywhere —
a CSV saved by Excel, a spec authored on a cp1252 Windows machine, and a clean
UTF-8 file from Linux must all import the same. These rules keep the file
boundary deterministic; apply them mechanically at every new read/capture site.

## Reads — explicit UTF-8, BOM-tolerant

Every text file crm reads uses an **explicit** encoding — never the process
locale default (`open(path)` / `Path.read_text()` with no `encoding=` inherits
the OS locale, so a cp1252 machine misreads a UTF-8 file).

- **`encoding="utf-8-sig"`** for files a user or external editor may author
  (CSV, JSONL, YAML/JSON specs, FetchXML, request bodies). `utf-8-sig` strips a
  leading UTF-8 BOM if present and reads pure UTF-8 unchanged, so "saved from
  Excel" is not a failure mode.
- Click file options get the encoding on the type:
  `click.File("r", encoding="utf-8-sig")`.

## Subprocess captures — pin the decode

Capturing another tool's output with `text=True` alone decodes with the locale
default. Pin it:

```python
subprocess.run(argv, capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=timeout)
```

`errors="replace"` keeps a stray non-UTF-8 byte from crashing the capture — the
output is for humans and logs, so a replacement char beats an exception.

## Writes — plain UTF-8, no BOM

Writes stay plain `encoding="utf-8"`. crm never emits a BOM; a BOM tolerated on
read must not become a BOM re-emitted on write.

## Value fidelity across the boundary

Reading the right bytes is not enough — a value's type must survive too. CSV
cells are coerced best-effort by shape (integer-looking → int, etc.), which is
wrong for a value whose identity is a string. The rule: **consult metadata, don't
guess from the value's shape.** An alternate-key column typed as a string keeps
its verbatim CSV text (an account number `"10023"`, a leading-zero code) so it
builds the correct quoted key URL instead of a bare numeric one that misses.
