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
- PDFs: a file that starts with ``%PDF-`` and a version digit (after
  whitespace), or a binary file (by the rule below) with that header in
  its first 1024 bytes. For each: the text layer (``pdftotext``), the
  document information including custom keys and the XMP metadata
  (``pdfinfo``), runs of printable bytes (high bytes included, read as
  cp1252) from the raw file, and, when it holds no control byte, its text.
  A text file that mentions or embeds the header further in is read as
  text only;
- zip containers (``.pptx``, ``.docx``, ``.xlsx``, ``.zip``, wheels) and
  gzip tar archives (sdists), member by member: every member's name,
  directories and links included, a link's target, zip comments, tar pax
  headers and a tar member's owner and group names. A member that cannot
  be read, or a tar with data after its last readable member, fails the
  run. An uncompressed tar is a binary file with no reader;
- a git LFS pointer is UNREADABLE: the checkout holds the pointer, not
  the file;
- with ``--dist``, or with ``--archive`` and ``--root``, built wheels and
  sdists AS WELL AS the tree at ``--root`` (``.`` when ``--dist`` is given
  without it), which is scanned first: an artifact is judged together
  with the tree it was built from, in one run with one verdict.
  ``--dist DIR`` is what a release runs: DIR must hold exactly one wheel
  (``.whl``) and one sdist (``.tar.gz``) and nothing else, dot files
  included, because that directory is what the upload sends. Each
  artifact's file name is read as a path under the unit ``wheel`` or
  ``sdist``. Its members are named ``wheel!<member>`` or
  ``sdist!<member>``, with the version taken out of member paths (the
  sdist's ``<name>-<version>/`` top directory dropped, the wheel's
  ``<name>-<version>.dist-info/`` written ``<name>.dist-info/``), so an
  exception survives a version bump and can never be confused with a tree
  file. Every member is read; three kinds are reported differently from
  other text:

  * a member whose bytes are identical to the file at the same path in
    the tree (a wheel's ``<name>.dist-info/licenses/<path>`` is at
    ``<path>``, where PEP 639 says it was taken from) has exactly that
    file's hits, which this run has already reported under the tree
    file's name and judged by the tree file's register entries, so its
    content is not reported a second time. Its member path, link target
    and headers still are. The same bytes at another path are read as any
    other member;
  * the core metadata (a wheel's ``<name>.dist-info/METADATA``, an
    sdist's top-level ``PKG-INFO``) is read whole, and a hit in it is
    reported by where it sits rather than by its line: in the header
    block as ``<unit>#<Field>:<n>``, the ``n``-th line carrying that
    field (``METADATA#Author:1``), except that a field the core metadata
    specification marks multiple-use (``Classifier``, ``Requires-Dist``,
    ``Project-URL`` and the others it lists) is keyed by its entry, as
    ``<unit>#<Field>@<digest>:<n>``: the digest is twelve decimal digits
    of the SHA-256 of the entry's lines (its field line and any
    continuation lines), and ``n`` counts the lines of the entries of that
    field with that digest, so each of two identical entries is a key of
    its own; in the body as the readme file's ``<path>:<line>`` when the
    body (the bytes after the first blank line) is byte-identical to the
    file ``pyproject.toml`` names as ``readme`` and its lines are the ones
    read after the header block, else as ``<unit>#body:<line>`` counted
    from the first line after the header block. Adding a classifier, a
    dependency or a URL moves no key. The key of a multiple-use entry
    moves when that entry changes, or when an entry identical to it is
    added or removed above it; the key of any other field moves when a
    line of that field is added or removed above it. A header block that
    is not ``Field: value`` lines is keyed by line, as
    any text. Only the top-level core metadata is read this way: a
    ``PKG-INFO`` or ``METADATA`` deeper in, or inside a nested container,
    is read as any other member;
  * a line of a wheel's ``RECORD`` that names a member and carries that
    member's SHA-256 and size is read as empty: everything on it is
    recomputed from bytes the gate reads, and a random digest matches a
    short token by chance. A line that does not check out is read as
    written.

  ``--archive`` without ``--root`` reads the artifacts alone, for a look
  at a build away from the work tree it came from: no tree is listed, so
  no member is judged by a tree file's entries and the core metadata body
  is never the readme's copy, and the run checks only the ``wheel!`` and
  ``sdist!`` entries of the kinds it read.

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
register cannot drift away from the lines it excuses. A run checks the
tree entries when it scans the tree, and the ``wheel!``/``sdist!`` entries
of the kinds of artifact it read, so no run reads an entry for something
it did not scan as stale.

The list is read from the ``NAME_GATE_TOKENS`` environment variable, or
from ``--list-file``. The value may be plain text or gzip + base64 (for
lists near the size limit of a secret). When no list is available the
gate FAILS: a pull request from a fork, or from a bot, does not receive
repository secrets, and a gate that passed without its list would pass
everything.

Usage:
    python scripts/ci/check-name-gate.py                    # the repo tree
    python scripts/ci/check-name-gate.py --root <path>
    python scripts/ci/check-name-gate.py --root . --dist dist   # the tree, then dist/
    python scripts/ci/check-name-gate.py --root . --archive dist/x.whl --archive dist/x.tar.gz
    python scripts/ci/check-name-gate.py --archive x.whl    # the artifact alone, no tree
    python scripts/ci/check-name-gate.py --list-file <private list>

Exit codes:
    0 -- no unexcused hit and no stale exception.
    1 -- at least one unexcused hit (UNREADABLE and BINARY included) or
         stale exception.
    2 -- the list is unavailable or invalid, the tree cannot be listed, an
         --archive argument or an entry of --dist is not a wheel or sdist,
         or --dist is empty, cannot be listed or does not hold exactly one
         wheel and one sdist.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import bisect
import csv
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
import tomllib
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
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e\x80-\xff]{8,}")
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
    # The tree scan's record: each path -> SHA-256 of the bytes it read there.
    tree: dict[str, str] = field(default_factory=dict)
    readme: str | None = None  # the tree path pyproject.toml names as the readme
    inherited: int = 0  # artifact members identical to the tree file at their path


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
    # Core metadata only: maps (unit, line) of a hit to the key it is reported under.
    rekey: Callable[[str, int], tuple[str, int]] | None = field(default=None, compare=False)


