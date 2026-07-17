# -*- coding: utf-8 -*-
"""
astrbot_plugin_regex_cleaner - 清理 LLM 输出异常格式 v1.10.0

用正则精确匹配 Gemini [{text=..., type=text}] 格式及其所有变体（含换行/空格），
外加 AI 套话清洗、破折号替换。
"""

import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig

# Gemini [{text=CONTENT, type=text}] 格式清理（v1.10 重写：正则替代 str.replace）
# 匹配从 [{text= 到 , type=text}] 的完整块，中间内容（含换行）提取为纯文本
_GEMINI_FORMAT_RE = re.compile(
    r'\[\{text\s*=\s*'       # [{text= 开头
    r'(.*?)'                   # 正文（非贪婪，跨行）
    r'\s*,\s*type\s*=\s*text\s*'  # , type=text
    r'\}\]',                   # }] 结尾
    re.DOTALL,
)

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

        # 最高优先级：防止 Bot 误 @everyone / @here
        if '@everyone' in text:
            text = text.replace('@everyone', '[禁止艾特所有人]')
            resp.completion_text = text

        # 快速检查是否有 Gemini 格式
        has_gemini = '{text=' in text or 'type=text' in text
        if not has_gemini:
            return

        old = text
        # v1.10: 正则精确匹配 [{text=..., type=text}] 及其所有变体（含换行/空格）
        text = _GEMINI_FORMAT_RE.sub(r'\1', text)
        # 兜底：残留的孤立标签
        text = text.replace('[{text=', '').replace('{text=', '')
        text = text.replace(', type=text}]', '').replace(', type=text}', '')
        text = text.replace('}]', '')

        if text != old:
            self.clean_count += 1
            logger.info(
                f"[RegexCleaner] 第 {self.clean_count} 次清理 Gemini 格式: "
                f"\"{old[:60]}...\" -> \"{text[:60]}...\""
            )
            resp.completion_text = text.strip()

        # AI 套话清洗（v1.5 新增）
        if self.yuliao_enabled:
            cleaned = _AI_TAOHUA_RE.sub('', resp.completion_text)
            if cleaned != resp.completion_text:
                self.yuliao_count += 1
                resp.completion_text = cleaned

        # 破折号替换为逗号（v1.6 新增）
        _dash_count = resp.completion_text.count('\u2014') + resp.completion_text.count('\u2013') + resp.completion_text.count('\u2015')
        if _dash_count > 0:
            resp.completion_text = re.sub(r'[\u2014\u2013\u2015]{1,3}', '，', resp.completion_text)
            self.yuliao_count += 1

    @filter.command("qingli")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看正则清理插件状态 /qingli"""
        status = "已启用" if self.enabled else "已禁用"
        yuliao_status = "已启用" if self.yuliao_enabled else "已禁用"
        yield event.plain_result(
            f"🧹 正则清理插件 v1.10.0\n"
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
