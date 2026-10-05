"""Behaviour of ``scripts/ci/check-name-gate.py`` on synthetic trees.

Every test plants a synthetic token that names nothing (``zqplant`` and
friends) and runs the gate as CI does, as a subprocess. The real token
list never enters the repository, so nothing here depends on it.

Each planted-file test has a clean control: the same tree without the
plant exits 0, so a pass is not a gate that cannot fail.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
import zlib
from pathlib import Path

import pytest
import yaml

from .test_quality_gates_run import _publish_jobs

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "ci" / "check-name-gate.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "name-gate.yml"

TOKEN = "zqplant"
MARKER = "zq-list-only-marker-7f3a"
LIST = f"""name-gate-list v1
marker {MARKER}
# a comment line
token S01 (?i)zqplant
token S02 zqother\\d+
token S03 zq\u2019mark
token S04 ^zqline$
token S05 zqm\u00fcller
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _tree(tmp_path: Path, files: dict[str, bytes | str], ignore: str = "") -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    _git(root, "init", "-q")
    if ignore:
        (root / ".gitignore").write_text(ignore, encoding="utf-8")
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_bytes(content.encode("utf-8"))
        else:
            path.write_bytes(content)
    _git(root, "add", "-A")
    return root


def _run(
    root: Path,
    tmp_path: Path,
    list_text: str | None = LIST,
    *extra: str,
    env_value: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "NAME_GATE_TOKENS"}
    args = [sys.executable, str(GATE), "--root", str(root), *extra]
    if env_value is not None:
        env["NAME_GATE_TOKENS"] = env_value
    elif list_text is not None:
        list_file = tmp_path / "list.txt"
        list_file.write_text(list_text, encoding="utf-8")
        args += ["--list-file", str(list_file)]
    return subprocess.run(args, capture_output=True, text=True, env=env, check=False, cwd=cwd)


def _hits(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        line.removeprefix("name-gate: ")
        for line in proc.stdout.splitlines()
        if line.startswith("name-gate: ") and " file(s) scanned" not in line
    ]


def _assert_no_leak(proc: subprocess.CompletedProcess[str]) -> None:
    out = proc.stdout + proc.stderr
    assert TOKEN not in out.lower(), "the gate printed the matched text"
    assert MARKER not in out, "the gate printed list content"


BASE = {"src/app.py": "print('hello')\n", "README.md": "# Demo\n"}


