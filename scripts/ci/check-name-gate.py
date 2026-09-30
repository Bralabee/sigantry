#!/usr/bin/env python3
"""sigantry: name gate.

Fails when the repository -- or a built wheel or sdist -- carries a name
from a token list that is held OUTSIDE the repository. The list is
private because naming what must not appear would itself publish it,
and the tokens are short enough that a hashed list could be reversed
with a dictionary, so the list is not hashed: it is kept out of the tree
and handed to CI as a repository secret.

What it is for, and what it is not
----------------------------------
It catches names written plainly: the accidental case, where a name
reaches a file, a file name, a document's metadata or a build artifact
because nobody noticed. It is not built to find a name hidden on
purpose (encoded, obfuscated, or assembled by code). What it cannot
read is reported, never skipped: an unreadable document fails the run,
and a binary file it has no reader for fails until the register accepts
that file by name.

What is read (no path, basename or suffix is exempt):

- the path of every tracked file and of every untracked file that is
  not ignored (``git ls-files --cached --others --exclude-standard``),
  and the path of every member inside a container;
- text: UTF-8 (with or without a byte-order mark), UTF-16 and UTF-32
  with a byte-order mark, and cp1252 where UTF-8 decoding fails. Line
  ends are normalised (CRLF and CR to LF), then every line is read as
  written and again with markup tags removed and character references
  and ``\\uXXXX`` escapes decoded, so ``a<b>c</b>``, ``&#8217;`` and
  ``\\u2019`` read as text;
- PDFs (``%PDF-`` and a version digit in the first 1024 bytes): the text
  layer (``pdftotext``), the document information including custom keys
  and the XMP metadata (``pdfinfo``), and printable runs of the raw bytes;
- zip containers (``.pptx``, ``.docx``, ``.xlsx``, ``.zip``, wheels) and
  gzip tar archives (sdists), member by member: every member's name,
  directories and links included, a link's target, zip comments and tar
  pax headers. A member that cannot be read, or a tar with data after its
  last readable member, fails the run. An uncompressed tar is a binary
  file with no reader;
- a git LFS pointer is UNREADABLE: the checkout holds the pointer, not
  the file;
- with ``--archive``, a built wheel or sdist instead of the tree. Its
  units are named ``wheel!<member>`` or ``sdist!<member>``, with the
  version taken out of member paths (the sdist's ``<name>-<version>/`` top
  directory dropped, the wheel's ``<name>-<version>.dist-info/`` written
  ``<name>.dist-info/``), so an exception survives a version bump and can
  never be confused with a tree file.

A file is binary when its first 8000 bytes hold a control byte other
than tab, line and page breaks, SUB and ESC (a NUL included), unless it
starts with a UTF-16 or UTF-32 byte-order mark.

Output is ``<unit>:<line> <pattern id>``. Line 0 means the path itself.
The matched text is never printed: a path component that matches a
token is printed as ``~`` plus the first 12 hex digits of its SHA-256,
and if the printed unit would still match a token (a name split by
``#`` or ``!``), the whole unit is printed that way.
The ids ``UNREADABLE`` (a document or container that could not be read)
and ``BINARY`` (a binary file with no reader) are the gate's own.

The list format (one entry per line; lines starting with ``#`` and blank
lines are ignored; there are no trailing comments)::

    name-gate-list v1
    marker <anything>
    token <ID> <regular expression>
    exception <unit>:<line> <ID>

Token ids are letters, digits, ``_`` and ``-``; ``UNREADABLE`` and
``BINARY`` are reserved. Patterns are compiled with ``re.MULTILINE``,
so ``^`` and ``$`` anchor at line ends. An ``exception`` excuses exactly
one hit: that pattern at that line of that unit, written with the real
path even where the output redacts it (a container member is
``<file>!<member>``; a derived unit carries a ``#`` suffix, e.g.
``#pdf-text``). ``exception <file>:0 BINARY`` accepts one binary file.
An exception that matches no hit is stale and fails the run, so the
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
    1 -- at least one unexcused hit (UNREADABLE and BINARY included) or
         stale exception.
    2 -- the list is unavailable or invalid, the tree cannot be listed, or
         an --archive argument is not a wheel or sdist.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import bisect
import gzip
import hashlib
import html
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

ENV_VAR = "NAME_GATE_TOKENS"
LIST_HEADER = "name-gate-list v1"
UNREADABLE_ID = "UNREADABLE"
BINARY_ID = "BINARY"
RESERVED_IDS = frozenset({UNREADABLE_ID, BINARY_ID})
ARCHIVE_KINDS = ("wheel", "sdist")
BINARY_PROBE_BYTES = 8000
PDF_HEADER_WINDOW = 1024
MAX_NESTING = 4
TOOL_TIMEOUT_SECONDS = 120
_ID = re.compile(r"[A-Za-z0-9_-]+")
_DIGITS = re.compile(r"[0-9]+")
_TRAILING_COMMENT = re.compile(r"\s#")
_TAG = re.compile(r"<[^<>\n]*>")
_LINE_BREAKS = re.compile(r"[\r\n]")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_CONTROL_BYTE = re.compile(rb"[\x00-\x08\x0e-\x19\x1c-\x1f]")
_PDF_HEADER = re.compile(rb"%PDF-[0-9]")
_LFS_POINTER = b"version https://git-lfs.github.com/spec/"
_UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e]{8,}")
_WHEEL_VERSIONED_DIR = re.compile(r"!([A-Za-z0-9_.]+?)-[^/!-]+\.(dist-info|data)/")

Tokens = list[tuple[str, "re.Pattern[str]"]]


class GateError(Exception):
    """The run cannot be judged. Messages never quote the list."""


@dataclass(frozen=True)
class Hit:
    unit: str
    line: int
    pattern_id: str


@dataclass
class TokenList:
    tokens: Tokens = field(default_factory=list)
    exceptions: set[Hit] = field(default_factory=set)


@dataclass
class Report:
    files: int = 0
    hits: set[Hit] = field(default_factory=set)


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
    except (binascii.Error, OSError, EOFError, UnicodeDecodeError, ValueError, zlib.error):
        raise GateError("the list is neither plain text nor gzip + base64") from None
    if not unpacked.strip().startswith(LIST_HEADER):
        raise GateError("the decoded list has no header line") from None
    return unpacked.strip()


def parse_list(text: str) -> TokenList:
    """Parse the list. Errors name a list line number, never its content."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != LIST_HEADER:
        raise GateError(f"the first line must be {LIST_HEADER!r}")
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
            expression = expression.strip()
            if not _ID.fullmatch(pattern_id) or not expression:
                raise GateError(f"list line {number}: a token needs an id and a pattern")
            if pattern_id in RESERVED_IDS:
                raise GateError(f"list line {number}: that token id is reserved")
            if pattern_id in seen_ids:
                raise GateError(f"list line {number}: duplicate token id")
            if _TRAILING_COMMENT.search(expression):
                raise GateError(
                    f"list line {number}: whitespace before '#' in a pattern reads as a "
                    "trailing comment, which the format does not have; write [#]"
                )
            try:
                compiled = re.compile(expression, re.MULTILINE)
            except re.error:
                raise GateError(f"list line {number}: the pattern does not compile") from None
            if compiled.search(""):
                raise GateError(f"list line {number}: the pattern matches the empty string")
            seen_ids.add(pattern_id)
            parsed.tokens.append((pattern_id, compiled))
        elif kind == "exception":
            location, _, pattern_id = rest.strip().rpartition(" ")
            unit, _, line_text = location.rpartition(":")
            if not unit or not _DIGITS.fullmatch(line_text) or not pattern_id:
                raise GateError(f"list line {number}: an exception is '<unit>:<line> <id>'")
            if pattern_id == UNREADABLE_ID:
                raise GateError(f"list line {number}: an unreadable unit cannot be excused")
            if pattern_id == BINARY_ID and int(line_text) != 0:
                raise GateError(f"list line {number}: a BINARY exception is '<file>:0 BINARY'")
            parsed.exceptions.add(Hit(unit, int(line_text), pattern_id))
        else:
            raise GateError(f"list line {number}: unknown entry kind")
    if not parsed.tokens:
        raise GateError("the list holds no tokens")
    unknown = {e.pattern_id for e in parsed.exceptions} - seen_ids - {BINARY_ID}
    if unknown:
        raise GateError(f"{len(unknown)} exception(s) name a token id the list does not define")
    return parsed


