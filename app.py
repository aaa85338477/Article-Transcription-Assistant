import streamlit as st
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
import io
import os
from docx import Document
from openai import OpenAI
import requests
import json
from bs4 import BeautifulSoup
import re

# ==========================================
# 0. 提示词持久化管理 (JSON 存储)
# ==========================================
PROMPTS_FILE = "prompts.json"

def load_prompts():
    """读取本地的 Prompt 配置文件，如果没有则创建默认模板"""
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
            
    default_data = {
        "editors": {
            "发行主编": "你是一位资深的海外发行主编。请深度分析素材，重点关注买量、ROI与发行策略...",
            "研发主编": "你是一位硬核游戏制作人。请深度拆解素材，重点关注核心循环、系统设计与工业化管线...",
            "游戏快讯编辑": "你是一位敏锐的游戏媒体编辑。请将素材提炼为通俗易懂、具有爆点的新闻快讯...",
            "客观转录编辑": "你是一位专业速记员。请剥离所有主观情绪，将素材客观、结构化地转录并总结..."
        },
        "reviewer": "你是一个极其严苛的资深游戏媒体主编兼风控专家。请严格核查初稿中的事实错误、逻辑漏洞及AI幻觉..."
    }
    save_prompts(default_data)
    return default_data

def save_prompts(data):
    """保存 Prompt 数据到本地 JSON 文件"""
    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# ==========================================
# 1. API 与外部推送函数
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

