#!/usr/bin/env python3
"""sigantry: name gate.

Fails when a file in the repository -- or a member of a built wheel or
sdist -- carries a name from a token list that is held OUTSIDE the
repository. The list is private because naming what must not appear
would itself publish it, and the tokens are short enough that a hashed
list could be reversed with a dictionary, so the list is not hashed:
it is kept out of the tree and handed to CI as a repository secret.

What is scanned (no path, basename or suffix is exempt):

- every tracked file and every untracked file that is not ignored
  (``git ls-files --cached --others --exclude-standard``);
- text files line by line;
- PDF text layers (through ``pdftotext``) and printable runs of the raw
  PDF bytes, so uncompressed metadata is seen too;
- zip containers (``.pptx``, ``.docx``, ``.xlsx``, wheels): each XML
  member both as written and with its tags removed, so a word split
  across formatting runs is still seen; other members recursively;
- gzip tar archives (sdists) recursively;
- with ``--archive``, a built wheel or sdist instead of the tree. Its
  units are named ``wheel!<member>`` or ``sdist!<member>``, with the
  version taken out of member paths (the sdist's ``<name>-<version>/`` top
  directory dropped, the wheel's ``<name>-<version>.dist-info/`` written
  ``<name>.dist-info/``), so an exception survives a version bump and can
  never be confused with a tree file.

Other binary files (a NUL byte in the first 8000 bytes, as git decides)
are counted and skipped. A PDF whose text cannot be extracted is a
failure, not a skip.

Output is ``<file>:<line> <pattern id>`` only. The matched text is never
printed, so a CI log does not republish what the gate exists to keep out.

The list format (one entry per line; ``#`` comments and blank lines are
ignored)::

    name-gate-list v1
    marker <anything>                  # ignored by the scanner
    token <ID> <regular expression>
    exception <file>:<line> <ID>

An ``exception`` excuses exactly one hit: that pattern at that line of
that file (a container member is written ``<file>!<member>``, and a
derived text unit carries a ``#`` suffix, e.g. ``#pdf-text``). An
exception that matches no hit is stale and fails the run, so the
register cannot drift away from the lines it excuses. A tree run checks
the tree entries and an ``--archive`` run the ``wheel!``/``sdist!``
entries, so neither reads the other's entries as stale.

The list is read from the ``NAME_GATE_TOKENS`` environment variable, or
from ``--list-file``. The value may be plain text or gzip + base64 (for
lists near the size limit of a secret). When no list is available the
gate FAILS: a pull request from a fork, or from a bot, does not receive
repository secrets, and a gate that passed without its list would pass
everything.

Usage:
    python scripts/ci/check-name-gate.py                    # the repo tree
    python scripts/ci/check-name-gate.py --root <path>
    python scripts/ci/check-name-gate.py --archive dist/x.whl --archive dist/x.tar.gz
    python scripts/ci/check-name-gate.py --list-file <private list>

Exit codes:
    0 -- no unexcused hit and no stale exception.
    1 -- at least one unexcused hit, stale exception or unreadable PDF.
    2 -- the list is unavailable or invalid, or the tree cannot be listed.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import bisect
import gzip
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

ENV_VAR = "NAME_GATE_TOKENS"
ARCHIVE_KINDS = ("wheel", "sdist")
LIST_HEADER = "name-gate-list v1"
UNREADABLE_ID = "UNREADABLE"
BINARY_PROBE_BYTES = 8000
MAX_NESTING = 4
_TAG = re.compile(r"<[^>]*>")
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e]{8,}")
_WHEEL_VERSIONED_DIR = re.compile(r"!([A-Za-z0-9_.]+?)-[^/!-]+\.(dist-info|data)/")


class ListError(Exception):
    """The token list is missing or invalid. The message never quotes it."""


@dataclass(frozen=True)
class Hit:
    unit: str
    line: int
    pattern_id: str

    def render(self) -> str:
        return f"{self.unit}:{self.line} {self.pattern_id}"


@dataclass
class TokenList:
    tokens: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    exceptions: set[Hit] = field(default_factory=set)


@dataclass
class Report:
    files: int = 0
    binary_skipped: int = 0
    hits: list[Hit] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def decode_payload(raw: str) -> str:
    """Return the list text from plain text or gzip + base64."""
    text = raw.strip()
    if text.startswith(LIST_HEADER):
        return text
    try:
        packed = base64.b64decode("".join(text.split()), validate=True)
        unpacked = gzip.decompress(packed).decode("utf-8")
    except (binascii.Error, OSError, EOFError, UnicodeDecodeError, ValueError):
        raise ListError("the list is neither plain text nor gzip + base64") from None
    if not unpacked.strip().startswith(LIST_HEADER):
        raise ListError("the decoded list has no header line") from None
    return unpacked.strip()


def parse_list(text: str) -> TokenList:
    """Parse the list. Errors name a list line number, never its content."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != LIST_HEADER:
        raise ListError(f"the first line must be {LIST_HEADER!r}")
    parsed = TokenList()
    seen_ids: set[str] = set()
    for number, line in enumerate(lines[1:], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kind, _, rest = stripped.partition(" ")
        if kind == "marker":
            continue
        if kind == "token":
            pattern_id, _, expression = rest.strip().partition(" ")
            if not pattern_id or not expression.strip():
                raise ListError(f"list line {number}: a token needs an id and a pattern")
            if pattern_id in seen_ids:
                raise ListError(f"list line {number}: duplicate token id")
            try:
                compiled = re.compile(expression.strip())
            except re.error:
                raise ListError(f"list line {number}: the pattern does not compile") from None
            if compiled.search(""):
                raise ListError(f"list line {number}: the pattern matches the empty string")
            seen_ids.add(pattern_id)
            parsed.tokens.append((pattern_id, compiled))
        elif kind == "exception":
            location, _, pattern_id = rest.strip().rpartition(" ")
            unit, _, line_text = location.rpartition(":")
            if not unit or not line_text.isdigit() or not pattern_id:
                raise ListError(f"list line {number}: an exception is '<file>:<line> <id>'")
            parsed.exceptions.add(Hit(unit, int(line_text), pattern_id))
        else:
            raise ListError(f"list line {number}: unknown entry kind")
    if not parsed.tokens:
        raise ListError("the list holds no tokens")
    unknown = {e.pattern_id for e in parsed.exceptions} - seen_ids
    if unknown:
        raise ListError(f"{len(unknown)} exception(s) name a token id the list does not define")
    return parsed


def load_list(list_file: str | None) -> TokenList:
    if list_file:
        try:
            raw = Path(list_file).read_text(encoding="utf-8")
        except OSError:
            raise ListError("--list-file cannot be read") from None
    else:
        raw = os.environ.get(ENV_VAR, "")
        if not raw.strip():
            raise ListError(
                f"{ENV_VAR} is empty or unset. A pull request from a fork or a bot "
                "does not receive repository secrets, so the gate cannot run and "
                "fails closed. Push the change to a branch of this repository."
            )
    return parse_list(decode_payload(raw))


# ---------------------------------------------------------------------------
# Units: (name, text) pairs extracted from one file's bytes
# ---------------------------------------------------------------------------


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_PROBE_BYTES]


