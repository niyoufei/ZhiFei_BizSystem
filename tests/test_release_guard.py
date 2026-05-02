import zipfile
from pathlib import Path

import pytest

from tools.release_guard import (
    ReleaseGuardError,
    ensure_annotated_tag,
    ensure_package_can_be_written,
    ensure_peel_matches,
    is_sensitive_path,
    normalize_prefix,
    sha256_file,
    validate_zip_entries,
)


PREFIX = "ZhiFei_BizSystem-v0.1.19-qingtian-analysis-bundle-markdown-copy/"


def _write_zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


def _valid_entries(prefix: str = PREFIX) -> dict[str, str]:
    return {
        prefix: "",
        prefix + "app/main.py": "print('ok')\n",
        prefix + "tests/test_delivery_page_entries.py": "def test_delivery(): pass\n",
        prefix + "tests/test_main.py": "def test_main(): pass\n",
        prefix + "README.md": "# readme\n",
        prefix + "pyproject.toml": "[tool.pytest.ini_options]\n",
        prefix + "requirements.txt": "pytest\n",
        prefix + "docs/": "",
        prefix + "docs/qingtian-report-evidence-delivery.md": "# docs\n",
    }


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".env", True),
        ("nested/.env", True),
        ("data/build/logs/run.log", True),
        (".pytest_cache/README.md", True),
        (".ruff_cache/CACHEDIR.TAG", True),
        ("app/__pycache__/main.cpython-312.pyc", True),
        ("app/main.pyc", True),
        (".DS_Store", True),
        ("notes.tmp", True),
        ("notes.bak", True),
        ("notes.swp", True),
        (".env.example", False),
        ("app/main.py", False),
    ],
)
def test_sensitive_path_matching(path, expected):
    assert is_sensitive_path(path) is expected


def test_prefix_normalization_explicit():
    assert normalize_prefix("/release-root//") == "release-root/"


def test_sha256_file(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("abc", encoding="utf-8")

    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_existing_zip_without_allow_overwrite_blocks(tmp_path):
    package_path = tmp_path / "release.zip"
    package_path.write_bytes(b"already here")

    with pytest.raises(ReleaseGuardError, match="already exists"):
        ensure_package_can_be_written(package_path, allow_overwrite=False)


def test_existing_zip_with_allow_overwrite_passes(tmp_path):
    package_path = tmp_path / "release.zip"
    package_path.write_bytes(b"already here")

    ensure_package_can_be_written(package_path, allow_overwrite=True)


def test_non_annotated_tag_blocks():
    with pytest.raises(ReleaseGuardError, match="not annotated"):
        ensure_annotated_tag("commit")


def test_peel_commit_mismatch_blocks():
    with pytest.raises(ReleaseGuardError, match="peel commit mismatch"):
        ensure_peel_matches("abc123", "def456")


@pytest.mark.parametrize(
    "entry",
    [
        PREFIX + ".env",
        PREFIX + "app/__pycache__/main.pyc",
        PREFIX + "app/main.pyc",
    ],
)
def test_zip_sensitive_entries_block(tmp_path, entry):
    entries = _valid_entries()
    entries[entry] = "secret"
    package_path = _write_zip(tmp_path / "release.zip", entries)

    with pytest.raises(ReleaseGuardError, match="sensitive/cache"):
        validate_zip_entries(package_path, prefix=PREFIX)


def test_zip_missing_critical_file_blocks(tmp_path):
    entries = _valid_entries()
    del entries[PREFIX + "app/main.py"]
    package_path = _write_zip(tmp_path / "release.zip", entries)

    with pytest.raises(ReleaseGuardError, match="missing critical"):
        validate_zip_entries(package_path, prefix=PREFIX)


def test_valid_zip_entries_pass(tmp_path):
    package_path = _write_zip(tmp_path / "release.zip", _valid_entries())

    result = validate_zip_entries(package_path, prefix=PREFIX)

    assert result["entry_count"] == len(_valid_entries())
    assert result["prefix"] == PREFIX
    assert result["critical_files"] == "present"
    assert result["sensitive_entries"] == []
    assert len(result["sha256"]) == 64
