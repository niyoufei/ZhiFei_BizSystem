#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Set

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAFE_CLICK_ID_RE = re.compile(r"safeClick0?\(\s*'([^']+)'")
ACTION_ID_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*:\s*\{\s*resultId\s*:")
ACTION_ROW_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\s*:\s*\{\s*resultId\s*:\s*'([^']+)'",
)
ELEMENT_VAR_RE = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*document\.getElementById\('([^']+)'\);"
)
DIRECT_ELEMENT_HANDLER_RE = re.compile(
    r"document\.getElementById\('([^']+)'\)\s*(?:\|\|\s*\{\s*\})?\.(onclick|onsubmit)\s*=",
    re.S,
)
DIRECT_ELEMENT_LISTENER_RE = re.compile(
    r"document\.getElementById\('([^']+)'\)\s*(?:\|\|\s*\{\s*\})?\.addEventListener\(\s*'(click|submit)'",
    re.S,
)
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

CRITICAL_EXPORT_BUTTON_IDS = [
    "btnWritingGuidancePatchBundleDownloadDocx",
]

INLINE_EXPECTED_MARKERS = {
    "btnGuidancePatchBundleInlineDownloadDocx": "downloadWritingGuidancePatchBundleDocx(projectId, 'guidanceResult');",
}

DYNAMIC_BUTTON_CONTRACTS = {
    "btnEvidenceTraceDownload": {
        "create_marker": 'id="btnEvidenceTraceDownload"',
        "bind_marker": "const dlBtn = document.getElementById('btnEvidenceTraceDownload');",
    },
    "btnGuidancePatchBundleInlineDownloadDocx": {
        "create_marker": 'id="btnGuidancePatchBundleInlineDownloadDocx"',
        "bind_marker": "const inlinePatchDocxBtn = document.getElementById('btnGuidancePatchBundleInlineDownloadDocx');",
    },
}

CRITICAL_VISIBLE_BUTTON_IDS = [
    "btnScoringFactors",
    "btnScoringFactorsMd",
    "btnAnalysisBundle",
    "btnAnalysisBundleDownload",
]

SMOKE_REQUIRED_BUTTON_IDS = [
    "btnSaveApiKey",
    "btnClearApiKey",
    "btnReloadPage",
    "btnStartNewProject",
    "btnCreateProjectFromTender",
    "deleteSelectedProjects",
    "refreshProjects",
    "btnSelectProjectBySearch",
    "btnUploadMaterials",
    "btnUploadBoq",
    "btnUploadDrawing",
    "btnUploadSitePhotos",
    "btnRefreshMaterials",
    "btnUploadShigong",
    "btnRefreshSubmissions",
    "btnRefreshGroundTruth",
]

SMOKE_ALLOWLIST_REASONS = {
    "btnCompilationInstructions": "hidden on /developer/debug; removed from main business UI",
    "btnDataHygiene": "hidden on /developer/debug; removed from main business UI",
    "btnEvalSummaryV2": "hidden on /developer/debug; removed from main business UI",
    "btnEvolutionHealth": "hidden on /developer/debug; removed from main business UI",
    "btnFeedbackGovernance": "hidden on /developer/debug; removed from main business UI",
    "btnSelfCheck": "hidden on /developer/debug; removed from main business UI",
    "btnSystemImprovementOverview": "hidden on /developer/debug; removed from main business UI",
    "btnTrialPreflight": "hidden on /developer/debug; removed from main business UI",
    "btnTrialPreflightDownload": "hidden on /developer/debug; removed from main business UI",
    "btnTrialPreflightDownloadDocx": "hidden on /developer/debug; removed from main business UI",
    "btnWritingGuidanceDownload": "hidden on /developer/debug; removed from main business UI",
    "btnWritingGuidancePatchBundleDownload": "hidden on /developer/debug; removed from main business UI",
    "materialsTrialPreflightFollowUpAction": "hidden on /developer/debug; removed from main business UI",
}


