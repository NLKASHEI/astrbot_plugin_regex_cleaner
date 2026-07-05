# -*- coding: utf-8 -*-
"""
astrbot_plugin_regex_cleaner - 正则清理 LLM 输出中的异常格式 v1.7

处理 Gemini 输出泄露：
1. [{text=..., type=text}] 标准格式
2. [{text=... 半截格式
3. [{text=[{text=[{text=... 嵌套格式
4. AI 套话清洗（消除"而/突然/一丝/不容置疑/一抹弧度"等AI写作套路）
5. 破折号替换为逗号（——/—/– → ，）
6. 定点清除残留的 , type=text 分隔符 v1.7 新增
"""

import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig

# 匹配完整的 {text=..., type=text} 片段
_GEMINI_RAW_RE = re.compile(
    r'\{text=([^}]*?)\}',
    re.DOTALL,
)

# 匹配不完整的 [{text=...（没有闭合标签，被截断）
_GEMINI_HALF_RE = re.compile(
    r'^\[\s*\{text=(.+)$',
    re.DOTALL,
)

# 整体匹配 [{...}, ...] 包裹体
_FULL_BLOCK_RE = re.compile(
    r'\[\s*\{text=[^}]*\}(?:\s*,\s*\{[^}]*\})*\s*\]',
    re.DOTALL,
)

# 处理嵌套 [{text=[{text=... 格式（v1.4 新增）
def _strip_nested_text(text: str) -> str:
    """剥离嵌套 [{text=[{text=... 前缀和尾部残留"""
    prefix = '[{text='
    count = 0
    while text.startswith(prefix):
        count += 1
        text = text[len(prefix):]
    text = text.rstrip()
    for _ in range(count):
        if text.endswith(']'):
            text = text[:-1].rstrip()
    # 清理尾部残留的 , type=text}, ], 等
    text = re.sub(r',?\s*type=text\}', '', text)
    text = re.sub(r',?\s*\{type=text\}\s*', '', text)
    text = text.rstrip(']').strip()
    return text


# AI 套话清洗（消除常见 AI 写作套路）v1.5
_AI_TAOHUA_RE = re.compile(
    r'而(?=是)'
    r'|(?<=[，"。\s])不是[\S]*?[，, 。]'
    r'|(个动作|个反应|个认知|个笑容)'
    r'|突然|忽然'
    r'|一(丝+)'
    r'|(、?)不容置疑([的地]?)'
    r'|(、?)(不易|难以)(觉察|察觉)([的地]?)'
    r'|(微|几)不可(查|察|闻)([的地]?)'
    r'|[，,]([^，,]*?)指(关节|节|尖)(.*?)白([^，,]*?)(?=[。，,])'
    r'|(?<=[\s"。])([^，\"\u201d]*?)(一抹|弧度)([^，]*?)[。，]'
    r'|[，,]([^，,\"\u201d]*?)(一抹|弧度)([^，]*?)(?=[。，,])'
    r'|(?<=[\s"。，])([^。，]*?)(话像)([^。，]*?)[。，]',
    re.DOTALL,
)

# system_prompt 注入的格式禁止指令
_FORMAT_BAN_PROMPT = (
    "\n[重要规则] 你的回复必须是纯自然语言文本，"
    "绝对不要使用 [{text=..., type=text}] 这种格式输出。"
    "直接输出你想说的话，不要用任何结构化标签包裹。\n"
)


class RegexCleaner(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.enabled = True
        cfg = config or {}
        self.yuliao_enabled = str(cfg.get("yuliao_enabled", "true")).lower() in ("true", "1", "yes")
        self.clean_count = 0
        self.yuliao_count = 0

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
        has_nested = text.strip().startswith('[{text=[{text=')

        if not has_full and not has_half and not has_nested:
            return

        # 处理嵌套 [{text=[{text=... 格式（v1.4 新增）
        if has_nested:
            cleaned = _strip_nested_text(text.strip())
            if cleaned and cleaned != text.strip():
                self.clean_count += 1
                logger.info(
                    f"[RegexCleaner] 第 {self.clean_count} 次清理嵌套格式: "
                    f"\"{text[:60]}...\" -> \"{cleaned[:60]}...\""
                )
                resp.completion_text = cleaned
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

        # AI 套话清洗（v1.5 新增）
        if self.yuliao_enabled:
            cleaned = _AI_TAOHUA_RE.sub('', resp.completion_text)
            if cleaned != resp.completion_text:
                self.yuliao_count += 1
                resp.completion_text = cleaned

        # 破折号替换为逗号（v1.6 新增）——消除 AI 爱用——破折号的毛病
        _dash_count = resp.completion_text.count('\u2014') + resp.completion_text.count('\u2013') + resp.completion_text.count('\u2015')
        if _dash_count > 0:
            resp.completion_text = re.sub(r'[\u2014\u2013\u2015]{1,3}', '，', resp.completion_text)
            self.yuliao_count += 1

        # 定点清除残留的 , type=text（v1.7 新增）
        # 匹配 , type=text / ,type=text / , type = text 等变体
        if 'type=text' in resp.completion_text:
            cleaned = re.sub(r',\s*type\s*=\s*text', '', resp.completion_text)
            if cleaned != resp.completion_text:
                resp.completion_text = cleaned
                self.yuliao_count += 1

    def _extract_text(self, raw: str) -> str:
        """从 [{text=A, type=text}, ...] 中提取纯文本"""
        parts = _GEMINI_RAW_RE.findall(raw)
        return ''.join(p.strip() for p in parts if p.strip())

    @filter.command("qingli")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看正则清理插件状态 /qingli"""
        status = "已启用" if self.enabled else "已禁用"
        yuliao_status = "已启用" if self.yuliao_enabled else "已禁用"
        yield event.plain_result(
            f"🧹 正则清理插件 v1.7\n"
            f"格式清理: {status} | 累计 {self.clean_count} 次\n"
            f"AI 套话清洗: {yuliao_status} | 累计 {self.yuliao_count} 次\n"
        )

    @filter.command("qingli_toggle")
    async def cmd_toggle(self, event: AstrMessageEvent):
        """开关格式清理 /qingli_toggle"""
        self.enabled = not self.enabled
        status = "已启用" if self.enabled else "已禁用"
        yield event.plain_result(f"🧹 格式清理: {status}")

    @filter.command("qingli_yuliao")
    async def cmd_yuliao_toggle(self, event: AstrMessageEvent):
        """开关 AI 套话清洗 /qingli_yuliao"""
        self.yuliao_enabled = not self.yuliao_enabled
        status = "已启用" if self.yuliao_enabled else "已禁用"
        yield event.plain_result(f"🧹 AI 套话清洗: {status}")

    async def terminate(self):
        """插件卸载时调用"""
        logger.info(
            f"[RegexCleaner] 插件已卸载，共清理 {self.clean_count} 次"
        )
