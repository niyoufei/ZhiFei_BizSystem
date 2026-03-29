from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict

LLM_RUNTIME_STATE_PATH = Path(__file__).resolve().parents[2] / "build" / "llm_runtime_state.json"
_STATE_LOCK = threading.Lock()
_KNOWN_PROVIDERS = ("openai", "gemini")


def _empty_state() -> Dict[str, Any]:
    return {
        "provider_failures": {},
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
    account_failures = payload.get("account_failures")
    state = _empty_state()
    if isinstance(provider_failures, dict):
        for provider, failed_at in provider_failures.items():
            if provider in _KNOWN_PROVIDERS:
                normalized = _normalize_timestamp(failed_at)
                if normalized is not None:
                    state["provider_failures"][provider] = normalized
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