class _StaticDomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.element_ids: Set[str] = set()
        self.button_ids: Set[str] = set()
        self.form_ids: Set[str] = set()
        self.button_form_ids: Dict[str, str] = {}
        self.button_hidden_states: Dict[str, bool] = {}
        self._form_stack: List[str] = []
        self._hidden_stack: List[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        element_id = str(attr_map.get("id") or "").strip()
        class_name = str(attr_map.get("class") or "").strip()
        class_tokens = {token for token in class_name.split() if token}
        parent_hidden = self._hidden_stack[-1] if self._hidden_stack else False
        current_hidden = parent_hidden or ("compact-hidden" in class_tokens)
        if element_id:
            self.element_ids.add(element_id)
        if tag.lower() == "form":
            if element_id:
                self.form_ids.add(element_id)
            self._form_stack.append(element_id)
            if tag.lower() not in VOID_TAGS:
                self._hidden_stack.append(current_hidden)
            return
        if tag.lower() == "button" and element_id:
            self.button_ids.add(element_id)
            self.button_hidden_states[element_id] = current_hidden
            current_form_id = self._form_stack[-1] if self._form_stack else ""
            if current_form_id:
                self.button_form_ids[element_id] = current_form_id
        if tag.lower() not in VOID_TAGS:
            self._hidden_stack.append(current_hidden)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._form_stack:
            self._form_stack.pop()
        if tag.lower() not in VOID_TAGS and self._hidden_stack:
            self._hidden_stack.pop()


def _extract_static_dom(
    page: str,
) -> tuple[Set[str], Set[str], Set[str], Dict[str, str], Dict[str, bool]]:
    parser = _StaticDomParser()
    parser.feed(page)
    return (
        parser.element_ids,
        parser.button_ids,
        parser.form_ids,
        parser.button_form_ids,
        parser.button_hidden_states,
    )


def _extract_safe_click_ids(page: str) -> Set[str]:
    return {str(item).strip() for item in SAFE_CLICK_ID_RE.findall(page) if str(item).strip()}


def _extract_bound_element_ids(page: str) -> Set[str]:
    ids: Set[str] = set()
    variable_ids = {
        str(variable).strip(): str(element_id).strip()
        for variable, element_id in ELEMENT_VAR_RE.findall(page)
        if str(variable).strip() and str(element_id).strip()
    }
    for variable, element_id in variable_ids.items():
        if re.search(rf"\b{re.escape(variable)}\.onclick\s*=", page):
            ids.add(element_id)
        if re.search(rf"\b{re.escape(variable)}\.onsubmit\s*=", page):
            ids.add(element_id)
        if re.search(rf"\b{re.escape(variable)}\.addEventListener\(\s*'click'", page):
            ids.add(element_id)
        if re.search(rf"\b{re.escape(variable)}\.addEventListener\(\s*'submit'", page):
            ids.add(element_id)
    for element_id, _ in DIRECT_ELEMENT_HANDLER_RE.findall(page):
        if str(element_id).strip():
            ids.add(str(element_id).strip())
    for element_id, _ in DIRECT_ELEMENT_LISTENER_RE.findall(page):
        if str(element_id).strip():
            ids.add(str(element_id).strip())
    return ids


def _extract_action_map_ids(page: str) -> Set[str]:
    return {
        str(item).strip()
        for item in ACTION_ID_RE.findall(page)
        if str(item).strip().startswith("btn")
    }


def _extract_action_rows(page: str) -> List[Dict[str, str]]:
    rows = []
    seen: Set[tuple[str, str]] = set()
    for button_id, result_id in ACTION_ROW_RE.findall(page):
        button = str(button_id).strip()
        result = str(result_id).strip()
        if not button or not result:
            continue
        key = (button, result)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"button_id": button, "result_id": result})
    return rows


def _extract_const_array_ids(page: str, const_name: str) -> Set[str]:
    pattern = re.compile(
        rf"const\s+{re.escape(const_name)}\s*=\s*(?:new Set\()?\[([\s\S]*?)\]\)?;",
        re.S,
    )
    match = pattern.search(page)
    if not match:
        return set()
    return {
        str(item).strip() for item in re.findall(r"'([^']+)'", match.group(1)) if str(item).strip()
    }


