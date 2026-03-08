from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

try:
    import pymupdf
except Exception:
    pymupdf = None
import typer

try:
    from docx import Document
except Exception:
    Document = None
from tqdm import tqdm

from app.cache import warmup_cache_from_file, warmup_cache_parallel
from app.config import load_config
from app.engine.docx_exporter import export_report_to_docx
from app.engine.llm_judge_spark import (
    run_spark_judge,
)
from app.engine.report_formatter import format_qingtian_word_report
from app.engine.scorer import score_text
from app.i18n import SUPPORTED_LOCALES, set_locale, t


def read_input_file(file_path: Path) -> str:
    """读取输入文件内容，支持 .txt、.docx 和 .pdf 格式。

    Args:
        file_path: 文件路径

    Returns:
        文件文本内容

    Raises:
        ValueError: 不支持的文件格式
    """
    suffix = file_path.suffix.lower()
    if suffix == ".txt":
        return file_path.read_text(encoding="utf-8")
    elif suffix == ".docx":
        if Document is None:
            raise ValueError("DOCX 解析不可用：请安装与当前系统架构兼容的 python-docx/lxml。")
        doc = Document(str(file_path))
        paragraphs = [p.text for p in doc.paragraphs]
        return "\n".join(paragraphs)
    elif suffix == ".pdf":
        if pymupdf is None:
            raise ValueError("PDF 解析不可用：请安装与当前系统架构兼容的 PyMuPDF。")
        doc = pymupdf.open(str(file_path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)
    else:
        raise ValueError(f"不支持的文件格式：{suffix}，仅支持 .txt、.docx 和 .pdf")


app = typer.Typer(help="施工组织设计评标评分 CLI")
score_app = typer.Typer(help="对施组文本进行评分。", invoke_without_command=True)
app.add_typer(score_app, name="score")
batch_app = typer.Typer(help="批量处理多个施组文本。")
app.add_typer(batch_app, name="batch")
warmup_app = typer.Typer(help="预热评分缓存（从文件加载文本并写入缓存）。")
app.add_typer(warmup_app, name="warmup")


@score_app.callback()
def score_command(
    input: str = typer.Option(..., "--input", "-i", help="施组文本路径（支持 .txt 和 .docx）"),
    project_type: Optional[str] = None,
    out: Optional[str] = typer.Option(None, "--out", "-o", help="输出 JSON 文件"),
    mode: str = typer.Option("rules", "--mode", help="评分模式：rules/openai/hybrid"),
    prompt: str = typer.Option("openai_judge_qingtian_v1", "--prompt", help="LLM Prompt 名称"),
    summary: bool = typer.Option(False, "--summary", help="输出评分报告正文"),
    summary_out: Optional[str] = typer.Option(None, "--summary-out", help="输出评分报告到文件"),
    docx_out: Optional[str] = typer.Option(None, "--docx-out", help="输出 DOCX 报告文件路径"),
    locale: Optional[str] = typer.Option(
        None, "--locale", "-l", help="输出语言：zh（中文）/ en（英文），默认 zh"
    ),
) -> None:
    """对施组文本进行评分。"""
    # 验证并设置语言
    effective_locale = locale or "zh"
    if effective_locale not in SUPPORTED_LOCALES:
        raise typer.BadParameter(
            f"不支持的语言：{effective_locale}，仅支持：{', '.join(SUPPORTED_LOCALES)}"
        )
    set_locale(effective_locale)

    normalized_mode = (mode or "").strip().lower()
    if normalized_mode == "spark":
        normalized_mode = "openai"

    path = Path(input)
    text = read_input_file(path)
    config = load_config()
    rules_report = score_text(text, config.rubric, config.lexicon)
    if normalized_mode == "rules":
        rules_report.judge_mode = "rules"
        rules_report.judge_source = "rules_engine"
        rules_report.spark_called = False
        output = json.dumps(rules_report.model_dump(), ensure_ascii=False, indent=2)
    elif normalized_mode == "openai":
        llm_payload = run_spark_judge(text, config.rubric, prompt, rules_report)
        if llm_payload.get("called_spark_api"):
            llm_payload["judge_mode"] = "openai"
            llm_payload["judge_source"] = "openai_api"
            output = json.dumps(llm_payload, ensure_ascii=False, indent=2)
        else:
            reason = llm_payload.get("reason", "unknown")
            rules_report.judge_mode = "fallback_rules"
            rules_report.judge_source = "rules_engine"
            rules_report.spark_called = False
            rules_report.fallback_reason = f"{reason}; prompt={prompt}"
            output = json.dumps(rules_report.model_dump(), ensure_ascii=False, indent=2)
    elif normalized_mode == "hybrid":
        llm_payload = run_spark_judge(text, config.rubric, prompt, rules_report)
        if llm_payload.get("called_spark_api"):
            base_score = rules_report.total_score
            llm_score = llm_payload.get("overall", {}).get("total_score_0_100", base_score)
            max_adjustment = float(
                config.rubric.get("llm_merge_policy", {}).get("max_adjustment", 10.0)
            )
            adjustment = max(-max_adjustment, min(max_adjustment, llm_score - base_score))
            output = json.dumps(
                {
                    "judge_mode": "hybrid",
                    "judge_source": "openai_api",
                    "spark_called": True,
                    "base_rules_score": base_score,
                    "llm_adjustment": adjustment,
                    "final_total_score": round(base_score + adjustment, 2),
                    "prompt_version": llm_payload.get("prompt_version"),
                },
                ensure_ascii=False,
                indent=2,
            )
        else:
            reason = llm_payload.get("reason", "unknown")
            output = json.dumps(
                {
                    "judge_mode": "hybrid_fallback_rules",
                    "judge_source": "rules_engine",
                    "spark_called": False,
                    "fallback_reason": f"{reason}; prompt={prompt}",
                    "base_rules_score": rules_report.total_score,
                    "llm_adjustment": 0.0,
                    "final_total_score": rules_report.total_score,
                },
                ensure_ascii=False,
                indent=2,
            )
    else:
        raise typer.BadParameter("mode 仅支持 rules/openai/hybrid（spark 作为兼容别名仍可用）")
    if out:
        Path(out).write_text(output, encoding="utf-8")
    print(output)
    if summary:
        report_json = json.loads(output)
        report_text = format_qingtian_word_report(report_json, locale=effective_locale)
        if summary_out:
            Path(summary_out).write_text(report_text, encoding="utf-8")
            msg_generated = t("cli.report_generated", locale=effective_locale)
            print(f"{msg_generated}{summary_out}")
            preview_lines = report_text.splitlines()[:50]
            print("\n".join(preview_lines))
        else:
            print("\n" + report_text)

    if docx_out:
        report_json = json.loads(output)
        docx_path = export_report_to_docx(report_json, docx_out)
        msg_docx = t("cli.docx_generated", locale=effective_locale)
        print(f"{msg_docx}{docx_path}")


def _process_single_file(
    input_path: Path,
    output_dir: Path,
    mode: str,
    prompt: str,
    docx: bool,
) -> dict:
    """处理单个文件，返回结果摘要。支持 .txt、.docx 和 .pdf 输入格式。"""
    config = load_config()
    text = read_input_file(input_path)
    rules_report = score_text(text, config.rubric, config.lexicon)

    normalized_mode = (mode or "").strip().lower()
    if normalized_mode == "spark":
        normalized_mode = "openai"

    if normalized_mode == "rules":
        rules_report.judge_mode = "rules"
        rules_report.judge_source = "rules_engine"
        rules_report.spark_called = False
        report_data = rules_report.model_dump()
    elif normalized_mode == "openai":
        llm_payload = run_spark_judge(text, config.rubric, prompt, rules_report)
        if llm_payload.get("called_spark_api"):
            llm_payload["judge_mode"] = "openai"
            llm_payload["judge_source"] = "openai_api"
            report_data = llm_payload
        else:
            reason = llm_payload.get("reason", "unknown")
            rules_report.judge_mode = "fallback_rules"
            rules_report.judge_source = "rules_engine"
            rules_report.spark_called = False
            rules_report.fallback_reason = f"{reason}; prompt={prompt}"
            report_data = rules_report.model_dump()
    else:
        rules_report.judge_mode = "rules"
        rules_report.judge_source = "rules_engine"
        rules_report.spark_called = False
        report_data = rules_report.model_dump()

    # 输出 JSON
    json_out = output_dir / f"{input_path.stem}_report.json"
    json_output = json.dumps(report_data, ensure_ascii=False, indent=2)
    json_out.write_text(json_output, encoding="utf-8")

    # 可选输出 DOCX
    docx_path = None
    if docx:
        docx_out = output_dir / f"{input_path.stem}_report.docx"
        docx_path = str(export_report_to_docx(report_data, str(docx_out)))

    return {
        "input": str(input_path),
        "json_output": str(json_out),
        "docx_output": docx_path,
        "total_score": report_data.get("total_score", 0),
        "status": "success",
    }


@batch_app.callback(invoke_without_command=True)
def batch_command(
    inputs: List[str] = typer.Option(
        ..., "--input", "-i", help="输入文件路径（可多次指定）或目录路径，支持 .txt 和 .docx"
    ),
    output_dir: str = typer.Option("build/batch_output", "--out-dir", "-o", help="输出目录"),
    mode: str = typer.Option("rules", "--mode", help="评分模式：rules/openai"),
    prompt: str = typer.Option("openai_judge_qingtian_v1", "--prompt", help="LLM Prompt 名称"),
    docx: bool = typer.Option(False, "--docx", help="同时生成 DOCX 报告"),
    pattern: str = typer.Option(
        "*.txt", "--pattern", help="目录模式下的文件匹配模式（如 *.txt 或 *.docx）"
    ),
    workers: int = typer.Option(1, "--workers", "-w", help="并行工作线程数（默认1=串行）"),
    executor: str = typer.Option(
        "thread",
        "--executor",
        "-e",
        help="执行器类型：thread（I/O密集）或 process（CPU密集）",
    ),
    progress: bool = typer.Option(False, "--progress", "-p", help="显示进度条（适用于交互式终端）"),
    locale: Optional[str] = typer.Option(
        None, "--locale", "-l", help="输出语言：zh（中文）/ en（英文），默认 zh"
    ),
) -> None:
    """批量处理多个施组文本文件（支持 .txt、.docx 和 .pdf 输入格式）。

    示例：
        # 处理多个指定文件
        python3 -m app.cli batch -i file1.txt -i file2.txt -o build/batch

        # 处理目录下所有 txt 文件
        python3 -m app.cli batch -i ./inputs/ -o build/batch --pattern "*.txt"

        # 处理目录下所有 docx 文件
        python3 -m app.cli batch -i ./inputs/ -o build/batch --pattern "*.docx"

        # 同时生成 DOCX
        python3 -m app.cli batch -i ./inputs/ -o build/batch --docx

        # 使用 4 个并行线程加速处理（I/O 密集型）
        python3 -m app.cli batch -i ./inputs/ -o build/batch --workers 4

        # 使用 4 个并行进程加速处理（CPU 密集型）
        python3 -m app.cli batch -i ./inputs/ -o build/batch --workers 4 --executor process

        # 显示进度条（交互式终端）
        python3 -m app.cli batch -i ./inputs/ -o build/batch --progress
    """
    # 验证并设置语言
    effective_locale = locale or "zh"
    if effective_locale not in SUPPORTED_LOCALES:
        raise typer.BadParameter(
            f"不支持的语言：{effective_locale}，仅支持：{', '.join(SUPPORTED_LOCALES)}"
        )
    set_locale(effective_locale)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 收集所有输入文件
    files_to_process: List[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            files_to_process.extend(p.glob(pattern))
        elif p.is_file():
            files_to_process.append(p)
        else:
            print(f"{t('cli.warning_skip_path', locale=effective_locale)} {inp}")

    if not files_to_process:
        print(t("cli.error_no_files", locale=effective_locale))
        raise typer.Exit(code=1)

    total_files = len(files_to_process)
    effective_workers = min(workers, total_files)
    print(t("cli.files_found", locale=effective_locale, count=total_files))
    print(f"{t('cli.output_dir', locale=effective_locale)}{out_path.absolute()}")
    # 验证 executor 参数
    if executor not in ("thread", "process"):
        print(f"{t('cli.error_executor', locale=effective_locale)}{executor}")
        raise typer.Exit(code=1)

    if effective_workers > 1:
        if executor == "thread":
            print(t("cli.parallel_mode_thread", locale=effective_locale, workers=effective_workers))
        else:
            print(
                t("cli.parallel_mode_process", locale=effective_locale, workers=effective_workers)
            )
    print("-" * 50)

    results = []

    # 检测是否为交互式终端，自动启用进度条或使用显式参数
    use_progress = progress and sys.stdout.isatty()

    # 获取本地化标签
    desc_processing = t("cli.processing", locale=effective_locale)
    desc_parallel = t("cli.parallel_processing", locale=effective_locale)
    unit_file = t("cli.file_unit", locale=effective_locale)
    label_score = t("cli.score_label", locale=effective_locale)
    label_error = t("cli.error_label", locale=effective_locale)

    if effective_workers <= 1:
        # 串行处理（向后兼容）
        if use_progress:
            # 使用 tqdm 进度条模式
            pbar = tqdm(
                files_to_process,
                desc=desc_processing,
                unit=unit_file,
                ncols=80,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
            )
            for file_path in pbar:
                pbar.set_postfix_str(file_path.name[:20])
                try:
                    result = _process_single_file(file_path, out_path, mode, prompt, docx)
                    results.append(result)
                except Exception as e:
                    results.append(
                        {
                            "input": str(file_path),
                            "status": "error",
                            "error": str(e),
                        }
                    )
            pbar.close()
        else:
            # 传统文本输出模式
            for idx, file_path in enumerate(files_to_process, 1):
                print(
                    f"[{idx}/{total_files}] {t('cli.processing_file', locale=effective_locale)}{file_path.name}...",
                    end=" ",
                )
                try:
                    result = _process_single_file(file_path, out_path, mode, prompt, docx)
                    results.append(result)
                    print(f"✓ {label_score}{result['total_score']:.1f}")
                except Exception as e:
                    results.append(
                        {
                            "input": str(file_path),
                            "status": "error",
                            "error": str(e),
                        }
                    )
                    print(f"✗ {label_error}{e}")
    else:
        # 并行处理
        completed_count = 0

        def _run_parallel_with(executor_class):
            nonlocal completed_count
            with executor_class(max_workers=effective_workers) as pool_executor:
                future_to_file = {
                    pool_executor.submit(
                        _process_single_file, file_path, out_path, mode, prompt, docx
                    ): file_path
                    for file_path in files_to_process
                }

                if use_progress:
                    pbar = tqdm(
                        total=total_files,
                        desc=desc_parallel,
                        unit=unit_file,
                        ncols=80,
                        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
                    )
                    for future in as_completed(future_to_file):
                        file_path = future_to_file[future]
                        try:
                            result = future.result()
                            results.append(result)
                            pbar.set_postfix_str(f"✓ {file_path.name[:15]}")
                        except Exception as e:
                            results.append(
                                {
                                    "input": str(file_path),
                                    "status": "error",
                                    "error": str(e),
                                }
                            )
                            pbar.set_postfix_str(f"✗ {file_path.name[:15]}")
                        pbar.update(1)
                    pbar.close()
                else:
                    for future in as_completed(future_to_file):
                        file_path = future_to_file[future]
                        completed_count += 1
                        try:
                            result = future.result()
                            results.append(result)
                            print(
                                f"[{completed_count}/{total_files}] ✓ {file_path.name} "
                                f"{label_score}{result['total_score']:.1f}"
                            )
                        except Exception as e:
                            results.append(
                                {
                                    "input": str(file_path),
                                    "status": "error",
                                    "error": str(e),
                                }
                            )
                            print(
                                f"[{completed_count}/{total_files}] ✗ {file_path.name} {label_error}{e}"
                            )

        executor_class = ProcessPoolExecutor if executor == "process" else ThreadPoolExecutor
        try:
            _run_parallel_with(executor_class)
        except PermissionError:
            if executor == "process":
                print("检测到当前环境不允许进程池，已自动回退为线程池。")
                completed_count = 0
                _run_parallel_with(ThreadPoolExecutor)
            else:
                raise

    # 输出汇总
    print("-" * 50)
    success_count = sum(1 for r in results if r.get("status") == "success")
    print(t("cli.completed", locale=effective_locale, success=success_count, total=len(results)))

    # 保存汇总报告
    summary_path = out_path / "_batch_summary.json"
    summary = {
        "total_files": len(results),
        "success_count": success_count,
        "error_count": len(results) - success_count,
        "output_dir": str(out_path.absolute()),
        "results": results,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{t('cli.summary_report', locale=effective_locale)}{summary_path}")


def _load_warmup_items(path: Path) -> list:
    """从文件加载预热条目列表。支持 .txt（每行一条）和 .json（数组）。"""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON 文件必须包含数组")
        return data
    if path.suffix.lower() == ".txt":
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        return [line.strip() for line in lines if line.strip()]
    raise ValueError(f"不支持的文件格式：{path.suffix}，仅支持 .txt 和 .json")


@warmup_app.callback(invoke_without_command=True)
def warmup_command(
    input_path: str = typer.Option(
        ..., "--input", "-i", help="输入文件路径（.txt 每行一条，.json 数组）"
    ),
    workers: int = typer.Option(1, "--workers", "-w", help="并行工作数（1=串行）"),
    no_skip_existing: bool = typer.Option(
        False, "--no-skip-existing", help="不跳过已存在的缓存项（默认跳过）"
    ),
    ttl: Optional[float] = typer.Option(None, "--ttl", help="缓存 TTL（秒），默认使用配置"),
    locale: Optional[str] = typer.Option(None, "--locale", "-l", help="输出语言：zh / en，默认 zh"),
) -> None:
    """从文件预热评分缓存，便于后续评分命中缓存。

    示例：
        python3 -m app.cli warmup -i sample_shigong.txt
        python3 -m app.cli warmup -i filelist.txt -w 4
    """
    effective_locale = locale or "zh"
    if effective_locale not in SUPPORTED_LOCALES:
        raise typer.BadParameter(
            f"不支持的语言：{effective_locale}，仅支持：{', '.join(SUPPORTED_LOCALES)}"
        )
    set_locale(effective_locale)

    path = Path(input_path)
    if not path.exists():
        print(f"错误：文件不存在 {path}")
        raise typer.Exit(code=1)

    config = load_config()

    def score_fn(text: str):
        report = score_text(text, config.rubric, config.lexicon)
        return report.model_dump()

    skip_existing = not no_skip_existing

    if workers <= 1:
        result = warmup_cache_from_file(
            str(path),
            score_fn=score_fn,
            skip_existing=skip_existing,
            ttl=ttl,
        )
    else:
        try:
            items = _load_warmup_items(path)
        except ValueError as e:
            print(f"错误：{e}")
            raise typer.Exit(code=1)
        if not items:
            print("错误：文件中没有有效条目")
            raise typer.Exit(code=1)
        result = warmup_cache_parallel(
            items,
            score_fn=score_fn,
            skip_existing=skip_existing,
            ttl=ttl,
            max_workers=min(workers, len(items)),
        )

    print(
        f"预热完成：共 {result.total_items} 条，"
        f"写入 {result.warmed}，跳过 {result.skipped}，失败 {result.failed}，"
        f"耗时 {result.duration_ms:.0f} ms"
    )
    if result.errors:
        for err in result.errors[:5]:
            print(f"  - {err}")


if __name__ == "__main__":
    app()
