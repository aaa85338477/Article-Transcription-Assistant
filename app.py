import streamlit as st
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
import io
from pathlib import Path
import os
import html as html_lib
from docx import Document
from openai import OpenAI
import requests
import json
from bs4 import BeautifulSoup
import re
import streamlit.components.v1 as components
import base64
from datetime import datetime
try:
    import pandas as pd
except Exception:
    pd = None


# ==========================================
# 0. 提示词与草稿持久化管理 (JSON 存储)
# ==========================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))

PROMPTS_PATH_CANDIDATES = [
    os.path.join(APP_DIR, "prompts.json"),
    os.path.join(APP_DIR, "prompt.json"),
    os.path.join(APP_DIR, "Prompt.json"),
    "prompts.json",
    "prompt.json",
    "Prompt.json",
]

PROMPTS_FILE = PROMPTS_PATH_CANDIDATES[0]
DRAFT_FILE = os.path.join(APP_DIR, "draft_state.json")
PROMPTS_LOAD_REPORT = ""


def get_prompt_file_candidates():
    candidates = []
    env_prompt_path = os.environ.get("PROMPTS_FILE", "").strip()
    if env_prompt_path:
        candidates.append(env_prompt_path)

    candidates.extend(PROMPTS_PATH_CANDIDATES)

    roots = [
        APP_DIR,
        os.getcwd(),
        os.path.dirname(APP_DIR),
        os.path.dirname(os.path.dirname(APP_DIR)),
    ]
    for root in roots:
        if not root:
            continue
        for name in ("prompts.json", "prompt.json", "Prompt.json"):
            candidates.append(os.path.join(root, name))

    try:
        app_depth = APP_DIR.rstrip(os.sep).count(os.sep)
        for walk_root, _, files in os.walk(APP_DIR):
            current_depth = walk_root.rstrip(os.sep).count(os.sep) - app_depth
            if current_depth > 2:
                continue
            for file_name in files:
                if file_name.lower() in {"prompts.json", "prompt.json"}:
                    candidates.append(os.path.join(walk_root, file_name))
    except Exception:
        pass

    unique_candidates = []
    seen = set()
    for path in candidates:
        normalized = os.path.abspath(path)
        if normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(normalized)
    return unique_candidates

DEFAULT_GLOBAL_PROMPT = """【全局强制写作规范（最高优先级）】
1. 切断 AI 八股句式：坚决禁用“不是……而是”、“不仅……甚至”、“总而言之”、“在这个瞬息万变的时代”、“正如前文所述”等强烈的机械感过渡句和排比句。
2. 禁用伪高级“黑话”：严禁滥用带双引号的互联网/营销词汇（如“赋能”、“底层逻辑”、“打法”、“组合拳”、“降维打击”）。遇到专业概念，请用人话直白解释，不要故作高深。
3. 拒绝强行升华：文章结尾禁止进行“爹味说教”或喊口号式的价值升华，客观给出冷酷的结论或留白即可。
4. 打破匀速节奏：多用短句！避免一口气读不完的复杂长句。允许出现少量口语化的标点停顿，模仿人类写稿时真实的“呼吸感”和偶尔的“毒舌”感。"""

def load_prompts():
    default_data = {
        "editors": {
            "发行主编": "你是一位资深的海外发行主编。请深度分析素材，重点关注买量、ROI与发行策略...",
            "研发主编": "你是一位硬核游戏制作人。请深度拆解素材，重点关注核心循环、系统设计与工业化管线...",
            "游戏快讯编辑": "你是一位敏锐的游戏媒体编辑。请将素材提炼为通俗易懂、具有爆点的新闻快讯...",
            "客观转录编辑": "你是一位专业速记员。请剥离所有主观情绪，将素材客观、结构化地转录并总结..."
        },
        "reviewer": "你是一个极其严苛的资深游戏媒体主编兼风控专家。请严格核查初稿中的事实错误、逻辑漏洞及AI幻觉...",
        "global_instruction": DEFAULT_GLOBAL_PROMPT
    }

    global PROMPTS_FILE
    global PROMPTS_LOAD_REPORT

    parse_errors = []
    for candidate in get_prompt_file_candidates():
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            editors = data.get("editors", {}) if isinstance(data, dict) else {}
            if not isinstance(editors, dict) or len(editors) == 0:
                raise ValueError("缺少 editors 配置或 editors 为空")

            if "global_instruction" not in data:
                data["global_instruction"] = DEFAULT_GLOBAL_PROMPT

            PROMPTS_FILE = candidate
            PROMPTS_LOAD_REPORT = f"loaded:{PROMPTS_FILE}:editors={len(editors)}"
            return data
        except Exception as e:
            parse_errors.append(f"{candidate} -> {str(e)}")

    PROMPTS_FILE = PROMPTS_PATH_CANDIDATES[0]
    PROMPTS_LOAD_REPORT = "fallback_default"
    if parse_errors:
        PROMPTS_LOAD_REPORT += " | " + " || ".join(parse_errors[:3])

    save_prompts(default_data)
    return default_data

def save_prompts(data):
    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
def save_draft():
    keys_to_save = [
        'current_step', 'article_url', 'video_url', 'source_content',
        'source_images', 'extraction_success', 'draft_article',
        'review_feedback', 'modified_article', 'final_article', 'highlighted_article', 'spoken_script',
        'chat_history', 'image_keywords', 'selected_role', 'target_article_words',
        'source_images_all', 'selected_source_image_ids', 'article_versions',
        'active_article_version_id', 'de_ai_model', 'de_ai_temperature',
        'de_ai_prompt_template'
    ]
    draft_data = {k: st.session_state[k] for k in keys_to_save if k in st.session_state}
    try:
        with open(DRAFT_FILE, "w", encoding="utf-8") as f:
            json.dump(draft_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"草稿保存失败: {e}")

def load_draft():
    if os.path.exists(DRAFT_FILE):
        try:
            with open(DRAFT_FILE, "r", encoding="utf-8") as f:
                draft_data = json.load(f)
            for k, v in draft_data.items():
                st.session_state[k] = v
            return True
        except Exception as e:
            print(f"草稿读取失败: {e}")
            return False
    return False

def clear_draft():
    if os.path.exists(DRAFT_FILE):
        try:
            os.remove(DRAFT_FILE)
        except:
            pass


def sanitize_editor_prompt(prompt_text):
    if not isinstance(prompt_text, str):
        return ""
    cleaned_lines = []
    for line in prompt_text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue

        has_digit = any(ch.isdigit() for ch in stripped)
        should_remove = (
            "\u76ee\u6807\u5b57\u6570" in stripped
            or "\u5b57\u6570\u8981\u6c42" in stripped
            or "\u603b\u5b57\u6570" in stripped
            or ("\u63a7\u5236\u5728" in stripped and "\u5b57" in stripped and has_digit)
            or ("\u4e0d\u8d85" in stripped and "\u5b57" in stripped and has_digit)
            or ("\u4e0d\u8d85\u8fc7" in stripped and "\u5b57" in stripped and has_digit)
            or ("\u4e25\u7981\u8d85\u8fc7" in stripped and "\u5b57" in stripped and has_digit)
            or ("\u6574\u4f53\u4e0d\u8d85" in stripped and "\u5b57" in stripped and has_digit)
        )
        if should_remove:
            continue
        cleaned_lines.append(line)
    cleaned_prompt = "\n".join(cleaned_lines)
    while "\n\n\n" in cleaned_prompt:
        cleaned_prompt = cleaned_prompt.replace("\n\n\n", "\n\n")
    return cleaned_prompt.strip()

def get_target_article_words():
    raw_value = st.session_state.get("target_article_words", 1500)
    try:
        value = int(raw_value)
    except Exception:
        value = 1500
    return max(200, min(5000, value))

def sync_target_article_words():
    raw_value = st.session_state.get("target_article_words_slider", get_target_article_words())
    try:
        value = int(raw_value)
    except Exception:
        value = 1500
    st.session_state.target_article_words = max(200, min(5000, value))
    save_draft()

def sync_selected_role():
    selected_role = st.session_state.get("selected_role_widget", "")
    if isinstance(selected_role, str) and selected_role.strip():
        st.session_state.selected_role = selected_role
        save_draft()



def build_target_length_instruction():
    target_words = get_target_article_words()
    return (
        f"【本轮全局字数要求（最高优先级）】\n"
        f"本次目标字数：约 {target_words} 字（允许 ±10% 浮动）。\n"
        "若角色提示中存在固定字数要求，请忽略并以本轮全局目标为准。"
    )


def build_editor_system_prompt(editor_prompt, global_instruction):
    prompt_parts = [
        sanitize_editor_prompt(editor_prompt),
        build_target_length_instruction(),
        global_instruction.strip() if isinstance(global_instruction, str) else ""
    ]
    return "\n\n".join([part for part in prompt_parts if part])


def build_modification_system_prompt(global_instruction):
    base_prompt = "你是一位专业文字编辑。请根据审稿意见全面修改初稿，直接输出最终成稿，不要额外解释。"
    prompt_parts = [
        base_prompt,
        build_target_length_instruction(),
        global_instruction.strip() if isinstance(global_instruction, str) else ""
    ]
    return "\n\n".join([part for part in prompt_parts if part])

DE_AI_MODELS = ["deepseek-v3-1-terminus", "deepseek-v3-2-exp", "qwen3.5-plus", "glm-5"]
ROLE_AUDIENCE_MAP = {
    "发行主编": "游戏行业从业者",
    "研发主编": "游戏圈同行和硬核玩家",
    "游戏快讯编辑": "行业从业者",
    "客观转录编辑": "公众读者",
    "游戏行业评论人": "游戏行业从业者",
}
ROLE_JARGON_MAP = {
    "发行主编": "ROI、LTV、买量",
    "研发主编": "核心循环、技术债、管线",
    "游戏快讯编辑": "版号、上线档期、发行节奏",
    "客观转录编辑": "留存、变现、本地化",
    "游戏行业评论人": "ROI、洗量、跑路",
}
ROLE_TONE_MAP = {
    "发行主编": "冷酷清醒",
    "研发主编": "毒舌但专业",
    "游戏快讯编辑": "克制冷静",
    "客观转录编辑": "娓娓道来",
    "游戏行业评论人": "一针见血",
}