def _decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str | None:
    """Text layer through pdftotext, or None when it cannot be extracted."""
    tool = shutil.which("pdftotext")
    if tool is None:
        return None
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "in.pdf"
        source.write_bytes(data)
        result = subprocess.run(
            [tool, "-q", "-enc", "UTF-8", str(source), "-"],
            capture_output=True,
            check=False,
        )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def extract_units(data: bytes, name: str, depth: int = 0) -> Iterator[tuple[str, str | None]]:
    """Yield ``(unit name, text)``. ``text`` is None for an unreadable unit.

    A binary file that is not a known container yields nothing.
    """
    if depth > MAX_NESTING:
        yield (name, None)
        return
    if data.startswith(b"%PDF"):
        text = _pdf_text(data)
        yield (f"{name}#pdf-text", text)
        raw = "\n".join(run.decode("ascii") for run in _PRINTABLE_RUN.findall(data))
        yield (f"{name}#pdf-raw", raw)
        return
    if data.startswith(b"PK\x03\x04"):
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
            members = [(info.filename, archive.read(info)) for info in archive.infolist()]
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError):
            yield (name, None)
            return
        for member, content in members:
            if member.endswith("/"):
                continue
            unit = f"{name}!{member}"
            if member.lower().endswith((".xml", ".rels")) or content.startswith(b"<?xml"):
                text = _decode_text(content)
                yield (unit, text)
                yield (f"{unit}#text", _TAG.sub("", text))
            else:
                yield from extract_units(content, unit, depth + 1)
        return
    if data.startswith(b"\x1f\x8b"):
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                members_tar: list[tuple[str, bytes]] = []
                for info in tar.getmembers():
                    if not info.isfile():
                        continue
                    handle = tar.extractfile(info)
                    if handle is not None:
                        members_tar.append((info.name, handle.read()))
        except (tarfile.TarError, OSError, EOFError):
            # A lone gzip file, not a tar: scan what it decompresses to.
            try:
                inner = gzip.decompress(data)
            except (OSError, EOFError):
                yield (name, None)
                return
            yield from extract_units(inner, f"{name}#gunzip", depth + 1)
            return
        for member, content in members_tar:
            yield from extract_units(content, f"{name}!{member}", depth + 1)
        return
    if _is_binary(data):
        return
    yield (name, _decode_text(data))


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_text(unit: str, text: str, tokens: list[tuple[str, re.Pattern[str]]]) -> set[Hit]:
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    hits: set[Hit] = set()
    for pattern_id, pattern in tokens:
        for match in pattern.finditer(text):
            line = bisect.bisect_right(starts, match.start())
            hits.add(Hit(unit, line, pattern_id))
    return hits


