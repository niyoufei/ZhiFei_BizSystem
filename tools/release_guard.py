#!/usr/bin/env python3
"""Release quality gate for tagged ZhiFei_BizSystem snapshots.

The guard is intentionally self-contained and standard-library only. It keeps
the release path auditable: check the git baseline, validate the annotated tag,
optionally create a zip with git archive, and verify the zip with zipfile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


SENSITIVE_RE = re.compile(
    r"(^|/)\.env$"
    r"|(^|/)data/build/logs/"
    r"|(^|/)\.pytest_cache/"
    r"|(^|/)\.ruff_cache/"
    r"|(^|/)__pycache__/"
    r"|\.pyc$"
    r"|\.DS_Store$"
    r"|\.tmp$"
    r"|\.bak$"
    r"|\.swp$"
)

CRITICAL_PATHS = (
    "app/main.py",
    "tests/test_delivery_page_entries.py",
    "tests/test_main.py",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
)

CRITICAL_DIRS = ("docs/",)


@dataclass
class GuardContext:
    mode: str
    repo: Path
    checks: dict[str, object] = field(default_factory=dict)
    sections: dict[str, object] = field(default_factory=dict)


class ReleaseGuardError(RuntimeError):
    """A blocking release gate failure."""

    def __init__(self, message: str, *, context: GuardContext | None = None) -> None:
        super().__init__(message)
        self.context = context


def run_command(args: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        list(args),
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise RuntimeError(detail)
    return proc.stdout.strip()


def git(args: Sequence[str], *, repo: Path) -> str:
    return run_command(("git", *args), cwd=repo)


def fail(context: GuardContext, message: str) -> None:
    context.checks["blocking_error"] = message
    raise ReleaseGuardError(message, context=context)


def infer_project_name(repo: Path) -> str:
    name = repo.name
    for suffix in ("-qingtian-clean", "-clean"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def normalize_prefix(prefix: str | None, *, repo: Path | None = None, tag: str | None = None) -> str:
    if prefix is None:
        if repo is None or tag is None:
            raise ValueError("repo and tag are required when prefix is not provided")
        prefix = f"{infer_project_name(repo)}-{tag}"
    normalized = prefix.strip().strip("/")
    if not normalized:
        raise ValueError("prefix must not be empty")
    return f"{normalized}/"


def default_package_path(repo: Path, tag: str) -> Path:
    return repo.parent / f"{infer_project_name(repo)}-{tag}.zip"


def is_sensitive_path(path: str) -> bool:
    normalized = path.replace(os.sep, "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return bool(SENSITIVE_RE.search(normalized))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_package_can_be_written(path: Path, allow_overwrite: bool) -> None:
    if path.exists() and not allow_overwrite:
        raise ReleaseGuardError(f"package already exists and --allow-overwrite was not set: {path}")


def ensure_annotated_tag(tag_type: str) -> None:
    if tag_type != "tag":
        raise ReleaseGuardError(f"tag is not annotated: git cat-file -t returned {tag_type!r}")


def ensure_peel_matches(peel_commit: str, expected_commit: str) -> None:
    if peel_commit != expected_commit:
        raise ReleaseGuardError(
            f"tag peel commit mismatch: actual {peel_commit}, expected {expected_commit}"
        )


def parse_remote_tag_refs(output: str, tag: str) -> tuple[str | None, str | None]:
    tag_ref = f"refs/tags/{tag}"
    peel_ref = f"{tag_ref}^{{}}"
    tag_object = None
    peel_commit = None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref == tag_ref:
            tag_object = sha
        elif ref == peel_ref:
            peel_commit = sha
    return tag_object, peel_commit


def infer_zip_prefix(entries: Sequence[str]) -> str:
    first = entries[0]
    if "/" not in first:
        raise ReleaseGuardError("cannot infer zip prefix from entries")
    prefix = first.split("/", 1)[0] + "/"
    if not prefix.strip("/"):
        raise ReleaseGuardError("cannot infer zip prefix from empty root")
    return prefix


def validate_zip_entries(
    package_path: Path,
    *,
    prefix: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, object]:
    if not package_path.exists():
        raise ReleaseGuardError(f"zip package does not exist: {package_path}")
    if not zipfile.is_zipfile(package_path):
        raise ReleaseGuardError(f"not a readable zip file: {package_path}")

    with zipfile.ZipFile(package_path) as zf:
        bad_entry = zf.testzip()
        if bad_entry is not None:
            raise ReleaseGuardError(f"zip integrity check failed at entry: {bad_entry}")
        entries = zf.namelist()

    if not entries:
        raise ReleaseGuardError("zip package has no entries")

    effective_prefix = prefix or infer_zip_prefix(entries)
    if not all(entry.startswith(effective_prefix) for entry in entries):
        raise ReleaseGuardError(f"not all zip entries are under prefix {effective_prefix!r}")

    relative_entries = [entry[len(effective_prefix) :] for entry in entries]
    missing_files = [path for path in CRITICAL_PATHS if path not in relative_entries]
    missing_dirs = [path for path in CRITICAL_DIRS if path not in relative_entries]
    if missing_files or missing_dirs:
        missing = missing_files + missing_dirs
        raise ReleaseGuardError(f"zip package is missing critical entries: {', '.join(missing)}")

    sensitive = [entry for entry in relative_entries if is_sensitive_path(entry)]
    if sensitive:
        raise ReleaseGuardError(f"zip package contains sensitive/cache entries: {', '.join(sensitive)}")

    actual_sha256 = sha256_file(package_path)
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ReleaseGuardError(
            f"zip sha256 mismatch: actual {actual_sha256}, expected {expected_sha256}"
        )

    return {
        "path": str(package_path),
        "bytes": package_path.stat().st_size,
        "sha256": actual_sha256,
        "entry_count": len(entries),
        "prefix": effective_prefix,
        "critical_files": "present",
        "sensitive_entries": [],
    }


def find_local_cache_dirs(repo: Path) -> list[str]:
    names = {".pytest_cache", ".ruff_cache", "__pycache__"}
    found: list[str] = []
    for root, dirs, _files in os.walk(repo):
        root_path = Path(root)
        if ".git" in root_path.parts:
            dirs[:] = []
            continue
        for dirname in list(dirs):
            if dirname in names:
                found.append(str((root_path / dirname).relative_to(repo)))
    return sorted(found)


def run_pytest_collect(repo: Path) -> str:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return run_command(
        (sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"),
        cwd=repo,
        env=env,
    )


def preflight_checks(
    repo: Path,
    *,
    tag: str,
    expected_commit: str,
    skip_remote: bool = False,
    collect_tests: bool = False,
) -> GuardContext:
    ctx = GuardContext(mode="preflight", repo=repo)

    try:
        head = git(("rev-parse", "HEAD"), repo=repo)
        origin_main = git(("rev-parse", "origin/main"), repo=repo)
        branch = git(("branch", "--show-current"), repo=repo)
        status = git(("status", "--short"), repo=repo)
        status_untracked = git(("status", "--short", "--untracked-files=all"), repo=repo)
        tracked_files = git(("ls-files",), repo=repo).splitlines()
    except RuntimeError as exc:
        fail(ctx, f"git baseline query failed: {exc}")

    ctx.checks.update(
        {
            "repo": str(repo),
            "head": head,
            "origin_main": origin_main,
            "expected_commit": expected_commit,
            "detached": branch == "",
            "worktree_clean": status == "",
            "untracked_empty": status_untracked == "",
            "tracked_file_count": len(tracked_files),
        }
    )

    if head != expected_commit:
        fail(ctx, f"HEAD mismatch: actual {head}, expected {expected_commit}")
    if origin_main != expected_commit:
        fail(ctx, f"origin/main mismatch: actual {origin_main}, expected {expected_commit}")
    if head != origin_main:
        fail(ctx, f"HEAD does not equal origin/main: {head} != {origin_main}")
    if status:
        fail(ctx, f"worktree is not clean: {status}")
    if status_untracked:
        fail(ctx, f"untracked files are present: {status_untracked}")

    try:
        tag_list = git(("tag", "--list", tag), repo=repo)
        tag_type = git(("cat-file", "-t", tag), repo=repo)
        tag_object = git(("rev-parse", tag), repo=repo)
        tag_peel = git(("rev-list", "-n", "1", tag), repo=repo)
    except RuntimeError as exc:
        fail(ctx, f"local tag query failed: {exc}")

    if tag_list != tag:
        fail(ctx, f"tag does not exist locally: {tag}")
    try:
        ensure_annotated_tag(tag_type)
        ensure_peel_matches(tag_peel, expected_commit)
    except ReleaseGuardError as exc:
        fail(ctx, str(exc))

    ctx.checks.update(
        {
            "tag": tag,
            "tag_type": tag_type,
            "tag_object": tag_object,
            "tag_peel_commit": tag_peel,
        }
    )

    remote_tag_object = "skipped"
    remote_peel_commit = "skipped"
    if not skip_remote:
        try:
            remote_output = git(("ls-remote", "--tags", "origin", f"refs/tags/{tag}*"), repo=repo)
        except RuntimeError as exc:
            fail(ctx, f"remote tag query failed; remote link abnormal: {exc}")
        remote_tag_object, remote_peel_commit = parse_remote_tag_refs(remote_output, tag)
        if not remote_tag_object:
            fail(ctx, f"remote tag object not found: refs/tags/{tag}")
        if remote_peel_commit != expected_commit:
            fail(
                ctx,
                "remote tag peel commit mismatch: "
                f"actual {remote_peel_commit}, expected {expected_commit}",
            )

    ctx.checks.update(
        {
            "remote_tag_object": remote_tag_object,
            "remote_peel_commit": remote_peel_commit,
        }
    )

    sensitive_tracked = [path for path in tracked_files if is_sensitive_path(path)]
    if sensitive_tracked:
        fail(ctx, f"tracked sensitive/cache files found: {', '.join(sensitive_tracked)}")
    ctx.checks["sensitive_tracked_files"] = []

    missing_files = [path for path in CRITICAL_PATHS if not (repo / path).is_file()]
    missing_dirs = [path for path in CRITICAL_DIRS if not (repo / path.rstrip("/")).is_dir()]
    if missing_files or missing_dirs:
        fail(ctx, f"critical release entries missing: {', '.join(missing_files + missing_dirs)}")
    ctx.checks["critical_entries"] = "present"
    ctx.checks["local_cache_dirs"] = find_local_cache_dirs(repo)

    if collect_tests:
        try:
            collect_output = run_pytest_collect(repo)
        except RuntimeError as exc:
            fail(ctx, f"pytest collect-only failed: {exc}")
        ctx.checks["test_collect"] = "executed"
        ctx.sections["test_collect_output"] = collect_output
    else:
        ctx.checks["test_collect"] = "skipped"

    return ctx


def create_package(
    repo: Path,
    *,
    tag: str,
    expected_commit: str,
    package_path: Path,
    prefix: str,
    allow_overwrite: bool = False,
    skip_remote: bool = False,
    collect_tests: bool = False,
) -> GuardContext:
    ctx = preflight_checks(
        repo,
        tag=tag,
        expected_commit=expected_commit,
        skip_remote=skip_remote,
        collect_tests=collect_tests,
    )
    ctx.mode = "package"

    try:
        ensure_package_can_be_written(package_path, allow_overwrite)
    except ReleaseGuardError as exc:
        fail(ctx, str(exc))

    ctx.checks["package_overwrite_allowed"] = allow_overwrite
    try:
        git(
            (
                "archive",
                "--format=zip",
                f"--prefix={prefix}",
                "-o",
                str(package_path),
                tag,
            ),
            repo=repo,
        )
    except RuntimeError as exc:
        fail(ctx, f"git archive failed: {exc}")

    try:
        zip_info = validate_zip_entries(package_path, prefix=prefix)
    except ReleaseGuardError as exc:
        fail(ctx, str(exc))
    ctx.sections["zip"] = zip_info
    return ctx


def verify_zip_only(
    repo: Path,
    *,
    package_path: Path,
    prefix: str | None,
    expected_sha256: str | None,
) -> GuardContext:
    ctx = GuardContext(mode="verify-zip", repo=repo)
    try:
        zip_info = validate_zip_entries(
            package_path,
            prefix=prefix,
            expected_sha256=expected_sha256,
        )
    except ReleaseGuardError as exc:
        fail(ctx, str(exc))
    ctx.sections["zip"] = zip_info
    return ctx


def render_markdown(ctx: GuardContext, *, success: bool) -> str:
    checks = ctx.checks
    lines = [
        f"# release_guard {ctx.mode} report",
        "",
        f"- result: {'PASS' if success else 'FAIL'}",
        f"- repo: `{ctx.repo}`",
    ]
    if "blocking_error" in checks:
        lines.append(f"- blocking_error: {checks['blocking_error']}")

    lines.extend(["", "## Git baseline"])
    for key in (
        "head",
        "origin_main",
        "expected_commit",
        "detached",
        "worktree_clean",
        "untracked_empty",
        "tracked_file_count",
    ):
        if key in checks:
            lines.append(f"- {key}: `{checks[key]}`")

    lines.extend(["", "## Tag"])
    for key in (
        "tag",
        "tag_type",
        "tag_object",
        "tag_peel_commit",
        "remote_tag_object",
        "remote_peel_commit",
    ):
        if key in checks:
            lines.append(f"- {key}: `{checks[key]}`")

    lines.extend(["", "## Release entries"])
    if "critical_entries" in checks:
        lines.append(f"- critical_entries: `{checks['critical_entries']}`")
    if "sensitive_tracked_files" in checks:
        lines.append("- sensitive_tracked_files: none")
    if "local_cache_dirs" in checks:
        cache_dirs = checks["local_cache_dirs"]
        if isinstance(cache_dirs, list) and cache_dirs:
            lines.append("- local_cache_dirs:")
            lines.extend(f"  - `{path}`" for path in cache_dirs)
        else:
            lines.append("- local_cache_dirs: none")
    if "test_collect" in checks:
        lines.append(f"- test_collect: `{checks['test_collect']}`")

    if "zip" in ctx.sections:
        zip_info = ctx.sections["zip"]
        assert isinstance(zip_info, dict)
        lines.extend(["", "## Zip"])
        for key in ("path", "bytes", "sha256", "entry_count", "prefix"):
            lines.append(f"- {key}: `{zip_info[key]}`")
        lines.append("- critical_files: present")
        lines.append("- sensitive_entries: none")

    if ctx.sections.get("test_collect_output"):
        lines.extend(["", "## pytest collect-only", "```text", str(ctx.sections["test_collect_output"]), "```"])

    lines.extend(
        [
            "",
            "## Conclusion",
            "Release guard completed." if success else "Release guard failed fast on a blocking item.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_json(ctx: GuardContext, *, success: bool) -> str:
    return json.dumps(
        {
            "success": success,
            "mode": ctx.mode,
            "repo": str(ctx.repo),
            "checks": ctx.checks,
            "sections": ctx.sections,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Release quality gate for tagged snapshots.")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_summary",
        help="append a JSON summary after the Markdown report",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    def add_common(subparser: argparse.ArgumentParser, *, package: bool = False) -> None:
        subparser.add_argument("--tag", required=True)
        subparser.add_argument("--expected-commit", required=True)
        subparser.add_argument("--prefix")
        subparser.add_argument("--skip-remote", action="store_true")
        subparser.add_argument("--collect-tests", action="store_true")
        if package:
            subparser.add_argument("--package-path")
            subparser.add_argument("--allow-overwrite", action="store_true")

    add_common(subparsers.add_parser("preflight"))
    add_common(subparsers.add_parser("package"), package=True)
    add_common(subparsers.add_parser("freeze"), package=True)

    verify = subparsers.add_parser("verify-zip")
    verify.add_argument("--package-path", required=True)
    verify.add_argument("--expected-sha256")
    verify.add_argument("--prefix")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = Path.cwd().resolve()

    try:
        if args.mode == "preflight":
            ctx = preflight_checks(
                repo,
                tag=args.tag,
                expected_commit=args.expected_commit,
                skip_remote=args.skip_remote,
                collect_tests=args.collect_tests,
            )
        elif args.mode == "package":
            prefix = normalize_prefix(args.prefix, repo=repo, tag=args.tag)
            package_path = (
                Path(args.package_path).resolve() if args.package_path else default_package_path(repo, args.tag)
            )
            ctx = create_package(
                repo,
                tag=args.tag,
                expected_commit=args.expected_commit,
                package_path=package_path,
                prefix=prefix,
                allow_overwrite=args.allow_overwrite,
                skip_remote=args.skip_remote,
                collect_tests=args.collect_tests,
            )
        elif args.mode == "freeze":
            prefix = normalize_prefix(args.prefix, repo=repo, tag=args.tag)
            package_path = (
                Path(args.package_path).resolve() if args.package_path else default_package_path(repo, args.tag)
            )
            ctx = create_package(
                repo,
                tag=args.tag,
                expected_commit=args.expected_commit,
                package_path=package_path,
                prefix=prefix,
                allow_overwrite=args.allow_overwrite,
                skip_remote=args.skip_remote,
                collect_tests=args.collect_tests,
            )
            ctx.mode = "freeze"
            validate_zip_entries(package_path, prefix=prefix)
        elif args.mode == "verify-zip":
            prefix = normalize_prefix(args.prefix) if args.prefix else None
            ctx = verify_zip_only(
                repo,
                package_path=Path(args.package_path).resolve(),
                prefix=prefix,
                expected_sha256=args.expected_sha256,
            )
        else:  # pragma: no cover - argparse prevents this
            parser.error(f"unsupported mode: {args.mode}")
    except ReleaseGuardError as exc:
        ctx = exc.context or GuardContext(mode=getattr(args, "mode", "unknown"), repo=repo)
        print(render_markdown(ctx, success=False), end="")
        if getattr(args, "json_summary", False):
            print("\n```json")
            print(render_json(ctx, success=False))
            print("```")
        return 1

    print(render_markdown(ctx, success=True), end="")
    if getattr(args, "json_summary", False):
        print("\n```json")
        print(render_json(ctx, success=True))
        print("```")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