def infer_role_persona(role_name, editor_prompt):
    prompt_text = (editor_prompt or "").strip()
    first_line = prompt_text.splitlines()[0].strip() if prompt_text else ""
    if first_line.startswith("# Role:"):
        persona = first_line.split(":", 1)[1].strip()
        if persona:
            return persona
    return role_name or "10年经验的资深行业作者"


def infer_article_topic(source_content):
    clean_text = " ".join((source_content or "").split())
    if not clean_text:
        return "当前游戏行业主题"
    return clean_text[:40] + ("..." if len(clean_text) > 40 else "")


def build_de_ai_prompt_template(role_name, editor_prompt, source_content):
    persona = infer_role_persona(role_name, editor_prompt)
    topic = infer_article_topic(source_content)
    audience = ROLE_AUDIENCE_MAP.get(role_name, "从业者")
    jargon = ROLE_JARGON_MAP.get(role_name, "ROI、买量、留存")
    tone = ROLE_TONE_MAP.get(role_name, "冷酷清醒")
    return f"""# Role: {persona}

# Context
我有一篇关于【{topic}】的草稿。这篇文章的核心骨架和信息增量是好的，但目前的文本带有严重的“AI 生成味”：结构八股、过渡词生硬、用词存在假大空的翻译腔，缺乏真正【{audience}】在交流时的真实感和血肉感。

# Task
请你完全代入【填写上述设定的 Role】的视角，对以下【草稿原文】进行彻底的去 AI 化重写。
你需要保留原文的全部核心信息、数据和逻辑推演，但必须完全摧毁现有的文本外壳，用人类专家的自然口吻重新表达。

# 🚫 核心约束：反 AI 审查清单（优先级最高，必须严格遵守）
1. 词汇黑名单：绝对禁止使用“毫无疑问”、“不仅...而且”、“在这个充满...的时代”、“一场名为...的”、“总而言之”、“不可否认”、“至关重要”、“双刃剑”、“随着...的发展”、“综上所述”等AI高频陈词滥调。
2. 结构粉碎：禁止使用“一、二、三”或“首先、其次、最后”等死板的枚举结构推进文章。必须使用情绪递进、场景带入或逻辑转折来做段落过渡。
3. 拒绝“绝对客观”：放弃 AI 惯用的“虽然A有缺点，但B也有不足”的端水句式。你的语气要有主观色彩、有锋芒，甚至可以带点行业人的自嘲或无奈。
4. 节奏控制：禁止全篇使用长度相似的陈述句。强制要求长短句结合。情绪宣泄和抛出观点时用短句（甚至单句成段），拆解复杂逻辑时用长句。
5. 行业语境注入：自然地（切忌堆砌）使用【{jargon}】等词汇，营造“圈内人对话”的真实感。

# Style & Tone
* 语气词：{tone}
* 排版格式：适合移动端阅读，多留白，避免大段密集的文字墙。

# Output Protocol
请严格按照以下格式输出，顺序不能变：
【纯净定稿】
这里输出不含任何HTML、颜色、解释或额外标题的最终成文。

【高亮阅读版】
这里输出基于同一份定稿制作的阅读增强版，只允许做包裹式标注，不能改写信息或增删内容。
高亮规则只有两类：
- 学习点 / 方法论 / 正向启发：用 <strong><span class="highlight-positive">...</span></strong>
- 避坑点 / 风险提醒 / 反例警示：用 <strong><span class="highlight-risk">...</span></strong>
额外约束：
- 只允许使用 <p>、<strong>、<span class="highlight-positive">、<span class="highlight-risk"> 这四类标签
- 禁止输出任何其他HTML标签、内联样式、脚本、解释性前言
- 每段最多 1-2 处高亮
- 不要整段上色
- 没有明显价值就不要高亮

# 【草稿原文】
[在此粘贴草稿内容]"""


def parse_de_ai_dual_output(response_text):
    clean_text = (response_text or "").strip()
    if not clean_text:
        return "", ""

    pure_marker = "【纯净定稿】"
    highlight_marker = "【高亮阅读版】"

    if pure_marker not in clean_text:
        return clean_text, ""

    pure_part = clean_text.split(pure_marker, 1)[1]
    if highlight_marker in pure_part:
        pure_text, highlighted_text = pure_part.split(highlight_marker, 1)
        return pure_text.strip(), highlighted_text.strip()

    return pure_part.strip(), ""


def sanitize_highlighted_article(html_text):
    clean_text = (html_text or "").strip()
    if not clean_text:
        return ""

    clean_text = clean_text.replace("<script", "&lt;script")
    clean_text = clean_text.replace("</script>", "&lt;/script&gt;")
    clean_text = clean_text.replace("<style", "&lt;style")
    clean_text = clean_text.replace("</style>", "&lt;/style&gt;")
    clean_text = clean_text.replace("onerror=", "data-onerror=")
    clean_text = clean_text.replace("onclick=", "data-onclick=")
    clean_text = clean_text.replace("onload=", "data-onload=")
    return clean_text


