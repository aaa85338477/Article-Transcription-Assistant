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
import streamlit.components.v1 as components

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
        'review_feedback', 'final_article', 'spoken_script', 
        'chat_history', 'image_keywords', 'selected_role', 'target_article_words'
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
    raw_value = st.session_state.get("target_article_words", 3000)
    try:
        value = int(raw_value)
    except Exception:
        value = 3000
    return max(200, min(5000, value))


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

# ==========================================
# 1. API 与外部推送函数
# ==========================================
def call_llm(api_key, base_url, model_name, system_prompt, user_content, image_urls=None, history=None):
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
            temperature=0.3 
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
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'image_keywords' not in st.session_state:
        st.session_state.image_keywords = ""
    if 'pending_completion_sound' not in st.session_state:
        st.session_state.pending_completion_sound = False
    if 'target_article_words' not in st.session_state:
        st.session_state.target_article_words = 3000
    st.session_state.target_article_words = get_target_article_words()

init_state()

def go_to_step(step):
    st.session_state.current_step = step
    save_draft()


def play_step_completion_sound():
    components.html(
        """
        <script>
        (function () {
            try {
                const AudioContextRef = window.AudioContext || window.webkitAudioContext;
                if (!AudioContextRef) return;
                const ctx = new AudioContextRef();
                const playTone = (startOffset, duration, startFreq, endFreq, peakGain) => {
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    const startAt = ctx.currentTime + startOffset;
                    const endAt = startAt + duration;
                    osc.type = "triangle";
                    osc.frequency.setValueAtTime(startFreq, startAt);
                    osc.frequency.exponentialRampToValueAtTime(endFreq, endAt);
                    gain.gain.setValueAtTime(0.0001, startAt);
                    gain.gain.exponentialRampToValueAtTime(peakGain, startAt + 0.03);
                    gain.gain.exponentialRampToValueAtTime(0.0001, endAt);
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.start(startAt);
                    osc.stop(endAt + 0.02);
                };

                // 双音提示：前短后长，更有提醒感，总时长约 2 秒
                playTone(0.00, 0.35, 1120, 900, 0.22);
                playTone(0.55, 1.40, 900, 620, 0.25);
                setTimeout(() => {
                    try { ctx.close(); } catch (e) {}
                }, 2300);
            } catch (e) {}
        })();
        </script>
        """,
        height=0,
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
        render_section_intro("开始提取", "系统会先抓取正文和图片，再将多源素材聚合成统一工作底稿。", "Actions")
        st.markdown("<p class='toolbar-note'>建议先把主题相近的文章和视频放在同一批次里，方便后续自动路由和统一改写。</p>", unsafe_allow_html=True)
        if st.button("开始批量提取内容", type="primary", use_container_width=True):
            article_urls = [url.strip() for url in article_url_input.split('\n') if url.strip()]
            video_urls = [url.strip() for url in video_url_input.split('\n') if url.strip()]

            if not article_urls and not video_urls:
                st.warning("请至少输入一个链接！")
            else:
                total_urls = len(article_urls) + len(video_urls)
                with st.spinner(f"启动全息解析引擎，正在批量获取 {total_urls} 个素材..."):
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

                    extracted_imgs = extracted_imgs[:15]

                    if combined_content:
                        st.session_state.article_url = article_url_input
                        st.session_state.video_url = video_url_input
                        st.session_state.source_content = combined_content
                        st.session_state.source_images = extracted_imgs
                        st.session_state.extraction_success = True
                        notify_step_completed()

                        if errors:
                            st.warning(f"部分内容提取成功 ({success_count}/{total_urls})，但有以下错误：\n" + "\n".join(errors))
                        else:
                            if len(extracted_imgs) > 0:
                                st.success(f"🎉 批量提取成功！共融合了 {success_count} 个素材，并提取到 {len(extracted_imgs)} 张核心配图。")
                            else:
                                st.success(f"🎉 批量提取成功！共融合了 {success_count} 个素材。(无有效配图，走纯文本模式)")
                    else:
                        st.session_state.extraction_success = False
                        st.error("❌ 所有链接提取均失败，请检查链接或网络状态。\n" + "\n".join(errors))

    if st.session_state.extraction_success:
        with st.container(border=True):
            render_section_intro("聚合素材预览", "先快速检查抓取结果，再决定是走手动精调还是全自动驾驶。", "Preview")
            if st.session_state.source_images:
                st.markdown("#### 核心配图")
                img_cols = st.columns(3)
                for idx, img_url in enumerate(st.session_state.source_images):
                    with img_cols[idx % 3]:
                        st.image(img_url, use_column_width=True)
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
                    go_to_step(5)
                    st.rerun()
# --- Step 2 (手动模式) ---
elif st.session_state.current_step == 2:
    render_section_intro("初稿生成", "选择合适的编辑角色，确认当前模型与写作规范，然后输出首版文章。", "Step 02")
    render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"目标字数：约 {get_target_article_words()} 字", f"分镜脚本：{'开启' if enable_script else '关闭'}"])
    
    editor_options = list(prompts_data["editors"].keys())
    if 'selected_role' not in st.session_state or st.session_state.selected_role not in editor_options:
        st.session_state.selected_role = editor_options[0]
        
    default_idx = editor_options.index(st.session_state.selected_role)
    
    editor_role = st.selectbox("选择【编辑】视角", editor_options, index=default_idx)
    st.session_state.selected_role = editor_role 

    target_article_words = st.slider(
        "🧮 全局目标字数（200-5000）",
        min_value=200,
        max_value=5000,
        value=get_target_article_words(),
        step=100,
        help="本轮稿件统一使用该字数目标；若角色 Prompt 里有固定字数要求，会自动被全局目标覆盖。"
    )
    st.session_state.target_article_words = target_article_words
    
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
                notify_step_completed(defer_until_rerun=True)
                go_to_step(3)
                st.rerun()