# An artifact's top-level members pass through this: None reads the member as
# usual; a list of units is read instead (empty: the tree scan read these bytes).
MemberHook = Callable[[str, bytes], "list[Unit] | None"]


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
    raw = "\n".join(run.decode("cp1252", errors="replace") for run in _PRINTABLE_RUN.findall(data))
    yield Unit(f"{name}#pdf-raw", (raw,))


def _zip_units(data: bytes, name: str, depth: int, hook: MemberHook | None) -> Iterator[Unit]:
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
        replaced = None if hook is None else hook(unit, content)
        yield from extract_units(content, unit, depth + 1) if replaced is None else replaced


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
            # Owner and group names come last, so the lines before them keep their numbers.
            pax = (f"{k}={v}" for k, v in info.pax_headers.items())
            extra = "\n".join([info.linkname, *pax, info.uname, info.gname])
            content: bytes | None = None
            if info.isfile():
                handle = tar.extractfile(info)
                content = None if handle is None else handle.read()
            members.append(_TarMember(info.name, content, info.isfile(), extra.strip()))
        return members, raw[tar.offset :]


def _gzip_units(data: bytes, name: str, depth: int, hook: MemberHook | None) -> Iterator[Unit]:
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
    except Exception:  # any tar that breaks while being read is reported, never echoed
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
        replaced = None if hook is None else hook(unit, member.content)
        yield from extract_units(member.content, unit, depth + 1) if replaced is None else replaced
    if tail.strip(b"\0"):
        yield Unit(f"{name}#tar-tail", None)


def extract_units(
    data: bytes, name: str, depth: int = 0, hook: MemberHook | None = None
) -> Iterator[Unit]:
    """Yield the readable units of ``data``.

    A path unit (``binary=True`` with the path as its only text) marks a
    container member's name, which is scanned at line 0. A binary file
    with no reader yields a single ``Unit(name, None, binary=True)``.
    ``hook`` sees the top-level members of a container, never deeper ones.
    """
    if depth > MAX_NESTING:
        yield Unit(name, None)
        return
    if data.startswith(_LFS_POINTER):
        yield Unit(name, None)
        return
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        yield from _zip_units(data, name, depth, hook)
        return
    if data.startswith(b"\x1f\x8b"):
        yield from _gzip_units(data, name, depth, hook)
        return
    if data[257:262] == b"ustar":  # an uncompressed tar: no reader
        yield Unit(name, None, binary=True)
        return
    binary = _is_binary(data)
    starts_as_pdf = _PDF_HEADER.match(data.lstrip()[:16]) is not None
    if starts_as_pdf or (binary and _PDF_HEADER.search(data[:PDF_HEADER_WINDOW])):
        yield from _pdf_units(data, name)
        if not binary:  # an all-text PDF: its strings are read as text as well
            yield Unit(name, _decodings(data))
        return
    if binary:
        yield Unit(name, None, binary=True)
        return
    # Text, even when it mentions or embeds the PDF magic further in.
    yield Unit(name, _decodings(data))


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """The text whose lines are counted: no BOM, LF line ends, no page breaks."""
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "")