def render_highlighted_article_panel(html_text):
    if not (html_text or "").strip():
        st.info("当前版本未生成高亮阅读视图。")
        return

    st.markdown(
        """
        <style>
        .highlight-article {
            padding: 1.15rem 1.2rem;
            border: 1px solid rgba(22, 33, 28, 0.08);
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.74);
            box-shadow: 0 18px 40px rgba(22, 33, 28, 0.06);
            line-height: 1.9;
            color: #16211c;
        }
        .highlight-article p {
            margin: 0 0 1rem;
        }
        .highlight-article .highlight-positive {
            display: inline;
            padding: 0.05rem 0.32rem;
            border-radius: 0.4rem;
            background: rgba(54, 111, 214, 0.15);
            color: #1f57b8;
        }
        .highlight-article .highlight-risk {
            display: inline;
            padding: 0.05rem 0.32rem;
            border-radius: 0.4rem;
            background: rgba(214, 76, 76, 0.14);
            color: #b3261e;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="highlight-article">{sanitize_highlighted_article(html_text)}</div>', unsafe_allow_html=True)

def generate_script_for_current_article(api_key, base_url, model_name, script_duration):
    if not st.session_state.get("final_article"):
        st.session_state.spoken_script = ""
        return
    script_sys_prompt = get_script_sys_prompt(script_duration)
    st.session_state.spoken_script = call_llm(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        system_prompt=script_sys_prompt,
        user_content=f"【请将以下深度文章转化为供剪映AI解析的{script_duration}口播与分镜脚本】：\n\n{st.session_state.final_article}"
    )

# ==========================================
# 1. API 与外部推送函数
# ==========================================
def call_llm(api_key, base_url, model_name, system_prompt, user_content, image_urls=None, history=None, temperature=None):
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
        
        messages = [{"role": "system", "content": system_prompt}]
        
        if history:
            messages.extend(history)
            
        if image_urls:
            message_content = [{"type": "text", "text": user_content}]
            for img_url in image_urls:
                message_content.append({
                    "type": "image_url",
                    "image_url": {"url": img_url}
                })
        else:
            message_content = user_content

        messages.append({"role": "user", "content": message_content})

        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.3 if temperature is None else temperature
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"API 调用失败: {str(e)}")
        st.stop()

def push_to_feishu(article_text, script_text=None):
    webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/a0f50778-0dd2-4963-a0b2-0c7b68e113d8"
    headers = {"Content-Type": "application/json"}
    
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
# (抓取函数保持不变)
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


MAX_UPLOAD_TEXT_CHARS = 18000
MAX_UPLOAD_IMAGE_BYTES = 6 * 1024 * 1024
SUPPORTED_UPLOAD_EXTENSIONS = [
    "png", "jpg", "jpeg", "webp", "bmp", "gif",
    "doc", "docx", "xlsx", "xls", "txt", "md", "csv", "log", "json"
]


def trim_uploaded_text(text, max_chars=MAX_UPLOAD_TEXT_CHARS):
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[内容过长，已截断。仅保留前 {max_chars} 个字符用于分析。]"


def decode_text_file_bytes(file_bytes):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return file_bytes.decode(encoding), None
        except Exception:
            continue
    return None, "文本文件编码无法识别，请尝试转为 UTF-8 后重传。"


def extract_text_from_docx_bytes(file_bytes):
    try:
        doc = Document(io.BytesIO(file_bytes))
        blocks = []
        for paragraph in doc.paragraphs:
            paragraph_text = paragraph.text.strip()
            if paragraph_text:
                blocks.append(paragraph_text)

        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))

        if not blocks:
            return None, "Word 文件没有可提取文本。"
        return "\n".join(blocks), None
    except Exception as exc:
        return None, f"Word 解析失败: {str(exc)}"


def extract_text_from_excel_bytes(file_bytes):
    if pd is None:
        return None, "当前环境未安装 pandas/openpyxl，暂时无法读取 Excel 文件。"

    try:
        workbook = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
    except Exception as exc:
        return None, f"Excel 解析失败: {str(exc)}"

    sheet_blocks = []
    for sheet_name, df in workbook.items():
        try:
            cleaned = df.dropna(how="all").fillna("")
        except Exception:
            cleaned = df

        if cleaned is None or getattr(cleaned, "empty", False):
            continue

        try:
            sheet_text = cleaned.astype(str).to_csv(index=False)
        except Exception:
            sheet_text = str(cleaned)

        if sheet_text.strip():
            sheet_blocks.append(f"[工作表: {sheet_name}]\n{sheet_text}")

    if not sheet_blocks:
        return None, "Excel 文件没有可提取内容。"
    return "\n\n".join(sheet_blocks), None


def image_bytes_to_data_url(file_bytes, mime_type):
    safe_mime = mime_type if mime_type and mime_type.startswith("image/") else "image/png"
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:{safe_mime};base64,{encoded}"


def get_image_preview_payload(image_ref):
    if isinstance(image_ref, str) and image_ref.startswith("data:image"):
        try:
            _, encoded = image_ref.split(",", 1)
            return base64.b64decode(encoded)
        except Exception:
            return None
    return image_ref


def extract_content_from_uploaded_files(uploaded_files):
    if not uploaded_files:
        return "", [], [], 0

    text_blocks = []
    image_refs = []
    errors = []
    success_count = 0

    image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
    text_suffixes = {".txt", ".md", ".csv", ".log", ".json"}

    for idx, uploaded_file in enumerate(uploaded_files, start=1):
        file_name = uploaded_file.name or f"upload_{idx}"
        suffix = Path(file_name).suffix.lower()

        try:
            file_bytes = uploaded_file.getvalue()
        except Exception as exc:
            errors.append(f"文件 {file_name} 读取失败: {str(exc)}")
            continue

        if not file_bytes:
            errors.append(f"文件 {file_name} 为空，已跳过。")
            continue

        mime_type = getattr(uploaded_file, "type", "") or ""

        if suffix in image_suffixes or mime_type.startswith("image/"):
            if len(file_bytes) > MAX_UPLOAD_IMAGE_BYTES:
                errors.append(f"图片 {file_name} 体积过大（>{MAX_UPLOAD_IMAGE_BYTES // (1024 * 1024)}MB），已跳过。")
                continue

            image_refs.append(image_bytes_to_data_url(file_bytes, mime_type))
            text_blocks.append(f"【上传图片素材 {idx}】文件名: {file_name}\n该文件为图片素材，已作为视觉依据加入分析。")
            success_count += 1
            continue

        extracted_text = None
        extract_error = None

        if suffix in text_suffixes:
            extracted_text, extract_error = decode_text_file_bytes(file_bytes)
        elif suffix == ".docx":
            extracted_text, extract_error = extract_text_from_docx_bytes(file_bytes)
        elif suffix == ".doc":
            extract_error = "暂不支持 .doc 老格式，请另存为 .docx 后上传。"
        elif suffix in {".xlsx", ".xls"}:
            extracted_text, extract_error = extract_text_from_excel_bytes(file_bytes)
        else:
            extract_error = f"文件类型不支持: {file_name}"

        if extract_error:
            errors.append(f"文件 {file_name} 处理失败: {extract_error}")
            continue

        cleaned_text = trim_uploaded_text((extracted_text or "").strip())
        if not cleaned_text:
            errors.append(f"文件 {file_name} 未提取到有效内容。")
            continue

        text_blocks.append(f"【上传文件素材 {idx}】文件名: {file_name}\n{cleaned_text}")
        success_count += 1

    deduped_images = []
    seen_images = set()
    for item in image_refs:
        if item in seen_images:
            continue
        seen_images.add(item)
        deduped_images.append(item)

    combined_text = "\n\n================\n\n".join(text_blocks)
    return combined_text, deduped_images, errors, success_count


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
    if 'source_images_all' not in st.session_state:
        st.session_state.source_images_all = list(st.session_state.source_images)
    if 'selected_source_image_ids' not in st.session_state:
        st.session_state.selected_source_image_ids = []
    if 'extraction_success' not in st.session_state:
        st.session_state.extraction_success = False
    if 'draft_article' not in st.session_state:
        st.session_state.draft_article = ""
    if 'review_feedback' not in st.session_state:
        st.session_state.review_feedback = ""
    if 'modified_article' not in st.session_state:
        st.session_state.modified_article = ""
    if 'final_article' not in st.session_state:
        st.session_state.final_article = ""
    if 'highlighted_article' not in st.session_state:
        st.session_state.highlighted_article = ""
    if 'spoken_script' not in st.session_state:
        st.session_state.spoken_script = ""
    if 'de_ai_model' not in st.session_state:
        st.session_state.de_ai_model = "deepseek-v3-1-terminus"
    if 'de_ai_temperature' not in st.session_state:
        st.session_state.de_ai_temperature = 0.75
    if 'de_ai_prompt_template' not in st.session_state:
        st.session_state.de_ai_prompt_template = ""
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'image_keywords' not in st.session_state:
        st.session_state.image_keywords = ""
    if 'pending_completion_sound' not in st.session_state:
        st.session_state.pending_completion_sound = False
    if 'target_article_words' not in st.session_state:
        st.session_state.target_article_words = 1500
    if 'selected_role_widget' not in st.session_state:
        st.session_state.selected_role_widget = st.session_state.get('selected_role', '')
    if 'target_article_words_slider' not in st.session_state:
        st.session_state.target_article_words_slider = get_target_article_words()
    if 'article_versions' not in st.session_state or not isinstance(st.session_state.article_versions, list):
        st.session_state.article_versions = []
    if 'active_article_version_id' not in st.session_state:
        st.session_state.active_article_version_id = None
    st.session_state.target_article_words = get_target_article_words()
    st.session_state.target_article_words_slider = get_target_article_words()
    current_role = st.session_state.get('selected_role', '')
    if isinstance(current_role, str) and current_role.strip():
        st.session_state.selected_role_widget = current_role


init_state()

def go_to_step(step):
    st.session_state.current_step = step
    save_draft()


def sync_selected_source_images():
    all_images = st.session_state.get("source_images_all", [])
    if not isinstance(all_images, list):
        all_images = []

    raw_selected = st.session_state.get("selected_source_image_ids", [])
    normalized_selected = []
    seen_ids = set()

    for item in raw_selected:
        try:
            idx = int(item)
        except Exception:
            continue
        if idx < 0 or idx >= len(all_images):
            continue
        if idx in seen_ids:
            continue
        seen_ids.add(idx)
        normalized_selected.append(idx)

    st.session_state.source_images_all = all_images
    st.session_state.selected_source_image_ids = normalized_selected
    st.session_state.source_images = [all_images[idx] for idx in normalized_selected]


sync_selected_source_images()


def ensure_article_version_state():
    if 'article_versions' not in st.session_state or not isinstance(st.session_state.article_versions, list):
        st.session_state.article_versions = []
    if 'active_article_version_id' not in st.session_state:
        st.session_state.active_article_version_id = None


def get_article_version_by_id(version_id):
    ensure_article_version_state()
    for version_item in st.session_state.article_versions:
        if version_item.get("id") == version_id:
            return version_item
    return None


def append_article_version(content, stage, role=None, model=None, parent_id=None):
    ensure_article_version_state()
    clean_content = (content or "").strip()
    if not clean_content:
        return None

    versions = st.session_state.article_versions
    if versions:
        last_version = versions[-1]
        if last_version.get("stage") == stage and (last_version.get("content") or "").strip() == clean_content:
            st.session_state.active_article_version_id = last_version.get("id")
            return last_version.get("id")

    version_id = f"V{len(versions) + 1:03d}"
    resolved_parent_id = parent_id if parent_id else st.session_state.get("active_article_version_id")
    version_item = {
        "id": version_id,
        "stage": stage,
        "content": clean_content,
        "role": role if role is not None else st.session_state.get("selected_role", ""),
        "model": model if model is not None else "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "word_count": len(clean_content),
        "parent_id": resolved_parent_id,
    }

    versions.append(version_item)
    if len(versions) > 30:
        versions = versions[-30:]
    st.session_state.article_versions = versions
    st.session_state.active_article_version_id = version_id
    return version_id


def bootstrap_article_versions():
    ensure_article_version_state()
    versions = st.session_state.article_versions

    if versions:
        active_id = st.session_state.get("active_article_version_id")
        if not active_id or not any(item.get("id") == active_id for item in versions):
            st.session_state.active_article_version_id = versions[-1].get("id")
        return

    final_text = (st.session_state.get("final_article") or "").strip()
    if final_text:
        append_article_version(final_text, "历史恢复定稿", model="")
        return

    draft_text = (st.session_state.get("draft_article") or "").strip()
    if draft_text:
        append_article_version(draft_text, "历史恢复初稿", model="")


bootstrap_article_versions()


def render_html_iframe(html_content, *, height=150, width=None, scrolling=False):
    iframe_html = "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body style='margin:0;padding:0;'>" + html_content + "</body></html>"
    iframe_src = "data:text/html;base64," + base64.b64encode(iframe_html.encode("utf-8")).decode("ascii")
    components.iframe(iframe_src, height=height, width=width, scrolling=scrolling)


def render_responsive_image(image_payload):
    try:
        st.image(image_payload, width="stretch")
    except TypeError:
        st.image(image_payload, use_container_width=True)


def play_step_completion_sound():
    render_html_iframe(
        """
        <script>
        (function () {
            try {
                const AudioContextRef = window.AudioContext || window.webkitAudioContext;
                if (!AudioContextRef) return;
                const ctx = new AudioContextRef();
                const playChime = (startOffset, freq, duration, peakGain) => {
                    const startAt = ctx.currentTime + startOffset;
                    const endAt = startAt + duration;
                    const mainOsc = ctx.createOscillator();
                    const harmonicOsc = ctx.createOscillator();
                    const mainGain = ctx.createGain();
                    const harmonicGain = ctx.createGain();
                    const masterGain = ctx.createGain();

                    mainOsc.type = "sine";
                    harmonicOsc.type = "sine";
                    mainOsc.frequency.setValueAtTime(freq, startAt);
                    harmonicOsc.frequency.setValueAtTime(freq * 2, startAt);

                    harmonicGain.gain.setValueAtTime(0.32, startAt);
                    masterGain.gain.setValueAtTime(0.0001, startAt);
                    masterGain.gain.exponentialRampToValueAtTime(peakGain, startAt + 0.012);
                    masterGain.gain.exponentialRampToValueAtTime(peakGain * 0.48, startAt + 0.06);
                    masterGain.gain.exponentialRampToValueAtTime(0.0001, endAt);

                    mainOsc.connect(mainGain);
                    harmonicOsc.connect(harmonicGain);
                    mainGain.connect(masterGain);
                    harmonicGain.connect(masterGain);
                    masterGain.connect(ctx.destination);

                    mainOsc.start(startAt);
                    harmonicOsc.start(startAt);
                    mainOsc.stop(endAt + 0.02);
                    harmonicOsc.stop(endAt + 0.02);
                };

                // iOS 风格：清脆、短促、偏高频的双音提示
                playChime(0.00, 1318.5, 0.18, 0.11);
                playChime(0.13, 1760.0, 0.24, 0.10);
                setTimeout(() => {
                    try { ctx.close(); } catch (e) {}
                }, 700);
            } catch (e) {}
        })();
        </script>
        """,
        height=0,
    )


def normalize_copy_text(text):
    normalized = (text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u2028", "\n").replace("\u2029", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def markdown_to_editor_html(markdown_text):
    normalized = normalize_copy_text(markdown_text)
    if not normalized:
        return "<p></p>"

    paragraphs = [item.strip() for item in re.split(r"\n{2,}", normalized) if item.strip()]
    if not paragraphs:
        return "<p></p>"

    html_parts = []
    for paragraph in paragraphs:
        escaped = html_lib.escape(paragraph)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
        escaped = escaped.replace("\n", "<br/>")
        html_parts.append(f"<p>{escaped}</p>")
    return "".join(html_parts)


def render_editor_friendly_copy_button(text, copy_key, label="📋 兼容复制（保留段落）"):
    plain_text = normalize_copy_text(text)
    if not plain_text:
        return

    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", str(copy_key))
    safe_label = html_lib.escape(label)
    plain_text_b64 = base64.b64encode(plain_text.encode("utf-8")).decode("ascii")
    html_text_b64 = base64.b64encode(markdown_to_editor_html(plain_text).encode("utf-8")).decode("ascii")

    render_html_iframe(
        f"""
        <div style="display:flex;align-items:center;gap:10px;margin:6px 0 0 0;">
          <button id="copy-btn-{safe_key}" style="border:1px solid #d9d9df;background:#ffffff;padding:6px 12px;border-radius:8px;cursor:pointer;font-size:13px;">
            {safe_label}
          </button>
          <span id="copy-status-{safe_key}" style="font-size:12px;color:#667085;"></span>
        </div>
        <script>
        (function () {{
            const plainBase64 = "{plain_text_b64}";
            const htmlBase64 = "{html_text_b64}";
            const btn = document.getElementById("copy-btn-{safe_key}");
            const status = document.getElementById("copy-status-{safe_key}");

            function decodeBase64Utf8(input) {{
                const binary = window.atob(input);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i += 1) {{
                    bytes[i] = binary.charCodeAt(i);
                }}
                if (window.TextDecoder) {{
                    return new TextDecoder().decode(bytes);
                }}
                let encoded = "";
                for (let i = 0; i < bytes.length; i += 1) {{
                    encoded += "%" + bytes[i].toString(16).padStart(2, "0");
                }}
                return decodeURIComponent(encoded);
            }}

            async function copyRichText() {{
                const plainText = decodeBase64Utf8(plainBase64).replace(/\\n/g, "\\r\\n");
                const htmlText = decodeBase64Utf8(htmlBase64);
                try {{
                    if (navigator.clipboard && window.ClipboardItem) {{
                        const payload = new ClipboardItem({{
                            "text/plain": new Blob([plainText], {{ type: "text/plain" }}),
                            "text/html": new Blob([htmlText], {{ type: "text/html" }})
                        }});
                        await navigator.clipboard.write([payload]);
                    }} else if (navigator.clipboard && navigator.clipboard.writeText) {{
                        await navigator.clipboard.writeText(plainText);
                    }} else {{
                        const ta = document.createElement("textarea");
                        ta.value = plainText;
                        ta.style.position = "fixed";
                        ta.style.left = "-9999px";
                        document.body.appendChild(ta);
                        ta.focus();
                        ta.select();
                        document.execCommand("copy");
                        document.body.removeChild(ta);
                    }}
                    status.textContent = "已复制，可直接粘贴到富文本编辑器。";
                }} catch (err) {{
                    status.textContent = "复制失败，请手动 Ctrl+C。";
                }}
            }}

            btn.addEventListener("click", copyRichText);
        }})();
        </script>
        """,
        height=56,
    )
def notify_step_completed(defer_until_rerun=False):
    if defer_until_rerun:
        st.session_state.pending_completion_sound = True
    else:
        play_step_completion_sound()

def render_pending_completion_sound():
    if st.session_state.get("pending_completion_sound"):
        st.session_state.pending_completion_sound = False
        play_step_completion_sound()

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
* 🗣️ **口播旁白**: "就在昨天，游戏圈又爆出了一个令人震惊的超级大瓜！" 
* 🎬 **画面Prompt**: "电影级中景镜头，一个震惊的年轻玩家坐在昏暗的房间里看着发光的电脑屏幕，屏幕上显示着数据代码，动态光影，逼真质感，平移运镜"
* ✨ **视觉特效/字幕**: 屏幕突然亮起，居中显示大字特效“超级大瓜”。
"""

