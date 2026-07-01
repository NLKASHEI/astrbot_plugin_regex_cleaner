# -*- coding: utf-8 -*-
"""
astrbot_plugin_regex_cleaner - 正则清理 LLM 输出中的异常格式

问题背景：Gemini 2.5 Pro 偶尔会将 tool call 的原始格式泄露到输出中，例如：
  [{text=这是正常的回复内容, type=text}]
  [{text=第一段, type=text}, {text=第二段, type=text}]

本插件通过 @filter.on_llm_response() 钩子拦截 LLM 响应，
用正则将这些异常格式提取为纯文本。
"""

import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

# 匹配 Gemini 泄露的 {text=..., type=text} 片段
_GEMINI_RAW_RE = re.compile(
    r'\{text=([^}]*?),\s*type=text\}',
    re.DOTALL,
)

# 整体匹配 [{...}, ...] 包裹体
_FULL_BLOCK_RE = re.compile(
    r'\[\s*\{text=.*?type=text\}\s*(?:,\s*\{text=.*?type=text\}\s*)*\]',
    re.DOTALL,
)


class RegexCleaner(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.enabled = True
        self.clean_count = 0

    @filter.on_llm_response()
    async def clean_llm_response(self, event: AstrMessageEvent, resp):
        """拦截 LLM 响应，清理 Gemini 原始格式"""
        if not self.enabled:
            return

        text = getattr(resp, 'completion_text', '')
        if not text:
            return

        # 检查是否包含需要清理的格式
        if '{text=' not in text or 'type=text' not in text:
            return

        # 如果整个响应就是一个 [{text=..., type=text}] 块
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

    @filter.command("regex_cleaner")
    async def status(self, event: AstrMessageEvent):
        """查看正则清理插件状态"""
        status = "已启用" if self.enabled else "已禁用"
        yield event.plain_result(
            f"正则清理插件 v1.0.0\n"
            f"状态: {status}\n"
            f"累计清理: {self.clean_count} 次\n"
            f"匹配格式: [{{text=..., type=text}}]"
        )

    @filter.command("regex_cleaner_toggle")
    async def toggle(self, event: AstrMessageEvent):
        """开关正则清理功能"""
        self.enabled = not self.enabled
        status = "已启用" if self.enabled else "已禁用"
        yield event.plain_result(f"正则清理: {status}")

    async def terminate(self):
        """插件卸载时调用"""
        logger.info(
            f"[RegexCleaner] 插件已卸载，共清理 {self.clean_count} 次"
        )