def scan_bytes(
    data: bytes, name: str, tokens: list[tuple[str, re.Pattern[str]]], report: Report
) -> None:
    report.files += 1
    produced = False
    for unit, text in extract_units(data, name):
        produced = True
        if text is None:
            report.hits.append(Hit(unit, 0, UNREADABLE_ID))
            continue
        report.hits.extend(scan_text(unit, text, tokens))
    if not produced:
        report.binary_skipped += 1


def list_tree(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ListError("git ls-files failed; is --root a git work tree?")
    names = [n for n in result.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n]
    return sorted(set(names))


def scan_tree(root: Path, tokens: list[tuple[str, re.Pattern[str]]], report: Report) -> None:
    for relative in list_tree(root):
        path = root / relative
        if path.is_symlink():
            data = os.readlink(path).encode("utf-8", errors="surrogateescape")
        elif path.is_file():
            data = path.read_bytes()
        else:
            continue  # tracked but deleted in the work tree
        scan_bytes(data, relative, tokens, report)


def archive_kind(path: Path) -> str:
    """``wheel`` for a ``.whl``, ``sdist`` for a ``.tar.gz``; else refuse."""
    if path.name.endswith(".whl"):
        return "wheel"
    if path.name.endswith(".tar.gz"):
        return "sdist"
    raise ListError("--archive takes a .whl or a .tar.gz")


def scan_archive(path: Path, tokens: list[tuple[str, re.Pattern[str]]], report: Report) -> None:
    """Scan a built wheel or sdist, naming its units by kind, not version."""
    kind = archive_kind(path)
    data = path.read_bytes()
    report.files += 1
    for unit, text in extract_units(data, kind):
        if kind == "sdist" and unit.startswith("sdist!"):
            # Drop the versioned top directory: sdist!pkg-1.2.3/README.md -> sdist!README.md
            _top, sep, member = unit[len("sdist!") :].partition("/")
            unit = f"sdist!{member}" if sep else unit
        elif kind == "wheel":
            # Unversion the metadata directories: pkg-1.2.3.dist-info/ -> pkg.dist-info/
            unit = _WHEEL_VERSIONED_DIR.sub(r"!\1.\2/", unit, count=1)
        if text is None:
            report.hits.append(Hit(unit, 0, UNREADABLE_ID))
            continue
        report.hits.extend(scan_text(unit, text, tokens))


def in_scope(entry: Hit, kinds: tuple[str, ...]) -> bool:
    """Is this register entry checked by a run over ``kinds`` (empty = the tree)?"""
    prefix = entry.unit.split("!", 1)[0]
    if not kinds:
        return prefix not in ARCHIVE_KINDS
    return prefix in kinds


def evaluate(report: Report, exceptions: set[Hit]) -> tuple[list[Hit], list[Hit], int]:
    """Return (unexcused hits, stale exceptions, excused count)."""
    found = set(report.hits)
    unexcused = sorted(found - exceptions, key=lambda h: (h.unit, h.line, h.pattern_id))
    stale = sorted(exceptions - found, key=lambda h: (h.unit, h.line, h.pattern_id))
    return unexcused, stale, len(found & exceptions)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--root", default=".", help="git work tree to scan (default: .)")
    parser.add_argument(
        "--archive",
        action="append",
        default=[],
        help="also scan a built wheel or sdist (repeatable); skips the tree scan",
    )
    parser.add_argument("--list-file", help="read the list from this file instead of the env var")
    args = parser.parse_args(argv)

    try:
        token_list = load_list(args.list_file)
    except ListError as exc:
        print(f"name-gate: ERROR: {exc}", file=sys.stderr)
        return 2

    report = Report()
    try:
        if args.archive:
            for archive in args.archive:
                scan_archive(Path(archive), token_list.tokens, report)
        else:
            scan_tree(Path(args.root), token_list.tokens, report)
    except ListError as exc:
        print(f"name-gate: ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"name-gate: ERROR: cannot read a file to scan ({exc.strerror})", file=sys.stderr)
        return 2

    kinds = tuple(sorted({archive_kind(Path(a)) for a in args.archive}))
    exceptions = {e for e in token_list.exceptions if in_scope(e, kinds)}
    unexcused, stale, excused = evaluate(report, exceptions)
    for hit in unexcused:
        print(f"name-gate: {hit.render()}")
    for entry in stale:
        print(f"name-gate: stale exception {entry.render()}")
    print(
        f"name-gate: {report.files} file(s) scanned, {report.binary_skipped} binary skipped, "
        f"{len(unexcused)} unexcused hit(s), {excused} excused, {len(stale)} stale exception(s)"
    )
    return 1 if unexcused or stale else 0


if __name__ == "__main__":
    sys.exit(main())