def inject_ui_theme():
    st.markdown(
        """
        <style>
        :root {
            --panel: rgba(255, 255, 255, 0.78);
            --border: rgba(41, 59, 51, 0.12);
            --text: #16211c;
            --text-muted: #5e6d66;
            --brand: #1f6f5f;
            --brand-strong: #174f44;
            --shadow-soft: 0 18px 40px rgba(22, 33, 28, 0.08);
            --shadow-strong: 0 24px 60px rgba(22, 33, 28, 0.12);
            --radius-lg: 24px;
            --radius-md: 18px;
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(31, 111, 95, 0.12), transparent 32%),
                radial-gradient(circle at top right, rgba(201, 139, 93, 0.10), transparent 24%),
                linear-gradient(180deg, #f4f8f5 0%, #ecf1ee 100%);
            color: var(--text);
        }
        [data-testid="stAppViewContainer"] > .main { padding-top: 1.5rem; }
        [data-testid="stHeader"] { background: rgba(244, 248, 245, 0.65); }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(18, 32, 27, 0.96), rgba(26, 44, 37, 0.92));
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }
        [data-testid="stSidebar"] * { color: #eef5f1; }
        [data-testid="stSidebar"] .stTextInput input,
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] > div,
        [data-testid="stSidebar"] .stTextArea textarea {
            background: rgba(255, 255, 255, 0.08) !important;
            border: 1px solid rgba(255, 255, 255, 0.10) !important;
            color: #eef5f1 !important;
        }
        .block-container {
            max-width: 1440px;
            padding-top: 0.5rem;
            padding-bottom: 4rem;
        }
        .app-hero {
            margin-bottom: 1.25rem;
            padding: 2rem 2.25rem;
            border-radius: var(--radius-lg);
            border: 1px solid rgba(255, 255, 255, 0.5);
            background: linear-gradient(135deg, rgba(16, 33, 29, 0.97), rgba(31, 111, 95, 0.90));
            box-shadow: var(--shadow-strong);
            color: #f5faf7;
        }
        .app-kicker {
            display: inline-flex;
            margin-bottom: 0.85rem;
            padding: 0.35rem 0.8rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.14);
            border: 1px solid rgba(255, 255, 255, 0.22);
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.04em;
        }
        .app-hero h1 { margin: 0; color: #f8fdfb; font-size: 2.3rem; }
        .app-hero p {
            max-width: 840px;
            margin: 0.7rem 0 1.25rem;
            color: rgba(248, 253, 251, 0.82);
            font-size: 1rem;
            line-height: 1.7;
        }
        .hero-metrics, .step-grid, .chip-row, .mode-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
        }
        .metric-card, .mode-card {
            color: var(--text);
            min-width: 160px;
            padding: 0.95rem 1rem;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.14);
            background: rgba(255, 255, 255, 0.10);
        }
        .metric-card strong, .mode-card strong {
            display: block;
            margin-bottom: 0.3rem;
            color: var(--text);
            font-size: 1.05rem;
        }
        .metric-card span, .mode-card span {
            color: var(--text-muted);
            font-size: 0.86rem;
            line-height: 1.5;
        }
        .stepper {
            margin: 0.5rem 0 1rem;
            padding: 1rem 1.1rem;
            border-radius: var(--radius-md);
            border: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.65);
            box-shadow: var(--shadow-soft);
        }
        .stepper-item {
            flex: 1 1 150px;
            min-width: 120px;
            padding: 0.9rem 1rem;
            border-radius: 16px;
            border: 1px solid var(--border);
            background: rgba(244, 248, 246, 0.88);
        }
        .stepper-item.active {
            background: linear-gradient(135deg, rgba(31, 111, 95, 0.12), rgba(201, 139, 93, 0.12));
            border-color: rgba(31, 111, 95, 0.28);
        }
        .stepper-item.done {
            background: rgba(31, 138, 85, 0.08);
            border-color: rgba(31, 138, 85, 0.20);
        }
        .step-index {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            margin-bottom: 0.45rem;
            border-radius: 50%;
            background: rgba(22, 33, 28, 0.08);
            color: var(--brand-strong);
            font-size: 0.82rem;
            font-weight: 700;
        }
        .stepper-item.active .step-index { background: var(--brand); color: #ffffff; }
        .step-label { display: block; margin-bottom: 0.2rem; color: var(--text); font-weight: 700; }
        .step-desc { color: var(--text-muted); font-size: 0.82rem; line-height: 1.5; }
        .section-head { display: flex; align-items: start; justify-content: space-between; gap: 1rem; margin-bottom: 0.8rem; }
        .section-title { margin: 0; font-size: 1.05rem; font-weight: 700; color: var(--text); }
        .section-subtitle { margin: 0.25rem 0 0; color: var(--text-muted); font-size: 0.92rem; line-height: 1.6; }
        .eyebrow {
            display: inline-flex;
            margin-bottom: 0.35rem;
            color: var(--brand);
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }
        .chip {
            display: inline-flex;
            align-items: center;
            padding: 0.42rem 0.72rem;
            border-radius: 999px;
            border: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.65);
            color: var(--text-muted);
            font-size: 0.8rem;
            font-weight: 600;
        }
        .chip.active {
            background: rgba(31, 111, 95, 0.10);
            border-color: rgba(31, 111, 95, 0.22);
            color: var(--brand-strong);
        }
        .context-strip {
            margin: 0.2rem 0 1rem;
            padding: 0.95rem 1.1rem;
            border-radius: 14px;
            border: 1px solid rgba(31, 111, 95, 0.12);
            background: rgba(31, 111, 95, 0.06);
        }
        .toolbar-note { color: var(--text-muted); font-size: 0.9rem; line-height: 1.6; }
        .stButton > button, .stDownloadButton > button {
            min-height: 2.85rem;
            border-radius: 14px;
            border: 1px solid rgba(31, 111, 95, 0.14);
            background: linear-gradient(180deg, #ffffff, #f5f8f6);
            color: var(--text);
            font-weight: 700;
            box-shadow: 0 8px 18px rgba(22, 33, 28, 0.06);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, var(--brand), var(--brand-strong));
            color: #ffffff;
            border-color: rgba(31, 111, 95, 0.55);
            box-shadow: 0 14px 28px rgba(31, 111, 95, 0.24);
        }
        .stTextInput input, .stTextArea textarea, .stSelectbox [data-baseweb="select"] > div {
            border-radius: 14px !important;
            border: 1px solid var(--border) !important;
            background: rgba(255, 255, 255, 0.88) !important;
        }
        .stCodeBlock, [data-testid="stCodeBlock"] {
            border-radius: 16px !important;
            border: 1px solid rgba(22, 33, 28, 0.08);
        }
        </style>
        """,
        unsafe_allow_html=True
    )


