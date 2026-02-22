from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
MATERIALS_DIR = DATA_DIR / "materials"
PROJECTS_PATH = DATA_DIR / "projects.json"
SUBMISSIONS_PATH = DATA_DIR / "submissions.json"
MATERIALS_PATH = DATA_DIR / "materials.json"
LEARNING_PATH = DATA_DIR / "learning_profiles.json"
HISTORY_PATH = DATA_DIR / "score_history.json"
PROJECT_CONTEXT_PATH = DATA_DIR / "project_context.json"
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth_scores.json"
EVOLUTION_REPORTS_PATH = DATA_DIR / "evolution_reports.json"
EXPERT_PROFILES_PATH = DATA_DIR / "expert_profiles.json"
SCORE_REPORTS_PATH = DATA_DIR / "score_reports.json"
PROJECT_ANCHORS_PATH = DATA_DIR / "project_anchors.json"
PROJECT_REQUIREMENTS_PATH = DATA_DIR / "project_requirements.json"
EVIDENCE_UNITS_PATH = DATA_DIR / "evidence_units.json"
QINGTIAN_RESULTS_PATH = DATA_DIR / "qingtian_results.json"
CALIBRATION_MODELS_PATH = DATA_DIR / "calibration_models.json"
DELTA_CASES_PATH = DATA_DIR / "delta_cases.json"
CALIBRATION_SAMPLES_PATH = DATA_DIR / "calibration_samples.json"
PATCH_PACKAGES_PATH = DATA_DIR / "patch_packages.json"
PATCH_DEPLOYMENTS_PATH = DATA_DIR / "patch_deployments.json"
HIGH_SCORE_FEATURES_PATH = DATA_DIR / "high_score_features.json"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_projects() -> List[Dict[str, Any]]:
    return load_json(PROJECTS_PATH, [])


def save_projects(data: List[Dict[str, Any]]) -> None:
    save_json(PROJECTS_PATH, data)


def load_submissions() -> List[Dict[str, Any]]:
    return load_json(SUBMISSIONS_PATH, [])


def save_submissions(data: List[Dict[str, Any]]) -> None:
    save_json(SUBMISSIONS_PATH, data)


def load_materials() -> List[Dict[str, Any]]:
    return load_json(MATERIALS_PATH, [])


def save_materials(data: List[Dict[str, Any]]) -> None:
    save_json(MATERIALS_PATH, data)


def load_learning_profiles() -> List[Dict[str, Any]]:
    return load_json(LEARNING_PATH, [])


def save_learning_profiles(data: List[Dict[str, Any]]) -> None:
    save_json(LEARNING_PATH, data)


def load_score_history() -> List[Dict[str, Any]]:
    return load_json(HISTORY_PATH, [])


def save_score_history(data: List[Dict[str, Any]]) -> None:
    save_json(HISTORY_PATH, data)


def append_score_history(entry: Dict[str, Any]) -> None:
    """追加单条评分历史记录"""
    history = load_score_history()
    history.append(entry)
    save_score_history(history)


def get_project_score_history(project_id: str) -> List[Dict[str, Any]]:
    """获取指定项目的评分历史（按时间排序）"""
    history = load_score_history()
    project_history = [h for h in history if h.get("project_id") == project_id]
    return sorted(project_history, key=lambda x: x.get("created_at", ""))


def load_project_context() -> Dict[str, Any]:
    """项目ID -> 投喂包/项目背景文本"""
    return load_json(PROJECT_CONTEXT_PATH, {})


def save_project_context(data: Dict[str, Any]) -> None:
    save_json(PROJECT_CONTEXT_PATH, data)


def load_ground_truth() -> List[Dict[str, Any]]:
    """真实评标记录列表（青天大模型等外部评标结果）"""
    return load_json(GROUND_TRUTH_PATH, [])


def save_ground_truth(data: List[Dict[str, Any]]) -> None:
    save_json(GROUND_TRUTH_PATH, data)