def load_list(list_file: str | None) -> TokenList:
    if list_file:
        try:
            raw = Path(list_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise GateError("--list-file cannot be read") from None
    else:
        raw = os.environ.get(ENV_VAR, "")
        if not raw.strip():
            raise GateError(
                f"{ENV_VAR} is empty or unset. A pull request from a fork or a bot "
                "does not receive repository secrets, so the gate cannot run and "
                "fails closed. Push the change to a branch of this repository."
            )
    return parse_list(decode_payload(raw))


# ---------------------------------------------------------------------------
# Reading: bytes -> units of (name, texts) or a verdict on the unit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    """One readable piece of a file. ``texts`` is None when it cannot be read."""

    name: str
    texts: tuple[str, ...] | None
    binary: bool = False


def _decodings(data: bytes) -> tuple[str, ...]:
    if data.startswith(_UTF32_BOMS):
        return (data.decode("utf-32", errors="replace"),)
    if data.startswith(_UTF16_BOMS):
        return (data.decode("utf-16", errors="replace"),)
    try:
        return (data.decode("utf-8-sig"),)
    except UnicodeDecodeError:
        return (
            data.decode("utf-8-sig", errors="replace"),
            data.decode("cp1252", errors="replace"),
        )


def _unescape_line(line: str) -> str:
    line = html.unescape(_TAG.sub("", line))
    line = _UNICODE_ESCAPE.sub(lambda m: chr(int(m[1], 16)), line)
    return _LINE_BREAKS.sub(" ", line)


def _read_as_text(text: str) -> str:
    """Each line with tags removed and escapes decoded; the line count is kept."""
    return "\n".join(_unescape_line(line) for line in text.split("\n"))


def _is_binary(data: bytes) -> bool:
    if data.startswith(_UTF32_BOMS + _UTF16_BOMS):
        return False
    return _CONTROL_BYTE.search(data[:BINARY_PROBE_BYTES]) is not None


def _run_tool(tool: str, args: list[str], data: bytes) -> str | None:
    """Run a poppler tool on ``data``; None when it is missing, fails or hangs."""
    path = shutil.which(tool)
    if path is None:
        return None
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "in.pdf"
        source.write_bytes(data)
        try:
            result = subprocess.run(
                [path, *args, str(source), *(["-"] if tool == "pdftotext" else [])],
                capture_output=True,
                check=False,
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def _pdf_units(data: bytes, name: str) -> Iterator[Unit]:
    text = _run_tool("pdftotext", ["-q", "-enc", "UTF-8"], data)
    yield Unit(f"{name}#pdf-text", None if text is None else (text,))
    info = _run_tool("pdfinfo", ["-custom", "-enc", "UTF-8"], data)
    xmp = _run_tool("pdfinfo", ["-meta"], data)
    meta = None if info is None or xmp is None else (info + "\n" + xmp,)
    yield Unit(f"{name}#pdf-meta", meta)
    raw = "\n".join(run.decode("ascii") for run in _PRINTABLE_RUN.findall(data))
    yield Unit(f"{name}#pdf-raw", (raw,))


def _zip_units(data: bytes, name: str, depth: int) -> Iterator[Unit]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError):
        yield Unit(name, None)
        return
    if archive.comment:
        yield Unit(f"{name}#comment", (archive.comment.decode("utf-8", errors="replace"),))
    for info in infos:
        unit = f"{name}!{info.filename}"
        yield Unit(unit, (info.filename,), binary=True)  # the member's path, as line 0
        if info.comment:
            yield Unit(f"{unit}#comment", (info.comment.decode("utf-8", errors="replace"),))
        if info.is_dir():
            continue
        try:
            content = archive.read(info)
        except Exception:  # any member that cannot be read is reported, never skipped
            yield Unit(unit, None)
            continue
        yield from extract_units(content, unit, depth + 1)


class _NotATarError(Exception):
    pass


@dataclass(frozen=True)
class _TarMember:
    name: str
    content: bytes | None  # a regular file's bytes; None when it could not be read
    is_file: bool
    extra: str  # a link's target and any pax header values, as text


def _read_tar(raw: bytes) -> tuple[list[_TarMember], bytes]:
    """Members of an uncompressed tar, and the bytes after the last one read.

    Raises ``_NotATarError`` when ``raw`` does not start as a tar, and
    ``tarfile.TarError``/``OSError`` when it breaks part-way.
    """
    try:
        tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")  # noqa: SIM115 -- closed below
    except tarfile.TarError:
        raise _NotATarError from None
    with tar:
        members: list[_TarMember] = []
        for info in tar.getmembers():
            extra = "\n".join([info.linkname, *(f"{k}={v}" for k, v in info.pax_headers.items())])
            content: bytes | None = None
            if info.isfile():
                handle = tar.extractfile(info)
                content = None if handle is None else handle.read()
            members.append(_TarMember(info.name, content, info.isfile(), extra.strip()))
        return members, raw[tar.offset :]


def _gzip_units(data: bytes, name: str, depth: int) -> Iterator[Unit]:
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError, zlib.error):
        yield Unit(name, None)
        return
    try:
        members, tail = _read_tar(raw)
    except _NotATarError:
        # A lone gzip file, not a tar: read what it decompresses to.
        yield from extract_units(raw, f"{name}#gunzip", depth + 1)
        return
    except (tarfile.TarError, OSError, EOFError):
        yield Unit(name, None)
        return
    for member in members:
        unit = f"{name}!{member.name}"
        yield Unit(unit, (member.name,), binary=True)  # the member's path, as line 0
        if member.extra:
            yield Unit(f"{unit}#tar-header", (member.extra,))
        if not member.is_file:
            continue
        if member.content is None:
            yield Unit(unit, None)
            continue
        yield from extract_units(member.content, unit, depth + 1)
    if tail.strip(b"\0"):
        yield Unit(f"{name}#tar-tail", None)