def render_app_hero():
    st.markdown(
        """
        <section class="app-hero">
            <div class="app-kicker">Professional Media Workspace</div>
            <h1>公众号文章生成助手</h1>
            <p>围绕游戏行业内容生产打造的一站式工作台。聚合多源素材、智能分配角色、统一审稿与定稿，并延伸到脚本、配图和分发环节。</p>
            <div class="hero-metrics">
                <div class="metric-card">
                    <strong>多源聚合</strong>
                    <span>文章链接、YouTube 字幕、网页图片统一进入同一工作流</span>
                </div>
                <div class="metric-card">
                    <strong>多角色协作</strong>
                    <span>编辑、审稿、精修、分镜脚本均可按角色和规范驱动</span>
                </div>
                <div class="metric-card">
                    <strong>一站式定稿</strong>
                    <span>从初稿到导出、飞书推送和配图建议全部在同一界面完成</span>
                </div>
            </div>
        </section>
        """,
        unsafe_allow_html=True
    )


def render_stepper(current_step):
    step_meta = [
        ("素材输入", "汇聚文章、视频与图像素材"),
        ("初稿生成", "选择角色并产出首版文章"),
        ("严格审稿", "核查事实、逻辑与风格"),
        ("定稿修订", "接收意见并形成最终版本"),
        ("分发工作台", "脚本、配图、导出与精修")
    ]
    st.markdown('<section class="stepper">', unsafe_allow_html=True)
    columns = st.columns(len(step_meta))
    for idx, ((label, desc), col) in enumerate(zip(step_meta, columns), start=1):
        if idx < current_step:
            class_name = "stepper-item done"
        elif idx == current_step:
            class_name = "stepper-item active"
        else:
            class_name = "stepper-item"
        with col:
            st.markdown(
                f"""
                <div class="{class_name}">
                    <div class="step-index">{idx}</div>
                    <span class="step-label">{label}</span>
                    <span class="step-desc">{desc}</span>
                </div>
                """,
                unsafe_allow_html=True
            )
    st.markdown("</section>", unsafe_allow_html=True)


