"""DOCX 导出模块测试。"""
import tempfile
from pathlib import Path

from app.engine.docx_exporter import (
    _qingtian_comment,
    _truncate_cn,
    export_report_to_docx,
)


def test_export_report_to_docx_creates_file():
    """测试 DOCX 导出功能能正常生成文件。"""
    # 构造最小化的报告数据
    report = {
        "total_score": 75.5,
        "judge_mode": "rules",
        "judge_source": "rules_engine",
        "spark_called": False,
        "dimension_scores": {
            "07": {
                "name": "重难点及危大工程",
                "score": 6.0,
                "max_score": 10.0,
                "hits": ["风险", "难点"],
                "evidence": [{"snippet": "存在风险。"}],
            },
            "09": {
                "name": "进度保障措施",
                "score": 8.0,
                "max_score": 10.0,
                "hits": ["里程碑"],
                "evidence": [],
            },
            "02": {
                "name": "安全生产管理与措施",
                "score": 5.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
            "03": {
                "name": "文明施工管理与措施",
                "score": 7.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
        },
        "penalties": [
            {
                "code": "P-ACTION-001",
                "deduct": 0.5,
                "message": "措施缺失",
                "evidence_span": {"snippet": "示例"},
            }
        ],
        "suggestions": [{"dimension": "01", "action": "补充内容", "expected_gain": 1.0}],
        "overall": {"confidence_0_1": 0.85},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_output.docx"
        result = export_report_to_docx(report, output_path)

        # 验证文件创建
        assert result.exists()
        assert result.suffix == ".docx"
        assert result.stat().st_size > 0


def test_export_report_to_docx_with_empty_penalties():
    """测试无扣分项时 DOCX 导出正常。"""
    report = {
        "total_score": 100.0,
        "judge_mode": "rules",
        "judge_source": "rules_engine",
        "spark_called": False,
        "dimension_scores": {
            "07": {
                "name": "测试维度",
                "score": 10.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
            "09": {
                "name": "测试维度2",
                "score": 10.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
            "02": {
                "name": "测试维度3",
                "score": 10.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
            "03": {
                "name": "测试维度4",
                "score": 10.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
        },
        "penalties": [],
        "suggestions": [],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_empty.docx"
        result = export_report_to_docx(report, output_path)

        assert result.exists()
        assert result.stat().st_size > 0


def test_truncate_cn_with_none():
    """测试 _truncate_cn 处理 None 值。"""
    result = _truncate_cn(None)
    assert result == ""


def test_truncate_cn_with_long_text():
    """测试 _truncate_cn 截断长文本。"""
    long_text = "这是一段很长的中文文本" * 20
    result = _truncate_cn(long_text, max_len=20)
    assert len(result) == 21  # 20 + "…"
    assert result.endswith("…")


def test_qingtian_comment_action_code():
    """测试 _qingtian_comment 处理 P-ACTION-001 代码。"""
    result = _qingtian_comment("P-ACTION-001", "任意消息")
    assert "措施缺" in result
    assert "参数/频次/验收/责任" in result


def test_qingtian_comment_empty_code():
    """测试 _qingtian_comment 处理 P-EMPTY-001 代码。"""
    result = _qingtian_comment("P-EMPTY-001", "任意消息")
    assert "承诺型" in result


def test_qingtian_comment_other_code():
    """测试 _qingtian_comment 处理其他代码。"""
    result = _qingtian_comment("P-OTHER-001", "这是一条消息")
    assert result == "这是一条消息"[:40]


def test_export_report_with_spark_called():
    """测试 spark_called=True 时的 DOCX 导出。"""
    report = {
        "total_score": 80.0,
        "judge_mode": "spark",
        "judge_source": "spark_llm",
        "spark_called": True,
        "overall": {"confidence_0_1": 0.9},
        "dimension_scores": {
            "07": {
                "name": "重难点及危大工程",
                "score": 7.0,
                "max_score": 10.0,
                "definition_points": ["定义点1", "定义点2"],
                "defects": ["缺陷1", "缺陷2"],
                "improvements": ["改进1", "改进2"],
                "evidence": [{"snippet": "证据片段"}],
            },
            "09": {
                "name": "进度保障措施",
                "score": 8.0,
                "max_score": 10.0,
                "definition_points": [],
                "defects": [],
                "improvements": [],
                "evidence": [],
            },
            "02": {
                "name": "安全生产管理与措施",
                "score": 6.0,
                "max_score": 10.0,
                "definition_points": ["安全定义"],
                "defects": ["安全缺陷"],
                "improvements": ["安全改进"],
                "evidence": [],
            },
            "03": {
                "name": "文明施工管理与措施",
                "score": 7.0,
                "max_score": 10.0,
                "definition_points": [],
                "defects": [],
                "improvements": [],
                "evidence": [],
            },
        },
        "penalties": [
            {
                "code": "P-EMPTY-001",
                "deduct": 0.3,
                "message": "空承诺",
                "evidence_span": {"snippet": "示例证据"},
            }
        ],
        "suggestions": [],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_spark.docx"
        result = export_report_to_docx(report, output_path)

        assert result.exists()
        assert result.stat().st_size > 0


def test_export_report_with_total_score_in_overall():
    """测试 total_score 在 overall 中时的处理。"""
    report = {
        "judge_mode": "rules",
        "judge_source": "rules_engine",
        "spark_called": False,
        "overall": {
            "total_score_0_100": 85.0,
            "confidence_0_1": 0.8,
        },
        "dimension_scores": {
            "07": {"name": "测试维度", "score": 8.0, "max_score": 10.0, "hits": [], "evidence": []},
            "09": {
                "name": "测试维度2",
                "score": 8.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
            "02": {
                "name": "测试维度3",
                "score": 8.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
            "03": {
                "name": "测试维度4",
                "score": 8.0,
                "max_score": 10.0,
                "hits": [],
                "evidence": [],
            },
        },
        "penalties": [],
        "suggestions": [],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_overall_score.docx"
        result = export_report_to_docx(report, output_path)

        assert result.exists()
        assert result.stat().st_size > 0
