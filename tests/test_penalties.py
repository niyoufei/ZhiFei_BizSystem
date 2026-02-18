from app.config import load_config
from app.engine.scorer import score_text


def test_empty_promises_penalty():
    text = "我们将严格确保质量管理到位。"
    config = load_config()
    report = score_text(text, config.rubric, config.lexicon)
    codes = [p.code for p in report.penalties]
    assert "P-EMPTY-001" in codes
