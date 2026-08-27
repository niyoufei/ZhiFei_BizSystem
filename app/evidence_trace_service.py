from __future__ import annotations

from collections import Counter
from typing import Dict, List


def build_evidence_trace_summary(report: Dict[str, object]) -> Dict[str, object]:
    req_hits_raw = report.get("requirement_hits")
    req_hits = req_hits_raw if isinstance(req_hits_raw, list) else []
    total_requirements = 0
    total_hits = 0
    mandatory_total = 0
    mandatory_hit = 0
    runtime_hits = 0
    source_pack_counter: Counter[str] = Counter()
    source_file_hits: List[str] = []
    preview_rows: List[Dict[str, object]] = []

    for item in req_hits:
        if not isinstance(item, dict):
            continue
        total_requirements += 1
        mandatory = bool(item.get("mandatory"))
        if mandatory:
            mandatory_total += 1
        if not bool(item.get("hit")):
            continue
        total_hits += 1
        if mandatory:
            mandatory_hit += 1

        source_pack = str(item.get("source_pack_id") or "").strip() or "unknown"
        source_pack_counter[source_pack] += 1
        if source_pack in {"runtime_material_rag", "runtime_material_consistency"}:
            runtime_hits += 1

        source_filename = str(item.get("source_filename") or "").strip()
        if not source_filename:
            chunk_id = str(item.get("chunk_id") or "").strip()
            if "#c" in chunk_id:
                source_filename = chunk_id.split("#c", 1)[0].strip()
        if source_filename and source_filename not in source_file_hits:
            source_file_hits.append(source_filename)

        if len(preview_rows) < 16:
            preview_rows.append(
                {
                    "dimension_id": str(item.get("dimension_id") or ""),
                    "label": str(item.get("label") or ""),
                    "reason": str(item.get("reason") or ""),
                    "mandatory": mandatory,
                    "source_pack_id": source_pack,
                    "material_type": str(item.get("material_type") or ""),
                    "source_filename": source_filename,
                    "chunk_id": str(item.get("chunk_id") or ""),
                }
            )

    mandatory_hit_rate = (
        round(float(mandatory_hit) / float(mandatory_total), 4) if mandatory_total > 0 else None
    )
    overall_hit_rate = (
        round(float(total_hits) / float(total_requirements), 4) if total_requirements > 0 else None
    )
    return {
        "total_requirements": total_requirements,
        "total_hits": total_hits,
        "overall_hit_rate": overall_hit_rate,
        "mandatory_total": mandatory_total,
        "mandatory_hit": mandatory_hit,
        "mandatory_hit_rate": mandatory_hit_rate,
        "runtime_material_hits": runtime_hits,
        "source_files_hit": source_file_hits[:120],
        "source_files_hit_count": len(source_file_hits),
        "source_pack_hit_counts": dict(source_pack_counter),
        "preview": preview_rows,
    }