def render_section_intro(title, subtitle=None, eyebrow=None):
    eyebrow_html = f'<div class="eyebrow">{eyebrow}</div>' if eyebrow else ""
    subtitle_html = f'<p class="section-subtitle">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f"""
        <div class="section-head">
            <div>
                {eyebrow_html}
                <h3 class="section-title">{title}</h3>
                {subtitle_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


def render_context_strip(items):
    chips = "".join([f'<span class="chip active">{item}</span>' for item in items if item])
    if chips:
        st.markdown(f'<div class="context-strip"><div class="chip-row">{chips}</div></div>', unsafe_allow_html=True)
# ==========================================
# 4. 页面与工作流渲染
# ==========================================
st.set_page_config(page_title="公众号文章生成助手", page_icon="🕹️", layout="wide")
inject_ui_theme()

prompts_data = load_prompts()
render_pending_completion_sound()

with st.sidebar:
    st.markdown("## 控制面板")
    st.caption("管理模型、脚本生成策略与写作 Prompt，所有改动都会直接作用到当前工作流。")
    st.caption(f"Prompt 文件：{PROMPTS_FILE}")
    if PROMPTS_LOAD_REPORT.startswith("fallback_default"):
        st.warning("未读取到有效 prompts 配置，当前使用默认角色。请确认部署目录中的 prompts.json。")
        st.caption(PROMPTS_LOAD_REPORT)
    st.header("⚙️ 引擎设置")
    api_provider = st.selectbox("🌐 选择 API 中转站", ["BLTCY (柏拉图次元)", "DeerAPI"])
    
    if api_provider == "BLTCY (柏拉图次元)":
        api_key = st.text_input("🔑 输入 BLTCY Key", type="password")
        current_base_url = "https://api.bltcy.ai/v1"
        available_models = [
            "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview-thinking-high",
            "gemini-3.1-flash-lite-preview-thinking-high",
            "claude-opus-4-6-thinking",
            "claude-opus-4-6",
            "claude-sonnet-4-6-thinking",
            "claude-opus-4-5-20251101-thinking",
            "gpt-5.4",
            "gpt-5.4-nano",
            "gpt-5.4-mini-2026-03-17",
            "MiniMax-M2.7",
            "MiniMax-M2.7-highspeed",
            "qwen3.5-plus",
            "kimi-k2.5"
        ]
    else:
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

    default_model_idx = 0
    if "gemini-3.1-pro-preview" in available_models:
        default_model_idx = available_models.index("gemini-3.1-pro-preview")
        
    selected_model = st.selectbox("🧠 选择驱动模型", available_models, index=default_model_idx)
    
    st.markdown("---")
    st.header("🎬 视频分镜设置")
    enable_script = st.toggle("启用伴生【短视频分镜脚本】", value=False)
    script_duration = st.selectbox(
        "⏱️ 设定分镜脚本目标时长", 
        ["1分钟", "3分钟", "5分钟", "8分钟"], 
        index=2, 
        disabled=not enable_script 
    )
    
    st.markdown("---")
    st.header("🗂️ 提示词管理中心")
    with st.expander("📝 角色与全局人设配置", expanded=False):
        tab1, tab2, tab3 = st.tabs(["✍️ 编辑人设", "🧐 审稿人设", "🌍 全局去AI味规范"])
        
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
                
        with tab3:
            st.info("💡 此处的规范将**强制追加**到所有【编辑】的提示词末尾，用于统一全局的写作风格。")
            new_global_prompt = st.text_area("全局强制规范", value=prompts_data.get("global_instruction", ""), height=300)
            if st.button("💾 保存全局规范"):
                prompts_data["global_instruction"] = new_global_prompt
                save_prompts(prompts_data)
                st.success("已成功更新全局规范！")
                st.rerun()

render_app_hero()
render_stepper(st.session_state.current_step)

# --- Step 1 ---
if st.session_state.current_step == 1:
    render_section_intro("素材输入中枢", "在同一界面批量汇聚文章链接、YouTube 链接与图片素材，统一进入后续的编辑与审稿流程。", "Step 01")

    with st.container(border=True):
        st.markdown("""<div class="mode-grid"><div class="mode-card"><strong>批量素材输入</strong><span>支持多篇文章和多个视频链接合并提取，适合做专题与深度整合。</span></div><div class="mode-card"><strong>两种工作流模式</strong><span>手动精调适合逐步把关，全自动驾驶适合快速直达定稿。</span></div><div class="mode-card"><strong>统一定稿工作台</strong><span>脚本、搜图、导出、飞书推送和精修助手都在最后一页集中处理。</span></div></div>""", unsafe_allow_html=True)

    if os.path.exists(DRAFT_FILE):
        with st.container(border=True):
            render_section_intro("继续上次工作", "如果上次停在中途，可以直接恢复到之前离开的步骤。", "Recovery")
            st.markdown("<p class='toolbar-note'>恢复草稿会还原素材、初稿、审稿意见和定稿上下文；清空则重新开始新的工作流。</p>", unsafe_allow_html=True)
            col_draft1, col_draft2 = st.columns(2)
            with col_draft1:
                if st.button("⚡ 一键恢复草稿", type="primary", use_container_width=True):
                    if load_draft():
                        st.success("草稿恢复成功！工作流已复原。")
                        st.rerun()
                    else:
                        st.error("草稿文件损坏，无法恢复。")
            with col_draft2:
                if st.button("🗑️ 抛弃旧草稿，全新开始", use_container_width=True):
                    clear_draft()
                    st.rerun()

    input_col1, input_col2 = st.columns(2)
    with input_col1:
        with st.container(border=True):
            render_section_intro("文章链接池", "适合导入多篇新闻、报告或博客文章；每行一个链接。", "Articles")
            article_url_input = st.text_area(
                "📝 输入文章链接 (每行一个，支持批量)",
                value=st.session_state.article_url,
                height=180,
                placeholder="https://example.com/article-1\nhttps://example.com/article-2"
            )
    with input_col2:
        with st.container(border=True):
            render_section_intro("视频链接池", "适合把 YouTube 字幕与文章一起并入同一份素材上下文。", "Videos")
            video_url_input = st.text_area(
                "📺 输入 YouTube 视频链接 (每行一个，支持批量)",
                value=st.session_state.video_url,
                height=180,
                placeholder="https://youtu.be/...\nhttps://www.youtube.com/watch?v=..."
            )

    with st.container(border=True):
        render_section_intro("文件资料上传", "支持上传本地图片、Word、Excel、TXT 等文件作为分析依据。", "Files")
        uploaded_source_files = st.file_uploader(
            "上传资料文件（支持图片、Word、Excel、TXT，可多选）",
            type=SUPPORTED_UPLOAD_EXTENSIONS,
            accept_multiple_files=True,
            key="source_files_uploader"
        )
        if uploaded_source_files:
            file_names = [file.name for file in uploaded_source_files]
            preview_names = "、".join(file_names[:6])
            if len(file_names) > 6:
                preview_names += f" 等 {len(file_names)} 个文件"
            st.caption(f"已选择 {len(file_names)} 个文件：{preview_names}")

    with st.container(border=True):
        render_section_intro("开始提取", "系统会先抓取正文和图片，再将多源素材聚合成统一工作底稿。", "Actions")
        st.markdown("<p class='toolbar-note'>建议先把主题相近的文章和视频放在同一批次里，方便后续自动路由和统一改写。</p>", unsafe_allow_html=True)
        if st.button("开始批量提取内容", type="primary", use_container_width=True):
            article_urls = [url.strip() for url in article_url_input.split('\n') if url.strip()]
            video_urls = [url.strip() for url in video_url_input.split('\n') if url.strip()]
            uploaded_file_count = len(uploaded_source_files) if uploaded_source_files else 0

            if not article_urls and not video_urls and uploaded_file_count == 0:
                st.warning("请至少输入链接或上传一个文件！")
            else:
                total_sources = len(article_urls) + len(video_urls) + uploaded_file_count
                with st.spinner(f"启动全息解析引擎，正在批量获取 {total_sources} 个素材..."):
                    combined_content = ""
                    extracted_imgs = []
                    errors = []
                    success_count = 0

                    for idx, a_url in enumerate(article_urls):
                        art_content, art_imgs, art_err = get_content_from_url(a_url)
                        if art_content:
                            combined_content += f"【文章素材 {idx+1}】来源于: {a_url}\n{art_content}\n\n================\n\n"
                            extracted_imgs.extend(art_imgs)
                            success_count += 1
                        else:
                            errors.append(f"文章 {idx+1} 提取失败: {art_err}")

                    for idx, v_url in enumerate(video_urls):
                        vid_content, vid_imgs, vid_err = get_content_from_url(v_url)
                        if vid_content:
                            combined_content += f"【视频素材 {idx+1}】来源于: {v_url}\n{vid_content}\n\n================\n\n"
                            success_count += 1
                        else:
                            errors.append(f"视频 {idx+1} 提取失败: {vid_err}")

                    uploaded_content, uploaded_imgs, uploaded_errors, uploaded_success_count = extract_content_from_uploaded_files(uploaded_source_files)
                    if uploaded_content:
                        combined_content += uploaded_content + "\n\n================\n\n"
                    if uploaded_imgs:
                        extracted_imgs.extend(uploaded_imgs)
                    if uploaded_errors:
                        errors.extend(uploaded_errors)
                    success_count += uploaded_success_count

                    deduped_imgs = []
                    seen_imgs = set()
                    for image_item in extracted_imgs:
                        if image_item in seen_imgs:
                            continue
                        seen_imgs.add(image_item)
                        deduped_imgs.append(image_item)
                    extracted_imgs = deduped_imgs[:15]

                    if combined_content.strip():
                        st.session_state.article_url = article_url_input
                        st.session_state.video_url = video_url_input
                        st.session_state.source_content = combined_content
                        st.session_state.source_images_all = extracted_imgs
                        st.session_state.selected_source_image_ids = []
                        st.session_state.source_images = []
                        for stale_key in [k for k in list(st.session_state.keys()) if k.startswith("source_image_pick_")]:
                            del st.session_state[stale_key]
                        st.session_state.extraction_success = True
                        notify_step_completed()

                        if errors:
                            st.warning(f"部分内容提取成功 ({success_count}/{total_sources})，但有以下错误：\n" + "\n".join(errors))
                        else:
                            if len(extracted_imgs) > 0:
                                st.success(f"批量提取成功！共融合了 {success_count} 个素材，并提取到 {len(extracted_imgs)} 张核心配图。")
                            else:
                                st.success(f"批量提取成功！共融合了 {success_count} 个素材。（无有效配图，走纯文本模式）")
                    else:
                        st.session_state.extraction_success = False
                        st.error("所有素材提取均失败，请检查链接、上传文件内容或网络状态。\n" + "\n".join(errors))

    if st.session_state.extraction_success:
        with st.container(border=True):
            render_section_intro("聚合素材预览", "先快速检查抓取结果，再决定是走手动精调还是全自动驾驶。", "Preview")
            all_source_images = st.session_state.get("source_images_all", [])
            if all_source_images:
                st.markdown("#### 核心配图（勾选纳入 AI 分析）")
                previous_selected_ids = set(st.session_state.get("selected_source_image_ids", []))
                selected_ids = []
                img_cols = st.columns(3)
                for idx, img_url in enumerate(all_source_images):
                    with img_cols[idx % 3]:
                        preview_image = get_image_preview_payload(img_url)
                        if preview_image is not None:
                            render_responsive_image(preview_image)
                        checked = st.checkbox(
                            f"图片 {idx + 1} 纳入分析",
                            value=(idx in previous_selected_ids),
                            key=f"source_image_pick_{idx}"
                        )
                        if checked:
                            selected_ids.append(idx)

                if selected_ids != st.session_state.get("selected_source_image_ids", []):
                    st.session_state.selected_source_image_ids = selected_ids
                    st.session_state.source_images = [all_source_images[i] for i in selected_ids]
                    save_draft()

                st.caption(f"当前已勾选 {len(st.session_state.source_images)} / {len(all_source_images)} 张图片进入分析。")
                if len(st.session_state.source_images) == 0:
                    st.info("当前未勾选任何图片，后续将按纯文本模式继续。")
                st.divider()

            st.markdown("#### 合并后的文本正文")
            preview_text = st.session_state.source_content[:1800] + "\n\n......(已省略后续内容)" if len(st.session_state.source_content) > 1800 else st.session_state.source_content
            st.code(preview_text, language="markdown")

        with st.container(border=True):
            render_section_intro("选择工作流模式", "手动精调适合逐步把关，全自动驾驶适合快速得到高完成度定稿。", "Workflow")
            st.caption(f"🧮 当前全局目标字数：约 {get_target_article_words()} 字")
            col_flow1, col_flow2 = st.columns(2)

            with col_flow1:
                st.markdown("<p class='toolbar-note'>逐步确认编辑角色、初稿、审稿意见与修改结果，适合需要人工把关的稿件。</p>", unsafe_allow_html=True)
                if st.button("👉 手动精调模式 (逐步确认)", use_container_width=True):
                    go_to_step(2)
                    st.rerun()

            with col_flow2:
                st.markdown("<p class='toolbar-note'>自动完成角色路由、写稿、审稿、改稿和可选脚本生成，适合快速交付。</p>", unsafe_allow_html=True)
                if st.button("🚀 一键全自动驾驶 (AI路由直达定稿)", type="primary", use_container_width=True):
                    if not api_key:
                        st.error("⚠️ 请先在左侧边栏输入 API Key！")
                        st.stop()

                    with st.status("🤖 全自动驾驶已启动，AI 正在接管工作流...", expanded=True) as status:
                        st.write("🔍 正在分析素材内容，为您匹配最佳编辑人设...")
                        editor_names = list(prompts_data["editors"].keys())
                        routing_prompt = f"""你是一个智能路由系统。请阅读以下素材，判断哪种身份最适合将其改写为深度文章。
                        请只输出角色的完整名称，绝不允许包含任何其他标点或解释废话！
                        可选角色：{', '.join(editor_names)}"""

                        chosen_editor_raw = call_llm(
                            api_key=api_key, base_url=current_base_url, model_name=selected_model,
                            system_prompt=routing_prompt, user_content=st.session_state.source_content[:5000]
                        )

                        chosen_editor = chosen_editor_raw.strip() if chosen_editor_raw else ""
                        if chosen_editor not in editor_names:
                            chosen_editor = editor_names[0]

                        st.session_state.selected_role = chosen_editor
                        st.write(f"✅ 意图识别完成，已自动指派：**【{chosen_editor}】**")

                        st.write("✍️ 编辑正在奋笔疾书，生成初稿中...")
                        editor_prompt = prompts_data["editors"][chosen_editor]
                        global_instruction = prompts_data.get("global_instruction", "")
                        final_editor_system_prompt = build_editor_system_prompt(editor_prompt, global_instruction)

                        draft_content = f"以下是多个来源的素材聚合内容，请结合附带的参考图片一起深度分析与融合：\n\n{st.session_state.source_content}"
                        st.session_state.draft_article = call_llm(
                            api_key=api_key, base_url=current_base_url, model_name=selected_model,
                            system_prompt=final_editor_system_prompt, user_content=draft_content, image_urls=st.session_state.source_images
                        )
                        append_article_version(st.session_state.draft_article, "自动驾驶初稿", role=chosen_editor, model=selected_model)

                        st.write("🧐 审稿主编介入，正在极其严苛地核对原文与逻辑...")
                        reviewer_prompt = prompts_data["reviewer"]
                        anti_hallucination_instruction = "\n\n【⚠️ 强制系统级指令：严禁幻觉】：你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】！绝对不允许使用自身知识库进行事实核对。"
                        final_reviewer_system_prompt = reviewer_prompt + anti_hallucination_instruction

                        combined_content = f"下面是聚合的【原始素材文本】（这是唯一的真相来源）：\n{st.session_state.source_content}\n\n================\n下面是【初稿】：\n{st.session_state.draft_article}"
                        st.session_state.review_feedback = call_llm(
                            api_key=api_key, base_url=current_base_url, model_name=selected_model,
                            system_prompt=final_reviewer_system_prompt, user_content=combined_content, image_urls=st.session_state.source_images
                        )

                        st.write("✨ 接收修改意见，正在进行最终打磨...")
                        modification_prompt = build_modification_system_prompt(global_instruction)
                        content_to_modify = f"【审稿意见】：\n{st.session_state.review_feedback}\n\n================\n\n【初稿】：\n{st.session_state.draft_article}"

                        st.session_state.final_article = call_llm(
                            api_key=api_key, base_url=current_base_url, model_name=selected_model,
                            system_prompt=modification_prompt, user_content=content_to_modify
                        )
                        append_article_version(st.session_state.final_article, "自动驾驶定稿", role=chosen_editor, model=selected_model)

                        if enable_script:
                            st.write("🎬 正在同步生成口播与纯中文分镜脚本...")
                            script_sys_prompt = get_script_sys_prompt(script_duration)
                            st.session_state.spoken_script = call_llm(
                                api_key=api_key, base_url=current_base_url, model_name=selected_model,
                                system_prompt=script_sys_prompt,
                                user_content=f"【请将以下深度文章转化为供剪映AI解析的{script_duration}口播与分镜脚本】：\n\n{st.session_state.final_article}"
                            )
                        else:
                            st.session_state.spoken_script = ""

                        status.update(label="🎉 全自动驾驶完成！即将跳转定稿页。", state="complete", expanded=False)

                    notify_step_completed(defer_until_rerun=True)
                    go_to_step(6)
                    st.rerun()
# --- Step 2 (手动模式) ---
elif st.session_state.current_step == 2:
    render_section_intro("初稿生成", "选择合适的编辑角色，确认当前模型与写作规范，然后输出首版文章。", "Step 02")
    context_strip_placeholder = st.empty()
    
    editor_options = list(prompts_data["editors"].keys())
    if 'selected_role' not in st.session_state or st.session_state.selected_role not in editor_options:
        st.session_state.selected_role = editor_options[0]
        
    if st.session_state.get("selected_role_widget") not in editor_options:
        st.session_state.selected_role_widget = st.session_state.selected_role
    
    st.selectbox(
        "选择【编辑】视角",
        editor_options,
        key="selected_role_widget",
        on_change=sync_selected_role
    )
    editor_role = st.session_state.selected_role
    if st.session_state.get("target_article_words_slider") != get_target_article_words():
        st.session_state.target_article_words_slider = get_target_article_words()
    st.slider(
        "🧮 全局目标字数（200-5000）",
        min_value=200,
        max_value=5000,
        step=100,
        key="target_article_words_slider",
        on_change=sync_target_article_words,
        help="本轮稿件统一使用该字数目标；若角色 Prompt 里有固定字数要求，会自动被全局目标覆盖。"
    )
    with context_strip_placeholder.container():
        render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"目标字数：约 {get_target_article_words()} 字", f"分镜脚本：{'开启' if enable_script else '关闭'}"])
    
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
            with st.spinner("编辑正在分析所有素材并奋笔疾书，请耐心等待..."):
                global_instruction = prompts_data.get("global_instruction", "")
                final_editor_system_prompt = build_editor_system_prompt(editor_prompt, global_instruction)
                
                if st.session_state.source_images:
                    editor_user_content = f"以下是多个来源的素材聚合内容，请结合附带的参考图片一起深度分析与融合：\n\n{st.session_state.source_content}"
                else:
                    editor_user_content = f"以下是多个来源的素材聚合内容，请根据纯文本进行深度分析与融合：\n\n{st.session_state.source_content}"
                
                st.session_state.draft_article = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=final_editor_system_prompt,
                    user_content=editor_user_content,
                    image_urls=st.session_state.source_images
                )
                append_article_version(st.session_state.draft_article, "手动初稿", role=editor_role, model=selected_model)
                notify_step_completed(defer_until_rerun=True)
                go_to_step(3)
                st.rerun()

