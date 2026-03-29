from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict

LLM_RUNTIME_STATE_PATH = Path(__file__).resolve().parents[2] / "build" / "llm_runtime_state.json"
_STATE_LOCK = threading.Lock()
_KNOWN_PROVIDERS = ("openai", "gemini")
_KNOWN_REVIEW_STATUSES = ("confirmed", "diverged", "unavailable", "fallback_only")


def _empty_provider_review_stats() -> Dict[str, Any]:
    return {
        "confirmed_count": 0,
        "diverged_count": 0,
        "unavailable_count": 0,
        "fallback_only_count": 0,
        "last_status": None,
        "last_at": None,
    }


def _empty_state() -> Dict[str, Any]:
    return {
        "provider_failures": {},
        "provider_quality_degraded": {},
        "provider_review_stats": {
            provider: _empty_provider_review_stats() for provider in _KNOWN_PROVIDERS
        },
        "account_failures": {provider: {} for provider in _KNOWN_PROVIDERS},
    }


def _runtime_state_path() -> Path:
    return LLM_RUNTIME_STATE_PATH


def _load_state_unlocked() -> Dict[str, Any]:
    path = _runtime_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(payload, dict):
        return _empty_state()
    provider_failures = payload.get("provider_failures")
    provider_quality_degraded = payload.get("provider_quality_degraded")
    provider_review_stats = payload.get("provider_review_stats")
    account_failures = payload.get("account_failures")
    state = _empty_state()
    if isinstance(provider_failures, dict):
        for provider, failed_at in provider_failures.items():
            if provider in _KNOWN_PROVIDERS:
                normalized = _normalize_timestamp(failed_at)
                if normalized is not None:
                    state["provider_failures"][provider] = normalized
    if isinstance(provider_quality_degraded, dict):
        for provider, degraded_at in provider_quality_degraded.items():
            if provider in _KNOWN_PROVIDERS:
                normalized = _normalize_timestamp(degraded_at)
                if normalized is not None:
                    state["provider_quality_degraded"][provider] = normalized
    if isinstance(provider_review_stats, dict):
        for provider, stats in provider_review_stats.items():
            if provider not in _KNOWN_PROVIDERS or not isinstance(stats, dict):
                continue
            row = _empty_provider_review_stats()
            for status in _KNOWN_REVIEW_STATUSES:
                row[f"{status}_count"] = max(
                    0, int(_normalize_timestamp(stats.get(f"{status}_count")) or 0)
                )
            last_status = str(stats.get("last_status") or "").strip()
            row["last_status"] = last_status if last_status in _KNOWN_REVIEW_STATUSES else None
            row["last_at"] = _normalize_timestamp(stats.get("last_at"))
            state["provider_review_stats"][provider] = row
    if isinstance(account_failures, dict):
        for provider in _KNOWN_PROVIDERS:
            rows = account_failures.get(provider)
            if not isinstance(rows, dict):
                continue
            for fingerprint, failed_at in rows.items():
                if not isinstance(fingerprint, str) or not fingerprint.strip():
                    continue
                normalized = _normalize_timestamp(failed_at)
                if normalized is not None:
                    state["account_failures"][provider][fingerprint] = normalized
    return state


