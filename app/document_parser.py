from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import pymupdf
except Exception:
    pymupdf = None
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None
try:
    from docx import Document
except Exception:
    Document = None
try:
    from PIL import Image
except Exception:
    Image = None
try:
    import pytesseract
except Exception:
    pytesseract = None


DEFAULT_PDF_TEXT_MIN_CHARS_FOR_OCR = 200
DEFAULT_PDF_OCR_MAX_PAGES = 30
_DEFAULT_BACKEND = object()


def _normalize_uploaded_filename(filename: str) -> str:
    raw = unicodedata.normalize("NFKC", str(filename or "")).replace("\u3000", " ").strip()
    base = Path(raw).name.strip()
    while base.endswith("."):
        base = base[:-1].rstrip()
    return base


def _decode_dxf_text(content: bytes) -> str:
    if b"\x00" in content[:4096]:
        raise ValueError("DXF 解析失败：检测到二进制 DXF，请先另存为 ASCII DXF。")
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _iter_dxf_group_pairs(text: str) -> List[tuple[int, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    pairs: List[tuple[int, str]] = []
    idx = 0
    while idx + 1 < len(lines):
        code_raw = lines[idx].strip()
        value = lines[idx + 1].strip()
        idx += 2
        if not code_raw:
            continue
        try:
            code = int(code_raw)
        except ValueError:
            continue
        pairs.append((code, value))
    return pairs


def _extract_dxf_text(content: bytes) -> str:
    raw_text = _decode_dxf_text(content)
    pairs = _iter_dxf_group_pairs(raw_text)
    if not pairs:
        return "[DXF解析摘要]\n未读取到有效 DXF 组码。"

    acadver = ""
    codepage = ""
    insunits: Optional[int] = None
    unit_map = {
        0: "未指定",
        1: "英寸",
        2: "英尺",
        4: "毫米",
        5: "厘米",
        6: "米",
        20: "秒",
        21: "分",
        22: "时",
    }
    for i, (code, value) in enumerate(pairs[:-1]):
        if code != 9:
            continue
        key = value.upper()
        next_code, next_value = pairs[i + 1]
        if key == "$ACADVER" and next_code in (1, 3):
            acadver = next_value.strip()
        elif key == "$DWGCODEPAGE" and next_code in (1, 3):
            codepage = next_value.strip()
        elif key == "$INSUNITS" and next_code in (70, 280):
            try:
                insunits = int(float(next_value.strip()))
            except Exception:
                insunits = None

    text_entity_types = {"TEXT", "MTEXT", "ATTDEF", "ATTRIB"}
    entity_counts: Dict[str, int] = {}
    extracted_texts: List[str] = []
    layers: set[str] = set()
    blocks: set[str] = set()

    in_entities = False
    waiting_section_name = False
    current_entity_type = ""
    current_entity_texts: List[str] = []
    current_layer = ""
    current_block = ""

    def _flush_entity() -> None:
        nonlocal current_entity_type, current_entity_texts, current_layer, current_block
        if not current_entity_type:
            return
        entity_counts[current_entity_type] = entity_counts.get(current_entity_type, 0) + 1
        if current_layer:
            layers.add(current_layer)
        if current_block:
            blocks.add(current_block)
        for item in current_entity_texts:
            if not item:
                continue
            normalized = (
                item.replace("\\P", "\n")
                .replace("\\~", " ")
                .replace("{", "")
                .replace("}", "")
                .strip()
            )
            if normalized and normalized not in extracted_texts:
                extracted_texts.append(normalized)
        current_entity_type = ""
        current_entity_texts = []
        current_layer = ""
        current_block = ""

    for code, value in pairs:
        token = value.upper().strip()
        if waiting_section_name and code == 2:
            in_entities = token == "ENTITIES"
            waiting_section_name = False
            continue

        if code == 0:
            if token == "SECTION":
                _flush_entity()
                waiting_section_name = True
                continue
            if token in {"ENDSEC", "EOF"}:
                _flush_entity()
                in_entities = False
                continue
            if in_entities:
                _flush_entity()
                current_entity_type = token
                continue

        if not in_entities or not current_entity_type:
            continue
        if code == 8:
            current_layer = value.strip()
            continue
        if code == 2 and current_entity_type == "INSERT":
            current_block = value.strip()
            continue
        if code in (1, 3) and current_entity_type in text_entity_types:
            current_entity_texts.append(value)

    _flush_entity()

    summary_lines = ["[DXF解析摘要]"]
    if acadver:
        summary_lines.append(f"ACAD版本: {acadver}")
    if codepage:
        summary_lines.append(f"编码页: {codepage}")
    if insunits is not None:
        summary_lines.append(f"插入单位: {insunits}({unit_map.get(insunits, '未知')})")
    if entity_counts:
        top_entities = sorted(entity_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        summary_lines.append(
            "实体统计: " + "、".join(f"{etype}:{count}" for etype, count in top_entities)
        )
    if layers:
        summary_lines.append("图层: " + "、".join(sorted(layers)[:20]))
    if blocks:
        summary_lines.append("块参照: " + "、".join(sorted(blocks)[:20]))

    if extracted_texts:
        summary_lines.append("")
        summary_lines.append("[DXF文本实体提取]")
        summary_lines.extend(extracted_texts[:160])
    return "\n".join(summary_lines).strip()


def _looks_like_ascii_dxf(content: bytes) -> bool:
    sample = content[:4096]
    if not sample or b"\x00" in sample:
        return False
    text = sample.decode("latin-1", errors="ignore").replace("\r", "\n").upper()
    return "SECTION" in text and ("ENTITIES" in text or "HEADER" in text) and "\n0\n" in text


def _extract_dwg_binary_markers(content: bytes, *, max_tokens: int = 30) -> Dict[str, object]:
    sample = content[: min(len(content), 1_500_000)]
    versions = sorted(
        {
            item.decode("ascii", errors="ignore")
            for item in re.findall(rb"AC10\d{2}", sample)
            if item
        }
    )
    raw_tokens = re.findall(rb"[A-Za-z_][A-Za-z0-9_./:-]{2,48}", sample)
    blocklist_prefix = ("http", "https", "xmlns", "schema", "version", "content")
    token_counter: Counter[str] = Counter()
    for token_bytes in raw_tokens:
        token = token_bytes.decode("latin-1", errors="ignore").strip()
        lower = token.lower()
        if len(token) < 3:
            continue
        if any(lower.startswith(prefix) for prefix in blocklist_prefix):
            continue
        if lower in {"acdb", "objectdbx", "autocad", "dwg"}:
            continue
        if re.fullmatch(r"[0-9a-f]{8,}", lower):
            continue
        token_counter[token] += 1
    top_tokens = [tok for tok, _ in token_counter.most_common(max(1, int(max_tokens)))]
    return {"versions": versions, "tokens": top_tokens}


def _dwg_converter_command_candidates(
    binary: str,
    *,
    in_path: Path,
    input_dir: Path,
    output_dir: Path,
) -> List[List[str]]:
    name = Path(binary).name.lower()
    out_file = output_dir / f"{in_path.stem}.dxf"
    candidates: List[List[str]] = []
    if "dwg2dxf" in name:
        candidates.append([binary, str(in_path), str(out_file)])
    elif any(mark in name for mark in ("odafileconverter", "oda_file_converter", "teigha")):
        # ODA/Teigha 不同版本参数略有差异，按候选命令依次尝试。
        candidates.append([binary, str(input_dir), str(output_dir), "ACAD2018", "DXF", "0", "1"])
        candidates.append(
            [binary, str(input_dir), str(output_dir), "ACAD2018", "DXF", "0", "1", "*.DWG"]
        )
        candidates.append([binary, str(in_path), str(out_file)])
    else:
        candidates.append([binary, str(in_path), str(out_file)])
        candidates.append([binary, str(input_dir), str(output_dir)])
    return candidates


def _resolve_dwg_converter_binaries() -> List[str]:
    converter_names: List[str] = []
    converter_paths: List[str] = []
    env_converters_raw = str(os.getenv("DWG_CONVERTER_BIN") or "").strip()
    if env_converters_raw:
        for item in re.split(r"[;,]", env_converters_raw):
            s = item.strip()
            if not s:
                continue
            p = Path(s)
            if p.exists() and p.is_file():
                converter_paths.append(str(p))
                continue
            if p.exists() and p.is_dir():
                for bin_name in (
                    "dwg2dxf",
                    "ODAFileConverter",
                    "oda_file_converter",
                    "TeighaFileConverter",
                ):
                    candidate = p / bin_name
                    if candidate.exists() and candidate.is_file():
                        converter_paths.append(str(candidate))
                continue
            if s not in converter_names:
                converter_names.append(s)
    converter_names.extend(
        [
            "dwg2dxf",
            "ODAFileConverter",
            "oda_file_converter",
            "TeighaFileConverter",
            "dwgread",
        ]
    )
    converter_names = list(dict.fromkeys(converter_names))
    binaries: List[str] = [p for p in converter_paths if p]
    common_paths = [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/Teigha File Converter.app/Contents/MacOS/TeighaFileConverter",
        "/opt/homebrew/bin/dwg2dxf",
        "/usr/local/bin/dwg2dxf",
        "/opt/homebrew/bin/dwgread",
        "/usr/local/bin/dwgread",
    ]
    for raw_path in common_paths:
        p = Path(raw_path)
        if p.exists() and p.is_file():
            binaries.append(str(p))
    for name in converter_names:
        if not name:
            continue
        resolved = name if Path(name).exists() else shutil.which(name)
        if resolved:
            binaries.append(str(resolved))
    return list(dict.fromkeys(binaries))


def _extract_dwg_text(
    content: bytes,
    filename: str,
    *,
    resolve_dwg_converter_binaries: Optional[Callable[[], List[str]]] = None,
) -> str:
    """
    DWG 预处理链：
    1) 优先尝试系统级转换器将 DWG 转 DXF（若已安装）
    2) 转换成功后复用 DXF 解析
    3) 无转换器或转换失败时保留元信息并给出明确提示
    """
    if _looks_like_ascii_dxf(content):
        try:
            dxf_text = _extract_dxf_text(content)
            return f"[DWG预处理] 文件: {filename}\n检测到ASCII DXF内容，按DXF解析。\n\n{dxf_text}"
        except Exception:
            pass
    # 对明显异常的小体量 DWG 直接走标识兜底，避免调用外部转换器造成长时间阻塞。
    if len(content) < 256:
        markers = _extract_dwg_binary_markers(content, max_tokens=26)
        versions = [str(x) for x in (markers.get("versions") or []) if str(x).strip()]
        token_preview = [str(x) for x in (markers.get("tokens") or []) if str(x).strip()]
        marker_text = "、".join(versions[:4]) if versions else "未识别"
        tokens_text = "、".join(token_preview[:16]) if token_preview else "未提取到有效标识"
        return (
            f"[DWG图纸] 文件: {filename}，字节数: {len(content)}\n"
            "DWG预处理: 文件体积过小，已跳过外部转换器尝试\n"
            f"版本标记: {marker_text}\n"
            f"二进制标识提取: {tokens_text}\n"
            "当前未完成稳定结构化解析，建议同时上传 PDF 或 ASCII DXF 以提升评分准确性。"
        )

    resolver = resolve_dwg_converter_binaries or _resolve_dwg_converter_binaries
    binaries = resolver()

    converter_display = ["dwg2dxf", "ODAFileConverter", "oda_file_converter", "TeighaFileConverter"]
    notes: List[str] = []
    with tempfile.TemporaryDirectory(prefix="dwg_bridge_") as tmpdir:
        tmp_root = Path(tmpdir)
        input_dir = tmp_root / "in"
        output_dir = tmp_root / "out"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        in_path = input_dir / _normalize_uploaded_filename(filename)
        in_path.write_bytes(content)

        for name in converter_display:
            if not any(Path(b).name.lower() == name.lower() for b in binaries):
                notes.append(f"{name}: not_found")
        for binary in binaries:
            cmd_candidates = _dwg_converter_command_candidates(
                binary,
                in_path=in_path,
                input_dir=input_dir,
                output_dir=output_dir,
            )
            for cmd in cmd_candidates:
                cmd_signature = " ".join(cmd[:3])
                try:
                    completed = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=45,
                        check=False,
                        text=True,
                    )
                    if completed.returncode != 0:
                        err = (completed.stderr or completed.stdout or "").strip().splitlines()
                        notes.append(
                            f"{Path(binary).name}: rc={completed.returncode} {err[0] if err else ''}"
                        )
                        continue
                    dxf_candidates = sorted(
                        {
                            *output_dir.rglob("*.dxf"),
                            *tmp_root.rglob(f"{in_path.stem}.dxf"),
                        }
                    )
                    if not dxf_candidates:
                        notes.append(f"{Path(binary).name}: no_dxf_output ({cmd_signature})")
                        continue
                    for dxf_candidate in dxf_candidates:
                        try:
                            dxf_text = _extract_dxf_text(dxf_candidate.read_bytes())
                        except Exception as exc:  # noqa: BLE001 - converter output might be malformed
                            notes.append(
                                f"{Path(binary).name}: dxf_parse_failed {type(exc).__name__}: {exc}"
                            )
                            continue
                        head = [
                            f"[DWG预处理] 文件: {filename}",
                            f"转换器: {Path(binary).name}",
                            f"命令: {cmd_signature}",
                            f"输出DXF: {dxf_candidate.name}",
                        ]
                        return "\n".join(head + ["", dxf_text]).strip()
                except subprocess.TimeoutExpired:
                    notes.append(f"{Path(binary).name}: timeout ({cmd_signature})")
                except Exception as exc:  # noqa: BLE001 - continue next converter
                    notes.append(f"{Path(binary).name}: exception {type(exc).__name__}: {exc}")

    markers = _extract_dwg_binary_markers(content, max_tokens=26)
    versions = [str(x) for x in (markers.get("versions") or []) if str(x).strip()]
    token_preview = [str(x) for x in (markers.get("tokens") or []) if str(x).strip()]
    notes_text = "；".join(notes[:6]) if notes else "未检测到可用转换器"
    marker_text = "、".join(versions[:4]) if versions else "未识别"
    tokens_text = "、".join(token_preview[:16]) if token_preview else "未提取到有效标识"
    return (
        f"[DWG图纸] 文件: {filename}，字节数: {len(content)}\n"
        f"DWG预处理: {notes_text}\n"
        f"版本标记: {marker_text}\n"
        f"二进制标识提取: {tokens_text}\n"
        "当前未完成稳定结构化解析，建议同时上传 PDF 或 ASCII DXF 以提升评分准确性。"
    )


def _extract_binary_text_snippet(content: bytes, *, max_chars: int = 4000) -> str:
    decoded = content.decode("utf-8", errors="ignore")
    cleaned = "".join(ch if ch.isprintable() else " " for ch in decoded)
    compact = " ".join(cleaned.split())
    if not compact:
        return ""
    return compact[: max(256, int(max_chars))]


def _extract_image_content(
    content: bytes,
    filename: str,
    *,
    image_backend: object = _DEFAULT_BACKEND,
    ocr_backend: object = _DEFAULT_BACKEND,
) -> str:
    active_image = Image if image_backend is _DEFAULT_BACKEND else image_backend
    active_ocr = pytesseract if ocr_backend is _DEFAULT_BACKEND else ocr_backend
    lines = [f"[图像资料] 文件: {filename}", f"字节数: {len(content)}"]
    if active_image is None:
        lines.append("图像解析: 当前环境未安装 Pillow，已纳入文件元信息。")
        return "\n".join(lines)
    try:
        with active_image.open(io.BytesIO(content)) as img:
            lines.append(f"格式: {img.format or '未知'}")
            lines.append(f"尺寸: {img.width}x{img.height}")
            lines.append(f"模式: {img.mode}")
            if active_ocr is not None:
                try:
                    ocr_text = str(
                        active_ocr.image_to_string(img, lang="chi_sim+eng") or ""
                    ).strip()
                except Exception:
                    ocr_text = ""
                if ocr_text:
                    lines.append("[OCR文本提取]")
                    lines.append(ocr_text[:4000])
                else:
                    lines.append("OCR文本提取: 未识别到有效文本。")
            else:
                lines.append("OCR文本提取: 当前环境未安装 pytesseract，已纳入图像元信息。")
    except Exception as exc:
        lines.append(f"图像解析失败: {exc}")
    return "\n".join(lines)


def _pdf_backend_name(
    *,
    pymupdf_backend: object = _DEFAULT_BACKEND,
    pdf_reader_backend: object = _DEFAULT_BACKEND,
) -> str:
    active_pymupdf = pymupdf if pymupdf_backend is _DEFAULT_BACKEND else pymupdf_backend
    active_pdf_reader = PdfReader if pdf_reader_backend is _DEFAULT_BACKEND else pdf_reader_backend
    if active_pymupdf is not None:
        return "pymupdf"
    if active_pdf_reader is not None:
        return "pypdf"
    return "none"


def _extract_pdf_text_with_pypdf(
    content: bytes,
    *,
    pdf_reader_backend: object = _DEFAULT_BACKEND,
) -> str:
    active_pdf_reader = PdfReader if pdf_reader_backend is _DEFAULT_BACKEND else pdf_reader_backend
    if active_pdf_reader is None:
        return ""
    if not bytes(content or b"").lstrip().startswith(b"%PDF"):
        return ""
    try:
        reader = active_pdf_reader(io.BytesIO(content))
    except Exception:
        return ""
    parts: List[str] = []
    for idx, page in enumerate(getattr(reader, "pages", []) or [], start=1):
        try:
            page_text = str(page.extract_text() or "")
        except Exception:
            page_text = ""
        page_text = page_text.strip()
        if page_text:
            parts.append(f"[PAGE:{idx}]\n{page_text}")
    return "\n\n".join(parts).strip()


def _extract_pdf_text(
    content: bytes,
    filename: str,
    *,
    pymupdf_backend: object = _DEFAULT_BACKEND,
    pdf_reader_backend: object = _DEFAULT_BACKEND,
    image_backend: object = _DEFAULT_BACKEND,
    ocr_backend: object = _DEFAULT_BACKEND,
) -> str:
    active_pymupdf = pymupdf if pymupdf_backend is _DEFAULT_BACKEND else pymupdf_backend
    active_pdf_reader = PdfReader if pdf_reader_backend is _DEFAULT_BACKEND else pdf_reader_backend
    active_image = Image if image_backend is _DEFAULT_BACKEND else image_backend
    active_ocr = pytesseract if ocr_backend is _DEFAULT_BACKEND else ocr_backend
    if active_pymupdf is not None:
        doc = active_pymupdf.open(stream=content, filetype="pdf")
        try:
            parts: List[str] = []
            for idx, page in enumerate(doc, start=1):
                # Embed stable page markers so downstream diagnostics can map evidence to pages.
                page_text = page.get_text() or ""
                parts.append(f"[PAGE:{idx}]\n{page_text}")
            merged_pdf_text = "\n\n".join(parts).strip()
            text_chars = len(merged_pdf_text.replace("\n", "").strip())
            need_ocr = text_chars < DEFAULT_PDF_TEXT_MIN_CHARS_FOR_OCR
            if need_ocr and active_ocr is not None and active_image is not None:
                ocr_chunks: List[str] = []
                for idx, page in enumerate(doc, start=1):
                    if idx > DEFAULT_PDF_OCR_MAX_PAGES:
                        break
                    try:
                        pix = page.get_pixmap(matrix=active_pymupdf.Matrix(2.0, 2.0), alpha=False)
                        with active_image.open(io.BytesIO(pix.tobytes("png"))) as img:
                            ocr_text = str(
                                active_ocr.image_to_string(img, lang="chi_sim+eng") or ""
                            ).strip()
                    except Exception:
                        ocr_text = ""
                    if ocr_text:
                        ocr_chunks.append(f"[PAGE_OCR:{idx}]\n{ocr_text[:5000]}")
                if ocr_chunks:
                    merged_pdf_text = (
                        merged_pdf_text + "\n\n[PDF_OCR_FALLBACK]\n" + "\n\n".join(ocr_chunks)
                    ).strip()
            if merged_pdf_text:
                return f"[PDF_BACKEND:pymupdf]\n{merged_pdf_text}"
        finally:
            doc.close()

    pypdf_text = _extract_pdf_text_with_pypdf(
        content,
        pdf_reader_backend=active_pdf_reader,
    )
    if pypdf_text:
        return f"[PDF_BACKEND:pypdf]\n{pypdf_text}"

    if (
        _pdf_backend_name(
            pymupdf_backend=active_pymupdf,
            pdf_reader_backend=active_pdf_reader,
        )
        == "none"
    ):
        raise ValueError(
            "PDF 解析不可用：请安装与当前系统架构兼容的 PyMuPDF，或安装 pypdf 作为兼容解析后端。"
        )
    return f"[PDF资料] 文件: {filename}（未提取到有效文本）"


def _read_uploaded_file_content(
    content: bytes,
    filename: str,
    *,
    document_backend: object = _DEFAULT_BACKEND,
    pymupdf_backend: object = _DEFAULT_BACKEND,
    pdf_reader_backend: object = _DEFAULT_BACKEND,
    image_backend: object = _DEFAULT_BACKEND,
    ocr_backend: object = _DEFAULT_BACKEND,
    resolve_dwg_converter_binaries: Optional[Callable[[], List[str]]] = None,
) -> str:
    """根据文件名解析上传文件为文本，覆盖招标/清单/图纸/现场照片常见格式。"""
    active_document = Document if document_backend is _DEFAULT_BACKEND else document_backend
    name = filename.lower()
    if name.endswith(".txt") or name.endswith(".md") or name.endswith(".csv"):
        return content.decode("utf-8", errors="ignore")
    if name.endswith(".docx"):
        if active_document is None:
            raise ValueError("DOCX 解析不可用：请安装与当前系统架构兼容的 python-docx/lxml。")
        doc = active_document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)
    if name.endswith(".doc") or name.endswith(".docm"):
        snippet = _extract_binary_text_snippet(content)
        if snippet:
            return snippet
        return f"[DOC资料] 文件: {filename}（当前环境未启用结构化解析，已纳入文件元信息）"
    if name.endswith(".pdf"):
        return _extract_pdf_text(
            content,
            filename,
            pymupdf_backend=pymupdf_backend,
            pdf_reader_backend=pdf_reader_backend,
            image_backend=image_backend,
            ocr_backend=ocr_backend,
        )
    if name.endswith(".json"):
        return content.decode("utf-8", errors="ignore")
    if name.endswith(".xlsx") or name.endswith(".xls") or name.endswith(".xlsm"):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            parts = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    parts.append("\t".join(str(c) if c is not None else "" for c in row))
            wb.close()
            return "\n".join(parts)
        except Exception as e:
            raise ValueError(f"Excel 解析失败: {e}") from e
    if name.endswith(".dxf"):
        try:
            return _extract_dxf_text(content)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"DXF 解析失败: {e}") from e
    if name.endswith(".dwg"):
        return _extract_dwg_text(
            content,
            filename,
            resolve_dwg_converter_binaries=resolve_dwg_converter_binaries,
        )
    if name.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
        return _extract_image_content(
            content,
            filename,
            image_backend=image_backend,
            ocr_backend=ocr_backend,
        )
    snippet = _extract_binary_text_snippet(content, max_chars=2000)
    if snippet:
        return snippet
    raise ValueError(
        "仅支持 .txt、.md、.csv、.doc/.docx/.docm、.pdf、.json、.xlsx/.xls/.xlsm、.dxf/.dwg、图片格式"
    )
