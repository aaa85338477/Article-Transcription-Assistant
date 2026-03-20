import streamlit as st
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
import io
from docx import Document
from openai import OpenAI
import requests
import json
from bs4 import BeautifulSoup

# ==========================================
# 0. API 与外部推送函数
# ==========================================
def call_llm(api_key, model_name, system_prompt, user_content):
    if not api_key:
        st.error("⚠️ 请先在左侧边栏输入 DeerAPI Key！")
        st.stop()
        
    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deerapi.com/v1" 
        )
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.7 
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"API 调用失败: {str(e)}")
        st.stop()

def push_to_feishu(text_content):
    webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/a0f50778-0dd2-4963-a0b2-0c7b68e113d8"
    headers = {"Content-Type": "application/json"}
    payload = {
        "msg_type": "text",
        "content": {
            "text": f"📣 【公众号文章定稿通知】\n\n{text_content}"
        }
    }
    
    try:
        response = requests.post(webhook_url, headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            resp_json = response.json()
            if resp_json.get("code") == 0:
                return True, "推送成功"
            else:
                return False, f"飞书返回错误: {resp_json.get('msg')}"
        else:
            return False, f"HTTP 请求失败，状态码: {response.status_code}"
    except Exception as e:
        return False, f"请求发生异常: {str(e)}"

# ==========================================
# 1. 核心抓取函数
# ==========================================
def extract_youtube_transcript(url):
    try:
        parsed_url = urlparse(url)
        if 'youtube.com' in parsed_url.netloc:
            video_id = parse_qs(parsed_url.query).get('v', [None])[0]
        elif 'youtu.be' in parsed_url.netloc:
            video_id = parsed_url.path.lstrip('/')
        else:
            return None, "未找到有效的 YouTube Video ID"

        if not video_id:
