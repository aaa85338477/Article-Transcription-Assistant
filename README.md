# Article Localizer for Game Ops

输入英文游戏行业文章的 URL，一键生成符合你给定模板的中文公众号稿。

## 准备
1. 安装依赖：`pip install -r requirements.txt`
2. 设置环境变量：`OPENAI_API_KEY`（或中转站提供的 Key）。可选：`AIAPI_URL`（中转 chat/completions URL，默认 https://api.bltcy.ai/v1/chat/completions）、`OPENAI_BASE_URL`（OpenAI SDK base_url）。

## 使用（CLI）
```bash
python article_localizer.py "https://example.com/article" --api-url https://api.bltcy.ai/v1/chat/completions
```
可选参数：
- `--model gemini-3.1-flash-lite-preview` 替换成你有额度的模型名
- `--max-tokens 1800` 控制输出长度
- `--api-url` 明确走中转站；若留空则使用 `--base-url`/官方
- `--base-url` OpenAI SDK base_url，自建/中转时使用

## 使用（Streamlit）
```bash
streamlit run app.py
```
侧边栏可填写中转 API URL（优先级最高）、base_url、模型、temperature、max tokens；未填 API Key 时读取环境变量 `OPENAI_API_KEY`。

## 输出
脚本直接打印符合以下结构的文本，可复制到公众号后台：
```
# 标题1
# 标题2
# 标题3
---
## 背景
...
---
## 核心观点一
...
## 核心观点二
...
## 核心观点三
...
---
## 从发行运营视角看
...
---
## 总结
...
```

## 注意
- 如果网页解析失败，脚本会回退到更简单的提取策略并提示。
- 默认不会保存文件，如需落地到本地，可用重定向：`python article_localizer.py URL > output.md`。

