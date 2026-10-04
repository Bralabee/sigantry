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
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "NAME_GATE_TOKENS"}
    args = [sys.executable, str(GATE), "--root", str(root), *extra]
    if env_value is not None:
        env["NAME_GATE_TOKENS"] = env_value
    elif list_text is not None:
        list_file = tmp_path / "list.txt"
        list_file.write_text(list_text, encoding="utf-8")
        args += ["--list-file", str(list_file)]
    return subprocess.run(args, capture_output=True, text=True, env=env, check=False)


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
    assert _hits(proc) == ["wheel!demo.dist-info/METADATA:2 S01"]


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

# The job in publish-pypi.yml that uploads to PyPI, refs dropped the same way.
_EXPECTED_PUBLISH_JOB = {
    "name": "Build & Publish to PyPI",
    "needs": ["quality"],
    "runs-on": "ubuntu-latest",
    "environment": {"name": "pypi", "url": "https://pypi.org/p/sigantry"},
    "permissions": {"id-token": "write", "contents": "read"},
    "steps": [
        {"name": "Checkout repository", "uses": "actions/checkout"},
        {
            "name": "Set up Python 3.11",
            "uses": "actions/setup-python",
            "with": {"python-version": "3.11", "cache": "pip"},
        },
        {"name": "Install the PDF text extractor", "run": _PDF_EXTRACTOR},
        {
            "name": "Scan the tree for names",
            "env": _TOKENS_ENV,
            "run": "python scripts/ci/check-name-gate.py --root .",
        },
        {
            "name": "Install packaging tools",
            "run": "python -m pip install --upgrade pip build twine",
        },
        {"name": "Build sdist and wheel", "run": "python -m build"},
        {"name": "Check package metadata with twine", "run": "twine check --strict dist/*"},
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
    """The one job that uploads to PyPI scans the released tree before it builds.

    ``quality`` runs ci.yml, which does not receive the token list, so
    without this a release from a ref that never passed the pull-request
    check, or a manual run on any branch, would upload unchecked. The job is
    compared whole, for the reasons ``test_the_gate_job_is_pinned_whole``
    gives: a step that changed the tree after the scan, or one that skipped
    or neutered it, would otherwise pass. Every publish job the quality-gate
    tests detect (``_publish_jobs``, with its pinned gaps) must be this one,
    so a second upload job cannot go round the scan.
    """
    publish_jobs = sorted(
        f"{path.name}::{job_id}"
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
        for job_id in _publish_jobs(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    )
    assert publish_jobs == ["publish-pypi.yml::build-and-publish"], (
        "every job that publishes must run the name gate first; give a new "
        f"publish job the scan and pin it here: {publish_jobs}"
    )
    wf = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    assert "env" not in wf and "defaults" not in wf, (
        "workflow-level env or defaults reach the scan step"
    )
    job = wf["jobs"]["build-and-publish"]
    assert _without_action_refs(job) == _EXPECTED_PUBLISH_JOB
