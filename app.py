import streamlit as st
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
import io
from docx import Document
from openai import OpenAI
import requests
import json
from bs4 import BeautifulSoup  # <--- 就是之前漏掉的这位救兵

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
            return None, "无法解析 YouTube 链接"

        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['zh-Hans', 'zh-Hant', 'en'])
        text = "\n".join([item['text'] for item in transcript_list])
        return text, None
    except Exception as e:
        return None, f"YouTube 字幕抓取失败: {str(e)}"

def extract_article_content(url):
    """提取网页正文（带反爬伪装和备用解析方案）"""
    try:
        # 尝试 1：用原生 trafilatura 抓取
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text: return text, None

        # 尝试 2：伪装成 Chrome 浏览器发起请求
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # 将拿到的 HTML 交给 trafilatura 解析
        text = trafilatura.extract(response.text)
        if text: return text, None

        # 尝试 3 (Fallback)：如果 trafilatura 还是提不出正文，用 BeautifulSoup 暴力提取所有段落
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        text = '\n'.join([p.get_text() for p in paragraphs])
        
        if text.strip():
            return text, None
        else:
            return None, "网页可能由动态 JS 渲染或存在强力验证码拦截，无法提取纯文本。"

    except Exception as e:
        # 这里已经修复了多余的括号
        return None, f"文章抓取失败: {str(e)}"

def get_content_from_url(url):
    if "youtube.com" in url or "youtu.be" in url:
        return extract_youtube_transcript(url)
    else:
        return extract_article_content(url)

# ==========================================
# 2. 初始化 Session State (状态管理)
# ==========================================
def init_state():
    if 'current_step' not in st.session_state:
        st.session_state.current_step = 1
    if 'article_url' not in st.session_state:
        st.session_state.article_url = ""
    if 'video_url' not in st.session_state:
        st.session_state.video_url = ""
    if 'source_content' not in st.session_state:
        st.session_state.source_content = ""
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
# 3. 页面与多步工作流渲染
# ==========================================
st.set_page_config(page_title="公众号文章生成助手", page_icon="🕹️", layout="wide")

# --- 侧边栏：全局设置 ---
with st.sidebar:
    st.header("⚙️ 引擎设置")
    api_key = st.text_input("🔑 输入 DeerAPI Key", type="password")
    selected_model = st.selectbox("🧠 选择驱动模型", [
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview-thinking",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview-thinking",
        "gpt-5.4-nano",
        "gpt-5.4",
        "qwen3.5-27b",
        "qwen3.5-flash"
    ])
    st.markdown("---")
    st.markdown("💡 **Tip**: 遇到长文拆解或需要拔高立意时，建议切换到带 `thinking` 的模型。")

st.title("🕹️ 公众号文章生成助手 - 多智能体工作流")

# --- Step 1: 录入素材 ---
if st.session_state.current_step == 1:
    st.header("第一步：输入素材源")
    st.markdown("💡 **提示**：你可以同时填入文章链接和视频链接，或者只填其中一项，AI 会自动合并素材进行创作。")
    
    col1, col2 = st.columns(2)
    with col1:
        article_url_input = st.text_input("📝 输入文章链接 (可选)", value=st.session_state.article_url)
    with col2:
        video_url_input = st.text_input("📺 输入 YouTube 视频链接 (可选)", value=st.session_state.video_url)
    
    if st.button("开始提取内容"):
        if not article_url_input and not video_url_input:
            st.warning("请至少输入一个链接！")
        else:
            with st.spinner("正在努力抓取并解析内容..."):
                combined_content = ""
                errors = []
                
                if article_url_input:
                    art_content, art_err = get_content_from_url(article_url_input)
                    if art_content:
                        combined_content += f"【文章素材】\n{art_content}\n\n================\n\n"
                        st.session_state.article_url = article_url_input
                    else:
                        errors.append(f"文章提取失败: {art_err}")
                        
                if video_url_input:
                    vid_content, vid_err = get_content_from_url(video_url_input)
                    if vid_content:
                        combined_content += f"【视频播客素材】\n{vid_content}\n\n================\n\n"
                        st.session_state.video_url = video_url_input
                    else:
                        errors.append(f"视频提取失败: {vid_err}")
                
                if combined_content:
                    st.session_state.source_content = combined_content
                    st.session_state.extraction_success = True
                    if errors:
                        st.warning(f"部分内容提取成功，但有以下错误：\n" + "\n".join(errors))
                    else:
                        st.success("🎉 所有素材提取成功！")
                else:
                    st.session_state.extraction_success = False
                    st.error("❌ 所有链接提取均失败，请检查链接或网络状态。\n" + "\n".join(errors))
            
    if st.session_state.extraction_success:
        with st.expander("预览抓取到的原文", expanded=False):
            preview_text = st.session_state.source_content[:1500] + "\n\n......(已省略后续内容)" if len(st.session_state.source_content) > 1500 else st.session_state.source_content
            st.text(preview_text)
            
        if st.button("确认无误，继续下一步 👉"):
            go_to_step(2)
            st.rerun()

