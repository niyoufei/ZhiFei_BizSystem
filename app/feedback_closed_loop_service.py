from __future__ import annotations

from typing import Any, Callable, Dict


def run_feedback_closed_loop(
    project_id: str,
    *,
    locale: str,
    trigger: str,
    refresh_project_reflection_objects: Callable[[str], None],
    auto_update_project_weights_from_delta_cases: Callable[[str], Dict[str, object]],
    sync_feedback_weights_to_evolution: Callable[..., Dict[str, object]],
    auto_run_reflection_pipeline: Callable[..., object],
    refresh_evolution_report_from_ground_truth: Callable[[str], Dict[str, object]],
) -> Dict[str, object]:
    result: Dict[str, object] = {
        "ok": True,
        "project_id": project_id,
        "trigger": trigger,
        "weight_update": {"updated": False},
        "weight_sync_to_evolution": {"synced": False},
        "auto_run": None,
        "evolution_refresh": {"refreshed": False},
    }
    try:
        refresh_project_reflection_objects(project_id)
    except Exception as exc:
        result["ok"] = False
        result["refresh_error"] = str(exc)
        return result

    try:
        result["weight_update"] = auto_update_project_weights_from_delta_cases(project_id)
    except Exception as exc:
        result["weight_update"] = {"updated": False, "error": str(exc)}
    try:
        result["weight_sync_to_evolution"] = sync_feedback_weights_to_evolution(
            project_id,
            result["weight_update"],
        )
    except Exception as exc:
        result["weight_sync_to_evolution"] = {"synced": False, "error": str(exc)}

    try:
        auto_resp = auto_run_reflection_pipeline(
            project_id=project_id,
            api_key=None,
            locale=locale,
        )
        if hasattr(auto_resp, "model_dump"):
            result["auto_run"] = auto_resp.model_dump()
        else:
            result["auto_run"] = dict(auto_resp)
    except Exception as exc:
        result["auto_run"] = {"ok": False, "error": str(exc)}
        result["ok"] = False
    try:
        result["evolution_refresh"] = refresh_evolution_report_from_ground_truth(project_id)
    except Exception as exc:
        result["evolution_refresh"] = {"refreshed": False, "error": str(exc)}
    return result


def run_feedback_closed_loop_safe(
    project_id: str,
    *,
    locale: str,
    trigger: str,
    run_feedback_closed_loop: Callable[..., object],
    logger: Any,
) -> Dict[str, object]:
    try:
        raw_result = run_feedback_closed_loop(project_id, locale=locale, trigger=trigger)
        if isinstance(raw_result, dict):
            result = dict(raw_result)
        elif hasattr(raw_result, "model_dump"):
            dumped = raw_result.model_dump()
            if isinstance(dumped, dict):
                result = dict(dumped)
            else:
                result = {
                    "ok": bool(getattr(raw_result, "ok", False)),
                    "project_id": project_id,
                    "trigger": trigger,
                    "raw": str(raw_result),
                }
        else:
            result = {
                "ok": bool(getattr(raw_result, "ok", False)),
                "project_id": project_id,
                "trigger": trigger,
                "raw": str(raw_result),
            }
        if not bool(result.get("ok", True)):
            logger.warning(
                "feedback_closed_loop_non_ok project_id=%s trigger=%s result=%s",
                project_id,
                trigger,
                result,
            )
        return result
    except Exception as exc:
        logger.exception(
            "feedback_closed_loop_exception project_id=%s trigger=%s error=%s",
            project_id,
            trigger,
            exc,
        )
        return {
            "ok": False,
            "project_id": project_id,
            "trigger": trigger,
            "error": f"{type(exc).__name__}: {exc}",
        }
