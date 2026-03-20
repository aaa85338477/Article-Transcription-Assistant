import streamlit as st
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
import io
from docx import Document

# ==========================================
# 1. 核心抓取函数
# ==========================================
def extract_youtube_transcript(url):
    """提取 YouTube 视频字幕"""
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

        # 获取中英文字幕（优先中文繁简体，其次英文）
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['zh-Hans', 'zh-Hant', 'en'])
        text = "\n".join([item['text'] for item in transcript_list])
        return text, None
    except Exception as e:
        return None, f"YouTube 字幕抓取失败: {str(e)}"

def extract_article_content(url):
    """提取普通网页文章正文"""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return None, "网页下载失败，可能遭遇反爬或链接无效"
        
        text = trafilatura.extract(downloaded)
        if text:
            return text, None
        else:
            return None, "未能从网页中提取到有效正文"
    except Exception as e:
        return None, f"文章抓取失败: {str(e)}"

def get_content_from_url(url):
    """路由函数：根据 URL 分发"""
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
    
    # 存储各阶段的数据
    if 'source_url' not in st.session_state:
        st.session_state.source_url = ""
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
st.title("🕹️ 公众号文章生成助手 - 多智能体工作流")

# --- Step 1: 录入素材 ---
if st.session_state.current_step == 1:
    st.header("第一步：输入素材源")
    url_input = st.text_input("🔗 输入文章或 YouTube 视频链接", value=st.session_state.source_url)
    
    if st.button("开始提取内容"):
        if url_input:
            with st.spinner("正在努力抓取并解析内容，请稍候..."):
                content, error_msg = get_content_from_url(url_input)
                
                if content:
                    st.session_state.source_url = url_input
                    st.session_state.source_content = content
                    st.session_state.extraction_success = True
                    st.success("🎉 内容提取成功！")
                else:
                    st.session_state.extraction_success = False
                    st.error(f"❌ 提取失败：{error_msg}")
        else:
            st.warning("请先输入链接！")
            
    # 如果提取成功，显示预览和下一步按钮 (避免 Streamlit 按钮嵌套问题)
    if st.session_state.extraction_success:
        with st.expander("预览抓取到的原文", expanded=False):
            preview_text = st.session_state.source_content[:1000] + "\n\n......(已省略后续内容)" if len(st.session_state.source_content) > 1000 else st.session_state.source_content
            st.text(preview_text)
            
        if st.button("确认无误，继续下一步 👉"):
            go_to_step(2)
            st.rerun()

# --- Step 2: 设定编辑并生成初稿 ---
elif st.session_state.current_step == 2:
    st.header("第二步：选择编辑与生成初稿")
    
    editor_role = st.selectbox("选择【编辑】视角", ["资深游戏制作人", "海外发行总监", "核心主策划"])
    
    default_prompt = f"你现在是一位{editor_role}。请以你的视角对以下素材进行拆解和转录，注意要有前后铺垫，娓娓道来，展现行业格局，不要受限于死板的格式。"
    editor_prompt = st.text_area("✍️ 编辑 Prompt (可自由修改)", value=default_prompt, height=100)
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔙 返回上一步"):
            go_to_step(1)
            st.rerun()
    with col2:
        if st.button("🚀 生成文章初稿"):
            with st.spinner("编辑正在奋笔疾书..."):
                # TODO: 接入 LLM API，传入 editor_prompt 和 st.session_state.source_content
                # response = call_llm(system_prompt=editor_prompt, user_content=st.session_state.source_content)
                
                # 模拟 LLM 输出
                st.session_state.draft_article = f"【系统提示：这里是将由大模型生成的初稿。当前使用的身份是：{editor_role}。内容基于提取的素材...】" 
                go_to_step(3)
                st.rerun()

# --- Step 3: 审稿员审查 ---
elif st.session_state.current_step == 3:
    st.header("第三步：审稿员审查初稿")
    
    with st.expander("📝 查看当前初稿内容", expanded=True):
        st.write(st.session_state.draft_article)
    
    st.divider()
    
    reviewer_prompt = st.text_area("🧐 审稿员 Prompt (可自由修改)", 
                                   value="你是一个严苛的公众号主编。请对比【原始素材】和【编辑初稿】，指出初稿中逻辑不顺畅、缺乏深度或丢失关键信息的部分，并给出明确修改建议。", 
                                   height=100)
    
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
        if st.button("🔍 开始严格审查"):
            with st.spinner("主编正在审阅..."):
                # TODO: 接入 LLM API 进行对比审查
                st.session_state.review_feedback = "【系统提示：这是主编给出的修改意见：1. 开篇铺垫不足；2. 缺少对核心机制的深入拆解...】"
                go_to_step(4)
                st.rerun()

# --- Step 4: 处理审查意见 ---
elif st.session_state.current_step == 4:
    st.header("第四步：处理审查意见")
    
    st.info(f"**审稿意见：**\n\n{st.session_state.review_feedback}")
    
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
        if st.button("✨ 接受意见，修改文章"):
            with st.spinner("编辑正在根据意见修改..."):
                # TODO: 将初稿和意见发给 LLM 进行二创
                st.session_state.final_article = "【系统提示：这是根据审稿意见完善后的最终成稿。】"
                go_to_step(5)
                st.rerun()

# --- Step 5: 最终输出与导出 ---
elif st.session_state.current_step == 5:
    st.header("第五步：文章定稿与导出")
    
    st.markdown("### 最终成稿 🏆")
    st.write(st.session_state.final_article)
    
    st.divider()
    
    # 构建 Word 文档内存流
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
            # TODO: 编写 requests 逻辑推送到飞书 Webhook
            st.success("已成功推送到飞书！")
    with col3:
        if st.button("🔄 开启新一篇"):
            for key in st.session_state.keys():
                del st.session_state[key]
            st.rerun()
