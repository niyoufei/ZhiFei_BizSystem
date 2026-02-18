from app.config import load_config
from app.engine.logic_lock import score_logic_lock


def test_logic_lock_breaks_when_missing_steps():
    # 测试文本仅包含分析（风险），不包含任何解决方案关键词
    text = "工程概况：工期30天。存在风险。现场情况复杂，存在安全隐患。"
    config = load_config()
    result, penalties = score_logic_lock(text, config.rubric, config.lexicon)
    assert "solution" in result["breaks"]
    assert any(p["code"] == "LOGIC_LOCK_MISSING_SOLUTION" for p in penalties)


def test_logic_lock_all_steps_present():
    text = "工期30天。存在风险。针对性措施：设备投入与验收流程。"
    config = load_config()
    result, penalties = score_logic_lock(text, config.rubric, config.lexicon)
    assert result["breaks"] == []
    assert penalties == []