def scan_text(unit: str, text: str, tokens: Tokens) -> set[Hit]:
    text = _normalise(text)
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
                hits = scan_text(name, text, tokens)
                if unit.rekey is not None:
                    rekey = unit.rekey
                    hits = {Hit(*rekey(h.unit, h.line), h.pattern_id) for h in hits}
                report.hits |= hits


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
        report.tree[relative] = hashlib.sha256(data).hexdigest()
        if relative == "pyproject.toml":
            report.readme = _declared_readme(data)
        scan_units(extract_units(data, relative), tokens, report, lambda n: n)


def _declared_readme(pyproject: bytes) -> str | None:
    """The path ``[project].readme`` names, or None when there is none to read."""
    try:
        project = tomllib.loads(pyproject.decode("utf-8")).get("project", {})
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    readme = project.get("readme") if isinstance(project, dict) else None
    if isinstance(readme, dict):
        readme = readme.get("file")
    return readme if isinstance(readme, str) else None


def archive_kind(path: Path, data: bytes) -> str:
    """``wheel`` for a zip ``.whl``, ``sdist`` for a gzip ``.tar.gz``; else refuse."""
    if path.name.endswith(".whl") and data.startswith(b"PK\x03\x04"):
        return "wheel"
    if path.name.endswith(".tar.gz") and data.startswith(b"\x1f\x8b"):
        try:
            inner = gzip.decompress(data)
        except (OSError, EOFError, zlib.error):
            raise GateError("an sdist artifact is not a readable gzip") from None
        if inner[257:262] != b"ustar":
            raise GateError("an sdist artifact is not a gzip tar")
        return "sdist"
    raise GateError("an artifact must be a wheel (.whl, zip) or an sdist (.tar.gz, gzip)")


def dist_files(directory: Path) -> list[Path]:
    """The wheel and the sdist in ``directory``, which must hold those two and nothing else.

    Every entry is listed, dot files included: the directory is what the
    upload sends, so an entry the scan skipped could be published unread,
    and a second wheel or sdist would share the first one's unversioned
    unit names. Names are never printed, because a name can carry a token.
    """
    try:
        with os.scandir(directory) as listing:
            entries = sorted(Path(entry.path) for entry in listing)
    except OSError:
        raise GateError("--dist cannot be listed") from None
    wheels = [p for p in entries if p.name.endswith(".whl")]
    sdists = [p for p in entries if p.name.endswith(".tar.gz")]
    hidden = [p for p in entries if p.name.startswith(".")]
    if len(entries) != 2 or len(wheels) != 1 or len(sdists) != 1 or hidden:
        raise GateError(
            f"--dist must hold exactly one wheel (.whl) and one sdist (.tar.gz) and nothing "
            f"else; it holds {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}: "
            f"{len(wheels)} wheel(s), {len(sdists)} sdist(s), {len(hidden)} dot file(s)"
        )
    return [*wheels, *sdists]


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


_CORE_METADATA = {
    "wheel": re.compile(r"wheel![^/]+\.dist-info/METADATA"),
    "sdist": re.compile(r"sdist![^/]+/PKG-INFO"),
}
_WHEEL_RECORD = re.compile(r"wheel![^/]+\.dist-info/RECORD")
_WHEEL_LICENSE = re.compile(r"[^/]+\.dist-info/licenses/(.+)")
_FIELD = re.compile(r"([A-Za-z0-9][A-Za-z0-9_-]*):")
# The fields the core metadata specification marks multiple-use (packaging's
# list and dict fields), compared without case, as header names are.
_REPEATABLE_FIELDS = frozenset(
    {
        "classifier",
        "dynamic",
        "import-name",
        "import-namespace",
        "license-file",
        "obsoletes",
        "obsoletes-dist",
        "platform",
        "project-url",
        "provides",
        "provides-dist",
        "provides-extra",
        "requires",
        "requires-dist",
        "requires-external",
        "supported-platform",
    }
)


def _entry_digest(lines: list[str]) -> str:
    """Twelve decimal digits of the SHA-256 of a header entry's lines.

    Digits rather than hex: a short name can be spelt in hex letters, and a
    key that matched a token would be printed redacted, so it could not be
    copied into the register.
    """
    digest = hashlib.sha256("\n".join(lines).encode("utf-8", errors="surrogatepass")).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 10**12:012d}"


def _metadata_rekey(content: bytes, report: Report) -> Callable[[str, int], tuple[str, int]]:
    """Key core-metadata hits by header field, or by body line (see the module docstring).

    Lines are split exactly as ``scan_text`` counts them. The header block
    runs to the first empty line; a line that starts with a space or a tab
    continues the entry above it. A repeatable field is keyed by its entry,
    so another entry of that field, added above it, does not renumber it.
    """
    lines = _normalise(_decodings(content)[0]).split("\n")
    entries: list[tuple[str, list[str]]] = []  # (field, the entry's lines)
    for line in lines:
        if line == "":
            break
        if entries and line[:1] in (" ", "\t"):
            entries[-1][1].append(line)
            continue
        match = _FIELD.match(line)
        if match is None:
            return lambda unit, number: (unit, number)  # not Field: value lines
        entries.append((match[1], [line]))
    keys: list[tuple[str, int]] = []
    seen: dict[str, int] = {}  # key name -> lines counted under it so far
    for field_name, entry in entries:
        name = field_name
        if field_name.lower() in _REPEATABLE_FIELDS:
            # Identical entries share a name and are counted on, so each is a key of its own.
            name = f"{field_name}@{_entry_digest(entry)}"
        for _line in entry:
            seen[name] = seen.get(name, 0) + 1
            keys.append((name, seen[name]))
    header = len(keys)
    _head, blank, body = content.partition(b"\n\n")
    # The readme's own scan numbered the lines of exactly these bytes. They
    # are its copy only when those lines are the ones this scan numbers after
    # the header block; a lone CR or a page break can end the header at
    # another place than the first blank line in the bytes.
    body_lines = _normalise(_decodings(body)[0]).split("\n")
    same_split = bool(blank) and lines[header + 1 :] == body_lines
    readme = report.readme
    digest = hashlib.sha256(body).hexdigest()
    origin = readme if same_split and readme and report.tree.get(readme) == digest else None

    def rekey(unit: str, number: int) -> tuple[str, int]:
        if 1 <= number <= header:
            return f"{unit}#{keys[number - 1][0]}", keys[number - 1][1]
        if number > header + 1:
            body_line = number - header - 1
            return (origin, body_line) if origin else (f"{unit}#body", body_line)
        return unit, number

    return rekey


def _record_mask(content: bytes, record: str, members: dict[str, tuple[str, int]]) -> bytes:
    """Blank each RECORD line whose path, SHA-256 and size all match a member."""
    out = []
    for raw in content.split(b"\n"):
        try:
            fields = next(csv.reader([raw.removesuffix(b"\r").decode("utf-8")]), [])
        except (UnicodeDecodeError, csv.Error):
            fields = []
        verified = False
        if len(fields) == 3:
            path, digest, size = fields
            if path == record:
                verified = digest == size == ""
            elif path in members:
                verified = (digest, size) == (f"sha256={members[path][0]}", str(members[path][1]))
        out.append(b"" if verified else raw)
    return b"\n".join(out)


