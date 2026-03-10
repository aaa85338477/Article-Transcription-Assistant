#!/usr/bin/env python
import argparse
import os
import sys
import textwrap
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup
import trafilatura
from openai import OpenAI


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def extract_article(html: str, url: str) -> Tuple[str, Optional[str]]:
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
        url=url,
    )
    title = None
    if not text:
        soup = BeautifulSoup(html, "lxml")
        paras = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
        text = "\n\n".join(paras)
    if not text:
        raise ValueError("未能从页面提取正文")

    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    return text, title


def build_user_prompt(article_text: str, source_url: str, title: Optional[str]) -> str:
    article_preview = article_text.strip()
    return textwrap.dedent(
        f"""
        我是一名在中国大陆工作的海外游戏发行运营，现在要把一篇英文游戏行业文章转译给国内读者。请按照以下要求输出：
        - 不是逐字翻译，而是行业口吻的转译和重写。
        - 保留核心信息、逻辑和观点，减少 AI 翻译痕迹。
        - 输出结构固定为：
          # 标题1\n# 标题2\n# 标题3\n---\n## 背景\n...\n---\n## 核心观点一\n...\n## 核心观点二\n...\n## 核心观点三\n...\n---\n## 从发行运营视角看\n...\n---\n## 总结\n...
        - 标题需符合公众号行业洞察/现象解析/方法论风格。
        - 背景部分控制在100字以内，说明文章来源、为何值得看、核心话题。
        - 核心观点整理3-5点，每点有小标题+解释，避免逐段翻译，可合并。
        - 从发行运营视角补充个人思考，聚焦长线运营（LiveOps）、商业化、买量、留存等。
        - 总结100字左右。
        - 遇到行业术语请用中国手游行业常用说法，例如：长线运营（LiveOps）、买量（UA）、商业化（Monetization）、休闲解谜（Puzzle）。
        - 输出语言为简体中文，口吻像中国游戏从业者写公众号文章。

        文章来源: {source_url}
        原文标题: {title or '未知'}
        原文内容如下（按需取用，可重组）：
        {article_preview}
        """
    ).strip()


def _call_via_requests(
    api_url: str,
    api_key: str,
    model: str,
    messages: list,
    temperature: float,
    max_tokens: int,
) -> str:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "DMXAPI/1.0.0",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"中转 API 请求失败: {resp.status_code} {resp.text}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise RuntimeError(f"解析中转 API 响应失败: {data}")


def generate_localized_text(
    url: str,
    model: str = "gemini-3.1-flash-lite-preview",
    max_tokens: int = 1800,
    temperature: float = 0.6,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_url: Optional[str] = None,
) -> str:
    """
    Fetch article, build prompt, and call OpenAI-compatible API to generate localized output.

    优先级：
    1) api_url 提供时走中转站示例的 HTTP 调用。
    2) 否则使用 OpenAI SDK，可通过 base_url/OPENAI_BASE_URL 指向中转。
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置 OPENAI_API_KEY")

    html = fetch_html(url)
    article_text, title = extract_article(html, url)

    max_chars = 12000
    if len(article_text) > max_chars:
        article_text = article_text[:max_chars] + "\n...[内容截断]"

    user_prompt = build_user_prompt(article_text, url, title)

    messages = [
        {
            "role": "system",
            "content": "你是一名在中国工作的海外手游发行运营从业者，精通买量、长线运营、商业化、产品设计与用户增长，善于把海外文章转译成中国手游行业从业者习惯的公众号口吻。",
        },
        {"role": "user", "content": user_prompt},
    ]

    # Path 1: direct requests to relay API
    if api_url:
        return _call_via_requests(
            api_url=api_url,
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # Path 2: OpenAI SDK (supports base_url for relay)
    client_params = {"api_key": api_key}
    base_url = base_url or os.getenv("OPENAI_BASE_URL")
    if base_url:
        base_url = base_url.rstrip("/")
        # 防止用户填入完整端点导致重复 /chat/completions/chat/completions
        if base_url.endswith("/chat/completions"):
            base_url = base_url.rsplit("/chat/completions", 1)[0]
        client_params["base_url"] = base_url
    client = OpenAI(**client_params)

    completion = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=messages,
    )

    return completion.choices[0].message.content.strip()


def main():
    parser = argparse.ArgumentParser(description="Fetch article URL and localize to CN game ops style")
    parser.add_argument("url", help="文章 URL")
    parser.add_argument("--model", default="gemini-3.1-flash-lite-preview", help="OpenAI 模型名，默认 gemini-3.1-flash-lite-preview")
    parser.add_argument("--max-tokens", type=int, default=1800, help="输出最大 tokens，默认 1800")
    parser.add_argument("--lang", default="zh", help="输出语言，占位参数")
    parser.add_argument("--api-url", default=os.getenv("AIAPI_URL"), help="自定义中转 API URL (优先级最高)")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL"), help="OpenAI SDK base_url，自建/中转时使用")
    args = parser.parse_args()

    try:
        output = generate_localized_text(
            url=args.url,
            model=args.model,
            max_tokens=args.max_tokens,
            base_url=args.base_url,
            api_url=args.api_url,
        )
    except Exception as e:
        sys.stderr.write(f"处理失败: {e}\n")
        sys.exit(1)

    print(output)


if __name__ == "__main__":
    main()

