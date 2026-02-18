"""Streamlit Web UI for 施工组织设计评分系统."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

try:
    import pymupdf
except Exception:
    pymupdf = None
import streamlit as st

try:
    from docx import Document
except Exception:
    Document = None

from app.config import load_config
from app.engine.docx_exporter import export_report_to_docx
from app.engine.scorer import score_text


def read_uploaded_file(uploaded_file) -> str:
    """读取上传的文件内容。"""
    file_name = uploaded_file.name.lower()

    if file_name.endswith(".txt"):
        return uploaded_file.read().decode("utf-8")

    elif file_name.endswith(".docx"):
        if Document is None:
            raise ValueError("DOCX 解析不可用：请安装与当前系统架构兼容的 python-docx/lxml。")
        doc = Document(io.BytesIO(uploaded_file.read()))
        paragraphs = [p.text for p in doc.paragraphs]
        return "\n".join(paragraphs)

    elif file_name.endswith(".pdf"):
        if pymupdf is None:
            raise ValueError("PDF 解析不可用：请安装与当前系统架构兼容的 PyMuPDF。")
        pdf_bytes = uploaded_file.read()
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)

    else:
        raise ValueError("不支持的文件格式，仅支持 .txt、.docx 和 .pdf")


def main():
    """主入口函数。"""
    st.set_page_config(
        page_title="施工组织设计评分系统",
        page_icon="📋",
        layout="wide",
    )

    st.title("📋 施工组织设计评分系统")
    st.markdown("上传施工组织设计文档，自动进行评分分析。")

    # 侧边栏
    with st.sidebar:
        st.header("使用说明")
        st.markdown(
            """
        1. 上传施工组织设计文档
        2. 支持格式：`.txt`、`.docx`、`.pdf`
        3. 点击「开始评分」按钮
        4. 查看评分结果和改进建议
        5. 下载评分报告
        """
        )
        st.divider()
        st.markdown("**版本**: v1.0")
        st.markdown("**测试数量**: 420")
        st.markdown("**覆盖率**: 99%")

    # 文件上传
    uploaded_file = st.file_uploader(
        "上传施工组织设计文档",
        type=["txt", "docx", "pdf"],
        help="支持 .txt、.docx、.pdf 格式",
    )

    if uploaded_file is not None:
        st.success(f"已上传文件：{uploaded_file.name}")

        # 评分按钮
        if st.button("🚀 开始评分", type="primary", use_container_width=True):
            with st.spinner("正在评分分析中..."):
                try:
                    # 读取文件内容
                    text = read_uploaded_file(uploaded_file)

                    if len(text.strip()) < 10:
                        st.error("文件内容过短，请上传有效的施工组织设计文档。")
                        return

                    # 加载配置并评分
                    config = load_config()
                    report = score_text(text, config.rubric, config.lexicon)
                    report_dict = report.model_dump()

                    # 存储到 session state
                    st.session_state["report"] = report_dict
                    st.session_state["file_name"] = uploaded_file.name

                except ValueError as e:
                    st.error(f"文件读取错误：{e}")
                    return
                except Exception as e:
                    st.error(f"评分过程出错：{e}")
                    return

    # 显示评分结果
    if "report" in st.session_state:
        report_dict = st.session_state["report"]
        file_name = st.session_state.get("file_name", "document")

        st.divider()

        # 总分展示
        col1, col2, col3 = st.columns(3)
        with col1:
            total_score = report_dict.get("total_score", 0)
            st.metric("总分", f"{total_score:.2f}", delta=None)
        with col2:
            dim_count = len(report_dict.get("dimension_scores", {}))
            st.metric("评分维度", f"{dim_count} 个")
        with col3:
            penalty_count = len(report_dict.get("penalties", []))
            st.metric("扣分项", f"{penalty_count} 个")

        st.divider()

        # 维度分数详情
        st.subheader("📊 各维度评分")

        dimension_scores = report_dict.get("dimension_scores", {})
        if dimension_scores:
            # 创建表格数据
            table_data = []
            for dim_id, dim_data in sorted(dimension_scores.items()):
                table_data.append(
                    {
                        "维度ID": dim_id,
                        "维度名称": dim_data.get("name", ""),
                        "模块": dim_data.get("module", ""),
                        "得分": f"{dim_data.get('score', 0):.2f}",
                        "满分": f"{dim_data.get('max_score', 10):.1f}",
                        "命中数": len(dim_data.get("hits", [])),
                    }
                )
            st.dataframe(table_data, use_container_width=True)

        # 扣分项
        penalties = report_dict.get("penalties", [])
        if penalties:
            st.subheader("⚠️ 扣分项")
            for p in penalties:
                st.warning(
                    f"**{p.get('code', '')}**: {p.get('message', '')} (扣 {p.get('deduct', 0):.1f} 分)"
                )

        # 改进建议
        suggestions = report_dict.get("suggestions", [])
        if suggestions:
            st.subheader("💡 改进建议")
            for s in suggestions[:10]:  # 只显示前10条
                st.info(
                    f"**维度 {s.get('dimension', '')}**: {s.get('action', '')} (预期提升 {s.get('expected_gain', 0):.1f} 分)"
                )

        st.divider()

        # 下载区域
        st.subheader("📥 下载报告")
        col_dl1, col_dl2 = st.columns(2)

        with col_dl1:
            # JSON 下载
            json_str = json.dumps(report_dict, ensure_ascii=False, indent=2)
            st.download_button(
                label="下载 JSON 报告",
                data=json_str,
                file_name=f"{Path(file_name).stem}_report.json",
                mime="application/json",
            )

        with col_dl2:
            # DOCX 下载
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                export_report_to_docx(report_dict, tmp.name)
                with open(tmp.name, "rb") as f:
                    docx_bytes = f.read()
                st.download_button(
                    label="下载 DOCX 报告",
                    data=docx_bytes,
                    file_name=f"{Path(file_name).stem}_report.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

        # 原始 JSON 展开
        with st.expander("查看完整 JSON 报告"):
            st.json(report_dict)


if __name__ == "__main__":
    main()