# --- Step 2: 设定编辑并生成初稿 ---
elif st.session_state.current_step == 2:
    st.header("第二步：选择编辑与生成初稿")
    
    editor_role = st.selectbox("选择【编辑】视角", ["资深游戏制作人", "海外发行总监", "核心主策划"])
    
    default_prompt = f"你现在是一位{editor_role}。请以你的视角对以下原始素材进行深度融合与转录。要求：\n1. 有前后铺垫，娓娓道来；\n2. 拔高格局，展现深刻的行业认知与产品思考；\n3. 结构要自然流畅，不要写成干瘪死板的汇报文档。\n4. 如果提供了多个素材来源，请将它们的核心观点自然地结合起来。"
    editor_prompt = st.text_area("✍️ 编辑 Prompt (可自由修改)", value=default_prompt, height=150)
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔙 返回上一步"):
            go_to_step(1)
            st.rerun()
    with col2:
        if st.button(f"🚀 使用 {selected_model} 生成文章初稿"):
            with st.spinner("编辑正在奋笔疾书，请耐心等待..."):
                st.session_state.draft_article = call_llm(
                    api_key=api_key, 
                    model_name=selected_model, 
                    system_prompt=editor_prompt, 
                    user_content=f"以下是提供的素材内容：\n\n{st.session_state.source_content}"
                )
                go_to_step(3)
                st.rerun()

# --- Step 3: 审稿员审查 ---
elif st.session_state.current_step == 3:
    st.header("第三步：审稿员审查初稿")
    
    with st.expander("📝 查看当前初稿内容", expanded=True):
        st.write(st.session_state.draft_article)
    
    st.divider()
    
    reviewer_prompt = st.text_area("🧐 审稿员 Prompt (可自由修改)", 
                                   value="你是一个严苛的公众号主编。请对比【原始素材】和【初稿】，指出初稿中：\n1. 丢失的核心信息（特别注意是否漏掉了某个素材源的关键观点）\n2. 逻辑不顺畅或缺乏深度的部分\n3. 语气不够专业的地方\n请直接列出明确的修改建议，不要输出废话。", 
                                   height=150)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 感觉不对，重写初稿"):
            go_to_step(2)
            st.rerun()
    with col2:
        if st.button("⏭️ 完美，跳过审查直接定稿"):
            st.session_state.final_article = st.session_state.draft_article
            go_to_step(5)
            st.rerun()
with col3:
        if st.button(f"✨ 使用 {selected_model} 接受意见并修改文章"):
            with st.spinner("编辑正在根据主编意见进行修改..."):
                modification_prompt = "你是一位专业的文字编辑。请根据以下【审稿意见】，对【初稿】进行全面修改。直接输出修改后的最终成稿，不要包含任何多余的解释说明。"
                content_to_modify = f"【审稿意见】：\n{st.session_state.review_feedback}\n\n================\n\n【初稿】：\n{st.session_state.draft_article}"
                
                st.session_state.final_article = call_llm(
                    api_key=api_key, 
                    model_name=selected_model, 
                    system_prompt=modification_prompt, 
                    user_content=content_to_modify
                )
                go_to_step(5)
                st.rerun()

# --- Step 4: 处理审查意见 ---
elif st.session_state.current_step == 4:
    st.header("第四步：处理审查意见")
    
    st.info(f"**主编审稿意见：**\n\n{st.session_state.review_feedback}")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 意见太水，重新审查"):
            go_to_step(3)
            st.rerun()
    with col2:
        if st.button("⏭️ 忽略意见，强行定稿"):
            st.session_state.final_article = st.session_state.draft_article
            go_to_step(5)
            st.rerun()
    with col3:
        if st.button(f"✨ 使用 {selected_model} 接受意见并修改文章"):