def fetch_page(base_url: str) -> str:
    request = urllib.request.Request(base_url, headers={"User-Agent": "web-button-contract-check"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _missing_from(values: Iterable[str], allowed: Set[str]) -> List[str]:
    return sorted({item for item in values if item not in allowed})


def _load_smoke_button_ids() -> tuple[Set[str], Set[str]]:
    from scripts.browser_button_smoke import BUTTON_SMOKE_MATRIX, WRITE_BUTTON_SMOKE_MATRIX

    read_smoke_ids = {
        str(item.get("id") or "").strip()
        for item in BUTTON_SMOKE_MATRIX
        if str(item.get("id") or "").strip()
    }
    write_smoke_ids = {
        str(item.get("id") or "").strip()
        for item in WRITE_BUTTON_SMOKE_MATRIX
        if str(item.get("id") or "").strip()
    }
    return read_smoke_ids, write_smoke_ids


def build_report_from_html(page: str) -> Dict[str, object]:
    (
        element_ids,
        button_ids,
        form_ids,
        button_form_ids,
        button_hidden_states,
    ) = _extract_static_dom(page)
    safe_click_ids = _extract_safe_click_ids(page)
    bound_element_ids = _extract_bound_element_ids(page)
    action_map_ids = _extract_action_map_ids(page)
    action_rows = _extract_action_rows(page)
    auth_protected_ids = _extract_const_array_ids(page, "AUTH_PROTECTED_ACTION_IDS")
    project_required_ids = _extract_const_array_ids(page, "PROJECT_REQUIRED_BUTTON_IDS")
    non_blocking_ids = _extract_const_array_ids(page, "NON_BLOCKING_ACTION_BUTTON_IDS")
    secure_desktop_blocked_ids = _extract_const_array_ids(page, "SECURE_DESKTOP_BLOCKED_BUTTON_IDS")

    submit_bound_button_ids = {
        button_id
        for button_id, form_id in button_form_ids.items()
        if form_id in bound_element_ids and form_id in form_ids
    }
    direct_button_ids = bound_element_ids & button_ids
    bound_ids = safe_click_ids | direct_button_ids | action_map_ids | submit_bound_button_ids
    missing_bindings = _missing_from(button_ids, bound_ids)

    export_contracts = []
    export_contract_ok = True
    for button_id in CRITICAL_EXPORT_BUTTON_IDS:
        row = {
            "button_id": button_id,
            "present_in_html": button_id in button_ids,
            "registered_in_action_map": button_id in action_map_ids,
            "auth_protected": button_id in auth_protected_ids,
            "project_required": button_id in project_required_ids,
            "non_blocking": button_id in non_blocking_ids,
            "secure_desktop_blocked": button_id in secure_desktop_blocked_ids,
        }
        row["ok"] = all(bool(v) for k, v in row.items() if k != "button_id")
        export_contract_ok = export_contract_ok and bool(row["ok"])
        export_contracts.append(row)

    inline_contracts = []
    inline_contract_ok = True
    for button_id, marker in INLINE_EXPECTED_MARKERS.items():
        row = {
            "button_id": button_id,
            "marker_present": marker in page,
        }
        row["ok"] = bool(row["marker_present"])
        inline_contract_ok = inline_contract_ok and bool(row["ok"])
        inline_contracts.append(row)

    dynamic_button_contracts = []
    dynamic_button_contract_ok = True
    for button_id, markers in DYNAMIC_BUTTON_CONTRACTS.items():
        row = {
            "button_id": button_id,
            "create_marker_present": str(markers.get("create_marker") or "") in page,
            "bind_marker_present": str(markers.get("bind_marker") or "") in page,
        }
        row["ok"] = bool(row["create_marker_present"] and row["bind_marker_present"])
        dynamic_button_contract_ok = dynamic_button_contract_ok and bool(row["ok"])
        dynamic_button_contracts.append(row)

    critical_visible_button_contracts = []
    critical_visible_button_contract_ok = True
    for button_id in CRITICAL_VISIBLE_BUTTON_IDS:
        row = {
            "button_id": button_id,
            "present_in_html": button_id in button_ids,
            "hidden_by_compact": bool(button_hidden_states.get(button_id, False)),
        }
        row["ok"] = bool(row["present_in_html"] and not row["hidden_by_compact"])
        critical_visible_button_contract_ok = critical_visible_button_contract_ok and bool(
            row["ok"]
        )
        critical_visible_button_contracts.append(row)

    dynamic_button_ids = {item["button_id"] for item in dynamic_button_contracts}
    known_actionable_ids = button_ids | dynamic_button_ids
    known_registered_ids = (
        known_actionable_ids | safe_click_ids | direct_button_ids | action_map_ids
    )

    action_result_contracts = []
    action_result_contract_ok = True
    for row in action_rows:
        allowlisted_reason = str(SMOKE_ALLOWLIST_REASONS.get(row["button_id"]) or "").strip()
        item = {
            "button_id": row["button_id"],
            "result_id": row["result_id"],
            "button_present": row["button_id"] in known_actionable_ids,
            "result_present": row["result_id"] in element_ids,
            "allowlisted": bool(allowlisted_reason),
            "reason": allowlisted_reason,
        }
        item["ok"] = bool(
            (item["button_present"] and item["result_present"]) or item["allowlisted"]
        )
        action_result_contract_ok = action_result_contract_ok and bool(item["ok"])
        action_result_contracts.append(item)

    guard_set_contracts = []
    guard_set_contract_ok = True
    for set_name, ids in [
        ("AUTH_PROTECTED_ACTION_IDS", auth_protected_ids),
        ("PROJECT_REQUIRED_BUTTON_IDS", project_required_ids),
        ("NON_BLOCKING_ACTION_BUTTON_IDS", non_blocking_ids),
        ("SECURE_DESKTOP_BLOCKED_BUTTON_IDS", secure_desktop_blocked_ids),
    ]:
        missing_ids = sorted(item for item in ids if item not in known_registered_ids)
        row = {
            "set_name": set_name,
            "count": len(ids),
            "missing_ids": missing_ids,
            "ok": not missing_ids,
        }
        guard_set_contract_ok = guard_set_contract_ok and bool(row["ok"])
        guard_set_contracts.append(row)

    read_smoke_ids, write_smoke_ids = _load_smoke_button_ids()
    smoke_coverage_contracts = []
    smoke_coverage_contract_ok = True
    smoke_required_ids = sorted(
        set(CRITICAL_EXPORT_BUTTON_IDS)
        | set(CRITICAL_VISIBLE_BUTTON_IDS)
        | set(SMOKE_REQUIRED_BUTTON_IDS)
        | set(DYNAMIC_BUTTON_CONTRACTS)
        | set(INLINE_EXPECTED_MARKERS)
    )
    for button_id in smoke_required_ids:
        row = {
            "button_id": button_id,
            "in_read_smoke": button_id in read_smoke_ids,
            "in_write_smoke": button_id in write_smoke_ids,
        }
        row["smoke_covered"] = bool(row["in_read_smoke"] or row["in_write_smoke"])
        row["ok"] = bool(row["smoke_covered"])
        smoke_coverage_contract_ok = smoke_coverage_contract_ok and bool(row["ok"])
        smoke_coverage_contracts.append(row)

    actionable_button_ids = sorted(button_ids | dynamic_button_ids)
    uncovered_actionable_ids = sorted(
        button_id
        for button_id in actionable_button_ids
        if button_id not in read_smoke_ids | write_smoke_ids
    )
    smoke_gap_contracts = []
    smoke_gap_contract_ok = True
    for button_id in uncovered_actionable_ids:
        reason = str(SMOKE_ALLOWLIST_REASONS.get(button_id) or "").strip()
        row = {
            "button_id": button_id,
            "allowlisted": bool(reason),
            "reason": reason,
        }
        row["ok"] = bool(row["allowlisted"])
        smoke_gap_contract_ok = smoke_gap_contract_ok and bool(row["ok"])
        smoke_gap_contracts.append(row)

    stale_smoke_allowlist_ids = sorted(
        button_id
        for button_id in SMOKE_ALLOWLIST_REASONS
        if button_id in read_smoke_ids or button_id in write_smoke_ids
    )
    smoke_allowlist_contract = {
        "stale_ids": stale_smoke_allowlist_ids,
        "ok": not stale_smoke_allowlist_ids,
    }

    ok = (
        not missing_bindings
        and export_contract_ok
        and inline_contract_ok
        and dynamic_button_contract_ok
        and critical_visible_button_contract_ok
        and action_result_contract_ok
        and guard_set_contract_ok
        and smoke_coverage_contract_ok
        and smoke_gap_contract_ok
        and bool(smoke_allowlist_contract["ok"])
    )
    return {
        "ok": ok,
        "button_count": len(button_ids),
        "bound_button_count": len(button_ids) - len(missing_bindings),
        "button_ids": sorted(button_ids),
        "safe_click_ids": sorted(safe_click_ids),
        "direct_onclick_ids": sorted(direct_button_ids),
        "submit_bound_button_ids": sorted(submit_bound_button_ids),
        "action_map_ids": sorted(action_map_ids),
        "missing_bindings": missing_bindings,
        "export_contracts": export_contracts,
        "inline_contracts": inline_contracts,
        "dynamic_button_contracts": dynamic_button_contracts,
        "critical_visible_button_contracts": critical_visible_button_contracts,
        "action_result_contracts": action_result_contracts,
        "guard_set_contracts": guard_set_contracts,
        "smoke_coverage_contracts": smoke_coverage_contracts,
        "smoke_gap_contracts": smoke_gap_contracts,
        "smoke_allowlist_contract": smoke_allowlist_contract,
    }


def to_markdown(report: Dict[str, object], *, base_url: str) -> str:
    lines: List[str] = []
    lines.append("# Web Button Contract")
    lines.append("")
    lines.append(f"- base_url: `{base_url}`")
    lines.append(f"- ok: `{report['ok']}`")
    lines.append(f"- button_count: `{report['button_count']}`")
    lines.append(f"- bound_button_count: `{report['bound_button_count']}`")
    lines.append("")
    lines.append("## Export Contracts")
    for item in report["export_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        lines.append(
            "- "
            f"[{flag}] `{item['button_id']}` "
            f"(html={item['present_in_html']}, action_map={item['registered_in_action_map']}, "
            f"auth={item['auth_protected']}, project_required={item['project_required']}, "
            f"non_blocking={item['non_blocking']}, secure_block={item['secure_desktop_blocked']})"
        )
    lines.append("")
    lines.append("## Inline Contracts")
    for item in report["inline_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        lines.append(f"- [{flag}] `{item['button_id']}` marker_present={item['marker_present']}")
    lines.append("")
    lines.append("## Dynamic Button Contracts")
    for item in report["dynamic_button_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        lines.append(
            "- "
            f"[{flag}] `{item['button_id']}` "
            f"(create_marker={item['create_marker_present']}, bind_marker={item['bind_marker_present']})"
        )
    lines.append("")
    lines.append("## Critical Visible Button Contracts")
    for item in report["critical_visible_button_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        lines.append(
            "- "
            f"[{flag}] `{item['button_id']}` "
            f"(html={item['present_in_html']}, hidden_by_compact={item['hidden_by_compact']})"
        )
    lines.append("")
    lines.append("## Action Result Contracts")
    for item in report["action_result_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        lines.append(
            "- "
            f"[{flag}] `{item['button_id']}` -> `{item['result_id']}` "
            f"(button_present={item['button_present']}, result_present={item['result_present']})"
        )
    lines.append("")
    lines.append("## Guard Set Contracts")
    for item in report["guard_set_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        lines.append(
            "- "
            f"[{flag}] `{item['set_name']}` count={item['count']} "
            f"missing_ids={item['missing_ids']}"
        )
    lines.append("")
    lines.append("## Smoke Coverage Contracts")
    for item in report["smoke_coverage_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        lines.append(
            "- "
            f"[{flag}] `{item['button_id']}` "
            f"(read={item['in_read_smoke']}, write={item['in_write_smoke']})"
        )
    lines.append("")
    lines.append("## Smoke Gap Contracts")
    for item in report["smoke_gap_contracts"]:
        flag = "OK" if item["ok"] else "MISS"
        reason = item["reason"] or "-"
        lines.append(f"- [{flag}] `{item['button_id']}` reason={reason}")
    lines.append("")
    lines.append("## Smoke Allowlist Contract")
    lines.append(f"- ok: `{report['smoke_allowlist_contract']['ok']}`")
    lines.append(f"- stale_ids: `{report['smoke_allowlist_contract']['stale_ids']}`")
    lines.append("")
    if report["missing_bindings"]:
        lines.append("## Missing Bindings")
        for item in report["missing_bindings"]:
            lines.append(f"- `{item}`")
        lines.append("")
    else:
        lines.append("## Binding Result")
        lines.append("- All static button ids have a JS binding or action entry.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Web button contract and export bindings.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/",
        help="Root page URL to inspect.",
    )
    parser.add_argument(
        "--output-json",
        default=str(ROOT / "build" / "web_button_contract.json"),
        help="Path to write JSON report.",
    )
    parser.add_argument(
        "--output-md",
        default=str(ROOT / "build" / "web_button_contract.md"),
        help="Path to write Markdown report.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when contract check fails.",
    )
    args = parser.parse_args()

    page = fetch_page(args.base_url)
    report = build_report_from_html(page)

    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = Path(args.output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(to_markdown(report, base_url=args.base_url), encoding="utf-8")

    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    print(f"ok: {report['ok']}")
    if report["missing_bindings"]:
        print("missing_bindings:")
        for item in report["missing_bindings"]:
            print(f"  - {item}")
    if args.strict and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