def _save_state_unlocked(state: Dict[str, Any]) -> None:
    path = _runtime_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _normalize_timestamp(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def fingerprint_api_key(raw_key: str) -> str:
    return hashlib.sha256(str(raw_key or "").strip().encode("utf-8")).hexdigest()


def get_provider_failure_timestamps() -> Dict[str, float]:
    with _STATE_LOCK:
        state = _load_state_unlocked()
    return {
        provider: float(failed_at)
        for provider, failed_at in (state.get("provider_failures") or {}).items()
        if provider in _KNOWN_PROVIDERS
    }


def get_provider_quality_degraded_timestamps() -> Dict[str, float]:
    with _STATE_LOCK:
        state = _load_state_unlocked()
    return {
        provider: float(degraded_at)
        for provider, degraded_at in (state.get("provider_quality_degraded") or {}).items()
        if provider in _KNOWN_PROVIDERS
    }


def get_provider_review_stats() -> Dict[str, Dict[str, Any]]:
    with _STATE_LOCK:
        state = _load_state_unlocked()
    out: Dict[str, Dict[str, Any]] = {}
    rows = state.get("provider_review_stats") or {}
    for provider in _KNOWN_PROVIDERS:
        stats = rows.get(provider)
        if not isinstance(stats, dict):
            continue
        out[provider] = {
            "confirmed_count": max(0, int(_normalize_timestamp(stats.get("confirmed_count")) or 0)),
            "diverged_count": max(0, int(_normalize_timestamp(stats.get("diverged_count")) or 0)),
            "unavailable_count": max(
                0, int(_normalize_timestamp(stats.get("unavailable_count")) or 0)
            ),
            "fallback_only_count": max(
                0, int(_normalize_timestamp(stats.get("fallback_only_count")) or 0)
            ),
            "last_status": (
                str(stats.get("last_status") or "").strip()
                if str(stats.get("last_status") or "").strip() in _KNOWN_REVIEW_STATUSES
                else None
            ),
            "last_at": _normalize_timestamp(stats.get("last_at")),
        }
    return out


def set_provider_failure(provider: str, failed_at: float) -> None:
    if provider not in _KNOWN_PROVIDERS:
        return
    with _STATE_LOCK:
        state = _load_state_unlocked()
        state["provider_failures"][provider] = float(failed_at)
        _save_state_unlocked(state)


def clear_provider_failure(provider: str) -> None:
    if provider not in _KNOWN_PROVIDERS:
        return
    with _STATE_LOCK:
        state = _load_state_unlocked()
        state["provider_failures"].pop(provider, None)
        _save_state_unlocked(state)


def set_provider_quality_degraded(provider: str, degraded_at: float) -> None:
    if provider not in _KNOWN_PROVIDERS:
        return
    with _STATE_LOCK:
        state = _load_state_unlocked()
        state["provider_quality_degraded"][provider] = float(degraded_at)
        _save_state_unlocked(state)


def clear_provider_quality_degraded(provider: str) -> None:
    if provider not in _KNOWN_PROVIDERS:
        return
    with _STATE_LOCK:
        state = _load_state_unlocked()
        state["provider_quality_degraded"].pop(provider, None)
        _save_state_unlocked(state)


def record_provider_review_outcome(provider: str, status: str, recorded_at: float) -> None:
    normalized_status = str(status or "").strip()
    if provider not in _KNOWN_PROVIDERS or normalized_status not in _KNOWN_REVIEW_STATUSES:
        return
    with _STATE_LOCK:
        state = _load_state_unlocked()
        row = (state.get("provider_review_stats") or {}).setdefault(
            provider, _empty_provider_review_stats()
        )
        count_key = f"{normalized_status}_count"
        row[count_key] = max(0, int(_normalize_timestamp(row.get(count_key)) or 0)) + 1
        row["last_status"] = normalized_status
        row["last_at"] = float(recorded_at)
        _save_state_unlocked(state)


def get_account_failure_timestamps(provider: str, keys: list[str]) -> Dict[str, float]:
    if provider not in _KNOWN_PROVIDERS:
        return {}
    with _STATE_LOCK:
        state = _load_state_unlocked()
    persisted = (state.get("account_failures") or {}).get(provider) or {}
    out: Dict[str, float] = {}
    for key in keys:
        raw = str(key or "").strip()
        if not raw:
            continue
        failed_at = persisted.get(fingerprint_api_key(raw))
        normalized = _normalize_timestamp(failed_at)
        if normalized is not None:
            out[raw] = normalized
    return out


def set_account_failure(provider: str, key: str, failed_at: float) -> None:
    raw = str(key or "").strip()
    if provider not in _KNOWN_PROVIDERS or not raw:
        return
    with _STATE_LOCK:
        state = _load_state_unlocked()
        provider_state = (state.get("account_failures") or {}).setdefault(provider, {})
        provider_state[fingerprint_api_key(raw)] = float(failed_at)
        _save_state_unlocked(state)


def clear_account_failure(provider: str, key: str) -> None:
    raw = str(key or "").strip()
    if provider not in _KNOWN_PROVIDERS or not raw:
        return
    with _STATE_LOCK:
        state = _load_state_unlocked()
        provider_state = (state.get("account_failures") or {}).setdefault(provider, {})
        provider_state.pop(fingerprint_api_key(raw), None)
        _save_state_unlocked(state)
