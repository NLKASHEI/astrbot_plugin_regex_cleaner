# astrbot_plugin_regex_cleaner

正则清理 LLM 输出中的异常格式。

## 问题

Gemini 2.5 Pro 偶尔会将 tool call 的原始格式泄露到输出中：

```
[{text=正常回复内容, type=text}]
[{text=第一段, type=text}, {text=第二段, type=text}]
```

## 解决方案

通过 `@filter.on_llm_response()` 钩子拦截 LLM 响应，正则匹配并提取纯文本。

## 命令

- `/regex_cleaner` — 查看运行状态和累计清理次数
- `/regex_cleaner_toggle` — 开关清理功能