class _ArtifactHook:
    """How an artifact's top-level members are read (see the module docstring)."""

    def __init__(self, kind: str, data: bytes, report: Report) -> None:
        self.kind = kind
        self.report = report
        self.rename = _unversion(kind)
        self.members: dict[str, tuple[str, int]] = {}  # wheel member -> (RECORD digest, size)
        if kind != "wheel":
            return
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    try:
                        body = archive.read(info)
                    except Exception:  # read again, and reported UNREADABLE, by the scan
                        continue
                    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest())
                    self.members[info.filename] = (digest.rstrip(b"=").decode(), len(body))
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError):
            pass  # the scan reports the archive UNREADABLE

    def __call__(self, unit: str, content: bytes) -> list[Unit] | None:
        path = self.rename(unit).partition("!")[2]
        licence = _WHEEL_LICENSE.fullmatch(path) if self.kind == "wheel" else None
        path = licence[1] if licence else path  # PEP 639 keeps the path from the project root
        if self.report.tree.get(path) == hashlib.sha256(content).hexdigest():
            self.report.inherited += 1
            return []  # the tree scan read these bytes at this path and reported their hits
        if _CORE_METADATA[self.kind].fullmatch(unit):
            units = list(extract_units(content, unit, 1))
            if len(units) == 1 and units[0].texts is not None and not units[0].binary:
                return [Unit(unit, units[0].texts, rekey=_metadata_rekey(content, self.report))]
            return units
        if self.kind == "wheel" and _WHEEL_RECORD.fullmatch(unit):
            masked = _record_mask(content, unit.partition("!")[2], self.members)
            return list(extract_units(masked, unit, 1))
        return None


def scan_archive(path: Path, tokens: Tokens, report: Report) -> str:
    """Scan a built wheel or sdist, naming its units by kind, not version.

    The tree is scanned first, in the same run: members are read against it.
    """
    try:
        data = path.read_bytes()
    except OSError:
        raise GateError("an artifact cannot be read") from None
    kind = archive_kind(path, data)
    report.files += 1
    # Its file name, as a path: the unit carries the kind, not the versioned name.
    report.hits |= scan_path(kind, path.name, tokens)
    hook = _ArtifactHook(kind, data, report)
    scan_units(extract_units(data, kind, hook=hook), tokens, report, _unversion(kind))
    return kind


# ---------------------------------------------------------------------------
# Verdict and output
# ---------------------------------------------------------------------------


def in_scope(entry: Hit, kinds: tuple[str, ...], tree: bool) -> bool:
    """Is this register entry checked by a run over ``kinds``, and the tree when ``tree``?"""
    prefix = re.split(r"[!#]", entry.unit, maxsplit=1)[0]
    return prefix in kinds if prefix in ARCHIVE_KINDS else tree


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
    parser.add_argument(
        "--root",
        help="git work tree to scan (default: .; with --archive, no tree unless this is given)",
    )
    artifacts_from = parser.add_mutually_exclusive_group()
    artifacts_from.add_argument(
        "--dist",
        help="also scan this directory, which must hold exactly one wheel and one sdist",
    )
    artifacts_from.add_argument(
        "--archive",
        action="append",
        default=[],
        help="scan a built wheel or sdist (repeatable), read against the tree if --root is given",
    )
    parser.add_argument("--list-file", help="read the list from this file instead of the env var")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")

    report = Report()
    try:
        token_list = load_list(args.list_file)
        dist = args.dist
        if dist == "":
            # Path("") is the working directory, which would be listed instead.
            raise GateError("--dist needs a directory")
        artifacts = dist_files(Path(dist)) if dist is not None else [Path(a) for a in args.archive]
        # --archive without --root reads the artifacts alone: no tree is listed or judged.
        root = args.root if args.root is not None or args.archive else "."
        if root is not None:
            # The tree first: an artifact's members are read against it.
            scan_tree(Path(root), token_list.tokens, report)
        scanned_kinds: set[str] = set()
        for artifact in artifacts:
            scanned_kinds.add(scan_archive(artifact, token_list.tokens, report))
        kinds = tuple(sorted(scanned_kinds))
    except GateError as exc:
        print(f"name-gate: ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"name-gate: ERROR: cannot read a file to scan ({exc.strerror})", file=sys.stderr)
        return 2

    exceptions = {e for e in token_list.exceptions if in_scope(e, kinds, root is not None)}
    unexcused, stale, excused = evaluate(report, exceptions)
    tokens = token_list.tokens
    for hit in unexcused:
        print(f"name-gate: {display(hit.unit, tokens)}:{hit.line} {hit.pattern_id}")
    for entry in stale:
        print(
            f"name-gate: stale exception {display(entry.unit, tokens)}:{entry.line} {entry.pattern_id}"
        )
    inherited = ""
    if kinds and root is not None:
        inherited = f", {report.inherited} artifact member(s) identical to the tree"
    print(
        f"name-gate: {report.files} file(s) scanned, {len(unexcused)} unexcused hit(s), "
        f"{excused} excused, {len(stale)} stale exception(s){inherited}"
    )
    return 1 if unexcused or stale else 0


if __name__ == "__main__":
    sys.exit(main())
