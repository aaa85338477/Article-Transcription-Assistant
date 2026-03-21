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
    """支持多模态（Vision）的通用 LLM 调用函数"""
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
        
        # 判断是否需要走 Vision 格式
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
# 1. 核心抓取函数 (视觉增强版)
# ==========================================
def extract_youtube_transcript(url):
    """YouTube 抓取：返回纯文本和空的图片列表"""
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
    """图文双抓取逻辑（攻克懒加载与相对路径伪装）"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # 1. 提取纯文本正文
        text = trafilatura.extract(response.text)
        
        # 2. 提取并清洗图片
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 尽量锁定正文区域，避免抓到网页头部/尾部/侧边栏的无用小图标
        main_content = soup.find('article') or soup.find('main') or soup.find(class_=lambda c: c and 'content' in c.lower()) or soup
        
        images = []
        for img in main_content.find_all('img'):
            # 按优先级尝试获取真实图片 URL（专门对付现代网页的懒加载机制）
            possible_attrs = ['data-original', 'data-lazy-src', 'data-src', 'src']
            src = None
            for attr in possible_attrs:
                val = img.get(attr)
                # 排除 base64 的透明占位图
                if val and not val.startswith('data:image'):
                    src = val
                    break
                    
            if not src:
                continue
                
            # 处理各类奇怪的相对路径或协议省略 (如 //cdn.domain.com/img.jpg)
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                parsed_url = urlparse(url)
                src = f"{parsed_url.scheme}://{parsed_url.netloc}{src}"
            elif not src.startswith('http'):
                continue
                
            # 过滤常见的干扰图片 (去掉了 logo 以免误杀公司财报图)
            src_lower = src.lower()
            junk_keywords = ['icon', 'avatar', 'spinner', 'svg', 'gif', 'button', 'tracker', 'footer', 'sidebar']
            if any(junk in src_lower for junk in junk_keywords):
                continue
                
            if src not in images:
                images.append(src)
                # 为防止大模型 Token 爆炸，最多抓取前 5 张核心配图
                if len(images) >= 5:
                    break
        
        # 3. 容错处理：如果 trafilatura 失败，用 BS4 暴力兜底
        if not text:
            paragraphs = soup.find_all('p')
            text = '\n'.join([p.get_text() for p in paragraphs])
            
        if text and text.strip():
            return text, images, None
        else:
            return None, [], "未能提取到有效纯文本。"
    except Exception as e:
        return None, [], f"文章抓取失败: {str(e)}"

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
        st.session_state.source_images = []  # 新增：用于存储抓取到的所有图片链接
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
    st.markdown("💡 **Tip**: 遇到包含图片的研报，请务必选择支持 Vision 功能的模型（如 Claude 4.6 家族、GPT-5.4 或 Gemini Pro），普通纯文本模型会报错。")

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
                        st.session_state.article_url = article_url_input
                    else:
                        errors.append(f"文章提取失败: {art_err}")
                        
                if video_url_input:
                    vid_content, vid_imgs, vid_err = get_content_from_url(video_url_input)
                    if vid_content:
                        combined_content += f"【视频播客素材】\n{vid_content}\n\n================\n\n"
                        st.session_state.video_url = video_url_input
                    else:
                        errors.append(f"视频提取失败: {vid_err}")
                
                if combined_content:
                    st.session_state.source_content = combined_content
                    st.session_state.source_images = extracted_imgs
                    st.session_state.extraction_success = True
                    if errors:
                        st.warning(f"部分内容提取成功，但有以下错误：\n" + "\n".join(errors))
                    else:
                        st.success(f"🎉 素材提取成功！共提取到 {len(extracted_imgs)} 张核心配图。")
                else:
                    st.session_state.extraction_success = False
                    st.error("❌ 所有链接提取均失败，请检查链接或网络状态。\n" + "\n".join(errors))
            
    if st.session_state.extraction_success:
        with st.expander("预览抓取到的原文与图表", expanded=False):
            # 渲染提取到的图片
            if st.session_state.source_images:
                st.markdown("🖼️ **提取到的核心配图：**")
                # 使用两列布局美观展示图片
                img_cols = st.columns(2)
                for idx, img_url in enumerate(st.session_state.source_images):
                    with img_cols[idx % 2]:
                        st.image(img_url, use_column_width=True)
                st.divider()
                
            st.markdown("📄 **提取到的文本正文：**")
            preview_text = st.session_state.source_content[:1500] + "\n\n......(已省略后续内容)" if len(st.session_state.source_content) > 1500 else st.session_state.source_content
            st.code(preview_text, language="markdown")
            
        if st.button("确认无误，继续下一步 👉"):
            go_to_step(2)
            st.rerun()

# --- Step 2 ---
elif st.session_state.current_step == 2:
    st.header("第二步：选择编辑与生成初稿")
    
    editor_role = st.selectbox("选择【编辑】视角", ["发行主编", "研发主编"])
    editor_prompt = st.text_area("✍️ 编辑 Prompt (可自由修改)", value=DEFAULT_PROMPTS[editor_role], height=250)
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔙 返回上一步"):
            go_to_step(1)
            st.rerun()
    with col2:
        if st.button(f"🚀 使用 {selected_model} 生成文章初稿"):
            with st.spinner("编辑正在分析图文并奋笔疾书，请耐心等待..."):
                # 传入图片列表，触发 LLM 的 Vision 分析能力
                st.session_state.draft_article = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=editor_prompt, 
                    user_content=f"以下是提供的素材内容，请结合附带的参考图片一起深度分析：\n\n{st.session_state.source_content}",
                    image_urls=st.session_state.source_images
                )
                go_to_step(3)
                st.rerun()

# --- Step 3 ---
elif st.session_state.current_step == 3:
    st.header("第三步：审稿员审查初稿")
    
    with st.expander("📝 查看当前初稿内容 (鼠标移至右上角可一键复制)", expanded=True):
        st.code(st.session_state.draft_article, language="markdown")
    
    st.divider()
    
    reviewer_prompt = st.text_area("🧐 审稿员 Prompt (可自由修改)", value=DEFAULT_PROMPTS["审稿员"], height=200)
    
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
        if st.button(f"🔍 使用 {selected_model} 开始严格审查"):
            with st.spinner("主编正在核对图表和原文..."):
                anti_hallucination_instruction = """
                \n\n【⚠️ 强制系统级指令：严禁幻觉】：
                你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】以及你所看到的【参考配图】！
                绝对不允许使用你自身的内部知识库进行事实核对。
                如果【初稿】中写了某个数据或结论，只要它在【原始素材文本】或【参考配图】中存在依据，你就必须判定它是正确的。
                绝对不允许以“我不知道”或“原文未提及”为由去否认实际存在的内容！如果确实无中生有，请明确指出。
                """
                
                final_reviewer_system_prompt = reviewer_prompt + anti_hallucination_instruction
                combined_content = f"下面是【原始素材文本】（这是你唯一的真相来源，请结合图片一起审查）：\n{st.session_state.source_content}\n\n================\n\n下面是【初稿】（你需要找茬的内容）：\n{st.session_state.draft_article}"
                
                # 审稿员也需要看到图片才能准确判断初稿对图表的解读是否正确
                st.session_state.review_feedback = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=final_reviewer_system_prompt, 
                    user_content=combined_content,
                    image_urls=st.session_state.source_images
                )
                go_to_step(4)
                st.rerun()

# --- Step 4 ---
elif st.session_state.current_step == 4:
    st.header("第四步：处理审查意见")
    
    st.info("**主编审稿意见 (鼠标移至下方框内右上角可复制)：**")
    st.code(st.session_state.review_feedback, language="markdown")
    
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
            with st.spinner("编辑正在根据主编意见进行修改..."):
                modification_prompt = "你是一位专业的文字编辑。请根据以下【审稿意见】，对【初稿】进行全面修改。直接输出修改后的最终成稿，不要包含任何多余的解释说明。"
                content_to_modify = f"【审稿意见】：\n{st.session_state.review_feedback}\n\n================\n\n【初稿】：\n{st.session_state.draft_article}"
                
                st.session_state.final_article = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=modification_prompt, 
                    user_content=content_to_modify
                    # 修改阶段一般只需文字重组，为节省 Token 不再附带图片
                )
                go_to_step(5)
                st.rerun()

# --- Step 5 ---
elif st.session_state.current_step == 5:
    st.header("第五步：文章定稿与导出")
    
    st.markdown("### 最终成稿 🏆 (鼠标移至右上角可一键复制全文)")
    st.code(st.session_state.final_article, language="markdown")
    
    st.divider()
    
    def create_docx(text):
        doc = Document()
        doc.add_paragraph(text)
        bio = io.BytesIO()
        doc.save(bio)
        return bio.getvalue()
        
    docx_data = create_docx(st.session_state.final_article)
    
    col1, col2, col3 = st.columns(3)
    with col1:
         st.download_button(
            label="📄 导出 Word 文档",
            data=docx_data,
            file_name="公众号文章_定稿.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    with col2:
        if st.button("✈️ 一键推送飞书群"):
            with st.spinner("正在推送到飞书..."):
                success, msg = push_to_feishu(st.session_state.final_article)
                if success:
                    st.success("🎉 已成功推送到飞书群！")
                    st.balloons()
                else:
                    st.error(f"❌ 推送失败：{msg}")
    with col3:
        if st.button("🔄 开启新一篇"):
            for key in st.session_state.keys():
                del st.session_state[key]
            st.rerun()