# --- Step 3 (手动模式) ---
elif st.session_state.current_step == 3:
    render_section_intro("严格审稿", "主编从事实、逻辑和风格三个维度核查初稿，确保对外可发布。", "Step 03")
    render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"目标字数：约 {get_target_article_words()} 字", f"分镜脚本：{'开启' if enable_script else '关闭'}"])
    
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
                
                if enable_script:
                    script_sys_prompt = get_script_sys_prompt(script_duration)
                    st.session_state.spoken_script = call_llm(
                        api_key=api_key, base_url=current_base_url, model_name=selected_model,
                        system_prompt=script_sys_prompt,
                        user_content=f"【请将以下深度文章转化为供剪映AI解析的{script_duration}口播与分镜脚本】：\n\n{st.session_state.final_article}"
                    )
                else:
                    st.session_state.spoken_script = ""
                    
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
    render_section_intro("定稿修订", "根据主编反馈完成最后一轮修改，并同步决定是否生成脚本。", "Step 04")
    render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"目标字数：约 {get_target_article_words()} 字", f"分镜脚本：{'开启' if enable_script else '关闭'}"])
    
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
                    
                notify_step_completed(defer_until_rerun=True)
                go_to_step(5)
                st.rerun()
    with col3:
        if st.button(f"✨ 使用 {selected_model} 接受意见并修改文章"):
            spinner_msg = f"编辑正在修改文章，并生成【{script_duration}口播及分镜脚本】..." if enable_script else "编辑正在根据主编意见修改文章..."
            with st.spinner(spinner_msg):
                global_instruction = prompts_data.get("global_instruction", "")
                modification_prompt = build_modification_system_prompt(global_instruction)
                
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
                
                notify_step_completed(defer_until_rerun=True)
                go_to_step(5)
                st.rerun()

# --- Step 5：终极版分栏 UI ---
elif st.session_state.current_step == 5:
    render_section_intro("分发工作台", "在统一界面完成定稿审阅、脚本联动、搜图建议、导出分发和后续精修。", "Step 05")
    render_context_strip([f"最终角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '自动路由'}", f"当前模型：{selected_model}", f"脚本状态：{'已生成' if st.session_state.spoken_script else '未生成'}"])
    
    st.markdown("<p class='toolbar-note'>主稿、分镜脚本、搜图和分发操作统一留在左侧主工作区；右侧专门用于精修、追问和追溯原文依据。</p>", unsafe_allow_html=True)
    left_col, right_col = st.columns([1.45, 0.95])
    
    with left_col:
        st.markdown("### 主稿面板")
        st.code(st.session_state.final_article, language="markdown")
        
        if st.session_state.spoken_script:
            st.divider()
            st.markdown(f"### 分镜脚本 · {script_duration}")
            st.code(st.session_state.spoken_script, language="markdown")
        
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




