def extract_units(data: bytes, name: str, depth: int = 0) -> Iterator[Unit]:
    """Yield the readable units of ``data``.

    A path unit (``binary=True`` with the path as its only text) marks a
    container member's name, which is scanned at line 0. A binary file
    with no reader yields a single ``Unit(name, None, binary=True)``.
    """
    if depth > MAX_NESTING:
        yield Unit(name, None)
        return
    if data.startswith(_LFS_POINTER):
        yield Unit(name, None)
        return
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        yield from _zip_units(data, name, depth)
        return
    if data.startswith(b"\x1f\x8b"):
        yield from _gzip_units(data, name, depth)
        return
    if data[257:262] == b"ustar":  # an uncompressed tar: no reader
        yield Unit(name, None, binary=True)
        return
    if _PDF_HEADER.search(data[:PDF_HEADER_WINDOW]):
        yield from _pdf_units(data, name)
        return
    if _is_binary(data):
        yield Unit(name, None, binary=True)
        return
    yield Unit(name, _decodings(data))


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_text(unit: str, text: str, tokens: Tokens) -> set[Hit]:
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "")
    hits: set[Hit] = set()
    for variant in (text, _read_as_text(text)):
        starts = [0] + [m.end() for m in re.finditer("\n", variant)]
        for pattern_id, pattern in tokens:
            for match in pattern.finditer(variant):
                hits.add(Hit(unit, bisect.bisect_right(starts, match.start()), pattern_id))
    return hits