def load_evolution_reports() -> Dict[str, Any]:
    """project_id -> 进化报告（高分逻辑、编制指导等）"""
    return load_json(EVOLUTION_REPORTS_PATH, {})


def save_evolution_reports(data: Dict[str, Any]) -> None:
    save_json(EVOLUTION_REPORTS_PATH, data)


def load_expert_profiles() -> List[Dict[str, Any]]:
    """专家关注度配置列表"""
    return load_json(EXPERT_PROFILES_PATH, [])


def save_expert_profiles(data: List[Dict[str, Any]]) -> None:
    save_json(EXPERT_PROFILES_PATH, data)


def load_score_reports() -> List[Dict[str, Any]]:
    """评分报告快照列表（不覆盖历史）"""
    return load_json(SCORE_REPORTS_PATH, [])


def save_score_reports(data: List[Dict[str, Any]]) -> None:
    save_json(SCORE_REPORTS_PATH, data)


def load_project_anchors() -> List[Dict[str, Any]]:
    """项目锚点列表"""
    return load_json(PROJECT_ANCHORS_PATH, [])


def save_project_anchors(data: List[Dict[str, Any]]) -> None:
    save_json(PROJECT_ANCHORS_PATH, data)


def load_project_requirements() -> List[Dict[str, Any]]:
    """项目要求矩阵列表"""
    return load_json(PROJECT_REQUIREMENTS_PATH, [])


def save_project_requirements(data: List[Dict[str, Any]]) -> None:
    save_json(PROJECT_REQUIREMENTS_PATH, data)


def load_evidence_units() -> List[Dict[str, Any]]:
    """证据单元列表"""
    return load_json(EVIDENCE_UNITS_PATH, [])


def save_evidence_units(data: List[Dict[str, Any]]) -> None:
    save_json(EVIDENCE_UNITS_PATH, data)


def load_qingtian_results() -> List[Dict[str, Any]]:
    """真实青天评标结果列表"""
    return load_json(QINGTIAN_RESULTS_PATH, [])


def save_qingtian_results(data: List[Dict[str, Any]]) -> None:
    save_json(QINGTIAN_RESULTS_PATH, data)


def load_calibration_models() -> List[Dict[str, Any]]:
    """校准器版本列表"""
    return load_json(CALIBRATION_MODELS_PATH, [])


def save_calibration_models(data: List[Dict[str, Any]]) -> None:
    save_json(CALIBRATION_MODELS_PATH, data)


def load_delta_cases() -> List[Dict[str, Any]]:
    """误差案例（DELTA_CASE）列表"""
    return load_json(DELTA_CASES_PATH, [])


def save_delta_cases(data: List[Dict[str, Any]]) -> None:
    save_json(DELTA_CASES_PATH, data)


def load_calibration_samples() -> List[Dict[str, Any]]:
    """校准训练样本（FEATURE_ROW）列表"""
    return load_json(CALIBRATION_SAMPLES_PATH, [])


def save_calibration_samples(data: List[Dict[str, Any]]) -> None:
    save_json(CALIBRATION_SAMPLES_PATH, data)


def load_patch_packages() -> List[Dict[str, Any]]:
    """候选补丁包列表"""
    return load_json(PATCH_PACKAGES_PATH, [])


def save_patch_packages(data: List[Dict[str, Any]]) -> None:
    save_json(PATCH_PACKAGES_PATH, data)


def load_patch_deployments() -> List[Dict[str, Any]]:
    """补丁发布记录列表"""
    return load_json(PATCH_DEPLOYMENTS_PATH, [])


def save_patch_deployments(data: List[Dict[str, Any]]) -> None:
    save_json(PATCH_DEPLOYMENTS_PATH, data)


def load_high_score_features() -> List[Dict[str, Any]]:
    """高分逻辑骨架特征库（可更新置信度）"""
    return load_json(HIGH_SCORE_FEATURES_PATH, [])


def save_high_score_features(data: List[Dict[str, Any]]) -> None:
    save_json(HIGH_SCORE_FEATURES_PATH, data)