# --- Step 3 (手动模式) ---
elif st.session_state.current_step == 3:
    render_section_intro("严格审稿", "主编从事实、逻辑和风格三个维度核查初稿，确保对外可发布。", "Step 03")
    render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"目标字数：约 {get_target_article_words()} 字", f"分镜脚本：{'开启' if enable_script else '关闭'}"])
    
    with st.expander("📝 查看当前初稿内容 (鼠标移至右上角可一键复制)", expanded=True):
        st.code(st.session_state.draft_article, language="markdown")
        render_editor_friendly_copy_button(st.session_state.draft_article, "draft_article_step3")
    
    st.divider()
    
    reviewer_prompt = st.text_area("🧐 审稿员 Prompt (支持临时微调)", value=prompts_data["reviewer"], height=200)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 感觉不对，重写初稿"):
            go_to_step(2)
            st.rerun()
    with col2:
        if st.button("⏭️ 完美，跳过审查进入去AI味"):
            with st.spinner("正在跳过审查，并将初稿送入去 AI 味步骤..."):
                st.session_state.modified_article = st.session_state.draft_article
                st.session_state.final_article = ""
                st.session_state.spoken_script = ""
                append_article_version(st.session_state.modified_article, "跳过审查修改稿", role=st.session_state.get("selected_role", ""), model=selected_model)
                notify_step_completed(defer_until_rerun=True)
                go_to_step(5)
                st.rerun()
    with col3:
        if st.button(f"🔍 使用 {selected_model} 开始严格审查"):
            with st.spinner("主编正在核对原文素材..."):
                if st.session_state.source_images:
                    anti_hallucination_instruction = """\n\n【⚠️ 强制系统级指令：严禁幻觉】：
                    你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】以及你所看到的【参考配图】！绝对不允许使用自身知识库进行事实核对。"""
                    combined_content = f"下面是聚合的【原始素材文本】（这是唯一的真相来源）：\n{st.session_state.source_content}\n\n================\n下面是【初稿】：\n{st.session_state.draft_article}"
                else:
                    anti_hallucination_instruction = """\n\n【⚠️ 强制系统级指令：严禁幻觉】：
                    你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】！绝对不允许使用自身知识库进行事实核对。"""
                    combined_content = f"下面是聚合的【原始素材文本】（这是唯一的真相来源）：\n{st.session_state.source_content}\n\n================\n下面是【初稿】：\n{st.session_state.draft_article}"
                
                final_reviewer_system_prompt = reviewer_prompt + anti_hallucination_instruction
                
                st.session_state.review_feedback = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=final_reviewer_system_prompt, 
                    user_content=combined_content,
                    image_urls=st.session_state.source_images
                )
                notify_step_completed(defer_until_rerun=True)
                go_to_step(4)
                st.rerun()

# --- Step 4 (手动模式) ---
elif st.session_state.current_step == 4:
    render_section_intro("修改稿确认", "根据主编反馈完成最后一轮修改，先产出修改稿，再决定是否进入去 AI 味步骤。", "Step 04")
    render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"目标字数：约 {get_target_article_words()} 字", f"分镜脚本：{'开启' if enable_script else '关闭'}"])

    st.info("**主编审稿意见 (鼠标移至下方框内右上角可复制)：**")
    st.code(st.session_state.review_feedback, language="markdown")
    render_editor_friendly_copy_button(st.session_state.review_feedback, "review_feedback_step4")

    if st.session_state.modified_article:
        st.divider()
        st.markdown("### 当前修改稿预览")
        st.code(st.session_state.modified_article, language="markdown")
        render_editor_friendly_copy_button(st.session_state.modified_article, "modified_article_step4")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 意见太水，重新审查"):
            go_to_step(3)
            st.rerun()
    with col2:
        if st.button("⏭️ 忽略意见，沿用初稿"):
            with st.spinner("正在跳过修改，准备进入去 AI 味步骤..."):
                st.session_state.modified_article = st.session_state.draft_article
                st.session_state.final_article = ""
                st.session_state.highlighted_article = ""
                st.session_state.spoken_script = ""
                append_article_version(st.session_state.modified_article, "忽略意见修改稿", role=st.session_state.get("selected_role", ""), model=selected_model)
                notify_step_completed(defer_until_rerun=True)
                go_to_step(5)
                st.rerun()
    with col3:
        if st.button(f"✨ 使用 {selected_model} 接受意见并生成修改稿"):
            with st.spinner("编辑正在根据主编意见生成修改稿..."):
                global_instruction = prompts_data.get("global_instruction", "")
                modification_prompt = build_modification_system_prompt(global_instruction)

                content_to_modify = f"【审稿意见】：\n{st.session_state.review_feedback}\n\n================\n\n【初稿】：\n{st.session_state.draft_article}"

                st.session_state.modified_article = call_llm(
                    api_key=api_key,
                    base_url=current_base_url,
                    model_name=selected_model,
                    system_prompt=modification_prompt,
                    user_content=content_to_modify
                )
                st.session_state.final_article = ""
                st.session_state.highlighted_article = ""
                st.session_state.spoken_script = ""
                append_article_version(st.session_state.modified_article, "接受审稿修改稿", role=st.session_state.get("selected_role", ""), model=selected_model)
                notify_step_completed(defer_until_rerun=True)
                go_to_step(5)
                st.rerun()