def push_to_feishu(article_text, script_text=None):
    webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/a0f50778-0dd2-4963-a0b2-0c7b68e113d8"
    headers = {"Content-Type": "application/json"}
    
    # 动态组装推送内容
    if script_text:
        text_content = f"📣 【公众号文章定稿通知】\n\n{article_text}\n\n================\n\n🎬【短视频 AI 分镜脚本】\n\n{script_text}"
    else:
        text_content = f"📣 【公众号文章定稿通知】\n\n{article_text}"
        
    payload = {
        "msg_type": "text",
        "content": {
            "text": text_content
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
# 2. 核心抓取函数
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

        target_langs = ['zh-Hans', 'zh-Hant', 'zh-CN', 'zh-TW', 'zh', 'en']
        try:
            if hasattr(YouTubeTranscriptApi, 'get_transcript'):
                transcript_fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=target_langs)
            else:
                ytt_api = YouTubeTranscriptApi()
                if hasattr(ytt_api, 'list'):
                    transcript_list = ytt_api.list(video_id)
                elif hasattr(ytt_api, 'list_transcripts'):
                    transcript_list = ytt_api.list_transcripts(video_id)
                else:
                    transcript_fetched = ytt_api.fetch(video_id)
                    transcript_list = None
                    
                if transcript_list is not None:
                    try:
                        transcript = transcript_list.find_transcript(target_langs)
                    except Exception:
                        transcript = list(transcript_list)[0]
                    transcript_fetched = transcript.fetch()

            texts = []
            for item in transcript_fetched:
                if isinstance(item, dict) and 'text' in item:
                    texts.append(item['text'])
                elif hasattr(item, 'text'):
                    texts.append(item.text)
                else:
                    texts.append(str(item))
                    
            text = "\n".join(texts)
            return text, [], None
            
        except Exception as inner_e:
            error_str = str(inner_e).lower()
            if "block" in error_str or "proxy" in error_str or "could not retrieve a transcript" in error_str:
                try:
                    jina_url = f"https://r.jina.ai/{url}"
                    headers = {"Accept": "text/plain"}
                    response = requests.get(jina_url, headers=headers, timeout=20)
                    response.raise_for_status()
                    
                    text = response.text
                    if text and len(text) > 50:
                        return text, [], None
                    else:
                        return None, [], "原生API被封锁，且备用穿透解析器未返回有效文本。"
                except Exception as jina_e:
                    return None, [], f"YouTube 提取失败: 原生报错({str(inner_e)[:30]}...) | 备用报错({str(jina_e)[:30]}...)"
            else:
                raise inner_e

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
        
        noise_tags = ['aside', 'nav', 'footer', 'header']
        for noise in soup.find_all(noise_tags):
            noise.decompose()
            
        noise_keywords = ['author', 'related', 'comment', 'share', 'widget', 'sidebar', 'ad-container', 'popup', 'newsletter']
        for noise in soup.find_all(attrs={'class': lambda c: c and any(k in str(c).lower() for k in noise_keywords)}):
            noise.decompose()
        for noise in soup.find_all(attrs={'id': lambda i: i and any(k in str(i).lower() for k in noise_keywords)}):
            noise.decompose()
            
        main_content = soup.find('article') or soup.find('main') or soup.find(class_=lambda c: c and 'content' in str(c).lower()) or soup

        images = []
        for img in main_content.find_all('img'):
            try:
                html_w = int(img.get('width', 0))
                html_h = int(img.get('height', 0))
            except:
                html_w, html_h = 0, 0
                
            src = None
            srcset = img.get('data-srcset') or img.get('srcset')
            if srcset:
                sources = []
                for s in srcset.split(','):
                    parts = s.strip().split()
                    if len(parts) == 2 and parts[1].endswith('w') and parts[1][:-1].isdigit():
                        sources.append((parts[0], int(parts[1][:-1])))
                if sources:
                    sources.sort(key=lambda x: x[1], reverse=True)
                    src = sources[0][0]
                    
            if not src:
                for attr in ['data-original', 'data-lazy-src', 'data-src', 'src']:
                    val = img.get(attr)
                    if val:
                        if isinstance(val, list): val = val[0]
                        val = str(val).strip()
                        if not val.startswith('data:image'):
                            src = val
                            break
                            
            if not src: continue
                
            if src.startswith('//'): src = 'https:' + src
            elif src.startswith('/'):
                parsed_url = urlparse(url)
                src = f"{parsed_url.scheme}://{parsed_url.netloc}{src}"
            elif not src.startswith('http'): continue
                
            src_lower = src.lower()
            junk_keywords = ['icon', 'spinner', 'svg', 'gif', 'button', 'tracker', 'avatar']
            if any(junk in src_lower for junk in junk_keywords):
                continue
                
            if html_w >= 300 or html_h >= 300:
                pass 
            else:
                match = re.search(r'-(\d{2,3})x(\d{2,3})\.(jpg|jpeg|png|webp)', src_lower)
                if match:
                    mw, mh = int(match.group(1)), int(match.group(2))
                    if mw <= 300 or mh <= 300:
                        continue
                        
            if src not in images:
                images.append(src)
                if len(images) >= 8:
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

def get_content_from_url(url):
    if "youtube.com" in url or "youtu.be" in url:
        return extract_youtube_transcript(url)
    else:
        return extract_article_content(url)

# ==========================================
# 3. 状态管理初始化
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
    if 'spoken_script' not in st.session_state:
        st.session_state.spoken_script = ""

init_state()

def go_to_step(step):
    st.session_state.current_step = step

def get_script_sys_prompt(duration_str):
    duration_map = {
        "1分钟": "200 - 250",
        "3分钟": "600 - 700",
        "5分钟": "1000 - 1200",
        "8分钟": "1600 - 1900"
    }
    target_words = duration_map.get(duration_str, "1000 - 1200")
    
    return f"""你是一位资深的AI视频流工业化编导。请将提供给你的长篇深度文章，浓缩提炼成一份可直接输入给【剪映AI】等工具解析的【{duration_str}口播与分镜脚本】。

【强制执行规则】：
1. 语速与篇幅：正常人中文语速约为220字/分钟，因此总的口播旁白字数必须严格控制在 {target_words} 字左右（对应约{duration_str}的短视频长度）。
2. 零废话输出：因为你的输出将被下一个自动化代码节点读取，请【绝对不要】在开头或结尾输出“好的，这是为您生成的脚本”等任何人类视角的寒暄语句，直接输出 Markdown 结构！
3. 适配剪映AI：画面Prompt必须全部使用【纯中文】描述，包含明确的画面主体、场景细节和镜头动作，以便国内视频AI引擎精准生图/视频。
4. 结构化呈现：必须严格按照以下 Markdown 列表格式输出每一个镜头（Scene），绝不能混用格式。

【标准输出格式范例】：
### Scene 01 (0s - 5s)
* 🗣️ **口播旁白**: "就在昨天，游戏圈又爆出了一个令人震惊的超级大瓜！" (要求：纯口语化，适合短视频抓人眼球)
* 🎬 **画面Prompt**: "电影级中景镜头，一个震惊的年轻玩家坐在昏暗的房间里看着发光的电脑屏幕，屏幕上显示着数据代码，动态光影，逼真质感，平移运镜" (要求：全中文撰写，详细描述画面内容)
* ✨ **视觉特效/字幕**: 屏幕突然亮起，居中显示大字特效“超级大瓜”。

### Scene 02 (5s - 12s)
* 🗣️ **口播旁白**: "..."
* 🎬 **画面Prompt**: "..."
* ✨ **视觉特效/字幕**: "..."
"""

# ==========================================
# 4. 页面与工作流渲染
# ==========================================
st.set_page_config(page_title="公众号文章生成助手", page_icon="🕹️", layout="wide")

prompts_data = load_prompts()

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
    st.header("🎬 视频分镜设置")
    
    # --- 新增：脚本生成总开关 ---
    enable_script = st.toggle("启用伴生【短视频分镜脚本】", value=False)
    
    script_duration = st.selectbox(
        "⏱️ 设定分镜脚本目标时长", 
        ["1分钟", "3分钟", "5分钟", "8分钟"], 
        index=2, # 默认选中 5分钟
        disabled=not enable_script # 如果没开启总开关，此选项变灰不可选
    )
    
    st.markdown("---")
    st.header("🗂️ 提示词管理中心")
    with st.expander("📝 角色与人设配置", expanded=False):
        tab1, tab2 = st.tabs(["✍️ 编辑人设", "🧐 审稿员人设"])
        
        with tab1:
            action = st.radio("操作类型", ["编辑现有角色", "新增角色", "删除角色"], horizontal=True)
            if action == "编辑现有角色":
                edit_role = st.selectbox("选择编辑", list(prompts_data["editors"].keys()))
                if edit_role:
                    new_prompt = st.text_area("系统 Prompt", value=prompts_data["editors"][edit_role], height=200)
                    if st.button("💾 保存修改", key="save_edit"):
                        prompts_data["editors"][edit_role] = new_prompt
                        save_prompts(prompts_data)
                        st.success(f"已保存【{edit_role}】的修改！")
                        st.rerun()
            elif action == "新增角色":
                new_role_name = st.text_input("新角色名称 (如：独立游戏分析师)")
                new_role_prompt = st.text_area("新角色 Prompt", height=200)
                if st.button("➕ 确认新增"):
                    if new_role_name and new_role_name not in prompts_data["editors"]:
                        prompts_data["editors"][new_role_name] = new_role_prompt
                        save_prompts(prompts_data)
                        st.success(f"已成功添加角色：【{new_role_name}】！")
                        st.rerun()
                    else:
                        st.error("角色名不能为空，或该角色已存在！")
            elif action == "删除角色":
                del_role = st.selectbox("选择要删除的编辑", list(prompts_data["editors"].keys()))
                if st.button("🗑️ 确认删除", type="primary"):
                    if len(prompts_data["editors"]) > 1:
                        del prompts_data["editors"][del_role]
                        save_prompts(prompts_data)
                        st.success(f"已删除【{del_role}】！")
                        st.rerun()
                    else:
                        st.error("操作失败：必须至少保留一个编辑角色！")
                        
        with tab2:
            new_reviewer_prompt = st.text_area("主编/审稿员系统指令", value=prompts_data["reviewer"], height=300)
            if st.button("💾 保存审稿员设置"):
                prompts_data["reviewer"] = new_reviewer_prompt
                save_prompts(prompts_data)
                st.success("已更新审稿员人设！")
                st.rerun()

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
            with st.spinner("启动全息解析引擎，智能获取素材..."):
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
                        if len(extracted_imgs) > 0:
                            st.success(f"🎉 成功提取素材内容！共为您提取到 {len(extracted_imgs)} 张核心配图。")
                        else:
                            st.success("🎉 成功提取素材内容！(该文章无有效配图，将走纯文本模式)")
                else:
                    st.session_state.extraction_success = False
                    st.error("❌ 所有链接提取均失败，请检查链接或网络状态。\n" + "\n".join(errors))
            
    if st.session_state.extraction_success:
        with st.expander("预览抓取到的原文与图表", expanded=False):
            if st.session_state.source_images:
                st.markdown("🖼️ **提取到的核心配图：**")
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
    
    editor_options = list(prompts_data["editors"].keys())
    if 'selected_role' not in st.session_state or st.session_state.selected_role not in editor_options:
        st.session_state.selected_role = editor_options[0]
        
    default_idx = editor_options.index(st.session_state.selected_role)
    
    editor_role = st.selectbox("选择【编辑】视角", editor_options, index=default_idx)
    st.session_state.selected_role = editor_role 
    
    editor_prompt = st.text_area(
        "✍️ 编辑 Prompt (支持临时微调)", 
        value=prompts_data["editors"][editor_role], 
        height=250,
        key=f"prompt_text_{editor_role}"
    )
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔙 返回上一步"):
            go_to_step(1)
            st.rerun()
    with col2:
        if st.button(f"🚀 使用 {selected_model} 生成文章初稿"):
            with st.spinner("编辑正在分析素材并奋笔疾书，请耐心等待..."):
                if st.session_state.source_images:
                    editor_user_content = f"以下是提供的素材内容，请结合附带的参考图片一起深度分析：\n\n{st.session_state.source_content}"
                else:
                    editor_user_content = f"以下是提供的素材内容，请根据纯文本进行深度分析：\n\n{st.session_state.source_content}"
                
                st.session_state.draft_article = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=editor_prompt, 
                    user_content=editor_user_content,
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
    
    reviewer_prompt = st.text_area("🧐 审稿员 Prompt (支持临时微调)", value=prompts_data["reviewer"], height=200)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 感觉不对，重写初稿"):
            go_to_step(2)
            st.rerun()
    with col2:
        if st.button("⏭️ 完美，跳过审查直接定稿"):
            spinner_msg = f"正在生成最终定稿与【{script_duration}口播及分镜脚本】..." if enable_script else "正在生成最终定稿..."
            with st.spinner(spinner_msg):
                st.session_state.final_article = st.session_state.draft_article
                
                # 根据开关决定是否提取分镜脚本
                if enable_script:
                    script_sys_prompt = get_script_sys_prompt(script_duration)
                    st.session_state.spoken_script = call_llm(
                        api_key=api_key, base_url=current_base_url, model_name=selected_model,
                        system_prompt=script_sys_prompt,
                        user_content=f"【请将以下深度文章转化为供剪映AI解析的{script_duration}口播与分镜脚本】：\n\n{st.session_state.final_article}"
                    )
                else:
                    st.session_state.spoken_script = ""
                    
                go_to_step(5)
                st.rerun()
    with col3:
        if st.button(f"🔍 使用 {selected_model} 开始严格审查"):
            with st.spinner("主编正在核对原文..."):
                if st.session_state.source_images:
                    anti_hallucination_instruction = """\n\n【⚠️ 强制系统级指令：严禁幻觉】：
                    你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】以及你所看到的【参考配图】！绝对不允许使用自身知识库进行事实核对。"""
                    combined_content = f"下面是【原始素材文本】（这是唯一的真相来源）：\n{st.session_state.source_content}\n\n================\n下面是【初稿】：\n{st.session_state.draft_article}"
                else:
                    anti_hallucination_instruction = """\n\n【⚠️ 强制系统级指令：严禁幻觉】：
                    你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】！绝对不允许使用自身知识库进行事实核对。"""
                    combined_content = f"下面是【原始素材文本】（这是唯一的真相来源）：\n{st.session_state.source_content}\n\n================\n下面是【初稿】：\n{st.session_state.draft_article}"
                
                final_reviewer_system_prompt = reviewer_prompt + anti_hallucination_instruction
                
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
            spinner_msg = f"正在生成最终定稿与【{script_duration}口播及分镜脚本】..." if enable_script else "正在生成最终定稿..."
            with st.spinner(spinner_msg):
                st.session_state.final_article = st.session_state.draft_article
                
                if enable_script:
                    script_sys_prompt = get_script_sys_prompt(script_duration)
                    st.session_state.spoken_script = call_llm(
                        api_key=api_key, base_url=current_base_url, model_name=selected_model,
                        system_prompt=script_sys_prompt,
                        user_content=f"【请将以下深度文章转化为供剪映AI解析的{script_duration}口播与分镜脚本】：\n\n{st.session_state.final_article}"
                    )
                else:
                    st.session_state.spoken_script = ""
                    
                go_to_step(5)
                st.rerun()
    with col3:
        if st.button(f"✨ 使用 {selected_model} 接受意见并修改文章"):
            spinner_msg = f"编辑正在修改文章，并生成【{script_duration}口播及分镜脚本】..." if enable_script else "编辑正在根据主编意见修改文章..."
            with st.spinner(spinner_msg):
                modification_prompt = "你是一位专业的文字编辑。请根据以下【审稿意见】，对【初稿】进行全面修改。直接输出修改后的最终成稿，不要包含任何多余的解释说明。"
                content_to_modify = f"【审稿意见】：\n{st.session_state.review_feedback}\n\n================\n\n【初稿】：\n{st.session_state.draft_article}"
                
                st.session_state.final_article = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=modification_prompt, 
                    user_content=content_to_modify
                )
                
                if enable_script:
                    script_sys_prompt = get_script_sys_prompt(script_duration)
                    st.session_state.spoken_script = call_llm(
                        api_key=api_key, base_url=current_base_url, model_name=selected_model,
                        system_prompt=script_sys_prompt,
                        user_content=f"【请将以下深度文章转化为供剪映AI解析的{script_duration}口播与分镜脚本】：\n\n{st.session_state.final_article}"
                    )
                else:
                    st.session_state.spoken_script = ""
                
                go_to_step(5)
                st.rerun()

