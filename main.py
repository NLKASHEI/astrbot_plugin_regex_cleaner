# -*- coding: utf-8 -*-
"""
astrbot_plugin_regex_cleaner - 正则清理 LLM 输出中的异常格式

问题背景：Gemini 2.5 Pro 偶尔会将 tool call 的原始格式泄露到输出中，例如：
  [{text=这是正常的回复内容, type=text}]
  [{text=第一段, type=text}, {text=第二段, type=text}]

本插件通过两种方式处理：
1. @filter.on_llm_request() — 在 system_prompt 注入指令，从源头阻止 Gemini 输出该格式
2. @filter.on_llm_response() — 兜底正则清理（万一又漏了）
"""

import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

# 匹配完整的 {text=..., type=text} 片段
_GEMINI_RAW_RE = re.compile(
    r'\{text=([^}]*?),\s*type=text\}',
    re.DOTALL,
)

# 匹配不完整的 [{text=...（没有闭合标签，被截断）
_GEMINI_HALF_RE = re.compile(
    r'^\[\s*\{text=(.+)$',
    re.DOTALL,
)

# 整体匹配 [{...}, ...] 包裹体
_FULL_BLOCK_RE = re.compile(
    r'\[\s*\{text=.*?type=text\}\s*(?:,\s*\{text=.*?type=text\}\s*)*\]',
    re.DOTALL,
)

# system_prompt 注入的格式禁止指令
_FORMAT_BAN_PROMPT = (
    "\n[重要规则] 你的回复必须是纯自然语言文本，"
    "绝对不要使用 [{text=..., type=text}] 这种格式输出。"
    "直接输出你想说的话，不要用任何结构化标签包裹。\n"
)


class RegexCleaner(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.enabled = True
        self.clean_count = 0

    # ==================== 源头预防 ====================

    @filter.on_llm_request()
    async def inject_format_ban(self, event: AstrMessageEvent, req):
        """在 system_prompt 中注入指令，禁止 Gemini 使用 [{text=..., type=text}] 格式"""
        if not self.enabled:
            return
        if hasattr(req, 'system_prompt') and req.system_prompt:
            req.system_prompt += _FORMAT_BAN_PROMPT

    # ==================== 兜底清理 ====================

    @filter.on_llm_response()
    async def clean_llm_response(self, event: AstrMessageEvent, resp):
        """拦截 LLM 响应，清理 Gemini 原始格式（兜底）"""
        if not self.enabled:
            return

        text = getattr(resp, 'completion_text', '')
        if not text:
            return

        # 检查是否包含需要清理的格式
        has_full = '{text=' in text and 'type=text' in text
        has_half = text.strip().startswith('[{text=') and 'type=text' not in text

        if not has_full and not has_half:
            return

        # 处理不完整的 [{text=... 开头（被截断，没有闭合）
        if has_half:
            match = _GEMINI_HALF_RE.match(text.strip())
            if match:
                cleaned = match.group(1).strip()
                if cleaned:
                    self.clean_count += 1
                    logger.info(
                        f"[RegexCleaner] 第 {self.clean_count} 次清理半截格式: "
                        f"\"{text[:80]}...\" -> \"{cleaned[:80]}...\""
                    )
                    resp.completion_text = cleaned
                    return

        # 处理完整的 [{text=..., type=text}] 块
        if _FULL_BLOCK_RE.fullmatch(text.strip()):
            parts = _GEMINI_RAW_RE.findall(text)
            if parts:
                cleaned = ''.join(p.strip() for p in parts if p.strip())
                if cleaned:
                    self.clean_count += 1
                    logger.info(
                        f"[RegexCleaner] 第 {self.clean_count} 次清理: "
                        f"\"{text[:80]}...\" -> \"{cleaned[:80]}...\""
                    )
                    resp.completion_text = cleaned
                    return

        # 文本中嵌入了 [{text=..., type=text}] 片段
        cleaned = _FULL_BLOCK_RE.sub(
            lambda m: self._extract_text(m.group(0)),
            text,
        )
        if cleaned != text:
            self.clean_count += 1
            logger.info(
                f"[RegexCleaner] 第 {self.clean_count} 次清理嵌入格式"
            )
            resp.completion_text = cleaned

    def _extract_text(self, raw: str) -> str:
        """从 [{text=A, type=text}, ...] 中提取纯文本"""
        parts = _GEMINI_RAW_RE.findall(raw)
        return ''.join(p.strip() for p in parts if p.strip())

    @filter.command("qingli")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看正则清理插件状态 /qingli"""
        status = "已启用" if self.enabled else "已禁用"
        yield event.plain_result(
            f"🧹 正则清理插件 v1.3\n"
            f"状态: {status}\n"
            f"累计清理: {self.clean_count} 次\n"
        )

    @filter.command("qingli_toggle")
    async def cmd_toggle(self, event: AstrMessageEvent):
        """开关正则清理功能 /qingli_toggle"""
        self.enabled = not self.enabled
        status = "已启用" if self.enabled else "已禁用"
        yield event.plain_result(f"🧹 正则清理: {status}")

    async def terminate(self):
        """插件卸载时调用"""
        logger.info(
            f"[RegexCleaner] 插件已卸载，共清理 {self.clean_count} 次"
        )
