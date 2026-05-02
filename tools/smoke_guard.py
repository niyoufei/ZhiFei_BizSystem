"""Runtime smoke guard for externally managed service checks.

The guard probes URLs and ports that are already expected to be running. It
does not start the project service unless the explicit start-probe mode is used.
"""

from __future__ import annotations

import argparse
import dataclasses
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, Sequence


DEFAULT_TIMEOUT = 5.0
DEFAULT_EXPECTED_STATUSES = frozenset({200, 204, 301, 302})


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
        return PortProbeResult(host=host, port=port, ok=False, latency_ms=latency_ms, error=str(exc))


def prepare_start_command(start_cmd: str) -> list[str]:
    parts = shlex.split(start_cmd)
    if not parts:
        raise SmokeGuardError("start command is empty.")
    if any(part.endswith(".sh") for part in parts):
        raise SmokeGuardError("start command must not invoke shell script files.")
    return parts


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
    lines.extend(["", "## summary", f"- checked: `{len(results)}`", f"- passed: `{len(results) - len(failures)}`"])
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


def _add_probe_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--paths", default="/")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--expect-status", default="")
    parser.add_argument("--expect-text", action="append", default=[])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Runtime smoke guard for externally managed services.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    probe = subparsers.add_parser("probe", help="Probe one or more HTTP paths.")
    _add_probe_options(probe)

    check_port_parser = subparsers.add_parser("check-port", help="Check whether a TCP port is open.")
    check_port_parser.add_argument("--host", default="127.0.0.1")
    check_port_parser.add_argument("--port", required=True, type=int)
    check_port_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    report = subparsers.add_parser("report", help="Alias for a Markdown URL probe report.")
    _add_probe_options(report)

    start_probe = subparsers.add_parser("start-probe", help="Start an explicit command, then probe URLs.")
    start_probe.add_argument("--start-cmd", required=True)
    start_probe.add_argument("--startup-timeout", type=float, default=10.0)
    _add_probe_options(start_probe)
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
    print(render_url_report(title=title, base_url=args.base_url, paths=paths, results=results), end="")
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
    print(render_url_report(title="start-probe", base_url=args.base_url, paths=paths, results=results), end="")
    return 0 if all(result.ok for result in results) else 1


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
