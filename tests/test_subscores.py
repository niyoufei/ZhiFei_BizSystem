from app.config import load_config
from app.engine.scorer import score_text


def test_subscores_for_07_and_09():
    text = (
        "危大工程需专项方案论证，存在风险隐患。"
        "控制在≤5mm，每周巡检，项目经理负责报验签认。"
        "监测旁站与隐蔽验收，制定应急预案与整改措施。"
        "总控计划、周计划、日计划齐全，节点里程碑明确。"
        "劳动力与机械调配，纠偏赶工，例会与日报跟踪。"
    )
    config = load_config()
    report = score_text(text, config.rubric, config.lexicon)
    dim07 = report.dimension_scores["07"]
    dim09 = report.dimension_scores["09"]
    assert dim07.sub_scores is not None
    assert dim09.sub_scores is not None
    assert len(dim07.sub_scores) == 5
    assert len(dim09.sub_scores) == 5