# --- Step 5 (手动模式) ---
elif st.session_state.current_step == 5:
    render_section_intro("去 AI 味", "在定稿前选择专用模型，把修改稿重写得更像真人专家输出。", "Step 05")
    render_context_strip([f"修改稿来源模型：{selected_model}", f"专用模型：{st.session_state.get('de_ai_model', DE_AI_MODELS[0])}", f"Temperature：{st.session_state.get('de_ai_temperature', 0.75):.2f}", f"分镜脚本：{'开启' if enable_script else '关闭'}"])

    current_role = st.session_state.get("selected_role", "")
    current_editor_prompt = prompts_data["editors"].get(current_role, "") if current_role in prompts_data["editors"] else ""
    st.session_state.de_ai_prompt_template = build_de_ai_prompt_template(current_role, current_editor_prompt, st.session_state.get("source_content", ""))

    st.markdown("### 当前修改稿")
    st.code(st.session_state.modified_article, language="markdown")
    render_editor_friendly_copy_button(st.session_state.modified_article, "modified_article_step5")

    with st.expander("查看本次去 AI 味专用 Prompt 模板（只读）", expanded=False):
        st.text_area(
            "去 AI 味 Prompt 模板",
            value=st.session_state.de_ai_prompt_template,
            height=360,
            disabled=True,
            key="de_ai_prompt_template_preview"
        )

    col_model, col_temp = st.columns([1.2, 1])
    with col_model:
        st.selectbox(
            "去 AI 味专用大模型",
            DE_AI_MODELS,
            key="de_ai_model",
            on_change=save_draft
        )
    with col_temp:
        st.slider(
            "Temperature",
            min_value=0.70,
            max_value=0.85,
            step=0.05,
            key="de_ai_temperature",
            on_change=save_draft
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔙 返回修改稿步骤"):
            go_to_step(4)
            st.rerun()
    with col2:
        if st.button("⏭️ 跳过去 AI 味，直接定稿"):
            spinner_msg = f"正在将修改稿设为定稿，并生成【{script_duration}口播及分镜脚本】..." if enable_script else "正在将修改稿设为定稿..."
            with st.spinner(spinner_msg):
                st.session_state.final_article = st.session_state.modified_article
                st.session_state.highlighted_article = ""
                append_article_version(st.session_state.final_article, "跳过去AI味定稿", role=current_role, model=selected_model)
                if enable_script:
                    generate_script_for_current_article(api_key, current_base_url, selected_model, script_duration)
                else:
                    st.session_state.spoken_script = ""
                notify_step_completed(defer_until_rerun=True)
                go_to_step(6)
                st.rerun()
    with col3:
        if st.button(f"✨ 使用 {st.session_state.get('de_ai_model', DE_AI_MODELS[0])} 去 AI 味重写"):
            spinner_msg = f"正在使用 {st.session_state.get('de_ai_model', DE_AI_MODELS[0])} 去 AI 味，并生成【{script_duration}口播及分镜脚本】..." if enable_script else f"正在使用 {st.session_state.get('de_ai_model', DE_AI_MODELS[0])} 去 AI 味重写..."
            with st.spinner(spinner_msg):
                de_ai_response = call_llm(
                    api_key=api_key,
                    base_url=current_base_url,
                    model_name=st.session_state.get('de_ai_model', DE_AI_MODELS[0]),
                    system_prompt=st.session_state.de_ai_prompt_template,
                    user_content=st.session_state.modified_article,
                    temperature=st.session_state.get('de_ai_temperature', 0.75)
                )
                pure_article, highlighted_article = parse_de_ai_dual_output(de_ai_response)
                st.session_state.final_article = pure_article or (de_ai_response or "").strip()
                st.session_state.highlighted_article = highlighted_article
                append_article_version(st.session_state.final_article, "去AI味定稿", role=current_role, model=st.session_state.get('de_ai_model', DE_AI_MODELS[0]))
                if enable_script:
                    generate_script_for_current_article(api_key, current_base_url, selected_model, script_duration)
                else:
                    st.session_state.spoken_script = ""
                if not highlighted_article:
                    st.warning("高亮版生成失败，本次仅保留纯净定稿。")
                notify_step_completed(defer_until_rerun=True)
                go_to_step(6)
                st.rerun()
# --- Step 6：终极版分栏 UI ---
elif st.session_state.current_step == 6:
    render_section_intro("分发工作台", "在统一界面完成定稿审阅、脚本联动、搜图建议、导出分发和后续精修。", "Step 06")
    render_context_strip([f"最终角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '自动路由'}", f"当前模型：{selected_model}", f"脚本状态：{'已生成' if st.session_state.spoken_script else '未生成'}"])
    
    st.markdown("<p class='toolbar-note'>主稿、分镜脚本、搜图和分发操作统一留在左侧主工作区；右侧专门用于精修、追问和追溯原文依据。</p>", unsafe_allow_html=True)
    left_col, right_col = st.columns([1.45, 0.95])
    
    with left_col:
        st.markdown("### 稿件版本时间线")
        versions = st.session_state.get("article_versions", [])
        if versions:
            version_map = {item.get("id"): item for item in versions if item.get("id")}
            version_options = [item.get("id") for item in reversed(versions) if item.get("id")]
            active_version_id = st.session_state.get("active_article_version_id")
            default_version_index = version_options.index(active_version_id) if active_version_id in version_options else 0

            def format_version_option(version_id):
                version_item = version_map.get(version_id, {})
                return f"{version_id} · {version_item.get('stage', '未命名阶段')} · {version_item.get('created_at', '')} · {version_item.get('word_count', 0)} 字"

            selected_version_id = st.selectbox(
                "选择版本节点",
                options=version_options,
                index=default_version_index,
                format_func=format_version_option,
                key="article_version_selector"
            )
            selected_version = version_map.get(selected_version_id)

            if selected_version_id != st.session_state.get("active_article_version_id"):
                st.session_state.active_article_version_id = selected_version_id
                save_draft()

            if selected_version:
                st.caption(f"当前阶段：{selected_version.get('stage', '未命名')}｜角色：{selected_version.get('role', '未记录')}｜模型：{selected_version.get('model', '未记录')}")
                if st.button("将此版本设为当前定稿", key="use_selected_version_as_final", use_container_width=True):
                    st.session_state.final_article = selected_version.get("content", "")
                    st.session_state.highlighted_article = ""
                    st.session_state.active_article_version_id = selected_version_id
                    save_draft()
                    notify_step_completed()
                    st.success(f"已切换到版本 {selected_version_id}")

                with st.expander("查看选中版本全文", expanded=False):
                    st.code(selected_version.get("content", ""), language="markdown")
                    render_editor_friendly_copy_button(selected_version.get("content", ""), f"version_{selected_version_id}")
        else:
            st.info("暂无版本记录。生成初稿或定稿后会自动写入版本时间线。")

        st.divider()
        st.markdown("### 主稿面板（当前定稿）")
        st.code(st.session_state.final_article, language="markdown")
        render_editor_friendly_copy_button(st.session_state.final_article, "final_article_step6")

        st.divider()
        st.markdown("### 高亮阅读版")
        render_highlighted_article_panel(st.session_state.get("highlighted_article", ""))
        
        if st.session_state.spoken_script:
            st.divider()
            st.markdown(f"### 分镜脚本 · {script_duration}")
            st.code(st.session_state.spoken_script, language="markdown")
            render_editor_friendly_copy_button(st.session_state.spoken_script, "spoken_script_step6")
        
        st.divider()
        st.markdown("### 智能配图助手")
        st.info("需要为文章寻找真实、高质的配图？点击下方按钮，AI 将根据文章核心内容提取 10 个精确的 Google 图片搜索关键词。")
        
        if st.button("💡 提取 10 个 Google 搜图关键词", use_container_width=True):
            with st.spinner("正在深度分析文章，提取精确的搜图词汇..."):
                keyword_prompt = """请根据以下文章内容，提取 10 个最适合在 Google 图片（Google Images）中搜索配图的精准关键词组合。
                
                【核心要求】：
                1. 必须精准输出 10 个。
                2. 为了在 Google 搜出最高质量的图，请尽量采用【中英文混合】或【纯英文】的专业搜索词（例如："Genshin Impact UI design", "Sensor Tower SLG revenue chart 2024", "Tencent Games logo transparent"）。
                3. 场景具体化：不要只搜游戏名，要加上明确的修饰词（如实机演示、数据图表、买量素材、应用商店截图等）。
                4. 直接用编号 1-10 列出，不要有任何废话解释。
                
                【文章定稿内容】：
                """ + st.session_state.final_article
                
                st.session_state.image_keywords = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt="你是一个专业的游戏媒体视觉编辑，熟知如何通过高级检索词在 Google 找到极具说服力的行业配图。", 
                    user_content=keyword_prompt
                )
                save_draft()
                notify_step_completed()
                
        if st.session_state.image_keywords:
            st.success("✅ 关键词提取成功！你可以直接复制这些词去 Google 搜图：")
            st.code(st.session_state.image_keywords, language="markdown")
            render_editor_friendly_copy_button(st.session_state.image_keywords, "image_keywords_step6")
            
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
        
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
             st.download_button(
                label="📄 导出 Word 文档" if not st.session_state.spoken_script else "📄 导出图文与脚本(Word)",
                data=docx_data,
                file_name="公众号文章_定稿.docx" if not st.session_state.spoken_script else "公众号与短视频脚本_定稿.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True
            )
            
        with btn_col2:
            if st.button("✈️ 推送通知到飞书群", use_container_width=True):
                with st.spinner("正在推送到飞书..."):
                    success, msg = push_to_feishu(st.session_state.final_article, st.session_state.spoken_script if st.session_state.spoken_script else None)
                    if success:
                        st.success("🎉 飞书推送成功！")
                        notify_step_completed()
                    else:
                        st.error(f"❌ 推送失败：{msg}")
                            
        with btn_col3:
            if st.button("🔄 开启新一篇工作流", use_container_width=True):
                clear_draft()
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

    with right_col:
        with st.container(border=True):
            render_section_intro("精修侧栏", "在这里追问出处、重写局部段落，或把主稿改成更适合公众号和视频的表达。", "Assistant")
            st.markdown("<p class='toolbar-note'>常用操作：追问某句结论的原文出处、要求重写某段、补一版更适合导语的开场、把段落改成更适合视频口播的语气。</p>", unsafe_allow_html=True)
            quick_col1, quick_col2 = st.columns(2)
            with quick_col1:
                st.markdown("- 重写一段为更克制的媒体口吻")
                st.markdown("- 追问文中某个数据的素材出处")
            with quick_col2:
                st.markdown("- 补一版更抓人的导语")
                st.markdown("- 改写为更适合口播的表达")

        with st.container(border=True):
            render_section_intro("快捷动作", "先给使用者几个明确的提问方向，降低上手成本。", "Shortcuts")
            shortcut_col1, shortcut_col2 = st.columns(2)
            with shortcut_col1:
                st.caption("适合改文")
                st.markdown("- 帮我把导语写得更抓人")
                st.markdown("- 把第三段改成更像媒体报道")
            with shortcut_col2:
                st.caption("适合追溯")
                st.markdown("- 这句结论在原文哪一段")
                st.markdown("- 这个数据的素材出处是什么")

        with st.container(border=True):
            render_section_intro("对话区", "所有精修历史都保存在这里，方便反复迭代。", "Chat")
            chat_container = st.container(height=500)

            with chat_container:
                for msg in st.session_state.chat_history:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

            if user_query := st.chat_input("输入你的修改指令或疑问（回车发送）..."):
                st.session_state.chat_history.append({"role": "user", "content": user_query})
                with chat_container:
                    with st.chat_message("user"):
                        st.markdown(user_query)

                    with st.chat_message("assistant"):
                        with st.spinner("思考与检索中..."):
                            chat_sys_prompt = f"""你是一个极其专业的文章精修与溯源助手。

                            【你的参考资料库（唯一的真相来源）】：
                            {st.session_state.source_content}

                            【当前正在精修的定稿文章】：
                            {st.session_state.final_article}

                            【你的任务】：
                            1. 如果用户要求溯源，请精准定位到【参考资料库】中的原文片段，并客观回答。
                            2. 如果用户要求重写某一段落，请直接输出修改后能够无缝替换回去的完美段落，不要说废话。
                            3. 如果用户基于文章进行衍生提问，请结合上述资料给出专业见解。
                            """

                            history_to_send = st.session_state.chat_history[:-1]

                            ai_response = call_llm(
                                api_key=api_key,
                                base_url=current_base_url,
                                model_name=selected_model,
                                system_prompt=chat_sys_prompt,
                                user_content=user_query,
                                history=history_to_send
                            )
                            st.markdown(ai_response)

                st.session_state.chat_history.append({"role": "assistant", "content": ai_response})
                save_draft()
                st.rerun()






































