def test_clean_tree_passes(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, BASE), tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _hits(proc) == []


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("src/app.py", "x = 1\n\n# ZqPlant here\n", "src/app.py:3 S01"),
        ("README.md", "# Demo\nzqplant\n", "README.md:2 S01"),
        ("docs/page.html", "<p>zqplant</p>\n", "docs/page.html:1 S01"),
        ("scripts/run.sh", "echo zqother42\n", "scripts/run.sh:1 S02"),
        ("scripts/creds.template", "a\nb\nzqplant\n", "scripts/creds.template:3 S01"),
        ("LICENSE", "zqplant\n", "LICENSE:1 S01"),
        (".github/workflows/x.yml", "name: zqplant\n", ".github/workflows/x.yml:1 S01"),
    ],
)
def test_a_plant_in_any_text_file_fails(
    tmp_path: Path, name: str, content: str, expected: str
) -> None:
    proc = _run(_tree(tmp_path, {**BASE, name: content}), tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert expected in _hits(proc)
    _assert_no_leak(proc)


def test_untracked_file_is_scanned_and_ignored_file_is_not(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE, ignore="build/\n")
    (root / "notes.txt").write_text("zqplant\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("zqplant\n", encoding="utf-8")
    proc = _run(root, tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["notes.txt:1 S01"]


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("img.png", b"\x89PNG\x00\x00zqplant"),
        ("bundle.tar", b"a" * 100 + b"\x00" * 400 + b"zqplant"),
        ("old.doc", b"\xd0\xcf\x11\xe0\x00\x00zqplant"),
        ("utf16-no-bom.txt", "zqplant\n".encode("utf-16-le")),
    ],
)
def test_a_binary_file_with_no_reader_fails_until_registered(
    tmp_path: Path, name: str, content: bytes
) -> None:
    root = _tree(tmp_path, {**BASE, name: content})
    proc = _run(root, tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == [f"{name}:0 BINARY"]
    registered = _run(root, tmp_path, LIST + f"exception {name}:0 BINARY\n")
    assert registered.returncode == 0, registered.stdout
    _assert_no_leak(proc)


def test_utf16_text_with_a_bom_is_read(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {**BASE, "notes.txt": "x\nzqplant\n".encode("utf-16")}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["notes.txt:2 S01"]


def test_cp1252_text_is_read_when_it_is_not_utf8(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {**BASE, "legacy.csv": b"id,name\n1,zq\x92mark\n"}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["legacy.csv:2 S03"]


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("page.html", "<p>zq<b></b>plant</p>\n"),
        ("page.md", "Made by zq&#112;lant.\n"),
        ("page.svg", "<text>zq&#x70;lant</text>\n"),
        ("notes.txt", "zq&amp;plant is not a hit, zq<i>plant</i> is\n"),
    ],
)
def test_markup_and_character_references_are_read_as_text(
    tmp_path: Path, name: str, content: str
) -> None:
    proc = _run(_tree(tmp_path, {**BASE, name: content}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == [f"{name}:1 S01"]


def test_anchors_match_at_line_ends(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {**BASE, "a.txt": "x\nzqline\ny\n"}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["a.txt:2 S04"]


def _redacted(path: str) -> str:
    return "~" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]


def test_a_name_in_a_file_path_is_a_hit_and_is_never_printed(tmp_path: Path) -> None:
    name = "docs/zqplant-report.md"
    proc = _run(_tree(tmp_path, {**BASE, name: "# Report\n\nzqother1\n"}), tmp_path)
    assert proc.returncode == 1
    assert sorted(_hits(proc)) == [f"{_redacted(name)}:0 S01", f"{_redacted(name)}:3 S02"]
    _assert_no_leak(proc)


def test_a_name_in_a_member_path_is_a_hit_and_is_never_printed(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("media/zqplant.txt", "clean\n")
    proc = _run(_tree(tmp_path, {**BASE, "docs/pack.zip": buf.getvalue()}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == [f"docs/pack.zip!{_redacted('media/zqplant.txt')}:0 S01"]
    _assert_no_leak(proc)


def test_a_stale_exception_for_a_redacted_path_is_printed_redacted(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, BASE), tmp_path, LIST + "exception gone/zqplant.md:2 S01\n")
    assert proc.returncode == 1
    assert _hits(proc) == [f"stale exception {_redacted('gone/zqplant.md')}:2 S01"]
    _assert_no_leak(proc)


def _office_zip(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", b'<?xml version="1.0"?><Types/>')
        z.writestr("ppt/slides/slide1.xml", xml)
    return buf.getvalue()


def test_a_token_split_across_office_runs_is_seen(tmp_path: Path) -> None:
    xml = b'<?xml version="1.0"?><p:sld><a:t>Zq</a:t><a:t>plant</a:t></p:sld>'
    proc = _run(_tree(tmp_path, {**BASE, "docs/deck.pptx": _office_zip(xml)}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["docs/deck.pptx!ppt/slides/slide1.xml:1 S01"]
    _assert_no_leak(proc)


def test_an_office_file_without_the_token_passes(tmp_path: Path) -> None:
    xml = b'<?xml version="1.0"?><p:sld><a:t>Quarterly</a:t></p:sld>'
    proc = _run(_tree(tmp_path, {**BASE, "docs/deck.pptx": _office_zip(xml)}), tmp_path)
    assert proc.returncode == 0, proc.stdout


def _pdf(text: str, title: str = "Report", author_hex: str = "", lead: bytes = b"") -> bytes:
    """A one-page PDF whose page text is Flate-compressed (not in the raw bytes)."""
    content = zlib.compress(f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode("latin-1"))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content)
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Title ("
        + title.encode("latin-1")
        + b")"
        + (b" /Author <" + author_hex.encode("ascii") + b">" if author_hex else b"")
        + b" >>",
    ]
    out = bytearray(lead + b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def test_pdf_page_text_is_read_or_the_gate_fails_closed(tmp_path: Path) -> None:
    pdf = _pdf("Prepared for zqplant")
    assert TOKEN.encode() not in pdf.lower(), "the plant must be compressed, not raw"
    proc = _run(_tree(tmp_path, {**BASE, "docs/report.pdf": pdf}), tmp_path)
    assert proc.returncode == 1, proc.stdout
    if shutil.which("pdftotext"):
        assert "docs/report.pdf#pdf-text:1 S01" in _hits(proc)
    else:
        assert "docs/report.pdf#pdf-text:0 UNREADABLE" in _hits(proc)
    _assert_no_leak(proc)


# A clean PDF passes only when BOTH poppler readers exist: without pdfinfo the
# metadata is UNREADABLE and the gate fails closed (a Windows runner can have
# pdftotext but not pdfinfo).
@pytest.mark.skipif(
    shutil.which("pdftotext") is None or shutil.which("pdfinfo") is None,
    reason="needs pdftotext and pdfinfo",
)
def test_a_pdf_without_the_token_passes(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {**BASE, "docs/report.pdf": _pdf("Quarterly")}), tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_pdf_metadata_in_a_utf16_hex_string_is_read_or_fails_closed(tmp_path: Path) -> None:
    author_hex = "FEFF" + "".join(f"{ord(c):04X}" for c in "zqplant")
    pdf = _pdf("Quarterly", author_hex=author_hex)
    assert TOKEN.encode() not in pdf.lower()
    proc = _run(_tree(tmp_path, {**BASE, "docs/report.pdf": pdf}), tmp_path)
    assert proc.returncode == 1
    if shutil.which("pdfinfo"):
        meta = [h for h in _hits(proc) if h.startswith("docs/report.pdf#pdf-meta:")]
        assert meta and all(h.endswith(" S01") for h in meta), _hits(proc)
    else:
        assert "docs/report.pdf#pdf-meta:0 UNREADABLE" in _hits(proc)


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="needs pdftotext")
def test_a_pdf_with_bytes_before_its_header_is_still_a_pdf(tmp_path: Path) -> None:
    pdf = _pdf("Prepared for zqplant", lead=b"\n")
    proc = _run(_tree(tmp_path, {**BASE, "docs/report.pdf": pdf}), tmp_path)
    assert proc.returncode == 1
    assert "docs/report.pdf#pdf-text:1 S01" in _hits(proc)


def test_pdf_metadata_is_read_from_the_raw_bytes(tmp_path: Path) -> None:
    pdf = _pdf("Quarterly", title="zqplant draft")
    proc = _run(_tree(tmp_path, {**BASE, "docs/report.pdf": pdf}), tmp_path)
    assert proc.returncode == 1
    assert any(h.startswith("docs/report.pdf#pdf-raw:") and h.endswith(" S01") for h in _hits(proc))


def _wheel(tmp_path: Path, member_text: str) -> Path:
    path = tmp_path / "demo-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("demo/__init__.py", member_text)
        z.writestr("demo-1.2.3.dist-info/METADATA", "Name: demo\n")
    return path


def _sdist(tmp_path: Path, member_text: str) -> Path:
    path = tmp_path / "demo-1.2.3.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = member_text.encode("utf-8")
        info = tarfile.TarInfo("demo-1.2.3/README.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path


def test_built_wheel_and_sdist_are_scanned_by_kind(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    wheel = _wheel(tmp_path, "x = 1\n# zqplant\n")
    sdist = _sdist(tmp_path, "zqother7\n")
    proc = _run(root, tmp_path, LIST, "--archive", str(wheel), "--archive", str(sdist))
    assert proc.returncode == 1
    assert sorted(_hits(proc)) == ["sdist!README.md:1 S02", "wheel!demo/__init__.py:2 S01"]
    _assert_no_leak(proc)


def test_wheel_metadata_units_carry_no_version(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    path = tmp_path / "demo-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("demo-1.2.3.dist-info/METADATA", "Name: demo\nSummary: zqplant\n")
    proc = _run(root, tmp_path, LIST, "--archive", str(path))
    assert _hits(proc) == ["wheel!demo.dist-info/METADATA#Summary:1 S01"]


def test_clean_built_artifacts_pass(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    wheel, sdist = _wheel(tmp_path, "x = 1\n"), _sdist(tmp_path, "# Demo\n")
    proc = _run(root, tmp_path, LIST, "--archive", str(wheel), "--archive", str(sdist))
    assert proc.returncode == 0, proc.stdout


def test_a_tar_with_data_after_its_last_readable_member_fails(tmp_path: Path) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        data = b"clean\n"
        info = tarfile.TarInfo("a.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    body = raw.getvalue()
    broken = body[:1024] + b"#" * 512 + b"zqplant" + body[1024:]
    proc = _run(_tree(tmp_path, {**BASE, "dist.tar.gz": gzip.compress(broken)}), tmp_path)
    assert proc.returncode == 1
    assert "dist.tar.gz#tar-tail:0 UNREADABLE" in _hits(proc)


def test_a_corrupt_zip_member_is_unreadable_not_a_crash(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # Varied text, so the compressed member is long enough to damage in the middle.
        z.writestr("a.txt", " ".join(str(i * 7919 % 10007) for i in range(400)))
    data = bytearray(buf.getvalue())
    start = data.index(b"a.txt") + len(b"a.txt")
    end = data.rindex(b"a.txt")  # the central directory's copy of the name
    assert end - start > 200
    for i in range(start + 100, start + 120):
        data[i] ^= 0xFF
    proc = _run(_tree(tmp_path, {**BASE, "pack.zip": bytes(data)}), tmp_path)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "pack.zip!a.txt:0 UNREADABLE" in _hits(proc)


@pytest.mark.parametrize(
    ("name", "content"),
    [("demo-1.0-py3-none-any.whl", b""), ("demo-1.0.tar.gz", b"plain text zqplant\n")],
)
def test_an_archive_argument_must_be_a_real_archive(
    tmp_path: Path, name: str, content: bytes
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    proc = _run(_tree(tmp_path, BASE), tmp_path, LIST, "--archive", str(path))
    assert proc.returncode == 2


@pytest.mark.parametrize("value", [None, "", "   \n"])
def test_the_gate_fails_closed_without_a_list(tmp_path: Path, value: str | None) -> None:
    root = _tree(tmp_path, BASE)
    # None: no --list-file and no env var at all; otherwise the env var is set but blank.
    proc = _run(root, tmp_path, None) if value is None else _run(root, tmp_path, env_value=value)
    assert proc.returncode == 2
    assert "fails closed" in proc.stderr


@pytest.mark.parametrize(
    "bad",
    [
        "token S01 zqplant\n",  # no header
        "name-gate-list v1\n# nothing\n",  # no tokens
        "name-gate-list v1\ntoken S01 (zqplant\n",  # does not compile
        "name-gate-list v1\ntoken S01 zq|\n",  # matches the empty string
        "name-gate-list v1\ntoken S01 zqplant\ntoken S01 zqother\n",  # duplicate id
        "name-gate-list v1\ntoken S01 zqplant\nexception a.py:1 S09\n",  # unknown id
        "name-gate-list v1\ntoken S01 zqplant\nexception a.py:x S01\n",  # bad line
        "name-gate-list v1\ntoken S01 zqplant\nstray zqplant\n",  # unknown kind
        "name-gate-list v1\ntoken S01 zqplant   # the thing\n",  # trailing comment
        "name-gate-list v1\ntoken UNREADABLE zqplant\n",  # reserved id
        "name-gate-list v1\ntoken BINARY zqplant\n",  # reserved id
        "name-gate-list v1\ntoken S/1 zqplant\n",  # bad id characters
        "name-gate-list v1\ntoken S01 zqplant\nexception a.py:\u00b2 S01\n",  # not ASCII digits
        "name-gate-list v1\ntoken S01 zqplant\nexception a.pdf#pdf-text:0 UNREADABLE\n",
        "name-gate-list v1\ntoken S01 zqplant\nexception a.png:3 BINARY\n",  # BINARY at a line
    ],
)
def test_an_invalid_list_is_refused_without_quoting_it(tmp_path: Path, bad: str) -> None:
    proc = _run(_tree(tmp_path, BASE), tmp_path, bad)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert TOKEN not in proc.stderr.lower()
    assert "zqother" not in proc.stderr


def test_the_list_is_read_from_the_environment(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**BASE, "a.py": "zqplant\n"})
    proc = _run(root, tmp_path, env_value=LIST)
    assert proc.returncode == 1
    assert _hits(proc) == ["a.py:1 S01"]


def test_a_gzip_base64_list_is_accepted(tmp_path: Path) -> None:
    packed = base64.b64encode(gzip.compress(LIST.encode("utf-8"))).decode("ascii")
    wrapped = "\n".join(packed[i : i + 60] for i in range(0, len(packed), 60))
    root = _tree(tmp_path, {**BASE, "a.py": "zqplant\n"})
    proc = _run(root, tmp_path, env_value=wrapped)
    assert proc.returncode == 1, proc.stderr
    assert _hits(proc) == ["a.py:1 S01"]


def test_garbage_that_is_not_a_list_is_refused(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, BASE), tmp_path, env_value="not a list at all")
    assert proc.returncode == 2


def test_an_exact_exception_excuses_one_hit(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**BASE, "a.py": "x\nzqplant\n"})
    proc = _run(root, tmp_path, LIST + "exception a.py:2 S01\n")
    assert proc.returncode == 0, proc.stdout
    assert "1 excused" in proc.stdout


def test_an_exception_one_line_off_fails_twice(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**BASE, "a.py": "x\nzqplant\n"})
    proc = _run(root, tmp_path, LIST + "exception a.py:1 S01\n")
    assert proc.returncode == 1
    assert _hits(proc) == ["a.py:2 S01", "stale exception a.py:1 S01"]


def test_an_exception_for_the_wrong_pattern_does_not_excuse(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**BASE, "a.py": "x\nzqplant\n"})
    proc = _run(root, tmp_path, LIST + "exception a.py:2 S02\n")
    assert proc.returncode == 1
    assert _hits(proc) == ["a.py:2 S01", "stale exception a.py:2 S02"]


def test_a_stale_exception_fails_a_clean_tree(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, BASE), tmp_path, LIST + "exception gone.py:4 S01\n")
    assert proc.returncode == 1
    assert _hits(proc) == ["stale exception gone.py:4 S01"]


def test_tree_and_archive_runs_do_not_read_each_others_exceptions_as_stale(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, {**BASE, "a.py": "zqplant\n"})
    wheel = _wheel(tmp_path, "# zqplant\n")
    both = LIST + "exception a.py:1 S01\nexception wheel!demo/__init__.py:1 S01\n"
    tree_run = _run(root, tmp_path, both)
    archive_run = _run(root, tmp_path, both, "--archive", str(wheel))
    assert tree_run.returncode == 0, tree_run.stdout
    assert archive_run.returncode == 0, archive_run.stdout


def test_a_non_repository_root_is_an_error_not_a_pass(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    proc = _run(empty, tmp_path)
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# Built artifacts, read against the tree they were built from (--dist)
# ---------------------------------------------------------------------------

_README = "# Demo\nmade by zqplant\n"
_PYPROJECT = '[project]\nname = "demo"\nreadme = "README.md"\n'
_README_ENTRY = "exception README.md:2 S01\n"
_WHEEL_AUTHOR = "exception wheel!demo.dist-info/METADATA#Author:1 S01\n"
_SDIST_AUTHOR = "exception sdist!PKG-INFO#Author:1 S01\n"


def _zip_of(path: Path, members: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for member, content in members.items():
            z.writestr(member, content)
    return path


def _sdist_of(
    path: Path, members: dict[str, str | bytes], top: str = "demo-1.2.3", owner: str = ""
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for member, content in members.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{top}/{member}")
            info.size = len(data)
            info.uname = info.gname = owner
            tar.addfile(info, io.BytesIO(data))
    return path


def _dist(
    tmp_path: Path,
    wheel: dict[str, str | bytes] | None = None,
    sdist: dict[str, str | bytes] | None = None,
    version: str = "1.2.3",
) -> Path:
    """A dist directory holding one wheel and one sdist, as a build writes it."""
    dist = tmp_path / f"dist-{version}"
    _zip_of(dist / f"demo-{version}-py3-none-any.whl", wheel or {"demo/__init__.py": "x = 1\n"})
    _sdist_of(
        dist / f"demo-{version}.tar.gz", sdist or {"demo/__init__.py": "x = 1\n"}, f"demo-{version}"
    )
    return dist


def _metadata(version: str = "1.2.3", extra: str = "", body: str = _README) -> str:
    return (
        f"Metadata-Version: 2.4\nName: demo\nVersion: {version}\n{extra}Author: zqplant\n"
        f"Description-Content-Type: text/markdown\n\n{body}"
    )


def _metadata_tree(tmp_path: Path, pyproject: str = _PYPROJECT, readme: str = _README) -> Path:
    return _tree(tmp_path, {**BASE, "README.md": readme, "pyproject.toml": pyproject})


def test_a_member_identical_to_the_tree_file_at_its_path_is_judged_by_the_tree_entry(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, {**BASE, "README.md": _README})
    dist = _dist(tmp_path, sdist={"README.md": _README})
    proc = _run(root, tmp_path, LIST + _README_ENTRY, "--dist", str(dist))
    assert proc.returncode == 0, proc.stdout
    assert "1 artifact member(s) identical to the tree" in proc.stdout
    # Without the tree entry the hit is reported once, under the tree file's name.
    proc = _run(root, tmp_path, LIST, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["README.md:2 S01"]
    _assert_no_leak(proc)


def test_a_member_that_differs_from_the_tree_file_is_read_in_full(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**BASE, "README.md": _README})
    dist = _dist(tmp_path, sdist={"README.md": _README + "\n"})  # one byte more
    proc = _run(root, tmp_path, LIST + _README_ENTRY, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["sdist!README.md:2 S01"]
    assert "0 artifact member(s) identical to the tree" in proc.stdout


def test_the_same_bytes_at_another_member_path_are_read_as_that_member(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**BASE, "README.md": _README})
    dist = _dist(tmp_path, wheel={"demo/README.md": _README})
    proc = _run(root, tmp_path, LIST + _README_ENTRY, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["wheel!demo/README.md:2 S01"]


def test_a_wheel_licence_file_is_read_as_the_tree_file_it_was_taken_from(tmp_path: Path) -> None:
    licence = "Copyright zqplant\n"
    root = _tree(tmp_path, {**BASE, "LICENSE": licence})
    listed = LIST + "exception LICENSE:1 S01\n"
    copied = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/licenses/LICENSE": licence})
    proc = _run(root, tmp_path, listed, "--dist", str(copied))
    assert proc.returncode == 0, proc.stdout
    elsewhere = _dist(
        tmp_path, wheel={"demo-1.2.3.dist-info/licenses/NOTICE": licence}, version="1.2.4"
    )
    proc = _run(root, tmp_path, listed, "--dist", str(elsewhere))
    assert proc.returncode == 1
    assert _hits(proc) == ["wheel!demo.dist-info/licenses/NOTICE:1 S01"]


def test_an_artifact_run_judges_the_tree_as_well(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**BASE, "a.py": "zqplant\n"})
    proc = _run(root, tmp_path, LIST, "--dist", str(_dist(tmp_path)))
    assert proc.returncode == 1
    assert _hits(proc) == ["a.py:1 S01"]


def test_a_metadata_header_hit_is_keyed_by_its_field_not_its_line(tmp_path: Path) -> None:
    root = _metadata_tree(tmp_path)
    listed = LIST + _README_ENTRY + _WHEEL_AUTHOR
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": _metadata()})
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    assert proc.returncode == 0, proc.stdout
    # Fields above Author move its line and not its key.
    moved = _metadata(extra="Project-URL: Home, https://example.invalid\nClassifier: A\n")
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": moved}, version="1.2.4")
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    assert proc.returncode == 0, proc.stdout
    # The n-th line of a repeated field, and a token in another field, are each their own key.
    other = _metadata(
        extra="Classifier: A\nClassifier: B\nClassifier: zqplant\nMaintainer: zqplant\n"
    )
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": other}, version="1.2.5")
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == [
        "wheel!demo.dist-info/METADATA#Classifier:3 S01",
        "wheel!demo.dist-info/METADATA#Maintainer:1 S01",
    ]
    _assert_no_leak(proc)


def test_the_sdist_core_metadata_is_keyed_by_field_too(tmp_path: Path) -> None:
    root = _metadata_tree(tmp_path)
    dist = _dist(tmp_path, sdist={"PKG-INFO": _metadata()})
    proc = _run(root, tmp_path, LIST + _README_ENTRY, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["sdist!PKG-INFO#Author:1 S01"]
    proc = _run(root, tmp_path, LIST + _README_ENTRY + _SDIST_AUTHOR, "--dist", str(dist))
    assert proc.returncode == 0, proc.stdout


@pytest.mark.parametrize(
    ("pyproject", "body"),
    [
        (_PYPROJECT, "# Demo\n\nmade by zqplant\n"),  # the body differs from the readme
        ('[project]\nname = "demo"\n', _README),  # pyproject.toml names no readme
        (_PYPROJECT, "# Notes\nmade by zqplant\n"),  # identical to a tree file, not the readme
    ],
)
def test_a_metadata_body_not_copied_from_the_readme_is_read_as_itself(
    tmp_path: Path, pyproject: str, body: str
) -> None:
    root = _tree(
        tmp_path,
        {
            **BASE,
            "README.md": _README,
            "pyproject.toml": pyproject,
            "docs/notes.md": "# Notes\nmade by zqplant\n",
        },
    )
    listed = LIST + _README_ENTRY + "exception docs/notes.md:2 S01\n" + _WHEEL_AUTHOR
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": _metadata(body=body)})
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    assert proc.returncode == 1
    line = body.split("\n").index("made by zqplant") + 1
    assert _hits(proc) == [f"wheel!demo.dist-info/METADATA#body:{line} S01"]


def test_a_metadata_header_that_is_not_field_lines_is_keyed_by_line(tmp_path: Path) -> None:
    """A header the gate cannot parse is keyed by line, which fails closed.

    Two unparsed lines sit above the hit, so a reading that skipped them, or
    took them as continuations of the field above, would give another key.
    """
    root = _metadata_tree(tmp_path)
    text = "Name: demo\nnot a field\nnot one either\nAuthor: zqplant\n\nbody\n"
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": text})
    proc = _run(root, tmp_path, LIST + _README_ENTRY, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["wheel!demo.dist-info/METADATA:4 S01"]


def test_a_continuation_line_is_the_next_line_of_the_field_above(tmp_path: Path) -> None:
    """A header line that starts with a space or a tab continues the field above it.

    Read as a field of its own it is not ``Field: value``, so the whole
    header would be keyed by line and every registered field entry would go
    stale.
    """
    root = _metadata_tree(tmp_path)
    folded = _metadata(extra="Summary: a summary\n        zqplant folded\n\tzqplant again\n")
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": folded})
    proc = _run(root, tmp_path, LIST + _README_ENTRY + _WHEEL_AUTHOR, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == [
        "wheel!demo.dist-info/METADATA#Summary:2 S01",
        "wheel!demo.dist-info/METADATA#Summary:3 S01",
    ]


@pytest.mark.parametrize(
    "header",
    [
        b"Name: demo\rVersion: 1.2.3\n\rKeywords: zqplant\n\n",  # a lone CR is a line end
        b"Name: demo\nVersion: 1.2.3\n\x0c\nKeywords: zqplant\n\n",  # a page break is not read
    ],
    ids=["lone CR", "page break"],
)
def test_a_metadata_body_is_the_readme_only_where_the_scan_reads_it_as_one(
    tmp_path: Path, header: bytes
) -> None:
    """The header block ends at the first blank line the scan reads, not the first in the bytes.

    In both headers the scan reads a blank line before ``Keywords``, so the
    body starts there and ``Keywords`` is its first line, while the bytes
    after the first ``\\n\\n`` are an exact copy of the readme. Keyed by
    that copy, every body hit would be reported against a readme line it is
    not on, and a registered readme line could excuse it.
    """
    root = _metadata_tree(tmp_path)
    listed = LIST + _README_ENTRY
    plain = b"Name: demo\nVersion: 1.2.3\nKeywords: zqplant\n\n" + _README.encode()
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": plain})
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    # The control: with a plain header the body is the readme's copy.
    assert _hits(proc) == ["wheel!demo.dist-info/METADATA#Keywords:1 S01"]
    crafted = _dist(
        tmp_path,
        wheel={"demo-1.2.3.dist-info/METADATA": header + _README.encode()},
        version="1.2.4",
    )
    proc = _run(root, tmp_path, listed, "--dist", str(crafted))
    assert proc.returncode == 1
    assert _hits(proc) == [
        "wheel!demo.dist-info/METADATA#body:1 S01",
        "wheel!demo.dist-info/METADATA#body:4 S01",
    ]


def test_a_readme_named_by_a_table_is_the_body_source_as_well(tmp_path: Path) -> None:
    pyproject = (
        '[project]\nname = "demo"\nreadme = {file = "README.md", content-type = "text/markdown"}\n'
    )
    root = _metadata_tree(tmp_path, pyproject=pyproject)
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": _metadata()})
    proc = _run(root, tmp_path, LIST + _README_ENTRY + _WHEEL_AUTHOR, "--dist", str(dist))
    assert proc.returncode == 0, proc.stdout


def _zip_bytes(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for member, content in members.items():
            z.writestr(member, content)
    return buf.getvalue()


_OTHER_METADATA = "Metadata-Version: 2.4\nName: other\nSummary: zqplant\n\nbody\n"


@pytest.mark.parametrize(
    ("kind", "member", "content", "unit"),
    [
        ("sdist", "demo.egg-info/PKG-INFO", _OTHER_METADATA, "sdist!demo.egg-info/PKG-INFO"),
        (
            "wheel",
            "demo/vendored-1.0.dist-info/METADATA",
            _OTHER_METADATA,
            "wheel!demo/vendored-1.0.dist-info/METADATA",
        ),
        (
            "wheel",
            "inner.zip",
            _zip_bytes({"other-1.0.dist-info/METADATA": _OTHER_METADATA}),
            "wheel!inner.zip!other.dist-info/METADATA",
        ),
    ],
    # Named here: pytest would build the ids from the values, and the zip's
    # bytes carry the local time it was built at, so the node id of the
    # nested case would change between two runs.
    ids=["sdist-deeper-pkg-info", "wheel-deeper-metadata", "wheel-nested-zip"],
)
def test_only_the_top_level_core_metadata_is_keyed_by_field(
    tmp_path: Path, kind: str, member: str, content: str | bytes, unit: str
) -> None:
    """Core metadata deeper in the artifact, or inside a nested container, is any other text.

    Only the artifact's own ``PKG-INFO`` or ``METADATA`` is described by the
    build; another one is keyed by line, as any member is.
    """
    root = _metadata_tree(tmp_path)
    dist = _dist(tmp_path, **{kind: {member: content}})
    proc = _run(root, tmp_path, LIST + _README_ENTRY, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == [f"{unit}:3 S01"]


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_a_member_of_a_nested_container_is_never_read_as_a_tree_file(
    tmp_path: Path, kind: str
) -> None:
    """Only an artifact's own members are matched to tree paths.

    The tree file ``inner.zip!README.md`` and the readme inside the member
    ``inner.zip`` share a name as the gate writes it, and their bytes, but
    the nested one was never a tree file, so it is read in full.
    """
    root = _tree(tmp_path, {**BASE, "inner.zip!README.md": _README})
    listed = LIST + "exception inner.zip!README.md:2 S01\n"
    dist = _dist(tmp_path, **{kind: {"inner.zip": _zip_bytes({"README.md": _README})}})
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == [f"{kind}!inner.zip!README.md:2 S01"]
    assert "0 artifact member(s) identical to the tree" in proc.stdout


def _record_digest(body: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()


@pytest.mark.parametrize(
    ("member", "record_body", "size_delta", "reads"),
    [
        ("demo/__init__.py", b"x = 1\n", 0, False),  # path, digest and size check out
        ("demo/__init__.py", b"x = 1\n", 1, True),  # the size is forged
        ("demo/__init__.py", b"x = 2\n", 0, True),  # the digest is another file's
        ("demo/other.py", b"x = 1\n", 0, True),  # the path is not a member
    ],
)
def test_a_record_line_is_read_as_empty_only_when_it_checks_out(
    tmp_path: Path, member: str, record_body: bytes, size_delta: int, reads: bool
) -> None:
    root = _tree(tmp_path, BASE)
    body = b"x = 1\n"
    digest = _record_digest(record_body)
    # A token the digest happens to contain: what a short name does by chance.
    listed = LIST + f"token S09 {re.escape(digest[4:16])}\n"
    record = (
        f"{member},sha256={digest},{len(record_body) + size_delta}\ndemo-1.2.3.dist-info/RECORD,,\n"
    )
    dist = _dist(tmp_path, wheel={"demo/__init__.py": body, "demo-1.2.3.dist-info/RECORD": record})
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    if reads:
        assert proc.returncode == 1
        assert _hits(proc) == ["wheel!demo.dist-info/RECORD:1 S09"]
    else:
        assert proc.returncode == 0, proc.stdout


@pytest.mark.parametrize(
    "self_row",
    [
        "sha256=zqplant,7",  # a digest and a size
        ",zqplant",  # a size only
        "sha256=zqplant,",  # a digest only
    ],
)
def test_the_record_row_for_record_itself_is_read_unless_it_is_empty(
    tmp_path: Path, self_row: str
) -> None:
    """RECORD lists itself with no digest and no size; anything on that row is read.

    The row is blank only when both fields are: a check of either field
    alone passes the row whose other field carries the token.
    """
    root = _tree(tmp_path, BASE)
    body = b"x = 1\n"
    first = f"demo/__init__.py,sha256={_record_digest(body)},{len(body)}\n"
    rows = ((",", []), (self_row, ["wheel!demo.dist-info/RECORD:2 S01"]))  # the control first
    for n, (row, hits) in enumerate(rows):
        record = f"{first}demo-1.2.3.dist-info/RECORD,{row}\n"
        dist = _dist(
            tmp_path / f"build{n}",
            wheel={"demo/__init__.py": body, "demo-1.2.3.dist-info/RECORD": record},
        )
        proc = _run(root, tmp_path, LIST, "--dist", str(dist))
        assert _hits(proc) == hits, row
        assert proc.returncode == (1 if hits else 0)


def test_a_field_entry_that_matches_no_hit_is_stale_and_a_tree_run_ignores_it(
    tmp_path: Path,
) -> None:
    root = _metadata_tree(tmp_path)
    listed = (
        LIST
        + _README_ENTRY
        + _WHEEL_AUTHOR
        + "exception wheel!demo.dist-info/METADATA#Summary:1 S01\n"
    )
    dist = _dist(tmp_path, wheel={"demo-1.2.3.dist-info/METADATA": _metadata()})
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["stale exception wheel!demo.dist-info/METADATA#Summary:1 S01"]
    tree_run = _run(root, tmp_path, listed)
    assert tree_run.returncode == 0, tree_run.stdout


def test_an_inherited_hit_needs_no_artifact_entry_and_a_line_entry_for_it_is_stale(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, {**BASE, "README.md": _README})
    dist = _dist(tmp_path, sdist={"README.md": _README})
    listed = LIST + _README_ENTRY + "exception sdist!README.md:2 S01\n"
    proc = _run(root, tmp_path, listed, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["stale exception sdist!README.md:2 S01"]


def test_an_unreadable_member_still_fails_and_no_list_is_still_an_error(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("demo/a.txt", " ".join(str(i * 7919 % 10007) for i in range(400)))
    data = bytearray(buf.getvalue())
    start = data.index(b"demo/a.txt") + len(b"demo/a.txt")
    for i in range(start + 100, start + 120):
        data[i] ^= 0xFF
    dist = _dist(tmp_path)
    (dist / "demo-1.2.3-py3-none-any.whl").write_bytes(bytes(data))
    root = _tree(tmp_path, BASE)
    proc = _run(root, tmp_path, LIST, "--dist", str(dist))
    assert proc.returncode == 1
    assert "wheel!demo/a.txt:0 UNREADABLE" in _hits(proc)
    proc = _run(root, tmp_path, None, "--dist", str(dist))
    assert proc.returncode == 2
    assert "fails closed" in proc.stderr


def _plant(dist: Path, name: str, content: bytes | None = None) -> None:
    if content is None:
        (dist / name).mkdir()
    else:
        (dist / name).write_bytes(content)


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "missing",
        "stray file",
        "dot file",
        "directory",
        "two wheels",
        "no sdist",
        "fake wheel",
        "dot-named sdist",
    ],
)
def test_dist_must_hold_exactly_one_wheel_and_one_sdist(tmp_path: Path, case: str) -> None:
    root = _tree(tmp_path, BASE)
    dist = _dist(tmp_path)
    assert _run(root, tmp_path, LIST, "--dist", str(dist)).returncode == 0  # the control
    wheel = (dist / "demo-1.2.3-py3-none-any.whl").read_bytes()
    if case == "empty":
        shutil.rmtree(dist)
        dist.mkdir()
    elif case == "missing":
        shutil.rmtree(dist)
    elif case == "stray file":
        _plant(dist, "notes.txt", b"x\n")
    elif case == "dot file":
        _plant(dist, ".gitkeep", b"")
    elif case == "directory":
        _plant(dist, "demo-1.2.3-py3-none-any.whl.d")
    elif case == "two wheels":
        _plant(dist, "demo-1.2.3-cp311-none-any.whl", wheel)
    elif case == "no sdist":
        (dist / "demo-1.2.3.tar.gz").unlink()
    elif case == "fake wheel":
        (dist / "demo-1.2.3-py3-none-any.whl").write_bytes(b"not a zip\n")
    elif case == "dot-named sdist":  # an upload's `dist/*` glob would not send it
        (dist / "demo-1.2.3.tar.gz").rename(dist / ".demo-1.2.3.tar.gz")
    proc = _run(root, tmp_path, LIST, "--dist", str(dist))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


def test_dist_and_archive_cannot_be_combined(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    dist = _dist(tmp_path)
    wheel = dist / "demo-1.2.3-py3-none-any.whl"
    proc = _run(root, tmp_path, LIST, "--dist", str(dist), "--archive", str(wheel))
    assert proc.returncode == 2


def test_an_empty_dist_argument_is_an_error_not_the_working_directory(tmp_path: Path) -> None:
    """``Path("")`` is the working directory: run from inside one, it would be scanned."""
    root = _tree(tmp_path, BASE)
    dist = _dist(tmp_path)
    assert _run(root, tmp_path, LIST, "--dist", ".", cwd=dist).returncode == 0  # the control
    proc = _run(root, tmp_path, LIST, "--dist", "", cwd=dist)
    assert proc.returncode == 2, proc.stdout
    assert "--dist needs a directory" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_an_artifact_file_name_is_read_as_a_path(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    dist = _dist(tmp_path)
    (dist / "demo-1.2.3-py3-none-any.whl").rename(dist / "zqplant-1.2.3-py3-none-any.whl")
    proc = _run(root, tmp_path, LIST, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["wheel:0 S01"]
    _assert_no_leak(proc)


@pytest.mark.parametrize("field_name", ["uname", "gname"])
def test_a_tar_member_owner_and_group_are_read(tmp_path: Path, field_name: str) -> None:
    root = _tree(tmp_path, {**BASE, "README.md": "# Demo\n"})
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo("demo-1.2.3/README.md")
        info.size = len(b"# Demo\n")
        setattr(info, field_name, "zqplant")
        tar.addfile(info, io.BytesIO(b"# Demo\n"))
    dist = _dist(tmp_path)
    (dist / "demo-1.2.3.tar.gz").write_bytes(gzip.compress(raw.getvalue()))
    proc = _run(root, tmp_path, LIST, "--dist", str(dist))
    assert proc.returncode == 1
    assert _hits(proc) == ["sdist!README.md#tar-header:1 S01"]
    _assert_no_leak(proc)


def test_a_version_bump_needs_no_register_edit(tmp_path: Path) -> None:
    """Two releases that differ in version, field order and readme length share one register.

    The second build adds a URL and a classifier above Author and a paragraph
    to the readme below the registered line, as an ordinary release does.
    """
    listed = LIST + _README_ENTRY + _WHEEL_AUTHOR + _SDIST_AUTHOR
    builds = [
        ("1.2.3", "", _README),
        (
            "1.3.0",
            "Project-URL: Home, https://example.invalid\nClassifier: B\n",
            _README + "\nMore.\n",
        ),
    ]
    for version, extra, readme in builds:
        base = tmp_path / version
        base.mkdir()
        root = _metadata_tree(base, readme=readme)
        metadata = _metadata(version, extra, readme)
        dist = _dist(
            base,
            wheel={f"demo-{version}.dist-info/METADATA": metadata, "demo/__init__.py": "x = 1\n"},
            sdist={"PKG-INFO": metadata, "README.md": readme, "pyproject.toml": _PYPROJECT},
            version=version,
        )
        proc = _run(root, base, listed, "--dist", str(dist))
        assert proc.returncode == 0, (version, proc.stdout)
        assert "3 excused, 0 stale exception(s)" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# The workflow that runs the gate
# ---------------------------------------------------------------------------


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_runs_on_pull_requests_and_main() -> None:
    on = _workflow()[True]  # PyYAML reads the bare key `on` as True
    assert "pull_request" in on
    assert "main" in on["push"]["branches"]


def test_workflow_is_read_only_and_hands_the_secret_only_to_the_scan() -> None:
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read"}
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    with_secret = [s for s in steps if "NAME_GATE_TOKENS" in str(s.get("env", {}))]
    assert len(with_secret) == 1
    # Nowhere else: not at workflow or job level, where every step would see it.
    assert WORKFLOW.read_text(encoding="utf-8").count("secrets.NAME_GATE_TOKENS") == 1
    scan = with_secret[0]
    assert scan["env"]["NAME_GATE_TOKENS"] == "${{ secrets.NAME_GATE_TOKENS }}"
    assert "scripts/ci/check-name-gate.py" in scan["run"]
    assert "||" not in scan["run"], "the gate's exit status must reach the job"
    for job in wf["jobs"].values():
        assert not job.get("continue-on-error"), "a gate that cannot fail the job is no gate"
        for step in job["steps"]:
            assert not step.get("continue-on-error")


def test_workflow_job_has_a_timeout() -> None:
    for job in _workflow()["jobs"].values():
        assert 0 < int(job["timeout-minutes"]) <= 30


def test_workflow_can_read_pdf_text() -> None:
    steps = [s for job in _workflow()["jobs"].values() for s in job["steps"]]
    assert any("poppler-utils" in str(s.get("run", "")) for s in steps)


def test_workflow_checkout_does_not_persist_credentials() -> None:
    steps = [s for job in _workflow()["jobs"].values() for s in job["steps"]]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts
    for step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False


# The check-run name branch protection matches. GitHub names a job's check
# run after its `name:`, and a matrix adds its values to that name.
_CHECK_RUN = "Name gate"


def _gate_job() -> dict:
    jobs = [j for j in _workflow()["jobs"].values() if j.get("name") == _CHECK_RUN]
    assert len(jobs) == 1, (
        f"exactly one job must be named {_CHECK_RUN!r}, the check-run name branch "
        f"protection matches; found {len(jobs)}. A renamed job reports under "
        f"another name, so no check run named {_CHECK_RUN!r} ever reports."
    )
    return jobs[0]


def _scan_step(job: dict) -> dict:
    scans = [s for s in job["steps"] if "scripts/ci/check-name-gate.py" in str(s.get("run", ""))]
    assert len(scans) == 1, f"the {_CHECK_RUN!r} job must run the scan exactly once"
    return scans[0]


def test_the_gate_check_is_the_job_that_scans() -> None:
    """The job named for the check is the one that runs the scan.

    Moving the scan to another job would leave a check with this name
    reporting green having scanned nothing, and a matrix would rename the
    check run so a check with this name never reports.
    """
    job = _gate_job()
    assert "strategy" not in job, "a matrix renames the check run"
    _scan_step(job)


def test_the_gate_check_cannot_be_skipped() -> None:
    """Nothing can turn the gate into a skipped run.

    GitHub counts a skipped check run as satisfying a required check, so
    an `if:` on the job or on any of its steps, or a `needs:` whose failure
    skips the job, would let a skipped run stand in for a scan. A path or
    branch filter on `pull_request` would leave pull requests unscanned.
    """
    on = _workflow()[True]  # PyYAML reads the bare key `on` as True
    pull_request = on["pull_request"] or {}
    for key in ("paths", "paths-ignore", "branches-ignore"):
        assert key not in pull_request, f"on.pull_request.{key} leaves pull requests unscanned"
    assert pull_request.get("branches") == ["main"]
    job = _gate_job()
    assert "if" not in job, "a job-level `if:` can skip the check"
    assert not job.get("needs"), "a failed `needs:` skips the job, which satisfies the check"
    for step in job["steps"]:
        assert "if" not in step, f"step {step.get('name') or step.get('uses')!r} has an `if:`"


def test_the_gate_scans_the_whole_checkout() -> None:
    """The scan command, the checkout inputs and each step's action or command are pinned.

    The gate lists files with `git -C <root> ls-files` and skips the content
    of a tracked file that is missing from the work tree. A narrower
    `--root`, a working directory with the script path adjusted to match, a
    checkout input such as `ref` or `sparse-checkout`, or a step that removes
    files before the scan can each let the check pass without reading the
    pull request's whole tree.
    """
    wf = _workflow()
    job = _gate_job()
    scan = _scan_step(job)
    assert scan["run"].strip() == "python scripts/ci/check-name-gate.py --root ."
    assert "working-directory" not in scan, "a step working directory can narrow the scan"
    assert "defaults" not in job, "job defaults can set a working directory for the scan"
    assert "defaults" not in wf, "workflow defaults can set a working directory for the scan"
    shape = [
        (str(s.get("uses", "")).split("@")[0], str(s.get("run", "")).strip()) for s in job["steps"]
    ]
    assert shape == [
        ("actions/checkout", ""),
        ("actions/setup-python", ""),
        (
            "",
            "sudo apt-get update -q && sudo apt-get install -y -q "
            "--no-install-recommends poppler-utils",
        ),
        ("", "python scripts/ci/check-name-gate.py --root ."),
    ], (
        "each step's action or command is pinned: "
        "a step added before the scan can change the tree it reads"
    )
    assert job["steps"][0].get("with") == {"persist-credentials": False}, (
        "a checkout input such as ref or sparse-checkout changes the tree the scan reads"
    )


# ---------------------------------------------------------------------------
# Pre-check hardening: each case with the control that shows the harness bites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["examples/C#/zqplant-client.cs", "notes/issue#12-zqplant.md", "a!b/zqplant.txt"],
)
def test_a_path_split_by_hash_or_bang_is_still_redacted(tmp_path: Path, name: str) -> None:
    proc = _run(_tree(tmp_path, {**BASE, name: "clean\n"}), tmp_path)
    assert proc.returncode == 1
    assert len(_hits(proc)) == 1 and _hits(proc)[0].endswith(":0 S01")
    _assert_no_leak(proc)  # the property that matters: the name is not printed


def test_a_gitlink_path_is_read(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    nested = root / "clients" / "zqplant"
    nested.mkdir(parents=True)
    _git(nested, "init", "-q")
    (nested / "x.txt").write_text("x\n", encoding="utf-8")
    _git(nested, "add", "x.txt")
    _git(nested, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x")
    _git(root, "add", "clients/zqplant")
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "clients"], capture_output=True, text=True
    ).stdout
    assert listed.startswith("160000"), "the harness must create a gitlink"
    proc = _run(root, tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == [f"{_redacted('clients/zqplant')}:0 S01"]


def _tar_gz(members: list[tarfile.TarInfo], payloads: dict[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info in members:
            data = payloads.get(info.name)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return gzip.compress(raw.getvalue())


def test_tar_directory_and_link_members_and_link_targets_are_read(tmp_path: Path) -> None:
    d = tarfile.TarInfo("clients/zqplant")
    d.type = tarfile.DIRTYPE
    s = tarfile.TarInfo("docs/ptr")
    s.type, s.linkname = tarfile.SYMTYPE, "zqother5.txt"
    h = tarfile.TarInfo("docs/copy.txt")
    h.type, h.linkname = tarfile.LNKTYPE, "docs/zqplant.txt"
    data = _tar_gz([d, s, h], {})
    proc = _run(_tree(tmp_path, {**BASE, "dist.tar.gz": data}), tmp_path)
    assert proc.returncode == 1
    hits = _hits(proc)
    assert f"dist.tar.gz!{_redacted('clients/zqplant')}:0 S01" in hits
    assert "dist.tar.gz!docs/ptr#tar-header:1 S02" in hits
    assert "dist.tar.gz!docs/copy.txt#tar-header:1 S01" in hits
    _assert_no_leak(proc)


def test_tar_pax_headers_are_read(tmp_path: Path) -> None:
    f = tarfile.TarInfo("a.txt")
    f.size = 2
    f.pax_headers = {"comment": "made for zqplant"}
    proc = _run(_tree(tmp_path, {**BASE, "dist.tar.gz": _tar_gz([f], {"a.txt": b"x\n"})}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["dist.tar.gz!a.txt#tar-header:1 S01"]


def test_zip_directory_entries_and_comments_are_read(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("clients/zqplant/"), b"")
        member = zipfile.ZipInfo("a.txt")
        member.comment = b"zqother9"
        z.writestr(member, "clean\n")
        z.comment = b"packed for zqplant"
    proc = _run(_tree(tmp_path, {**BASE, "pack.zip": buf.getvalue()}), tmp_path)
    assert proc.returncode == 1
    assert sorted(_hits(proc)) == sorted(
        [
            f"pack.zip!{_redacted('clients/zqplant/')}:0 S01",
            "pack.zip!a.txt#comment:1 S02",
            "pack.zip#comment:1 S01",
        ]
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"x\r\nzqline\r\ny\r\n", "notes.txt:2 S04"),  # CRLF
        (b"x\rzqline\ry\r", "notes.txt:2 S04"),  # CR only
        (b"\xef\xbb\xbfzqline\r\n", "notes.txt:1 S04"),  # UTF-8 BOM
        ("x\r\nzqline\r\n".encode("utf-16"), "notes.txt:2 S04"),  # UTF-16 BOM + CRLF
        ("zqplant\n".encode("utf-32"), "notes.txt:1 S01"),  # UTF-32 BOM
        (b'{"name": "zq\\u2019mark"}\n', "notes.txt:1 S03"),  # JSON ASCII escape
    ],
)
def test_line_ends_byte_order_marks_and_escapes(
    tmp_path: Path, content: bytes, expected: str
) -> None:
    proc = _run(_tree(tmp_path, {**BASE, "notes.txt": content}), tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert _hits(proc) == [expected]


def test_a_nul_free_binary_is_binary_not_cp1252_text(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {**BASE, "status.md.br": b"\x1b\x05\x01zqplant\x02"}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["status.md.br:0 BINARY"]


def test_text_that_mentions_the_pdf_magic_is_read_as_text(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {**BASE, "sniff.py": "MAGIC = b'%PDF'  # zqother3\n"}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["sniff.py:1 S02"]


def test_a_plain_tar_is_binary_even_when_a_pdf_comes_first(tmp_path: Path) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, data in (("report.pdf", _pdf("Quarterly")), ("names.txt", b"zqplant\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    proc = _run(_tree(tmp_path, {**BASE, "docs.tar": raw.getvalue()}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["docs.tar:0 BINARY"]


def test_a_zip_with_a_stored_pdf_first_still_reads_every_member(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:  # ZIP_STORED: the PDF's bytes sit near the start
        z.writestr("report.pdf", _pdf("Quarterly"))
        z.writestr("names.txt", "zqplant\n")
    proc = _run(_tree(tmp_path, {**BASE, "docs.zip": buf.getvalue()}), tmp_path)
    assert proc.returncode == 1
    assert "docs.zip!names.txt:1 S01" in _hits(proc)


def test_a_git_lfs_pointer_is_unreadable(tmp_path: Path) -> None:
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 12\n"
    proc = _run(_tree(tmp_path, {**BASE, "docs/big.pdf": pointer}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["docs/big.pdf:0 UNREADABLE"]


def test_an_sdist_that_is_not_a_tar_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "demo-1.0.tar.gz"
    path.write_bytes(gzip.compress(b"just text zqplant\n"))
    proc = _run(_tree(tmp_path, BASE), tmp_path, LIST, "--archive", str(path))
    assert proc.returncode == 2


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="needs pdfinfo")
def test_custom_pdf_information_keys_are_read(tmp_path: Path) -> None:
    pdf = _pdf("Quarterly").replace(b"/Title (Report)", b"/Title (Report) /Client (zqplant)")
    pdf = _repair_xref(pdf)
    proc = _run(_tree(tmp_path, {**BASE, "docs/report.pdf": pdf}), tmp_path)
    assert proc.returncode == 1
    assert any(
        h.startswith("docs/report.pdf#pdf-meta:") and h.endswith(" S01") for h in _hits(proc)
    )


def _repair_xref(pdf: bytes) -> bytes:
    """Recompute xref offsets after an in-place edit of a _pdf() document."""
    head, _, _ = pdf.partition(b"xref\n")
    offsets, pos = [], 0
    while True:
        i = head.find(b" 0 obj\n", pos)
        if i < 0:
            break
        start = head.rfind(b"\n", 0, i) + 1
        offsets.append(start)
        pos = i + 1
    out = bytearray(head)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\n" % (len(offsets) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


@pytest.mark.skipif(
    sys.platform != "linux", reason="needs a file system that allows undecodable names"
)
def test_an_undecodable_file_name_does_not_crash_the_output(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    name = os.fsencode(str(root)) + b"/caf\xe9-notes.txt"
    with open(name, "wb") as handle:
        handle.write(b"zqother4\n")
    proc = _run(root, tmp_path)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert any(h.endswith(":1 S02") for h in _hits(proc))
    assert " file(s) scanned" in proc.stdout


# ---------------------------------------------------------------------------
# Review round 2
# ---------------------------------------------------------------------------


def test_a_text_file_that_mentions_the_pdf_header_is_plain_text(tmp_path: Path) -> None:
    readme = "# Demo\n\nEvery PDF we produce starts with `%PDF-1.7`.\n"
    clean = _run(_tree(tmp_path, {**BASE, "README.md": readme}), tmp_path)
    assert clean.returncode == 0, clean.stdout
    (tmp_path / "tree").rename(tmp_path / "tree-clean")
    hit = _run(_tree(tmp_path, {**BASE, "README.md": readme + "zqother8\n"}), tmp_path)
    assert _hits(hit) == ["README.md:4 S02"]


def test_a_text_file_that_embeds_pdf_bytes_is_still_read_as_text(tmp_path: Path) -> None:
    source = (
        '# maintainer: zqmüller\nSAMPLE = b"""'
        + _pdf("Quarterly").decode("latin-1").encode("unicode_escape").decode("ascii")
        + '"""\n'
    )
    proc = _run(_tree(tmp_path, {**BASE, "tests/fixture_pdf.py": source}), tmp_path)
    assert proc.returncode == 1
    assert _hits(proc) == ["tests/fixture_pdf.py:1 S05"]


def test_a_non_ascii_name_in_a_raw_pdf_string_is_read(tmp_path: Path) -> None:
    pdf = _pdf("Quarterly", title="Reviewed by zqmüller")
    assert b"zqm\xfcller" in pdf, "the name must sit in the raw bytes as Latin-1"
    proc = _run(_tree(tmp_path, {**BASE, "docs/report.pdf": pdf}), tmp_path)
    assert proc.returncode == 1
    assert any(h.startswith("docs/report.pdf#pdf-raw:") and h.endswith(" S05") for h in _hits(proc))


def test_an_all_text_pdf_is_read_as_a_pdf_and_as_text(tmp_path: Path) -> None:
    pdf = (
        b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n"
        b"2 0 obj << /Length 40 >> stream\nBT (Prepared for zqplant) Tj ET\nendstream endobj\n"
        b"trailer << /Root 1 0 R >>\n%%EOF\n"
    )
    proc = _run(_tree(tmp_path, {**BASE, "docs/plain.pdf": pdf}), tmp_path)
    assert proc.returncode == 1
    assert "docs/plain.pdf:4 S01" in _hits(proc)  # the text reading, line 4


def test_a_tar_whose_header_breaks_the_reader_is_unreadable_and_not_echoed(
    tmp_path: Path,
) -> None:
    f = tarfile.TarInfo("a.txt")
    f.size = 2
    f.pax_headers = {"GNU.sparse.size": "zqplant"}
    proc = _run(_tree(tmp_path, {**BASE, "dist.tar.gz": _tar_gz([f], {"a.txt": b"x\n"})}), tmp_path)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "dist.tar.gz:0 UNREADABLE" in _hits(proc)
    _assert_no_leak(proc)


def test_an_archive_comment_exception_is_in_the_archive_scope(tmp_path: Path) -> None:
    root = _tree(tmp_path, BASE)
    path = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("demo/__init__.py", "x = 1\n")
        z.comment = b"built for zqplant"
    listed = LIST + "exception wheel#comment:1 S01\n"
    archive_run = _run(root, tmp_path, listed, "--archive", str(path))
    tree_run = _run(root, tmp_path, listed)
    assert archive_run.returncode == 0, archive_run.stdout
    assert tree_run.returncode == 0, tree_run.stdout


PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"

_PDF_EXTRACTOR = (
    "sudo apt-get update -q && sudo apt-get install -y -q --no-install-recommends poppler-utils"
)
_TOKENS_ENV = {"NAME_GATE_TOKENS": "${{ secrets.NAME_GATE_TOKENS }}"}

# The gate job as name-gate.yml defines it, each action's `@<ref>` dropped
# (tests/prereqs/test_workflow_sha_pinning.py checks the pins).
_EXPECTED_GATE_JOB = {
    "name": _CHECK_RUN,
    "runs-on": "ubuntu-latest",
    "timeout-minutes": 15,
    "steps": [
        {"uses": "actions/checkout", "with": {"persist-credentials": False}},
        {"uses": "actions/setup-python", "with": {"python-version": "3.11"}},
        {"name": "Install the PDF text extractor", "run": _PDF_EXTRACTOR},
        {
            "name": "Scan the tree",
            "env": _TOKENS_ENV,
            "run": "python scripts/ci/check-name-gate.py --root .",
        },
    ],
}

# The release workflow, refs dropped the same way. `quality` is the build
# half: it runs ci.yml, whose `build` job uploads the `dist` artifact and
# records its SHA-256, with no secret and no id-token. `publish` builds
# nothing: it verifies that artifact, scans it with the tree, and uploads it.
_EXPECTED_QUALITY_JOB = {
    "name": "CI",
    "permissions": {"contents": "read"},
    "uses": "./.github/workflows/ci.yml",
}
_VERIFY_RECORDED = (
    'printf \'%s\\n\' "$RECORDED" > "$RUNNER_TEMP/dist.sha256"\n'
    'sha256sum --check --strict "$RUNNER_TEMP/dist.sha256"\n'
    "test \"$(find . -mindepth 1 -maxdepth 1 -printf '%f\\n' | sort)\" = "
    '"$(sed -n \'s/^[0-9a-f]\\{64\\} [ *]//p\' "$RUNNER_TEMP/dist.sha256" | sort)"\n'
)
_DIST_SCAN = "python scripts/ci/check-name-gate.py --root . --dist dist"
_EXPECTED_PUBLISH_JOB = {
    "name": "Publish to PyPI",
    "needs": ["quality"],
    "runs-on": "ubuntu-latest",
    "environment": {"name": "pypi", "url": "https://pypi.org/p/sigantry"},
    "permissions": {"id-token": "write", "contents": "read"},
    "steps": [
        {
            "name": "Checkout repository",
            "uses": "actions/checkout",
            "with": {"persist-credentials": False},
        },
        {
            "name": "Set up Python 3.11",
            "uses": "actions/setup-python",
            "with": {"python-version": "3.11"},
        },
        {"name": "Install the PDF text extractor", "run": _PDF_EXTRACTOR},
        {
            "name": "Download the distributions CI built and checked",
            "uses": "actions/download-artifact",
            "with": {"name": "dist", "path": "dist/"},
        },
        {
            "name": "Verify them against the SHA-256 recorded at build",
            "env": {"RECORDED": "${{ needs.quality.outputs.dist-sha256 }}"},
            "working-directory": "dist",
            "run": _VERIFY_RECORDED,
        },
        {
            "name": "Scan the tree and the distributions for names",
            "env": _TOKENS_ENV,
            "run": _DIST_SCAN,
        },
        {
            "name": "Publish package distributions to PyPI",
            "uses": "pypa/gh-action-pypi-publish",
            "with": {"skip-existing": True},
        },
    ],
}


def _without_action_refs(job: dict) -> dict:
    steps = [
        {**step, "uses": step["uses"].split("@", 1)[0]} if "uses" in step else step
        for step in job["steps"]
    ]
    return {**job, "steps": steps}


def test_the_gate_job_is_pinned_whole() -> None:
    """The gate job is pinned whole, with the workflow's top-level keys and permissions.

    Naming the keys that can neuter the scan one at a time never ends: a
    step's ``shell:``, an ``env:`` entry such as ``PYTHONPATH`` or
    ``BASH_ENV``, a ``container:``, another runner, or a workflow-level
    ``env:`` or ``defaults:`` can each let the check pass without reading the
    tree. So the whole job is compared. A change to the job updates
    ``_EXPECTED_GATE_JOB`` in the same commit, where review sees it. The
    triggers are checked by the tests above, and the action refs by
    ``tests/prereqs/test_workflow_sha_pinning.py``, which requires a commit
    SHA but does not say which.
    """
    wf = _workflow()
    assert set(wf) == {"name", True, "permissions", "concurrency", "jobs"}, (
        f"name-gate.yml's top-level keys changed: {sorted(map(str, wf))}"
    )
    assert wf["permissions"] == {"contents": "read"}
    assert set(wf["jobs"]) == {"name-gate"}, "name-gate.yml runs exactly one job"
    assert _without_action_refs(_gate_job()) == _EXPECTED_GATE_JOB


def test_no_other_job_reports_under_the_gate_check_name() -> None:
    """Only the gate job is named for the check.

    Branch protection matches a check run by its name, whichever workflow
    posts it, so a job given the same name in another workflow could report
    a passing check without scanning. A job with no ``name:`` reports under
    its id, which cannot contain a space. A name built from an expression is
    not resolved here.
    """
    others = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (workflow.get("jobs") or {}).items():
            if isinstance(job, dict) and job.get("name") == _CHECK_RUN and path != WORKFLOW:
                others.append(f"{path.name}::{job_id}")
    assert others == [], f"only name-gate.yml may name a job {_CHECK_RUN!r}: {others}"


def test_the_release_path_runs_the_gate() -> None:
    """The one job that uploads to PyPI scans the tree and the distributions it uploads.

    ``quality`` runs ci.yml, which does not receive the token list, so
    without this a release from a ref that never passed the pull-request
    check would upload unchecked. The scan reads the downloaded ``dist``
    directory, which is what the publish step uploads, and it is the last
    step before that upload. The job is compared whole, for the reasons
    ``test_the_gate_job_is_pinned_whole`` gives: a step that changed the
    files after the scan, or one that skipped or neutered it, would otherwise
    pass. Every publish job the quality-gate tests detect (``_publish_jobs``,
    with its pinned gaps) must be this one, so a second upload job cannot go
    round the scan, and the workflow holds no job besides it and its gate.
    """
    publish_jobs = sorted(
        f"{path.name}::{job_id}"
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
        for job_id in _publish_jobs(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    )
    assert publish_jobs == ["publish-pypi.yml::publish"], (
        "every job that publishes must run the name gate first; give a new "
        f"publish job the scan and pin it here: {publish_jobs}"
    )
    wf = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    assert set(wf) == {"name", True, "concurrency", "jobs"}, (
        "publish-pypi.yml's top-level keys changed; workflow-level env, defaults "
        f"or permissions reach every step: {sorted(map(str, wf))}"
    )
    assert set(wf["jobs"]) == {"quality", "publish"}, (
        f"publish-pypi.yml runs its gate and its publish job only: {sorted(wf['jobs'])}"
    )
    assert wf["jobs"]["quality"] == _EXPECTED_QUALITY_JOB
    job = wf["jobs"]["publish"]
    steps = [(str(s.get("uses", "")).split("@")[0], str(s.get("run", ""))) for s in job["steps"]]
    for step in (("actions/download-artifact", ""), ("", _DIST_SCAN)):
        assert step in steps, f"the publish job has no {step[0] or step[1]!r} step"
    download = steps.index(("actions/download-artifact", ""))
    scan = steps.index(("", _DIST_SCAN))
    upload = steps.index(("pypa/gh-action-pypi-publish", ""))
    assert download < scan == upload - 1, (
        "the scan must read the downloaded distributions and be the last step before the upload"
    )
    assert _without_action_refs(job) == _EXPECTED_PUBLISH_JOB
