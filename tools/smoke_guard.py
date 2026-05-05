"""Runtime smoke guard for externally managed service checks.

The guard probes URLs and ports that are already expected to be running. It
does not start the project service unless the explicit start-probe mode is used.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_TIMEOUT = 5.0
DEFAULT_EXPECTED_STATUSES = frozenset({200, 204, 301, 302})
DEFAULT_SCENARIO_BASE_URL = "http://127.0.0.1:8013"
DEFAULT_SCENARIO_STATUS = "200"
LOCAL_SCENARIO_HOSTS = frozenset({"127.0.0.1", "localhost"})
SCENARIO_NAMES = (
    "basic-runtime",
    "light-page",
    "api-status",
    "delivery-read",
    "qingtian-runtime-v1",
    "qingtian-data-preflight-v1",
    "qingtian-external-data-runtime-v1",
)
SCENARIO_NAMES_HELP = ", ".join(SCENARIO_NAMES)
SCENARIO_FORBIDDEN_FRAGMENTS = (
    "/score",
    "rescore",
    "evolve",
    "ollama",
    "compare_report",
    "download",
    "export",
    ".md",
)
DEFAULT_BROWSER_COPY_BUTTON_SELECTOR = "#btnAnalysisBundleMarkdownCopy"
BROWSER_COPY_STATUS_MARKERS = (
    "analysis bundle Markdown 已复制",
    "不重新评分",
    "不触发 rescore",
    "不写 data",
    "不接 Ollama",
    "不接核心评分主链",
)


class SmokeGuardError(RuntimeError):
    """Raised when a smoke guard check cannot continue."""


@dataclasses.dataclass(frozen=True)
class UrlProbeResult:
    path: str
    url: str
    ok: bool
    status_code: int | None
    latency_ms: float
    content_type: str
    body_bytes: int
    expected_text: tuple[str, ...]
    text_matches: tuple[bool, ...]
    error: str = ""


@dataclasses.dataclass(frozen=True)
class PortProbeResult:
    host: str
    port: int
    ok: bool
    latency_ms: float
    error: str = ""


@dataclasses.dataclass(frozen=True)
class ScenarioPlan:
    name: str
    project_id: str
    submission_id: str
    paths: tuple[str, ...]
    report_latest_skipped: bool


@dataclasses.dataclass(frozen=True)
class BrowserMarkdownCopyPolicy:
    project_id: str
    init_readonly_paths: tuple[str, ...]
    browser_optional_paths: tuple[str, ...]
    click_delta_paths: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class DataPreflightResult:
    project_id: str
    data_dir: Path
    ok: bool
    reasons: tuple[str, ...]
    selected_submission_id: str = ""
    selected_submission_status: str = ""
    submissions_for_project: int = 0


@dataclasses.dataclass(frozen=True)
class ExternalDataRuntimeResult:
    project_id: str
    data_dir: Path
    ok: bool
    returncode: int = 0
    qingtian_data_dir: str = ""
    storage_data_dir_match: bool = False
    storage_submissions_path_match: bool = False
    evidence_trace_status: str = ""
    scoring_basis_status: str = ""
    evidence_trace_submission_id: str = ""
    scoring_basis_submission_id: str = ""
    scoring_status: str = ""
    error: str = ""


def parse_paths(value: str | None) -> list[str]:
    if not value:
        return ["/"]
    paths: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.startswith("/"):
            item = "/" + item
        paths.append(item)
    return paths or ["/"]


def parse_statuses(value: str | None) -> set[int]:
    if not value:
        return set(DEFAULT_EXPECTED_STATUSES)
    statuses: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            statuses.add(int(item))
        except ValueError as exc:
            raise SmokeGuardError(f"Invalid status code: {item}") from exc
    if not statuses:
        raise SmokeGuardError("No valid expected status codes were provided.")
    return statuses


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def load_json_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SmokeGuardError(f"invalid json: {path}") from exc


def _is_scored_submission(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    report = row.get("report")
    if isinstance(report, dict):
        status = str(report.get("scoring_status") or "").strip().lower()
        if status == "scored":
            return True
        if status in {"pending", "blocked"}:
            return False
        for key in ("rule_total_score", "pred_total_score", "total_score"):
            if report.get(key) is not None:
                return True
    return row.get("total_score") is not None


def _submission_status(row: object) -> str:
    if not isinstance(row, dict):
        return "unknown"
    report = row.get("report")
    if isinstance(report, dict):
        status = str(report.get("scoring_status") or "").strip()
        if status:
            return status
    if _is_scored_submission(row):
        return "scored"
    return "latest"


def run_data_preflight(project_id: str, data_dir: Path) -> DataPreflightResult:
    project_id = (project_id or "").strip()
    reasons: list[str] = []
    if not project_id:
        reasons.append("missing project_id")
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )

    if not data_dir.exists() or not data_dir.is_dir():
        reasons.append("missing data directory")
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )

    projects_path = data_dir / "projects.json"
    submissions_path = data_dir / "submissions.json"

    if not projects_path.exists():
        reasons.append("missing data/projects.json")
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )

    try:
        projects = load_json_file(projects_path)
    except SmokeGuardError as exc:
        reasons.append(str(exc))
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )
    if not isinstance(projects, list):
        reasons.append("invalid json: data/projects.json is not a list")
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )
    if not any(
        isinstance(row, dict) and str(row.get("id") or "") == project_id for row in projects
    ):
        reasons.append("missing project_id")
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )

    if not submissions_path.exists():
        reasons.append("missing data/submissions.json")
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )

    try:
        submissions = load_json_file(submissions_path)
    except SmokeGuardError as exc:
        reasons.append(str(exc))
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )
    if not isinstance(submissions, list):
        reasons.append("invalid json: data/submissions.json is not a list")
        return DataPreflightResult(
            project_id=project_id, data_dir=data_dir, ok=False, reasons=tuple(reasons)
        )

    project_rows = [
        row
        for row in submissions
        if isinstance(row, dict) and str(row.get("project_id") or "") == project_id
    ]
    if not project_rows:
        reasons.append("no submissions for project_id")
        return DataPreflightResult(
            project_id=project_id,
            data_dir=data_dir,
            ok=False,
            reasons=tuple(reasons),
            submissions_for_project=0,
        )

    selectable_rows = [row for row in project_rows if str(row.get("id") or "").strip()]
    selectable_rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    selected = next((row for row in selectable_rows if _is_scored_submission(row)), None)
    if selected is None and selectable_rows:
        selected = selectable_rows[0]
    if selected is None:
        reasons.append("no selectable latest/scored submission")
        return DataPreflightResult(
            project_id=project_id,
            data_dir=data_dir,
            ok=False,
            reasons=tuple(reasons),
            submissions_for_project=len(project_rows),
        )

    return DataPreflightResult(
        project_id=project_id,
        data_dir=data_dir,
        ok=True,
        reasons=(),
        selected_submission_id=str(selected.get("id") or ""),
        selected_submission_status=_submission_status(selected),
        submissions_for_project=len(project_rows),
    )


def _parse_key_value_output(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def run_external_data_runtime(
    project_id: str,
    data_dir: Path,
    *,
    repo: Path | None = None,
    timeout: float = 30.0,
) -> ExternalDataRuntimeResult:
    project_id = (project_id or "").strip()
    resolved_data_dir = data_dir.expanduser().resolve()
    if not project_id:
        return ExternalDataRuntimeResult(
            project_id=project_id,
            data_dir=resolved_data_dir,
            ok=False,
            returncode=1,
            error="missing project_id",
        )

    repo_root = (repo or Path(__file__).resolve().parents[1]).resolve()
    env = os.environ.copy()
    env["QINGTIAN_DATA_DIR"] = str(resolved_data_dir)
    env["QINGTIAN_PROJECT_ID"] = project_id
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = (
        str(repo_root)
        if not env.get("PYTHONPATH")
        else f"{repo_root}{os.pathsep}{env['PYTHONPATH']}"
    )

    script = r"""