def scan_path(unit: str, path: str, tokens: Tokens) -> set[Hit]:
    return {Hit(unit, 0, pattern_id) for pattern_id, p in tokens if p.search(path)}


def scan_units(
    units: Iterator[Unit], tokens: Tokens, report: Report, rename: Callable[[str], str]
) -> None:
    for unit in units:
        name = rename(unit.name)
        if unit.binary and unit.texts is not None:
            report.hits |= scan_path(name, unit.texts[0], tokens)
        elif unit.texts is None:
            report.hits.add(Hit(name, 0, BINARY_ID if unit.binary else UNREADABLE_ID))
        else:
            for text in unit.texts:
                report.hits |= scan_text(name, text, tokens)


def list_tree(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise GateError("git ls-files failed; is --root a git work tree?")
    names = [n for n in result.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n]
    return sorted(set(names))


def scan_tree(root: Path, tokens: Tokens, report: Report) -> None:
    for relative in list_tree(root):
        report.files += 1
        report.hits |= scan_path(relative, relative, tokens)
        path = root / relative
        if path.is_symlink():
            data = os.readlink(path).encode("utf-8", errors="surrogateescape")
        elif path.is_file():
            data = path.read_bytes()
        else:
            continue  # a gitlink, or tracked but deleted in the work tree: the path was read
        scan_units(extract_units(data, relative), tokens, report, lambda n: n)


def archive_kind(path: Path, data: bytes) -> str:
    """``wheel`` for a zip ``.whl``, ``sdist`` for a gzip ``.tar.gz``; else refuse."""
    if path.name.endswith(".whl") and data.startswith(b"PK\x03\x04"):
        return "wheel"
    if path.name.endswith(".tar.gz") and data.startswith(b"\x1f\x8b"):
        try:
            inner = gzip.decompress(data)
        except (OSError, EOFError, zlib.error):
            raise GateError("--archive sdist is not a readable gzip") from None
        if inner[257:262] != b"ustar":
            raise GateError("--archive sdist is not a gzip tar")
        return "sdist"
    raise GateError("--archive takes a wheel (.whl, zip) or an sdist (.tar.gz, gzip)")


def _unversion(kind: str) -> Callable[[str], str]:
    def rename(unit: str) -> str:
        if kind == "sdist" and unit.startswith("sdist!"):
            # sdist!pkg-1.2.3/README.md -> sdist!README.md
            _top, sep, member = unit[len("sdist!") :].partition("/")
            return f"sdist!{member}" if sep else unit
        if kind == "wheel":
            # wheel!pkg-1.2.3.dist-info/METADATA -> wheel!pkg.dist-info/METADATA
            return _WHEEL_VERSIONED_DIR.sub(r"!\1.\2/", unit, count=1)
        return unit

    return rename


def scan_archive(path: Path, tokens: Tokens, report: Report) -> str:
    """Scan a built wheel or sdist, naming its units by kind, not version."""
    try:
        data = path.read_bytes()
    except OSError:
        raise GateError("an --archive argument cannot be read") from None
    kind = archive_kind(path, data)
    report.files += 1
    scan_units(extract_units(data, kind), tokens, report, _unversion(kind))
    return kind


# ---------------------------------------------------------------------------
# Verdict and output
# ---------------------------------------------------------------------------


def in_scope(entry: Hit, kinds: tuple[str, ...]) -> bool:
    """Is this register entry checked by a run over ``kinds`` (empty = the tree)?"""
    prefix = entry.unit.split("!", 1)[0]
    if not kinds:
        return prefix not in ARCHIVE_KINDS
    return prefix in kinds


def _order(hit: Hit) -> tuple[str, int, str]:
    return (hit.unit, hit.line, hit.pattern_id)


def evaluate(report: Report, exceptions: set[Hit]) -> tuple[list[Hit], list[Hit], int]:
    """Return (unexcused hits, stale exceptions, excused count)."""
    unexcused = sorted(report.hits - exceptions, key=_order)
    stale = sorted(exceptions - report.hits, key=_order)
    return unexcused, stale, len(report.hits & exceptions)


def display(unit: str, tokens: Tokens) -> str:
    """The unit with every path component that matches a token redacted."""
    parts = []
    for component in unit.split("!"):
        path, sep, suffix = component.partition("#")
        if any(p.search(path) for _, p in tokens):
            path = (
                "~"
                + hashlib.sha256(path.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]
            )
        parts.append(path + sep + suffix)
    shown = "!".join(parts)
    if any(p.search(shown) for _, p in tokens):
        # A name split across '#' or '!' survives the per-component check.
        shown = (
            "~" + hashlib.sha256(unit.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]
        )
    return shown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--root", default=".", help="git work tree to scan (default: .)")
    parser.add_argument(
        "--archive",
        action="append",
        default=[],
        help="scan a built wheel or sdist instead of the tree (repeatable)",
    )
    parser.add_argument("--list-file", help="read the list from this file instead of the env var")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")

    report = Report()
    try:
        token_list = load_list(args.list_file)
        scanned_kinds: set[str] = set()
        for archive in args.archive:
            scanned_kinds.add(scan_archive(Path(archive), token_list.tokens, report))
        kinds = tuple(sorted(scanned_kinds))
        if not args.archive:
            scan_tree(Path(args.root), token_list.tokens, report)
    except GateError as exc:
        print(f"name-gate: ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"name-gate: ERROR: cannot read a file to scan ({exc.strerror})", file=sys.stderr)
        return 2

    exceptions = {e for e in token_list.exceptions if in_scope(e, kinds)}
    unexcused, stale, excused = evaluate(report, exceptions)
    tokens = token_list.tokens
    for hit in unexcused:
        print(f"name-gate: {display(hit.unit, tokens)}:{hit.line} {hit.pattern_id}")
    for entry in stale:
        print(
            f"name-gate: stale exception {display(entry.unit, tokens)}:{entry.line} {entry.pattern_id}"
        )
    print(
        f"name-gate: {report.files} file(s) scanned, {len(unexcused)} unexcused hit(s), "
        f"{excused} excused, {len(stale)} stale exception(s)"
    )
    return 1 if unexcused or stale else 0


if __name__ == "__main__":
    sys.exit(main())
