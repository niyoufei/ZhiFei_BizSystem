"""
进化报告 LLM 增强的共享逻辑：统一 prompt 结构与响应解析，供 Spark/OpenAI/Gemini 复用。
保证多后端输出格式一致、可解析、可追溯。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

EVOLUTION_PROMPT = """你是一位施工组织设计评标与编制指导专家。根据「规则版进化报告」与真实评标数据摘要，请生成更清晰、可执行的高分逻辑总结与编制指导。

要求：
1. 输出唯一一个 JSON 对象，不要其他说明文字。
2. JSON 必须包含两个键：high_score_logic（字符串数组，3～8 条）、writing_guidance（字符串数组，3～8 条）。
3. high_score_logic：提炼真实高分施组的共性规律，语言简洁，便于编制人理解「什么样的写法容易得高分」。
4. writing_guidance：具体可执行的编制建议，每条尽量包含动作或量化要素（如参数、频次、责任、验收），避免空泛表述。
5. 若规则版内容已足够清晰，可在其基础上润色、合并或拆分，不要偏离事实。

输出格式示例：
{"high_score_logic": ["条1", "条2", ...], "writing_guidance": ["条1", "条2", ...]}
"""


def build_evolution_prompt(
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> str:
    """构建进化用 prompt，控制 token 用量，供各 LLM 后端复用。"""
    lines = [EVOLUTION_PROMPT, "\n---\n【规则版进化报告】"]
    lines.append("高分逻辑（规则）：")
    for s in (report.get("high_score_logic") or [])[:15]:
        lines.append(f"- {s}")
    lines.append("编制指导（规则）：")
    for s in (report.get("writing_guidance") or [])[:15]:
        lines.append(f"- {s}")
    lines.append(f"\n样本数：{report.get('sample_count', 0)}")
    if ground_truth_records:
        finals = [float(r.get("final_score", 0)) for r in ground_truth_records]
        lines.append(f"真实评标条数：{len(ground_truth_records)}")
        lines.append(f"最终得分范围：{min(finals):.1f}～{max(finals):.1f}")
    if project_context and project_context.strip():
        ctx = project_context.strip()[:800]
        if len(project_context.strip()) > 800:
            ctx += "…"
        lines.append(f"\n项目背景摘要：\n{ctx}")
    lines.append("\n请按上述要求输出唯一 JSON 对象。")
    return "\n".join(lines)


def parse_evolution_response(parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 LLM 返回的 JSON 中取出 high_score_logic / writing_guidance，校验为字符串列表。"""
    if not isinstance(parsed, dict):
        return None
    h = parsed.get("high_score_logic")
    w = parsed.get("writing_guidance")
    if not isinstance(h, list) or not isinstance(w, list):
        return None
    high = [str(x).strip() for x in h if x]
    guidance = [str(x).strip() for x in w if x]
    if not high or not guidance:
        return None
    return {"high_score_logic": high, "writing_guidance": guidance}
