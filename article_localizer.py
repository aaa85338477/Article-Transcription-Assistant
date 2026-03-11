#!/usr/bin/env python
import argparse
import os
import sys
import textwrap
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
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


def extract_youtube_id(url: str) -> Optional[str]:
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    return None


def fetch_yt_transcript(yt_url: str) -> Optional[str]:
    video_id = extract_youtube_id(yt_url)
    if not video_id:
        return None
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
        return " ".join(item.get("text", "") for item in transcript if item.get("text"))
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return None
    except Exception:
        return None


def build_user_prompt(article_text: str, source_url: str, title: Optional[str], yt_summary: Optional[str]) -> str:
    article_preview = article_text.strip()
    video_block = f"\n视频参考章节总结：\n{yt_summary}\n" if yt_summary else ""
    return textwrap.dedent(
        f"""
        Prompt：
        二、写作方式
        请按照以下思考方式处理文章：
        1 先理解文章
        思考：
        这篇文章主要在讲什么？
        为什么作者要写这篇文章？
        哪些观点最值得关注？
        不要逐段翻译。
        2 提炼最重要的信息
        从文章中提炼：
        3～5个最有价值的观点
        有意思的数据或案例
        行业变化趋势
        如果原文有很多细节，可以进行合并和重组。
        3 用中国游戏行业的表达方式重新写
        在写作时：
        使用中国游戏行业常见术语
        语言自然
        可以略微口语化
        避免翻译腔
        例如：
        不要写：
        该文章指出……
        可以写：
        文章里其实提到了一个挺有意思的现象。
        4 可以适当加入行业理解
        在合适的位置，可以补充一些：
        作为发行运营的理解
        对行业的观察
        对产品或商业化的启发
        但不要写成分析报告。
        三、文章结构
        文章结构不需要固定。
        可以根据内容自然组织，例如：
        现象 → 原因 → 启发
        案例 → 分析 → 总结
        观点 → 举例 → 讨论
        但整体阅读逻辑要清晰。
        四、标题
        生成 3个公众号风格标题。
        风格可以是：
        行业观察
        趋势分析
        现象解读
        例如：
        《为什么越来越多手游开始从 Day1 就做 LiveOps》
        《Steam 上最近的一种新趋势》
        《这家公司做运营的方式有点不一样》
        五、语言要求
        文章整体风格：
        自然
        像行业从业者写的
        不要有明显 AI 味道
        避免：
        逐句翻译
        “首先、其次、最后”结构
        教科书语气
        六、输出内容
        输出：
        1️⃣ 三个标题
        2️⃣ 一篇完整中文文章
        文章长度：
        1500–2000字左右
        文章来源: {source_url}
        原文标题: {title or '未知'}
        原文内容如下（按需取用，可重组）：
        {article_preview}
        {video_block}
        """
    ).strip()


def _call_via_requests(api_url: str, api_key: str, model: str, messages: list, temperature: float, max_tokens: int) -> str:
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


def summarize_video(transcript_text: str, model: str, temperature: float, max_tokens: int, api_key: str, base_url: Optional[str], api_url: Optional[str]) -> Optional[str]:
    if not transcript_text:
        return None
    summary_prompt = textwrap.dedent(
        f"""
        请将下面的英文视频转录内容做成结构化的分章节总结，输出格式：
        1. [时间戳] 章节标题 - 核心要点（中文）
        2. ...
        时间戳按分秒标记到分钟级即可，例如 [03:15]。
        语言使用中文行业口吻，聚焦游戏发行/运营相关信息。

        视频转录：
        {transcript_text}
        """
    ).strip()
    messages = [
        {"role": "system", "content": "你是资深游戏发行运营，擅长将英文视频要点总结成中文章节。"},
        {"role": "user", "content": summary_prompt},
    ]
    if api_url:
        return _call_via_requests(api_url, api_key, model, messages, temperature, max_tokens)
    client_params = {"api_key": api_key}
    if base_url:
        base_url = base_url.rstrip("/")
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


def generate_localized_text(
    url: str,
    model: str = "gemini-3.1-flash-lite-preview",
    max_tokens: int = 1800,
    temperature: float = 0.6,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_url: Optional[str] = None,
    yt_url: Optional[str] = None,
) -> str:
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置 OPENAI_API_KEY")

    html = fetch_html(url)
    article_text, title = extract_article(html, url)

    max_chars = 12000
    if len(article_text) > max_chars:
        article_text = article_text[:max_chars] + "\n...[内容截断]"

    yt_summary = None
    if yt_url:
        transcript = fetch_yt_transcript(yt_url)
        if transcript:
            transcript = transcript[:12000]
            try:
                yt_summary = summarize_video(
                    transcript_text=transcript,
                    model=model,
                    temperature=0.5,
                    max_tokens=600,
                    api_key=api_key,
                    base_url=base_url,
                    api_url=api_url,
                )
            except Exception:
                yt_summary = None

    user_prompt = build_user_prompt(article_text, url, title, yt_summary)

    messages = [
        {
            "role": "system",
            "content": "请严格按照用户提供的提示执行。",
        },
        {"role": "user", "content": user_prompt},
    ]

    if api_url:
        return _call_via_requests(api_url, api_key, model, messages, temperature, max_tokens)

    client_params = {"api_key": api_key}
    base_url = base_url or os.getenv("OPENAI_BASE_URL")
    if base_url:
        base_url = base_url.rstrip("/")
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
    parser.add_argument("--model", default="gemini-3.1-flash-lite-preview", help="模型名，默认 gemini-3.1-flash-lite-preview")
    parser.add_argument("--max-tokens", type=int, default=1800, help="输出最大 tokens，默认 1800")
    parser.add_argument("--lang", default="zh", help="输出语言，占位参数")
    parser.add_argument("--api-url", default=os.getenv("AIAPI_URL"), help="自定义中转 API URL (优先级最高)")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL"), help="OpenAI SDK base_url，自建/中转时使用")
    parser.add_argument("--yt-url", default=None, help="可选 YouTube 视频链接，用于生成章节参考")
    args = parser.parse_args()

    try:
        output = generate_localized_text(
            url=args.url,
            model=args.model,
            max_tokens=args.max_tokens,
            base_url=args.base_url,
            api_url=args.api_url,
            yt_url=args.yt_url,
        )
    except Exception as e:
        sys.stderr.write(f"处理失败: {e}\n")
        sys.exit(1)

    print(output)


if __name__ == "__main__":
    main()
