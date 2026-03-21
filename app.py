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
def call_llm(api_key, base_url, model_name, system_prompt, user_content, image_urls=None):
    if not api_key:
        st.error("⚠️ 请先在左侧边栏输入 API Key！")
        st.stop()
        
    if image_urls is None:
        image_urls = []
        
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url 
        )
        
        if image_urls:
            message_content = [{"type": "text", "text": user_content}]
            for img_url in image_urls:
                message_content.append({
                    "type": "image_url",
                    "image_url": {"url": img_url}
                })
        else:
            message_content = user_content

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_content}
            ],
            temperature=0.3 
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
# 1. 核心抓取函数 (视觉增强 + 反懒加载版)
# ==========================================
def extract_youtube_transcript(url):
    try:
        parsed_url = urlparse(url)
        if 'youtube.com' in parsed_url.netloc:
            video_id = parse_qs(parsed_url.query).get('v', [None])[0]
        elif 'youtu.be' in parsed_url.netloc:
            video_id = parsed_url.path.lstrip('/')
        else:
            return None, [], "未找到有效的 YouTube Video ID"

        if not video_id:
            return None, [], "无法解析 YouTube 链接"

        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['zh-Hans', 'zh-Hant', 'en'])
        text = "\n".join([item['text'] for item in transcript_list])
        return text, [], None
    except Exception as e:
        return None, [], f"YouTube 字幕抓取失败: {str(e)}"

def extract_article_content(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        text = trafilatura.extract(response.text)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 定位正文，防止抓到边缘无效图标
        main_content = soup.find('article') or soup.find('main') or soup.find(class_=lambda c: c and 'content' in c.lower()) or soup
        
        images = []
        for img in main_content.find_all('img'):
            # 穿透懒加载机制
            possible_attrs = ['data-original', 'data-lazy-src', 'data-src', 'src']
            src = None
            for attr in possible_attrs:
                val = img.get(attr)
                if val:
                    if isinstance(val, list):
                        val = val[0]
                    val = str(val)
                    if not val.startswith('data:image'):
                        src = val
                        break
                        
            if not src:
                continue
                
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                parsed_url = urlparse(url)
                src = f"{parsed_url.scheme}://{parsed_url.netloc}{src}"
            elif not src.startswith('http'):
                continue
                
            src_lower = src.lower()
            junk_keywords = ['icon', 'avatar', 'spinner', 'svg', 'gif', 'button', 'tracker', 'footer', 'sidebar']
            if any(junk in src_lower for junk in junk_keywords):
                continue
                
            if src not in images:
                images.append(src)
                if len(images) >= 5:
                    break
        
        if not text:
            paragraphs = soup.find_all('p')
            text = '\n'.join([p.get_text() for p in paragraphs])
            
        if text and text.strip():
            return text, images, None
        else:
            return None, [], "未能提取到有效纯文本。"
    except Exception as e:
        return None, [], f"文章抓取失败: {str(e)}"

# 就是这个函数刚才被不小心删掉了！
def get_content_from_url(url):
    if "youtube.com" in url or "youtu.be" in url:
        return extract_youtube_transcript(url)
    else:
        return extract_article_content(url)

# ==========================================
# 2. 状态管理与 Prompt 预设
# ==========================================
# ⚠️ 注意：请把你 Excel 里的内容直接覆盖掉下面三段文字
DEFAULT_PROMPTS = {
    "发行主编": """我是发行主编的默认Prompt，请将 sheet1 的内容完整粘贴覆盖这段文字。""",
    
    "研发主编": """我是研发主编的默认Prompt，请将 sheet2 的内容完整粘贴覆盖这段文字。""",
    
    "审稿员": """我是审稿员的默认Prompt，请将 sheet3 的内容完整粘贴覆盖这段文字。"""
}

def init_state():
    if 'current_step' not in st.session_state:
        st.session_state.current_step = 1
    if 'article_url' not in st.session_state:
        st.session_state.article_url = ""
    if 'video_url' not in st.session_state:
        st.session_state.video_url = ""
    if 'source_content' not in st.session_state:
        st.session_state.source_content = ""
    if 'source_images' not in st.session_state:
        st.session_state.source_images = []
    if 'extraction_success' not in st.session_state:
        st.session_state.extraction_success = False
    if 'draft_article' not in st.session_state:
        st.session_state.draft_article = ""
    if 'review_feedback' not in st.session_state:
        st.session_state.review_feedback = ""
    if 'final_article' not in st.session_state:
        st.session_state.final_article = ""

init_state()

def go_to_step(step):
    st.session_state.current_step = step

# ==========================================
# 3. 页面与工作流渲染
# ==========================================
st.set_page_config(page_title="公众号文章生成助手", page_icon="🕹️", layout="wide")

with st.sidebar:
    st.header("⚙️ 引擎设置")
    api_provider = st.selectbox("🌐 选择 API 中转站", ["DeerAPI", "BLTCY (柏拉图次元)"])
    
    if api_provider == "DeerAPI":
        api_key = st.text_input("🔑 输入 DeerAPI Key", type="password")
        current_base_url = "https://api.deerapi.com/v1"
        available_models = [
            "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview-thinking",
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash-lite-preview-thinking",
            "gpt-5.4-nano",
            "gpt-5.4",
            "qwen3.5-27b",
            "qwen3.5-flash"
        ]
    else:
        api_key = st.text_input("🔑 输入 BLTCY Key", type="password")
        current_base_url = "https://api.bltcy.ai/v1"
        available_models = [
            "claude-opus-4-6-thinking",
            "claude-opus-4-6",
            "claude-sonnet-4-6-thinking",
            "claude-opus-4-5-20251101-thinking",
            "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview-thinking-high",
            "gemini-3.1-flash-lite-preview-thinking-high",
            "gpt-5.4",
            "gpt-5.4-nano",
            "gpt-5.4-mini-2026-03-17"
        ]

    selected_model = st.selectbox("🧠 选择驱动模型", available_models)
    
    st.markdown("---")
    st.markdown("💡 **Tip**: 遇到包含图片的研报，请务必选择支持 Vision 功能的模型。")

st.title("🕹️ 公众号文章生成助手 - 多智能体工作流")

# --- Step 1 ---
if st.session_state.current_step == 1:
    st.header("第一步：输入素材源")
    
    col1, col2 = st.columns(2)
    with col1:
        article_url_input = st.text_input("📝 输入文章链接 (可选)", value=st.session_state.article_url)
    with col2:
        video_url_input = st.text_input("📺 输入 YouTube 视频链接 (可选)", value=st.session_state.video_url)
    
    if st.button("开始提取内容"):
        if not article_url_input and not video_url_input:
            st.warning("请至少输入一个链接！")
        else:
            with st.spinner("正在努力抓取图文并解析内容..."):
                combined_content = ""
                extracted_imgs = []
                errors = []
                
                if article_url_input:
                    art_content, art_imgs, art_err = get_content_from_url(article_url_input)
                    if art_content:
                        combined_content += f"【文章素材文本】\n{art_content}\n\n================\n\n"
                        extracted_imgs.extend(art_imgs)