# --- Step 5 ---
elif st.session_state.current_step == 5:
    st.header("第五步：文章定稿与多端分发")
    
    st.markdown("### 🏆 最终成稿 (深度图文)")
    st.code(st.session_state.final_article, language="markdown")
    
    # 根据是否生成了脚本，动态展示UI
    if st.session_state.spoken_script:
        st.divider()
        st.markdown(f"### 🎬 🎙️ {script_duration} 剪映AI 分镜脚本")
        st.code(st.session_state.spoken_script, language="markdown")
    
    st.divider()
    
    def create_docx(article_text, script_text=None):
        doc = Document()
        doc.add_heading('【最终成稿】', level=1)
        doc.add_paragraph(article_text)
        
        if script_text:
            doc.add_heading('【短视频 AI 分镜脚本】', level=1)
            doc.add_paragraph(script_text)
        
        bio = io.BytesIO()
        doc.save(bio)
        return bio.getvalue()
        
    docx_data = create_docx(st.session_state.final_article, st.session_state.spoken_script if st.session_state.spoken_script else None)
    
    col1, col2, col3 = st.columns(3)
    with col1:
         st.download_button(
            label="📄 导出 Word 文档" if not st.session_state.spoken_script else "📄 导出包含双版本的 Word",
            data=docx_data,
            file_name="公众号文章_定稿.docx" if not st.session_state.spoken_script else "公众号与AI短视频脚本_定稿.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    with col2:
        if st.button("✈️ 一键打包推送飞书群"):
            with st.spinner("正在推送到飞书..."):
                success, msg = push_to_feishu(st.session_state.final_article, st.session_state.spoken_script if st.session_state.spoken_script else None)
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
