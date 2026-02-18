from app.config import load_config
from app.engine.dimensions import DIMENSIONS, score_dimension


def test_dimensions_smoke_scores():
    text = "工程概况 安全管理 文明施工 材料验收 新工艺 关键工序 危大工程 质量保障 进度计划 专项施工 劳动力 施工工艺 设备配置 图纸会审 总体配置 技术措施"
    config = load_config()
    for dim_id in DIMENSIONS.keys():
        score, hits, evidence = score_dimension(dim_id, text, config.rubric, config.lexicon)
        assert score >= 0
        assert isinstance(hits, list)
        assert isinstance(evidence, list)
