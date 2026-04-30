from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _assert_keywords_visible(text: str, keywords: list[str]) -> None:
    missing = [keyword for keyword in keywords if keyword not in text]
    assert missing == []


def _assert_any_keyword_visible(text: str, label: str, keywords: list[str]) -> None:
    assert any(keyword in text for keyword in keywords), f"{label}: {keywords}"


def test_delivery_page_entries_match_delivery_docs():
    main_text = _read_repo_file("app/main.py")
    demo_doc = _read_repo_file("docs/qingtian-business-demo-acceptance.md")
    delivery_doc = _read_repo_file("docs/qingtian-report-evidence-delivery.md")

    _assert_keywords_visible(
        main_text,
        [
            "创建项目",
            "upload_materials",
            "上传施组",
            "upload_shigong",
            "评分施组",
            "rescore",
            "reports/latest",
            "证据追溯",
            "evidence_trace",
            "评分依据",
            "scoring_basis",
            "analysis_bundle",
            "compare_report",
            "对比报告",
            "复制优化清单",
            "导出优化清单 JSON",
            "direct_apply_text",
            "replacement_text",
            "insertion_content",
            "Ollama 增强预览",
            "ollama_preview",
            "复制预览结果",
            "导出 JSON",
            "核心评分主链",
            "navigator.clipboard",
            "Blob",
            "createObjectURL",
            "download",
        ],
    )
    _assert_any_keyword_visible(main_text, "latest report", ["latest report", "latest_report"])

    _assert_keywords_visible(
        demo_doc,
        [
            "青天业务演示与验收路径",
            "创建项目",
            "上传材料",
            "评分施组",
            "对比报告",
            "复制优化清单",
            "导出优化清单 JSON",
            "Ollama 增强预览",
            "报告交付",
            "评分报告与证据链交付说明",
            "qingtian-report-evidence-delivery.md",
            "不改变运行逻辑",
            "不接核心评分主链",
            "单独授权",
        ],
    )

    _assert_keywords_visible(
        delivery_doc,
        [
            "青天评分报告与证据链交付说明",
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
            "不重新评分",
            "不触发 rescore",
            "不写 data",
            "不接 Ollama",
            "不接核心评分主链",
            "scorer.py",
            "v2_scorer.py",
            "storage.py",
            "ollama serve",
            "git clean",
        ],
    )


def test_report_evidence_delivery_entry_is_visible_on_home_page():
    main_text = _read_repo_file("app/main.py")

    _assert_keywords_visible(
        main_text,
        [
            "评分报告 / 证据链交付入口",
            "评分报告 JSON",
            "latest report",
            "reports/latest",
            "评分报告 DOCX",
            "evidence trace",
            "evidence_trace",
            "scoring basis",
            "scoring_basis",
            "analysis bundle",
            "analysis_bundle",
            "compare_report",
            "复制优化清单",
            "导出优化清单 JSON",
            "Ollama 增强预览",
            "不改变运行逻辑",
            "不接核心评分主链",
            "scorer.py",
            "v2_scorer.py",
            "storage.py",
            "单独授权",
            "ollama serve",
        ],
    )


def test_latest_report_json_actions_are_visible_on_home_page():
    main_text = _read_repo_file("app/main.py")

    _assert_keywords_visible(
        main_text,
        [
            "复制 latest report JSON",
            "导出 latest report JSON",
            "reports/latest",
            "latest report",
            "navigator.clipboard",
            "Blob",
            "createObjectURL",
            "download",
            "不重新评分",
            "不触发 rescore",
            "不写 data",
            "不接 Ollama",
            "不接核心评分主链",
        ],
    )
