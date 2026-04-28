from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _assert_keywords_visible(text: str, keywords: list[str]) -> None:
    missing = [keyword for keyword in keywords if keyword not in text]
    assert missing == []


def test_report_evidence_delivery_doc_keywords_are_visible():
    report_doc = (REPO_ROOT / "docs/qingtian-report-evidence-delivery.md").read_text(
        encoding="utf-8"
    )
    demo_doc = (REPO_ROOT / "docs/qingtian-business-demo-acceptance.md").read_text(encoding="utf-8")

    _assert_keywords_visible(
        report_doc,
        [
            "青天评分报告与证据链交付说明",
            "文档用途",
            "交付物总表",
            "评分报告交付路径",
            "证据链与评分依据交付路径",
            "对比报告与优化清单交付路径",
            "验收清单",
            "需要服务或授权的动作",
            "与现有阶段成果关系",
            "评分报告 JSON",
            "评分报告 DOCX",
            "latest report",
            "evidence trace",
            "scoring basis",
            "analysis bundle",
            "compare_report",
            "复制优化清单",
            "导出优化清单 JSON",
            "Ollama 增强预览",
            "direct_apply_text",
            "replacement_text",
            "insertion_content",
            "issue",
            "insertion_guidance",
            "rewrite_instruction",
            "不重新评分",
            "不触发 rescore",
            "不写 data",
            "不接 Ollama",
            "不改变运行逻辑",
            "不接核心评分主链",
            "不改变 data 写入结构",
            "scorer.py",
            "v2_scorer.py",
            "storage.py",
            ".env",
            "密钥",
            "ollama serve",
            "doctor.sh",
            "restart_server.sh",
            "data_hygiene",
            "e2e_api_flow.sh",
            "git clean",
            "force push",
            "reset",
            "v0.1.7-qingtian-copy-export",
            "v0.1.8-qingtian-health-stability",
            "v0.1.9-qingtian-health-selfcheck-boundaries",
            "v0.1.10-qingtian-diagnostic-scripts-boundaries",
            "v0.1.11-qingtian-stage-delivery-index",
            "v0.1.12-qingtian-business-demo-acceptance",
        ],
    )

    _assert_keywords_visible(
        demo_doc,
        [
            "评分报告与证据链交付说明",
            "qingtian-report-evidence-delivery.md",
            "评分报告 JSON",
            "DOCX",
            "latest report",
            "evidence trace",
            "scoring basis",
            "analysis bundle",
            "不改变运行逻辑",
            "不接核心评分主链",
            "单独授权",
        ],
    )