from pathlib import Path
import os
import urllib.parse

from fastapi.testclient import TestClient

from app import storage
from app.main import app

project_id = os.environ["QINGTIAN_PROJECT_ID"]
expected_data_dir = Path(os.environ["QINGTIAN_DATA_DIR"]).expanduser().resolve()
quoted_project_id = urllib.parse.quote(project_id, safe="")

print(f"QINGTIAN_DATA_DIR={storage.DATA_DIR}")
print(f"DATA_DIR_MATCH={storage.DATA_DIR == expected_data_dir}")
print(f"SUBMISSIONS_PATH_MATCH={storage.SUBMISSIONS_PATH == expected_data_dir / 'submissions.json'}")

client = TestClient(app)
evidence = client.get(f"/api/v1/projects/{quoted_project_id}/evidence_trace/latest")
scoring_basis = client.get(f"/api/v1/projects/{quoted_project_id}/scoring_basis/latest")
print(f"EVIDENCE_TRACE_STATUS={evidence.status_code}")
print(f"SCORING_BASIS_STATUS={scoring_basis.status_code}")

evidence_payload = evidence.json() if evidence.status_code == 200 else {}
scoring_basis_payload = scoring_basis.json() if scoring_basis.status_code == 200 else {}
print(f"EVIDENCE_TRACE_SUBMISSION_ID={evidence_payload.get('submission_id', '')}")
print(f"SCORING_BASIS_SUBMISSION_ID={scoring_basis_payload.get('submission_id', '')}")
print(f"SCORING_STATUS={scoring_basis_payload.get('scoring_status', '')}")
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ExternalDataRuntimeResult(
            project_id=project_id,
            data_dir=resolved_data_dir,
            ok=False,
            returncode=1,
            error=f"external data runtime subprocess timed out: {exc}",
        )

    values = _parse_key_value_output(proc.stdout)
    evidence_status = values.get("EVIDENCE_TRACE_STATUS", "")
    scoring_basis_status = values.get("SCORING_BASIS_STATUS", "")
    evidence_submission_id = values.get("EVIDENCE_TRACE_SUBMISSION_ID", "")
    scoring_basis_submission_id = values.get("SCORING_BASIS_SUBMISSION_ID", "")
    data_dir_match = values.get("DATA_DIR_MATCH", "").lower() == "true"
    submissions_path_match = values.get("SUBMISSIONS_PATH_MATCH", "").lower() == "true"
    ok = (
        proc.returncode == 0
        and data_dir_match
        and submissions_path_match
        and evidence_status == "200"
        and scoring_basis_status == "200"
        and bool(evidence_submission_id)
        and evidence_submission_id == scoring_basis_submission_id
    )
    error = ""
    if not ok:
        error = proc.stderr.strip() or "external data runtime validation failed"

    return ExternalDataRuntimeResult(
        project_id=project_id,
        data_dir=resolved_data_dir,
        ok=ok,
        returncode=proc.returncode,
        qingtian_data_dir=values.get("QINGTIAN_DATA_DIR", ""),
        storage_data_dir_match=data_dir_match,
        storage_submissions_path_match=submissions_path_match,
        evidence_trace_status=evidence_status,
        scoring_basis_status=scoring_basis_status,
        evidence_trace_submission_id=evidence_submission_id,
        scoring_basis_submission_id=scoring_basis_submission_id,
        scoring_status=values.get("SCORING_STATUS", ""),
        error=error,
    )


