# requirements-pin-checker-cli

Check every requirements.txt entry is pinned with ==, fail CI on unpinned.

[![CI](https://github.com/haiderstudio/requirements-pin-checker-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/haiderstudio/requirements-pin-checker-cli/actions/workflows/ci.yml) [![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)](LICENSE)

## What it does

`requirements_pin_checker` reads a `requirements.txt` file and checks
every entry is pinned to an exact version with `==`. It reports each
unpinned entry with its file and line number, and exits 1 when any
entry is unpinned, so a CI step can fail the build until the list is
fully pinned. It exits 0 when every entry is pinned.

An entry counts as pinned only when its version is exactly one `==`
clause with an exact version — `requests==2.31.0` passes, with or
without extras (`requests[security]==2.31.0`) or a marker
(`requests==2.31.0; python_version < "3.9"`). Everything else is
reported, with the reason named:

- bare name — `flask` → "no version"
- `>=`, `>`, `<`, `<=`, `!=`, `~=` — `django>=4.2` → "uses >= instead of =="
- `===` — "uses === instead of =="
- wildcard pin — `numpy==1.26.*` → "wildcard pin (==1.26.*), not an exact version"
- a pin plus a range — `pkg==2.31.0,>=2.0` → "not an exact == pin"
- direct reference — `mypkg @ https://...` → "direct reference (no == pin)"

It is report-only: it never writes or changes any file.

- It follows `-r` include lines, relative to the file that names
  them, and reports the real file and line of each entry. An include
  cycle is an error, not a silent loop.
- It skips comments, blank lines, and option lines (`-e`, `-c`,
  `--index-url`, ...) — they name no package.
- `--allow` skips named packages, for the rare entry that is meant
  to float.
- `--json` prints the result as JSON on stdout, with the summary on
  standard error, so the JSON stays parseable.
- It runs fully offline: it sends nothing anywhere and needs no API
  key.

## What it does NOT do

- It does not write `requirements.txt`. It reports unpinned entries;
  pinning them is your call.
- It does not check whether a pinned version exists on PyPI, or
  whether it is the newest. It checks the file, not the package
  index.
- It does not check whether a declared package is installed, or
  which version is installed.
- It does not check imports against requirements — that is
  [import-sync-cli](https://github.com/haiderstudio/import-sync-cli),
  which compares code with the file. This tool checks the file
  itself.
- It does not read `-c` constraints files. A constraints file is a
  different list, and it is skipped, not checked.

## How this differs

- `wummel/python-update-checker` (10 stars) *updates* pinned
  dependencies to newer versions — it rewrites the file. This tool
  never rewrites anything; it only reports what is not pinned.
- `YashikKhunt/pypi-dependency-checker` (0 stars) checks pinned
  requirements against the PyPI JSON API — it needs the network.
  This tool reads the file only, offline.
- `pinnochio` enforces pinning for UV projects using `pyproject.toml`,
  not `requirements.txt`. This tool checks `requirements.txt` only.
- A pinned check is a different job from a drift check: import-sync
  asks whether the code and the file agree; this tool asks whether
  the file itself is pinned.

## Install

```sh
git clone https://github.com/haiderstudio/requirements-pin-checker-cli.git
cd requirements-pin-checker-cli
```

No install step, no dependencies — Python 3.12 or newer, standard
library only.

## Usage

Check the default file, `requirements.txt`:

```sh
python3 src/requirements_pin_checker.py
```

Check a named file:

```sh
python3 src/requirements_pin_checker.py examples/mixed-requirements.txt
```

```
UNPINNED  examples/mixed-requirements.txt:3: flask — no version
UNPINNED  examples/mixed-requirements.txt:4: django — uses >= instead of ==
UNPINNED  examples/included.txt:2: celery — uses >= instead of ==
6 checked, 3 unpinned
```

Exit code 1 — three entries are not pinned, and the `-r included.txt`
line was followed, so the entry in the included file is reported with
its own file and line.

Check a file that is fully pinned:

```sh
python3 src/requirements_pin_checker.py examples/pinned-requirements.txt
```

```
OK — all 5 entries pinned.
```

Exit code 0.

Skip a package that is meant to float:

```sh
python3 src/requirements_pin_checker.py --allow flask examples/mixed-requirements.txt
```

```
UNPINNED  examples/mixed-requirements.txt:4: django — uses >= instead of ==
UNPINNED  examples/included.txt:2: celery — uses >= instead of ==
6 checked, 2 unpinned
```

Machine-readable output for CI:

```sh
python3 src/requirements_pin_checker.py --json examples/mixed-requirements.txt
```

```json
{
  "checked": 6,
  "unpinned": 3,
  "pass": false,
  "entries": [
    {
      "name": "flask",
      "file": "examples/mixed-requirements.txt",
      "line": 3,
      "pinned": false,
      "reason": "no version"
    },
    {
      "name": "django",
      "file": "examples/mixed-requirements.txt",
      "line": 4,
      "pinned": false,
      "reason": "uses >= instead of =="
    },
    {
      "name": "celery",
      "file": "examples/included.txt",
      "line": 2,
      "pinned": false,
      "reason": "uses >= instead of =="
    }
  ]
}
```

The summary line (`6 checked, 3 unpinned`) goes to standard error,
so stdout is clean JSON.

## Examples

### A file with every kind of unpinned entry

```
requests==2.31.0
numpy==1.26.*
celery~=5.3
mypkg @ https://example.com/mypkg.tar.gz
```

```sh
python3 src/requirements_pin_checker.py pins-demo.txt
```

```
UNPINNED  pins-demo.txt:2: numpy — wildcard pin (==1.26.*), not an exact version
UNPINNED  pins-demo.txt:3: celery — uses ~= instead of ==
UNPINNED  pins-demo.txt:4: mypkg — direct reference (no == pin)
4 checked, 3 unpinned
```

Exit code 1. Only the first entry is pinned.

### A file with an include

`examples/mixed-requirements.txt` has a `-r included.txt` line on
line 5. The check follows it and reports the included entries with
their own file and line — `examples/included.txt:2: celery`. An
include cycle (`a.txt` includes `b.txt` includes `a.txt`) is an
error, exit code 2, not a silent loop.

### A file that passes

`examples/pinned-requirements.txt` has five entries, all pinned
with `==`, and the check passes with exit code 0:

```
OK — all 5 entries pinned.
```

## How it is tested

```sh
python3 tests/test_requirements_pin_checker.py
```

Tests, standard library `unittest` only — no third-party packages.
They cover: the pin rule (exact `==`, bare names, `>=`, `~=`, `!=`,
`>`, `<`, `<=`, `===`, wildcard pins, pin-plus-range, unreadable
specs), comment stripping, requirement parsing (extras, markers,
direct references, bad names), file reading (blank lines, comments,
option lines, `-r` includes, include cycles, missing files,
non-UTF-8 files), the `--allow` flag, multiple files, the CLI
(exit 0/1/2, `--json` output, `--version`, bad flags, the default
file), the bundled examples (the committed
`examples/sample-output.txt` is compared against a fresh run, and
the README's quoted outputs are checked against it), the example
script, and a check that the code and the tests import standard
library only.

The bundled example is a real run, not made-up output:

```sh
sh examples/run-example.sh
```

It checks the pinned file (passes) and the mixed file (fails on
purpose). The committed `examples/sample-output.txt` is the output
of the mixed-file check.

## Contributing

Small, focused improvements are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT — see [LICENSE](LICENSE).

## Original work

Built from scratch for this repo. The requirements parsing is plain
Python string handling written for this tool; no code was copied
from any existing pin checker. The idea of checking a requirements
file is an old one — the nearest tools update pins or check them
against PyPI — and this tool's slice (pinned-with-`==` only,
report-only, offline, fails CI) is its own.
