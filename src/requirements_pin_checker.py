"""requirements-pin-checker-cli: check every entry in requirements.txt
is pinned to an exact version with ``==``.

One job only: read one or more requirements files, report every entry
that is not pinned with ``==`` (bare name, ``>=``, ``~=``, ``>``, ``<``,
``<=``, ``!=``, ``===``, wildcard pins like ``==2.31.*``, direct
references), with the file and line of each one. Exit 1 when any entry
is unpinned, so a CI step can fail the build. Exit 0 when every entry
is pinned. Exit 2 on a usage problem (missing file, unparsable line,
include cycle, bad flag).

Report-only: it never writes or changes any file.

Standard library only.
"""

import argparse
import re
import sys
from pathlib import Path

VERSION = "1.0.0"

# A valid package name (PEP 508): letters, digits, ``.``, ``-``, ``_``.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# One version clause, e.g. ``==2.31.0`` or ``>=2.0``.
CLAUSE_RE = re.compile(r"^(===|==|~=|!=|>=|<=|>|<)\s*([^\s]+)$")
# ``name[extras] spec`` — the spec part may be empty.
REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$"
)


class PinError(Exception):
    """A requirements file that cannot be read or parsed."""


class Entry:
    """One requirement entry, with where it came from."""

    def __init__(self, name, line, source, pinned, reason):
        self.name = name
        self.line = line
        self.source = source
        self.pinned = pinned
        self.reason = reason

    def where(self):
        return "%s:%d" % (self.source, self.line)


def strip_comment(line):
    """Cut a ``#`` comment: a ``#`` at the start of the line, or one
    preceded by whitespace. A ``#`` inside a word (a URL fragment) is
    left alone."""
    for i, ch in enumerate(line):
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def check_spec(spec):
    """Decide whether a version specifier is an exact ``==`` pin.

    Returns ``(pinned, reason)``. ``reason`` is empty when pinned.
    Pinned means exactly one clause, ``==``, with an exact version —
    no wildcard, no extra range."""
    spec = spec.strip()
    if not spec:
        return False, "no version"
    clauses = [c.strip() for c in spec.split(",") if c.strip()]
    if len(clauses) == 1:
        match = CLAUSE_RE.match(clauses[0])
        if match:
            op, version = match.group(1), match.group(2)
            if op == "==":
                if version.endswith(".*"):
                    return False, "wildcard pin (%s), not an exact version" % clauses[0]
                return True, ""
            return False, "uses %s instead of ==" % op
    # More than one clause, or a clause we cannot read: not an exact pin.
    first = clauses[0] if clauses else spec
    match = CLAUSE_RE.match(first)
    if match and match.group(1) == "==":
        return False, "not an exact == pin (%s)" % spec
    if match:
        return False, "uses %s instead of ==" % match.group(1)
    return False, "cannot read version spec (%s)" % spec


def parse_requirement(text, line, source):
    """Parse one requirement line into an Entry.

    Raises PinError on a line that is not a requirement at all."""
    # Environment markers come after the first ``;``.
    spec_part = text.split(";", 1)[0].strip()
    if " @ " in spec_part:
        name = spec_part.split(" @ ", 1)[0].strip()
        if not NAME_RE.match(name):
            raise PinError("%s:%d: cannot read requirement %r" % (source, line, text))
        return Entry(name, line, source, False, "direct reference (no == pin)")
    match = REQUIREMENT_RE.match(spec_part)
    if not match:
        raise PinError("%s:%d: cannot read requirement %r" % (source, line, text))
    name, _extras, spec = match.group(1), match.group(2), match.group(3)
    pinned, reason = check_spec(spec)
    return Entry(name, line, source, pinned, reason)


def read_requirements(path, seen=None):
    """Read a requirements file and return a list of Entry objects.

    ``-r`` include lines are followed, relative to the file that names
    them. An include cycle is an error. Option lines (``-e``, ``-c``,
    ``--index-url``, ...) are skipped: they name no package."""
    path = Path(path)
    if seen is None:
        seen = set()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved in seen:
        raise PinError("include cycle: %s includes itself" % path)
    seen = seen | {resolved}
    if not path.is_file():
        raise PinError("no such file: %s" % path)
    entries = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise PinError("%s: not UTF-8 text" % path)
    for number, raw in enumerate(text.splitlines(), start=1):
        line = strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("-"):
            parts = line.split(None, 1)
            flag = parts[0]
            if flag in ("-r", "--requirement"):
                if len(parts) != 2 or not parts[1].strip():
                    raise PinError("%s:%d: -r with no file" % (path, number))
                target = parts[1].strip()
                include = path.parent / target
                entries.extend(read_requirements(include, seen))
            # Every other option line names no package: skip it.
            continue
        entries.append(parse_requirement(line, number, str(path)))
    return entries


def check_files(files, allowed):
    """Check every file. Returns (entries, unpinned)."""
    entries = []
    for f in files:
        entries.extend(read_requirements(f))
    unpinned = [e for e in entries if not e.pinned and e.name not in allowed]
    return entries, unpinned


def format_text(entries, unpinned):
    lines = []
    for entry in unpinned:
        lines.append(
            "UNPINNED  %s: %s — %s" % (entry.where(), entry.name, entry.reason)
        )
    if unpinned:
        lines.append(
            "%d checked, %d unpinned" % (len(entries), len(unpinned))
        )
    else:
        lines.append("OK — all %d entries pinned." % len(entries))
    return "\n".join(lines) + "\n"


def format_json(entries, unpinned):
    import json

    data = {
        "checked": len(entries),
        "unpinned": len(unpinned),
        "pass": not unpinned,
        "entries": [
            {
                "name": e.name,
                "file": e.source,
                "line": e.line,
                "pinned": e.pinned,
                "reason": e.reason,
            }
            for e in unpinned
        ],
    }
    return json.dumps(data, indent=2) + "\n"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="requirements-pin-checker",
        description="Check every entry in requirements.txt is pinned with ==.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        default=["requirements.txt"],
        help="requirements file(s) to check (default: requirements.txt)",
    )
    parser.add_argument(
        "--allow",
        default="",
        help="comma-separated package names to skip, e.g. --allow pip,setuptools",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the result as JSON on stdout (summary goes to stderr)",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + VERSION
    )
    return parser


def run(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on a bad flag and 0 on --version; return the
        # code instead of raising, so the CLI is testable as a function.
        return exc.code if isinstance(exc.code, int) else 2
    allowed = {n.strip() for n in args.allow.split(",") if n.strip()}
    try:
        entries, unpinned = check_files(args.files, allowed)
    except PinError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(format_json(entries, unpinned), end="")
        summary = "%d checked, %d unpinned" % (len(entries), len(unpinned))
        if not unpinned:
            summary = "OK — all %d entries pinned." % len(entries)
        print(summary, file=sys.stderr)
    else:
        print(format_text(entries, unpinned), end="")
    return 1 if unpinned else 0


def main():
    sys.exit(run())


if __name__ == "__main__":
    main()
