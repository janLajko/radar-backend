from __future__ import annotations

import json
import re
from dataclasses import dataclass

from radar_backend.llm.provider import LLMProvider

_SYSTEM_PROMPT = """\
你是一个贸易合规分析师。判断以下政府文件是否应进入"近期政策更新"feed。

## 应 ingest 的条件（满足任意一条）
- 调整进口关税税率（加征、豁免、暂停、阶段性变化）
- 变更特定 HTS code 的分类规则
- 修改 de minimis 免税门槛或适用范围
- 新增或修改进口配额、许可证、合规要求
- 修订、延期或撤销已有关税措施

## 应 discard 的条件
- 纯仪式性公告（节日、纪念日、荣誉表彰）
- 不涉及货物进口的行政令（移民、国防、人事、国内政策）
- 与贸易合规无关的程序性通知（内部组织变更、人员任命）
- HTS change record 中 Source 字段为 "PP xxxxx"、"Executive Order" 或 "Notice" 的条目（这些已由其他数据源覆盖，无需重复处理）

## 输出格式
严格输出如下 JSON，包裹在 <json></json> 标签中，不输出任何其他内容：

<json>
{
  "should_ingest": true,
  "discard_reason": null,
  "reference_number": "Proclamation 11002",
  "headline": "简短标题（一句话）",
  "summary": "2-4 句摘要，说明政策内容和影响范围",
  "briefing_markdown": "## 政策背景\\n...\\n## 主要内容\\n...\\n## 对进口商的影响\\n...",
  "effective_date": "2025-02-04"
}
</json>

规则：
- should_ingest=true 时，headline、summary、briefing_markdown 必须非空
- should_ingest=false 时，discard_reason 必须非空，headline/summary/briefing_markdown 可为空字符串
- effective_date 格式为 YYYY-MM-DD，无法确定时为 null
- 所有文字内容用中文输出
"""

_JSON_RE = re.compile(r"<json>\s*(.*?)\s*</json>", re.DOTALL)


@dataclass(frozen=True)
class PolicyFilterInput:
    source_key: str
    source_label: str
    title: str
    raw_content: str
    attachment_text: str  # empty string when no PDFs


@dataclass(frozen=True)
class PolicyUpdateDraft:
    should_ingest: bool
    discard_reason: str | None
    reference_number: str | None
    headline: str
    summary: str
    briefing_markdown: str
    effective_date: str | None


def filter_and_generate(
    llm: LLMProvider,
    input: PolicyFilterInput,
) -> PolicyUpdateDraft:
    """Call the LLM to decide whether to ingest and generate a briefing.

    Raises ``ValueError`` on invalid/unparseable output.
    The caller is responsible for treating this as a failed attempt.
    """
    user = _build_user_message(input)
    raw = llm.complete(_SYSTEM_PROMPT, user, max_tokens=2048)
    return _parse_response(raw)


def _build_user_message(input: PolicyFilterInput) -> str:
    parts = [
        f"数据源：{input.source_label}（{input.source_key}）",
        f"标题：{input.title}",
        "",
        "## 正文内容",
        input.raw_content,
    ]
    if input.attachment_text:
        parts += ["", "## 附件内容", input.attachment_text]
    return "\n".join(parts)


def _parse_response(raw: str) -> PolicyUpdateDraft:
    match = _JSON_RE.search(raw)
    if not match:
        raise ValueError(f"no <json> block found in LLM response: {raw[:200]!r}")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in LLM response: {exc}") from exc

    should_ingest = bool(data.get("should_ingest", False))

    if should_ingest:
        for field in ("headline", "summary", "briefing_markdown"):
            if not data.get(field, "").strip():
                raise ValueError(
                    f"should_ingest=true but {field!r} is empty"
                )
    else:
        if not data.get("discard_reason", "").strip():
            raise ValueError("should_ingest=false but discard_reason is empty")

    return PolicyUpdateDraft(
        should_ingest=should_ingest,
        discard_reason=data.get("discard_reason") or None,
        reference_number=data.get("reference_number") or None,
        headline=data.get("headline") or "",
        summary=data.get("summary") or "",
        briefing_markdown=data.get("briefing_markdown") or "",
        effective_date=data.get("effective_date") or None,
    )