def build_url(base_url: str, path: str) -> str:
    if not base_url:
        raise SmokeGuardError("--base-url is required.")
    if not path.startswith("/"):
        path = "/" + path
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _decode_body(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip() or "utf-8"
    return body.decode(charset, errors="replace")


def probe_url(
    base_url: str,
    path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    expected_statuses: Iterable[int] = DEFAULT_EXPECTED_STATUSES,
    expected_text: Sequence[str] = (),
) -> UrlProbeResult:
    expected = set(expected_statuses)
    url = build_url(base_url, path)
    started = time.monotonic()
    status_code: int | None = None
    content_type = ""
    body = b""
    error = ""

    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(response.getcode())
            content_type = response.headers.get("content-type", "")
            body = response.read()
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        content_type = exc.headers.get("content-type", "") if exc.headers else ""
        body = exc.read()
        error = f"HTTP {status_code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = str(exc)

    latency_ms = (time.monotonic() - started) * 1000
    decoded = _decode_body(body, content_type) if expected_text else ""
    text_matches = tuple(text in decoded for text in expected_text)
    status_ok = status_code in expected
    text_ok = all(text_matches) if expected_text else True
    ok = bool(status_ok and text_ok and not (status_code is None and error))
    if status_code is not None and status_code not in expected:
        error = error or f"Unexpected status {status_code}"
    if expected_text and not text_ok:
        missing = [text for text, matched in zip(expected_text, text_matches) if not matched]
        error = error or "Missing expected text: " + ", ".join(missing)
    return UrlProbeResult(
        path=path,
        url=url,
        ok=ok,
        status_code=status_code,
        latency_ms=latency_ms,
        content_type=content_type,
        body_bytes=len(body),
        expected_text=tuple(expected_text),
        text_matches=text_matches,
        error=error,
    )


def probe_urls(
    base_url: str,
    paths: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    expected_statuses: Iterable[int] = DEFAULT_EXPECTED_STATUSES,
    expected_text: Sequence[str] = (),
) -> list[UrlProbeResult]:
    return [
        probe_url(
            base_url,
            path,
            timeout=timeout,
            expected_statuses=expected_statuses,
            expected_text=expected_text,
        )
        for path in paths
    ]


def check_port(host: str, port: int, *, timeout: float = DEFAULT_TIMEOUT) -> PortProbeResult:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = (time.monotonic() - started) * 1000
            return PortProbeResult(host=host, port=port, ok=True, latency_ms=latency_ms)
    except OSError as exc:
        latency_ms = (time.monotonic() - started) * 1000
        return PortProbeResult(
            host=host, port=port, ok=False, latency_ms=latency_ms, error=str(exc)
        )


def prepare_start_command(start_cmd: str) -> list[str]:
    parts = shlex.split(start_cmd)
    if not parts:
        raise SmokeGuardError("start command is empty.")
    if any(part.endswith(".sh") for part in parts):
        raise SmokeGuardError("start command must not invoke shell script files.")
    return parts


def scenario_forbidden_fragments(path: str) -> list[str]:
    path_lower = path.lower()
    return [fragment for fragment in SCENARIO_FORBIDDEN_FRAGMENTS if fragment in path_lower]


def is_allowed_scenario_base_url(base_url: str) -> bool:
    parsed = urllib.parse.urlparse(base_url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in LOCAL_SCENARIO_HOSTS


def normalize_request_path(url: str, base_url: str) -> tuple[str, bool]:
    """Return the request path and whether the URL stays under the local base host."""
    parsed_url = urllib.parse.urlparse(url)
    parsed_base = urllib.parse.urlparse(base_url)
    if not parsed_url.scheme:
        path = parsed_url.path or "/"
        return path, True
    same_host = (
        parsed_url.scheme in {"http", "https"}
        and parsed_url.hostname in LOCAL_SCENARIO_HOSTS
        and parsed_url.hostname == parsed_base.hostname
        and (parsed_url.port or default_port(parsed_url.scheme))
        == (parsed_base.port or default_port(parsed_base.scheme))
    )
    return parsed_url.path or "/", same_host


def default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def build_browser_markdown_copy_policy(
    project_id: str,
    *,
    allow_init_readonly: bool = True,
) -> BrowserMarkdownCopyPolicy:
    project_id = (project_id or "").strip()
    if not project_id:
        raise SmokeGuardError("project_id is required for browser-markdown-copy")
    init_paths = ("/",)
    if allow_init_readonly:
        init_paths = (
            "/",
            "/api/v1/projects",
            f"/api/v1/projects/{project_id}/expert-profile",
            f"/api/v1/projects/{project_id}/submissions",
            f"/api/v1/projects/{project_id}/materials",
            f"/api/v1/projects/{project_id}/scoring_readiness",
            f"/api/v1/projects/{project_id}/ground_truth",
        )
    return BrowserMarkdownCopyPolicy(
        project_id=project_id,
        init_readonly_paths=init_paths,
        browser_optional_paths=("/favicon.ico",),
        click_delta_paths=(f"/api/v1/projects/{project_id}/analysis_bundle",),
    )


def browser_markdown_copy_forbidden_fragments(path: str) -> list[str]:
    return scenario_forbidden_fragments(path)


def classify_browser_markdown_copy_request(
    *,
    method: str,
    url: str,
    base_url: str,
    phase: str,
    policy: BrowserMarkdownCopyPolicy,
) -> tuple[str, str, tuple[str, ...]]:
    path, local = normalize_request_path(url, base_url)
    if not local:
        return "external", path, ()
    if method.upper() != "GET":
        return "forbidden_method", path, ()
    fragments = tuple(browser_markdown_copy_forbidden_fragments(path))
    if fragments:
        return "forbidden_path", path, fragments
    if path in policy.browser_optional_paths:
        return "browser_optional_get", path, ()
    if phase == "init" and path in policy.init_readonly_paths:
        return "init_readonly_requests", path, ()
    if phase == "click" and path in policy.click_delta_paths:
        return "click_delta_requests", path, ()
    return "forbidden_path", path, ()


def browser_copy_markers(project_id: str) -> tuple[str, ...]:
    return ("# 项目分析包", "项目ID", project_id, "验收指标")


def validate_browser_executable(browser_executable: str) -> str:
    if not browser_executable:
        raise SmokeGuardError("browser executable is required for browser-markdown-copy")
    path = Path(browser_executable).expanduser()
    if not path.exists() or not path.is_file() or not os.access(path, os.X_OK):
        raise SmokeGuardError(
            f"browser executable not found or not executable: {browser_executable}"
        )
    return str(path)


def load_playwright_sync_api():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SmokeGuardError(
            "Playwright is required for browser-markdown-copy; "
            "install it separately or use an environment where it is already available."
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def build_scenario_plan(
    scenario_name: str,
    *,
    project_id: str = "p1",
    submission_id: str = "",
) -> ScenarioPlan:
    project_id = (project_id or "").strip()
    submission_id = (submission_id or "").strip()
    if not project_id:
        raise SmokeGuardError("project_id is required for scenario")
    if scenario_name not in SCENARIO_NAMES:
        raise SmokeGuardError(f"Unsupported scenario name: {scenario_name}")

    report_latest_skipped = False
    if scenario_name == "basic-runtime":
        paths = ["/health", "/ready", "/"]
    elif scenario_name == "light-page":
        paths = ["/", "/__ping__"]
    elif scenario_name == "api-status":
        paths = ["/api/v1/auth/status", "/api/v1/rate_limit/status", "/api/v1/config/status"]
    elif scenario_name == "delivery-read":
        if not submission_id:
            raise SmokeGuardError("submission_id required for delivery-read")
        paths = [
            f"/api/v1/submissions/{submission_id}/reports/latest",
            f"/api/v1/projects/{project_id}/analysis_bundle",
        ]
    elif scenario_name in {
        "qingtian-data-preflight-v1",
        "qingtian-external-data-runtime-v1",
    }:
        paths = []
    else:
        paths = [
            "/health",
            "/ready",
            "/",
            "/__ping__",
            "/api/v1/auth/status",
            "/api/v1/rate_limit/status",
            "/api/v1/config/status",
            f"/api/v1/projects/{project_id}/analysis_bundle",
        ]
        if submission_id:
            paths.append(f"/api/v1/submissions/{submission_id}/reports/latest")
        else:
            report_latest_skipped = True

    return ScenarioPlan(
        name=scenario_name,
        project_id=project_id,
        submission_id=submission_id,
        paths=tuple(paths),
        report_latest_skipped=report_latest_skipped,
    )


def wait_until_ready(
    base_url: str,
    paths: Sequence[str],
    *,
    timeout: float,
    probe_timeout: float,
    expected_statuses: Iterable[int],
) -> list[UrlProbeResult]:
    deadline = time.monotonic() + timeout
    last_results: list[UrlProbeResult] = []
    while time.monotonic() <= deadline:
        last_results = probe_urls(
            base_url,
            paths,
            timeout=probe_timeout,
            expected_statuses=expected_statuses,
        )
        if all(result.ok for result in last_results):
            return last_results
        time.sleep(0.2)
    return last_results


def run_start_probe(
    start_cmd: str,
    base_url: str,
    paths: Sequence[str],
    *,
    startup_timeout: float,
    timeout: float,
    expected_statuses: Iterable[int],
) -> list[UrlProbeResult]:
    command = prepare_start_command(start_cmd)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return wait_until_ready(
            base_url,
            paths,
            timeout=startup_timeout,
            probe_timeout=timeout,
            expected_statuses=expected_statuses,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def render_url_report(
    *,
    title: str,
    base_url: str,
    paths: Sequence[str],
    results: Sequence[UrlProbeResult],
) -> str:
    passed = all(result.ok for result in results)
    failures = [result for result in results if not result.ok]
    lines = [
        f"# smoke_guard {title} report",
        "",
        f"- result: {'PASS' if passed else 'FAIL'}",
        f"- base_url: `{base_url}`",
        f"- paths: `{', '.join(paths)}`",
        "",
        "| path | status_code | latency_ms | content_type | body_bytes | expected_text | result |",
        "|---|---:|---:|---|---:|---|---|",
    ]
    for result in results:
        expected_text = ", ".join(
            f"{text}:{'hit' if matched else 'miss'}"
            for text, matched in zip(result.expected_text, result.text_matches)
        )
        lines.append(
            "| {path} | {status} | {latency:.2f} | {content_type} | {body_bytes} | {expected_text} | {result} |".format(
                path=result.path,
                status=result.status_code if result.status_code is not None else "",
                latency=result.latency_ms,
                content_type=result.content_type or "",
                body_bytes=result.body_bytes,
                expected_text=expected_text or "",
                result="PASS" if result.ok else f"FAIL: {result.error}",
            )
        )
    lines.extend(["", "## failures"])
    if failures:
        for result in failures:
            lines.append(f"- `{result.path}`: {result.error or 'probe failed'}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## summary",
            f"- checked: `{len(results)}`",
            f"- passed: `{len(results) - len(failures)}`",
        ]
    )
    return "\n".join(lines) + "\n"


def render_port_report(result: PortProbeResult) -> str:
    lines = [
        "# smoke_guard check-port report",
        "",
        f"- result: {'PASS' if result.ok else 'FAIL'}",
        f"- host: `{result.host}`",
        f"- port: `{result.port}`",
        f"- open: `{result.ok}`",
        f"- latency_ms: `{result.latency_ms:.2f}`",
        f"- error: `{result.error}`" if result.error else "- error: none",
        "",
        "## summary",
        f"- port_status: `{'open' if result.ok else 'closed'}`",
    ]
    return "\n".join(lines) + "\n"


def render_scenario_report(
    *,
    plan: ScenarioPlan,
    base_url: str,
    results: Sequence[UrlProbeResult],
    forbidden_seen: bool,
    external_base_url_blocked: bool,
    error: str = "",
) -> str:
    passed = (
        bool(results)
        and all(result.ok for result in results)
        and not forbidden_seen
        and not external_base_url_blocked
        and not error
    )
    if error:
        passed = False
    lines = [
        "# smoke_guard scenario report",
        "",
        "- mode: scenario",
        f"- scenario_name: {plan.name}",
        f"- base_url: {base_url}",
        f"- project_id: {plan.project_id}",
        f"- submission_id_present: {str(bool(plan.submission_id)).lower()}",
        f"- request_count: {len(results)}",
        f"- forbidden_seen: {str(forbidden_seen).lower()}",
        f"- external_base_url_blocked: {str(external_base_url_blocked).lower()}",
        f"- report_latest_skipped: {str(plan.report_latest_skipped).lower()}",
        f"- final_result: {'PASS' if passed else 'FAIL'}",
    ]
    if error:
        lines.append(f"- error: {error}")
    lines.extend(
        [
            "",
            "## requests",
            "| method | path | status | ok |",
            "|---|---|---:|---|",
        ]
    )
    if results:
        for result in results:
            lines.append(
                "| GET | {path} | {status} | {ok} |".format(
                    path=result.path,
                    status=result.status_code if result.status_code is not None else "",
                    ok=str(result.ok).lower(),
                )
            )
    else:
        lines.append("|  |  |  |  |")
    return "\n".join(lines) + "\n"


def _format_request_list(requests: Sequence[tuple[str, str]]) -> str:
    if not requests:
        return "[]"
    return "[" + ", ".join(f"{method} {path}" for method, path in requests) + "]"


def render_browser_markdown_copy_report(
    *,
    base_url: str,
    project_id: str,
    browser_executable: str,
    clicked_target: str,
    init_readonly_requests: Sequence[tuple[str, str]],
    browser_optional_get: Sequence[tuple[str, str]],
    click_delta_requests: Sequence[tuple[str, str]],
    forbidden_method_seen: bool,
    forbidden_path_seen: bool,
    external_network_seen: bool,
    ollama_seen: bool,
    clipboard_write_count: int,
    copied_text: str,
    marker_hits: dict[str, bool],
    status_text: str,
    final_result: bool,
    error: str = "",
) -> str:
    copied_text_preview = copied_text[:120].replace("\n", " ")
    lines = [
        "# smoke_guard browser-markdown-copy report",
        "",
        "- mode: browser-markdown-copy",
        f"- base_url: {base_url}",
        f"- project_id: {project_id}",
        f"- browser_executable: {browser_executable}",
        f"- clicked_target: {clicked_target}",
        f"- init_readonly_requests: {_format_request_list(init_readonly_requests)}",
        f"- browser_optional_get: {_format_request_list(browser_optional_get)}",
        f"- click_delta_requests: {_format_request_list(click_delta_requests)}",
        f"- forbidden_method_seen: {str(forbidden_method_seen).lower()}",
        f"- forbidden_path_seen: {str(forbidden_path_seen).lower()}",
        f"- external_network_seen: {str(external_network_seen).lower()}",
        f"- ollama_seen: {str(ollama_seen).lower()}",
        f"- clipboard_write_count: {clipboard_write_count}",
        f"- copied_text_captured: {str(bool(copied_text)).lower()}",
        f"- copied_text_length: {len(copied_text)}",
        f"- copied_text_preview: {copied_text_preview}",
        f"- marker_hits: {marker_hits}",
        f"- status_text: {status_text[:240]}",
        "- system_clipboard_written: false",
        f"- final_result: {'PASS' if final_result else 'FAIL'}",
    ]
    if error:
        lines.append(f"- error: {error}")
    return "\n".join(lines) + "\n"


def render_data_preflight_report(result: DataPreflightResult) -> str:
    reasons = result.reasons or ("-",)
    affected = (
        "/api/v1/projects/{project_id}/evidence_trace/latest, "
        "/api/v1/projects/{project_id}/scoring_basis/latest"
    ).format(project_id=result.project_id or "-")
    lines = [
        "# smoke_guard data-preflight report",
        "",
        "- mode: data-preflight",
        f"- project_id: {result.project_id or '-'}",
        f"- data_dir: {result.data_dir}",
        f"- data_dir_exists: {str(result.data_dir.exists()).lower()}",
        f"- projects_json_exists: {str((result.data_dir / 'projects.json').exists()).lower()}",
        f"- submissions_json_exists: {str((result.data_dir / 'submissions.json').exists()).lower()}",
        f"- submissions_for_project: {result.submissions_for_project}",
        f"- selected_submission_id: {result.selected_submission_id or '-'}",
        f"- selected_submission_status: {result.selected_submission_status or '-'}",
        f"- affected_endpoints: {affected}",
        "- latest_submission_required: evidence_trace/latest and scoring_basis/latest need latest submission precondition data",
        f"- missing_reasons: {', '.join(reasons)}",
        f"- final_result: {'PASS' if result.ok else 'FAIL'}",
    ]
    return "\n".join(lines) + "\n"


def render_data_preflight_scenario_report(
    *,
    scenario_name: str,
    result: DataPreflightResult,
) -> str:
    reasons = result.reasons or ("-",)
    affected = (
        "/api/v1/projects/{project_id}/evidence_trace/latest, "
        "/api/v1/projects/{project_id}/scoring_basis/latest"
    ).format(project_id=result.project_id or "-")
    lines = [
        "# smoke_guard scenario report",
        "",
        "- mode: scenario",
        f"- scenario_name: {scenario_name}",
        "- scenario_kind: data-preflight",
        f"- project_id: {result.project_id or '-'}",
        f"- data_dir: {result.data_dir}",
        "- http_access_used: false",
        f"- data_dir_exists: {str(result.data_dir.exists()).lower()}",
        f"- projects_json_exists: {str((result.data_dir / 'projects.json').exists()).lower()}",
        f"- submissions_json_exists: {str((result.data_dir / 'submissions.json').exists()).lower()}",
        f"- submissions_for_project: {result.submissions_for_project}",
        f"- selected_submission_id: {result.selected_submission_id or '-'}",
        f"- selected_submission_status: {result.selected_submission_status or '-'}",
        f"- affected_endpoints: {affected}",
        "- latest_submission_required: evidence_trace/latest and scoring_basis/latest need latest submission precondition data",
        f"- missing_reasons: {', '.join(reasons)}",
        f"- final_result: {'PASS' if result.ok else 'FAIL'}",
    ]
    return "\n".join(lines) + "\n"


def render_external_data_runtime_scenario_report(
    *,
    scenario_name: str,
    result: ExternalDataRuntimeResult,
) -> str:
    lines = [
        "# smoke_guard scenario report",
        "",
        "- mode: scenario",
        f"- scenario_name: {scenario_name}",
        "- scenario_kind: external-data-runtime",
        f"- project_id: {result.project_id or '-'}",
        f"- data_dir: {result.data_dir}",
        "- uvicorn_started: false",
        "- http_server_started: false",
        "- browser_started: false",
        "- external_network_used: false",
        "- ollama_used: false",
        f"- qingtian_data_dir: {result.qingtian_data_dir or '-'}",
        f"- storage_data_dir_match: {str(result.storage_data_dir_match).lower()}",
        (
            "- storage_submissions_path_match: "
            f"{str(result.storage_submissions_path_match).lower()}"
        ),
        f"- subprocess_exit_code: {result.returncode}",
        f"- evidence_trace_status: {result.evidence_trace_status or '-'}",
        f"- scoring_basis_status: {result.scoring_basis_status or '-'}",
        f"- evidence_trace_submission_id: {result.evidence_trace_submission_id}",
        f"- scoring_basis_submission_id: {result.scoring_basis_submission_id}",
        f"- scoring_status: {result.scoring_status}",
        f"- final_result: {'PASS' if result.ok else 'FAIL'}",
    ]
    if result.error:
        lines.append(f"- error: {result.error}")
    return "\n".join(lines) + "\n"


def _add_probe_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--paths", default="/")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--expect-status", default="")
    parser.add_argument("--expect-text", action="append", default=[])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Runtime smoke guard for externally managed services."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    probe = subparsers.add_parser("probe", help="Probe one or more HTTP paths.")
    _add_probe_options(probe)

    check_port_parser = subparsers.add_parser(
        "check-port", help="Check whether a TCP port is open."
    )
    check_port_parser.add_argument("--host", default="127.0.0.1")
    check_port_parser.add_argument("--port", required=True, type=int)
    check_port_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    report = subparsers.add_parser("report", help="Alias for a Markdown URL probe report.")
    _add_probe_options(report)

    start_probe = subparsers.add_parser(
        "start-probe", help="Start an explicit command, then probe URLs."
    )
    start_probe.add_argument("--start-cmd", required=True)
    start_probe.add_argument("--startup-timeout", type=float, default=10.0)
    _add_probe_options(start_probe)

    scenario = subparsers.add_parser(
        "scenario",
        help=f"Run a predefined optional runtime acceptance scenario: {SCENARIO_NAMES_HELP}.",
    )
    scenario.add_argument("--scenario-name", dest="scenario_name", choices=SCENARIO_NAMES)
    scenario.add_argument("--name", dest="scenario_name", choices=SCENARIO_NAMES)
    scenario.add_argument("--base-url", default=DEFAULT_SCENARIO_BASE_URL)
    scenario.add_argument("--project-id", default="p1")
    scenario.add_argument("--submission-id", default="")
    scenario.add_argument("--data-dir", default="")
    scenario.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    scenario.add_argument("--allow-status", default=DEFAULT_SCENARIO_STATUS)

    data_preflight = subparsers.add_parser(
        "data-preflight", help="Check read-only data preconditions for latest delivery APIs."
    )
    data_preflight.add_argument("--project-id", default="p1")
    data_preflight.add_argument("--data-dir", default=str(default_data_dir()))

    browser_copy = subparsers.add_parser(
        "browser-markdown-copy",
        help="Optionally verify the analysis bundle Markdown copy UI with an existing browser.",
    )
    browser_copy.add_argument("--base-url", default=DEFAULT_SCENARIO_BASE_URL)
    browser_copy.add_argument("--project-id", default="p1")
    browser_copy.add_argument("--browser-executable", default="")
    browser_copy.add_argument("--button-selector", default=DEFAULT_BROWSER_COPY_BUTTON_SELECTOR)
    browser_copy.add_argument("--timeout", type=float, default=10.0)
    browser_copy.add_argument(
        "--allow-init-readonly",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow known read-only homepage initialization GET requests.",
    )
    return parser


def run_probe_mode(args: argparse.Namespace, *, title: str = "probe") -> int:
    paths = parse_paths(args.paths)
    statuses = parse_statuses(args.expect_status)
    results = probe_urls(
        args.base_url,
        paths,
        timeout=args.timeout,
        expected_statuses=statuses,
        expected_text=args.expect_text,
    )
    print(
        render_url_report(title=title, base_url=args.base_url, paths=paths, results=results), end=""
    )
    return 0 if all(result.ok for result in results) else 1


def run_check_port_mode(args: argparse.Namespace) -> int:
    result = check_port(args.host, args.port, timeout=args.timeout)
    print(render_port_report(result), end="")
    return 0 if result.ok else 1


def run_start_probe_mode(args: argparse.Namespace) -> int:
    paths = parse_paths(args.paths)
    statuses = parse_statuses(args.expect_status)
    results = run_start_probe(
        args.start_cmd,
        args.base_url,
        paths,
        startup_timeout=args.startup_timeout,
        timeout=args.timeout,
        expected_statuses=statuses,
    )
    print(
        render_url_report(
            title="start-probe", base_url=args.base_url, paths=paths, results=results
        ),
        end="",
    )
    return 0 if all(result.ok for result in results) else 1


def run_scenario_mode(args: argparse.Namespace) -> int:
    scenario_name = (getattr(args, "scenario_name", "") or "").strip()
    if not scenario_name:
        fallback_plan = ScenarioPlan(
            name="-",
            project_id=(args.project_id or "").strip(),
            submission_id=(args.submission_id or "").strip(),
            paths=(),
            report_latest_skipped=False,
        )
        print(
            render_scenario_report(
                plan=fallback_plan,
                base_url=args.base_url,
                results=[],
                forbidden_seen=False,
                external_base_url_blocked=False,
                error="scenario name is required",
            ),
            end="",
        )
        return 2

    if scenario_name == "qingtian-data-preflight-v1":
        result = run_data_preflight(
            project_id=args.project_id,
            data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        )
        print(
            render_data_preflight_scenario_report(
                scenario_name=scenario_name,
                result=result,
            ),
            end="",
        )
        return 0 if result.ok else 1

    if scenario_name == "qingtian-external-data-runtime-v1":
        data_dir_arg = (args.data_dir or "").strip()
        if not data_dir_arg:
            result = ExternalDataRuntimeResult(
                project_id=(args.project_id or "").strip(),
                data_dir=Path("-"),
                ok=False,
                returncode=1,
                error="missing --data-dir for qingtian-external-data-runtime-v1",
            )
            print(
                render_external_data_runtime_scenario_report(
                    scenario_name=scenario_name,
                    result=result,
                ),
                end="",
            )
            return 1

        result = run_external_data_runtime(
            project_id=args.project_id,
            data_dir=Path(data_dir_arg),
            timeout=args.timeout,
        )
        print(
            render_external_data_runtime_scenario_report(
                scenario_name=scenario_name,
                result=result,
            ),
            end="",
        )
        return 0 if result.ok else 1

    try:
        plan = build_scenario_plan(
            scenario_name,
            project_id=args.project_id,
            submission_id=args.submission_id,
        )
    except SmokeGuardError as exc:
        fallback_plan = ScenarioPlan(
            name=scenario_name,
            project_id=(args.project_id or "").strip(),
            submission_id=(args.submission_id or "").strip(),
            paths=(),
            report_latest_skipped=False,
        )
        print(
            render_scenario_report(
                plan=fallback_plan,
                base_url=args.base_url,
                results=[],
                forbidden_seen=False,
                external_base_url_blocked=False,
                error=str(exc),
            ),
            end="",
        )
        return 2

    external_base_url_blocked = not is_allowed_scenario_base_url(args.base_url)
    if external_base_url_blocked:
        print(
            render_scenario_report(
                plan=plan,
                base_url=args.base_url,
                results=[],
                forbidden_seen=False,
                external_base_url_blocked=True,
                error="external base URL blocked",
            ),
            end="",
        )
        return 2

    forbidden_paths = {
        path: fragments for path in plan.paths if (fragments := scenario_forbidden_fragments(path))
    }
    if forbidden_paths:
        detail = "; ".join(
            f"{path}: {', '.join(fragments)}" for path, fragments in forbidden_paths.items()
        )
        print(
            render_scenario_report(
                plan=plan,
                base_url=args.base_url,
                results=[],
                forbidden_seen=True,
                external_base_url_blocked=False,
                error=f"forbidden scenario path fragments: {detail}",
            ),
            end="",
        )
        return 2

    statuses = parse_statuses(args.allow_status)
    results = probe_urls(
        args.base_url,
        list(plan.paths),
        timeout=args.timeout,
        expected_statuses=statuses,
    )
    print(
        render_scenario_report(
            plan=plan,
            base_url=args.base_url,
            results=results,
            forbidden_seen=False,
            external_base_url_blocked=False,
        ),
        end="",
    )
    return 0 if all(result.ok for result in results) else 1


def run_data_preflight_mode(args: argparse.Namespace) -> int:
    result = run_data_preflight(
        project_id=args.project_id,
        data_dir=Path(args.data_dir),
    )
    print(render_data_preflight_report(result), end="")
    return 0 if result.ok else 1


def run_browser_markdown_copy_mode(args: argparse.Namespace) -> int:
    project_id = (args.project_id or "").strip()
    base_url = args.base_url
    clicked_target = args.button_selector
    policy = build_browser_markdown_copy_policy(
        project_id,
        allow_init_readonly=bool(args.allow_init_readonly),
    )
    init_readonly_requests: list[tuple[str, str]] = []
    browser_optional_get: list[tuple[str, str]] = []
    click_delta_requests: list[tuple[str, str]] = []
    forbidden_method_seen = False
    forbidden_path_seen = False
    external_network_seen = False
    ollama_seen = False
    clipboard_write_count = 0
    copied_text = ""
    status_text = ""
    marker_hits: dict[str, bool] = {}
    browser = None
    final_result = False
    error = ""

    def emit() -> int:
        print(
            render_browser_markdown_copy_report(
                base_url=base_url,
                project_id=project_id,
                browser_executable=args.browser_executable,
                clicked_target=clicked_target,
                init_readonly_requests=init_readonly_requests,
                browser_optional_get=browser_optional_get,
                click_delta_requests=click_delta_requests,
                forbidden_method_seen=forbidden_method_seen,
                forbidden_path_seen=forbidden_path_seen,
                external_network_seen=external_network_seen,
                ollama_seen=ollama_seen,
                clipboard_write_count=clipboard_write_count,
                copied_text=copied_text,
                marker_hits=marker_hits,
                status_text=status_text,
                final_result=final_result,
                error=error,
            ),
            end="",
        )
        return 0 if final_result else 2

    if not is_allowed_scenario_base_url(base_url):
        external_network_seen = True
        error = "external base URL blocked"
        return emit()

    try:
        browser_executable = validate_browser_executable(args.browser_executable)
        sync_playwright, PlaywrightTimeoutError = load_playwright_sync_api()
    except SmokeGuardError as exc:
        error = str(exc)
        return emit()

    phase = "init"

    def record_request(method: str, url: str) -> None:
        nonlocal forbidden_method_seen
        nonlocal forbidden_path_seen
        nonlocal external_network_seen
        nonlocal ollama_seen
        category, path, fragments = classify_browser_markdown_copy_request(
            method=method,
            url=url,
            base_url=base_url,
            phase=phase,
            policy=policy,
        )
        if "ollama" in path.lower() or "ollama" in fragments:
            ollama_seen = True
        if category == "external":
            external_network_seen = True
        elif category == "forbidden_method":
            forbidden_method_seen = True
        elif category == "forbidden_path":
            forbidden_path_seen = True
        elif category == "browser_optional_get":
            browser_optional_get.append((method.upper(), path))
        elif category == "init_readonly_requests":
            init_readonly_requests.append((method.upper(), path))
        elif category == "click_delta_requests":
            click_delta_requests.append((method.upper(), path))

    timeout_ms = int(args.timeout * 1000)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=browser_executable,
                args=["--disable-gpu", "--no-first-run", "--no-default-browser-check"],
            )
            context = browser.new_context(base_url=base_url)
            context.add_init_script(
                """
                (() => {
                  window.__copiedText = null;
                  window.__clipboardWriteCount = 0;
                  Object.defineProperty(navigator, 'clipboard', {
                    configurable: true,
                    value: {
                      writeText: async (text) => {
                        window.__copiedText = String(text);
                        window.__clipboardWriteCount += 1;
                        return Promise.resolve();
                      }
                    }
                  });
                })();
                """
            )
            page = context.new_page()
            page.on("request", lambda request: record_request(request.method, request.url))
            page.goto("/", wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass

            for selector in (
                "#projectId",
                "#project_id",
                "input[name='project_id']",
                "input[name='projectId']",
            ):
                try:
                    locator = page.locator(selector)
                    if locator.count() > 0:
                        locator.first.fill(project_id)
                        break
                except Exception:
                    pass

            phase = "click"
            click_delta_requests.clear()
            button = page.locator(args.button_selector)
            if button.count() == 0:
                button = page.get_by_text("复制 analysis bundle Markdown", exact=True)
            button.first.click(timeout=timeout_ms)
            page.wait_for_function(
                "() => window.__clipboardWriteCount > 0 && typeof window.__copiedText === 'string'",
                timeout=timeout_ms,
            )

            copied_text = str(page.evaluate("window.__copiedText") or "")
            clipboard_write_count = int(page.evaluate("window.__clipboardWriteCount") or 0)

            for selector in (
                "#analysisBundleMarkdownCopyStatus",
                "[data-testid='analysisBundleMarkdownCopyStatus']",
            ):
                try:
                    locator = page.locator(selector)
                    if locator.count() > 0:
                        status_text = locator.first.inner_text(timeout=2000)
                        break
                except Exception:
                    pass

            marker_hits = {
                marker: (marker in copied_text) for marker in browser_copy_markers(project_id)
            }
            status_hits = {
                marker: (marker in status_text) for marker in BROWSER_COPY_STATUS_MARKERS
            }
            final_result = (
                not forbidden_method_seen
                and not forbidden_path_seen
                and not external_network_seen
                and not ollama_seen
                and clipboard_write_count >= 1
                and len(copied_text) > 1000
                and all(marker_hits.values())
                and all(status_hits.values())
            )
            if not final_result:
                missing_markers = [marker for marker, hit in marker_hits.items() if not hit]
                missing_status = [marker for marker, hit in status_hits.items() if not hit]
                details = []
                if missing_markers:
                    details.append("missing copied text markers: " + ", ".join(missing_markers))
                if missing_status:
                    details.append("missing status text markers: " + ", ".join(missing_status))
                if details:
                    error = "; ".join(details)
    except Exception as exc:
        error = str(exc)
        final_result = False
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass

    return emit()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.mode == "probe":
            return run_probe_mode(args, title="probe")
        if args.mode == "report":
            return run_probe_mode(args, title="report")
        if args.mode == "check-port":
            return run_check_port_mode(args)
        if args.mode == "start-probe":
            return run_start_probe_mode(args)
        if args.mode == "scenario":
            return run_scenario_mode(args)
        if args.mode == "data-preflight":
            return run_data_preflight_mode(args)
        if args.mode == "browser-markdown-copy":
            return run_browser_markdown_copy_mode(args)
    except SmokeGuardError as exc:
        print("# smoke_guard error report")
        print()
        print("- result: FAIL")
        print(f"- error: `{exc}`")
        return 2
    parser.error(f"Unsupported mode: {args.mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
