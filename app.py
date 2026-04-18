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
import httpx
import requests
import json
from bs4 import BeautifulSoup
import re
import streamlit.components.v1 as components
import base64
import hashlib
from datetime import datetime
import time
import ctypes
try:
    import pandas as pd
except Exception:
    pd = None

from podcast_audio import (
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
    VOICE_LABELS as PODCAST_VOICE_LABELS,
    PodcastAudioError,
    synthesize_podcast,
)
from podcast_script import get_podcast_sys_prompt, normalize_podcast_segments, parse_podcast_script_segments


# ==========================================
# 0. 提示词与草稿持久化管理 (JSON 存储)
# ==========================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_AUDIO_DIR = os.path.join(APP_DIR, "runtime_audio")
PODCAST_OUTPUT_DIR = os.path.join(RUNTIME_AUDIO_DIR, "podcasts")
PODCAST_CACHE_DIR = os.path.join(RUNTIME_AUDIO_DIR, "tts_cache")

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
AI_DIAGNOSTIC_LOG = os.path.join(APP_DIR, "ai_diagnostics.log")
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


def build_exception_chain(exc):
    chain = []
    current = exc
    seen = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        label = type(current).__name__
        message = str(current).strip()
        chain.append(f"{label}: {message}" if message else label)
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return chain


def format_llm_exception(exc):
    chain = build_exception_chain(exc)
    if not chain:
        return "Unknown error."
    if len(chain) == 1:
        return chain[0]
    return " -> ".join(chain)


def build_proxy_diagnostic_snapshot():
    env_proxy_map = {}
    for env_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            env_proxy_map[env_name] = env_value

    return {
        "http_proxy": env_proxy_map.get("HTTP_PROXY", ""),
        "https_proxy": env_proxy_map.get("HTTPS_PROXY", ""),
        "all_proxy": env_proxy_map.get("ALL_PROXY", ""),
        "no_proxy": env_proxy_map.get("NO_PROXY", ""),
        "env_proxy_keys": sorted(env_proxy_map.keys()),
        "trust_env_disabled": True,
    }


def write_ai_diagnostic_log(stage_name, base_url, model_name, error_message, user_content, image_count=0, history_count=0, extra_details=None):
    payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage_name or "",
        "base_url": base_url or "",
        "model": model_name or "",
        "error": error_message,
        "image_count": image_count,
        "history_count": history_count,
        "user_content_chars": len(user_content or ""),
    }
    if extra_details:
        payload.update(extra_details)
    try:
        with open(AI_DIAGNOSTIC_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass

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
        'review_feedback', 'modified_article', 'final_article', 'title_candidates', 'highlighted_article', 'spoken_script',
        'podcast_enabled', 'podcast_duration', 'podcast_script_raw', 'podcast_script_segments',
        'podcast_audio_path', 'podcast_audio_manifest', 'podcast_last_error', 'podcast_voice',
        'podcast_tts_provider', 'podcast_tts_api_key_present',
        'chat_history', 'image_keywords', 'selected_role', 'target_article_words',
        'source_images_all', 'selected_source_image_ids', 'article_versions',
        'active_article_version_id', 'de_ai_model', 'de_ai_variant', 'de_ai_temperature',
        'de_ai_prompt_template', 'pending_ai_stage', 'last_completed_ai_stage',
        'last_completed_ai_target_step', 'last_ai_error', 'recovered_ai_notice',
        'obsidian_enabled', 'obsidian_vault_path', 'obsidian_max_hits',
        'obsidian_show_hits', 'obsidian_hits', 'obsidian_research_brief',
        'obsidian_retrieval_error', 'obsidian_query_terms', 'obsidian_wiki_root',
        'obsidian_last_indexed_at', 'obsidian_retrieval_signature',
        'obsidian_influence_map', 'obsidian_influence_summary', 'obsidian_influence_signature'
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


def ensure_podcast_runtime_dirs():
    os.makedirs(PODCAST_OUTPUT_DIR, exist_ok=True)
    os.makedirs(PODCAST_CACHE_DIR, exist_ok=True)


def reset_podcast_outputs(delete_audio=False):
    audio_path = st.session_state.get("podcast_audio_path", "")
    if delete_audio and audio_path and os.path.exists(audio_path):
        try:
            os.remove(audio_path)
        except OSError:
            pass
    st.session_state.podcast_script_raw = ""
    st.session_state.podcast_script_segments = []
    st.session_state.podcast_audio_path = ""
    st.session_state.podcast_audio_manifest = {}
    st.session_state.podcast_last_error = ""


def generate_podcast_script_for_current_article(api_key, base_url, model_name, podcast_duration):
    final_article = get_article_body_text((st.session_state.get("final_article") or "").strip())
    if not final_article:
        reset_podcast_outputs(delete_audio=True)
        return False

    raw_script = call_llm(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        system_prompt=get_podcast_sys_prompt(podcast_duration),
        user_content=f"请将以下文章改写为适合 {podcast_duration} 的单人中文播客解说 JSON：\n\n{final_article}",
    )

    try:
        segments = parse_podcast_script_segments(raw_script)
    except ValueError as exc:
        reset_podcast_outputs(delete_audio=True)
        st.session_state.podcast_script_raw = raw_script
        st.session_state.podcast_last_error = str(exc)
        save_draft()
        return False

    st.session_state.podcast_script_raw = raw_script
    st.session_state.podcast_script_segments = segments
    st.session_state.podcast_audio_path = ""
    st.session_state.podcast_audio_manifest = {}
    st.session_state.podcast_last_error = ""
    save_draft()
    return True

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
        f"[Global Length Requirement | Highest Priority]\n"
        f"Target length for this run: about {target_words} words (allow +/-10%).\n"
        "If any role prompt contains a fixed word-count target, ignore it and follow this run-level target instead."
    )


ARTICLE_TITLE_MARKER = "\u3010\u5907\u9009\u6807\u9898\u3011"
ARTICLE_BODY_MARKER = "\u3010\u6b63\u6587\u3011"
PURE_TITLE_MARKER = "\u3010\u7eaf\u51c0\u6807\u9898\u7ec4\u3011"
PURE_BODY_MARKER = "\u3010\u7eaf\u51c0\u5b9a\u7a3f\u3011"
HIGHLIGHT_MARKER = "\u3010\u9ad8\u4eae\u9605\u8bfb\u7248\u3011"


def build_article_structure_instruction(target_words=None):
    if target_words is None:
        target_words = get_target_article_words()
    try:
        target_words = int(target_words)
    except Exception:
        target_words = get_target_article_words()

    if target_words < 1200:
        heading_instruction = "Use exactly 3 `##` sections for a short article."
    elif target_words <= 2500:
        heading_instruction = "Use 3-5 `##` sections so the middle of the article is clearly structured."
    else:
        heading_instruction = "Use 4-5 `##` sections by default; add `###` only when one section is genuinely complex."

    return "\n".join([
        "[Article Structure Protocol | Highest Priority]",
        "The article body must be written in Markdown.",
        "Use a short opening lede of 1-2 paragraphs, then move into the main analysis.",
        heading_instruction,
        "Each `##` section should usually contain 2-4 natural paragraphs.",
        "Section titles must be informative and specific; do not use empty headings like `Background`, `Summary`, or `Some Thoughts`.",
        "Keep the structure clear without making every section mechanically equal.",
    ])


def build_reviewer_structure_instruction():
    structure_check_marker = "\u3010\u7ed3\u6784\u6027\u68c0\u67e5\u3011"
    background_check_marker = "\u3010\u80cc\u666f\u5b8c\u6574\u6027\u68c0\u67e5\u3011"
    title_check_marker = "\u3010\u6807\u9898\u7ec4\u68c0\u67e5\u3011"
    return "\n".join([
        "[Structural Review Requirements]",
        f"You must add a dedicated `{structure_check_marker}` section to the review report.",
        f"Inside that section, you must explicitly cover `{background_check_marker}` and `{title_check_marker}`.",
        "Check whether the opening 1-2 paragraphs clearly tell the reader which game, event, company, or controversy the article is about.",
        "Check whether the opening also explains why this case matters now before the analysis begins.",
        "Check whether 3-5 candidate titles are still present, aligned with the body, and not drifting in framing.",
        "Check whether any `##` section is too long, whether headings are too vague, and whether the article still reads like one uninterrupted wall of text.",
        "If the structure is good, say so explicitly. If it is not, give concrete repair instructions in the revision feedback.",
    ])


def build_reviewer_system_prompt(reviewer_prompt, anti_hallucination_instruction=""):
    prompt_parts = [
        reviewer_prompt.strip() if isinstance(reviewer_prompt, str) else "",
        build_reviewer_structure_instruction(),
        anti_hallucination_instruction.strip() if isinstance(anti_hallucination_instruction, str) else "",
    ]
    return "\n\n".join([part for part in prompt_parts if part])


def build_article_output_instruction():
    return "\n".join([
        "[Opening Background Protocol | Highest Priority]",
        "Paragraph 1 must tell the reader exactly which game, event, company, or controversy this article is about.",
        "Paragraph 2 should explain the immediate background, the current debate, or why this case is worth analyzing now.",
        "Do not start with conclusions, monetization analysis, or system-level breakdown before the object/event is introduced.",
        "[Structured Output Protocol | Strict]",
        "Keep the title group alive at every stage. Do not swallow it into the body.",
        "All candidate titles and the body must be written in Simplified Chinese.",
        f"Output exactly two blocks in this order:\n{ARTICLE_TITLE_MARKER}\n1. ...\n2. ...\n3. ...\nProvide 3-5 candidate titles.\n\n{ARTICLE_BODY_MARKER}\nWrite the full article body here.",
    ])


def build_editor_system_prompt(editor_prompt, global_instruction):
    prompt_parts = [
        sanitize_editor_prompt(editor_prompt),
        build_target_length_instruction(),
        build_article_structure_instruction(),
        build_article_output_instruction(),
        global_instruction.strip() if isinstance(global_instruction, str) else "",
    ]
    return "\n\n".join([part for part in prompt_parts if part])


def build_modification_system_prompt(global_instruction):
    base_prompt = (
        "You are a professional article editor. Rewrite the draft according to the review feedback. "
        "Preserve the candidate-title block, preserve the factual order, and output the final revised article directly with no extra explanation."
    )
    prompt_parts = [
        base_prompt,
        build_target_length_instruction(),
        build_article_structure_instruction(),
        build_article_output_instruction(),
        global_instruction.strip() if isinstance(global_instruction, str) else "",
    ]
    return "\n\n".join([part for part in prompt_parts if part])


OBSIDIAN_CATEGORY_WEIGHTS = {
    "00_overviews": 60,
    "05_analyses": 50,
    "03_concepts": 40,
    "01_sources": 30,
    "02_entities": 24,
    "04_topics": 22,
    "06_queries": 18,
}

OBSIDIAN_CATEGORY_LABELS = {
    "00_overviews": "综述",
    "01_sources": "来源",
    "02_entities": "实体",
    "03_concepts": "概念",
    "04_topics": "主题",
    "05_analyses": "分析",
    "06_queries": "查询",
}

OBSIDIAN_CATEGORY_ORDER = [
    "00_overviews",
    "05_analyses",
    "03_concepts",
    "01_sources",
    "02_entities",
    "04_topics",
    "06_queries",
]

QUERY_STOPWORDS_EN = {
    "about", "after", "again", "also", "among", "an", "and", "any", "are", "article",
    "because", "before", "being", "between", "block", "brief", "can", "could", "content",
    "does", "for", "from", "game", "games", "gaming", "have", "how", "into", "its",
    "just", "more", "most", "news", "over", "report", "said", "same", "sort", "than",
    "that", "the", "their", "there", "these", "they", "this", "those", "through", "under",
    "using", "video", "were", "what", "when", "where", "which", "while", "will", "with",
    "would", "your", "you",
}

QUERY_STOPWORDS_ZH = {
    "\u6e38\u620f", "\u884c\u4e1a", "\u6587\u7ae0", "\u7d20\u6750", "\u5185\u5bb9", "\u89c6\u9891", "\u4fe1\u606f", "\u76f8\u5173", "\u4eca\u5929", "\u672c\u6b21", "\u8fd9\u6b21",
    "\u8fd9\u7bc7", "\u8fd9\u4e2a", "\u90a3\u4e2a", "\u6211\u4eec", "\u4ed6\u4eec", "\u4ee5\u53ca", "\u56e0\u4e3a", "\u6240\u4ee5", "\u53ef\u4ee5", "\u5982\u679c", "\u5bf9\u4e8e",
    "\u5173\u4e8e", "\u901a\u8fc7", "\u8fdb\u884c", "\u8868\u793a", "\u8ba4\u4e3a", "\u663e\u793a", "\u5176\u4e2d", "\u5df2\u7ecf", "\u53ef\u80fd",
}

ENGLISH_SIGNAL_HINTS = {
    "abtest", "arpdau", "battlepass", "battle-pass", "casual", "cpi", "cpp", "creative",
    "event", "fail", "gacha", "hybridcasual", "hybrid-casual", "iap", "idle", "liveops",
    "ltv", "market", "match3", "merge", "meta", "midcore", "monetization", "puzzle",
    "retention", "revenue", "revive", "roi", "rpg", "season", "shop", "slg", "subgenre",
    "ua", "webshop", "web-shop",
}

LOW_SIGNAL_DOC_FILENAMES = {"index.md", "log.md"}
LOW_SIGNAL_DIRNAMES = {"07_attachments", "raw", "schema", "templates"}
LOW_SIGNAL_SECTION_MARKERS = (
    "suggested pages", "related pages", "related page", "related notes", "related sources", "see also",
    "\u63a8\u8350\u9605\u8bfb", "\u76f8\u5173\u9875\u9762", "\u76f8\u5173\u9875", "\u76f8\u5173\u9605\u8bfb", "\u76f8\u5173\u6765\u6e90", "\u53c2\u89c1",
)

DEFINITION_SECTION_MARKERS = (
    "definition", "overview", "summary", "core", "thesis", "framework", "signals", "metrics",
    "\u5b9a\u4e49", "\u6982\u5ff5", "\u662f\u4ec0\u4e48", "\u6838\u5fc3", "\u7279\u5f81", "\u673a\u5236", "\u73a9\u6cd5", "\u6846\u67b6",
    "\u7814\u7a76\u4e0e\u6307\u6807", "\u5173\u952e\u4e3b\u5f20", "\u603b\u89c8", "\u6458\u8981",
)

def reset_obsidian_context():
    st.session_state.obsidian_hits = []
    st.session_state.obsidian_research_brief = ""
    st.session_state.obsidian_retrieval_error = ""
    st.session_state.obsidian_query_terms = []
    st.session_state.obsidian_wiki_root = ""
    st.session_state.obsidian_last_indexed_at = ""
    st.session_state.obsidian_retrieval_signature = ""
    st.session_state.obsidian_influence_map = []
    st.session_state.obsidian_influence_summary = ""
    st.session_state.obsidian_influence_signature = ""


def resolve_obsidian_wiki_root(vault_path):
    raw_path = (vault_path or "").strip()
    if not raw_path:
        return None, ""

    base_path = Path(os.path.expandvars(raw_path)).expanduser()
    candidate_paths = [
        base_path / "LLM Wiki" / "wiki",
        base_path / "wiki",
        base_path,
    ]
    for candidate in candidate_paths:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if not resolved.exists() or not resolved.is_dir():
            continue
        if resolved.name.lower() == "wiki":
            return str(resolved), ""
        if (resolved / "00_overviews").exists() or (resolved / "01_sources").exists():
            return str(resolved), ""
    return None, "??????? Obsidian wiki ???????????LLM Wiki ???? LLM Wiki/wiki ???"

def read_markdown_file(path_obj):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with open(path_obj, "r", encoding=encoding) as f:
                return f.read(), ""
        except Exception:
            continue
    return "", f"Unsupported file encoding: {path_obj.name}"


def strip_markdown_frontmatter(text):
    if not isinstance(text, str):
        return ""
    if text.startswith("---"):
        match = re.match(r"^---\s*\n.*?\n---\s*\n?", text, flags=re.DOTALL)
        if match:
            return text[match.end():]
    return text


def split_markdown_blocks(text):
    return [block.strip() for block in re.split(r"\n\s*\n", text or "") if block.strip()]

def strip_wikilink_markup(text):
    return re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda match: match.group(2) or match.group(1), text or "")

def is_low_signal_heading(line):
    cleaned = re.sub(r"^[#>\-*\s]+", "", (line or "")).strip().casefold()
    return any(marker in cleaned for marker in LOW_SIGNAL_SECTION_MARKERS)

def is_pure_wikilink_block(block):
    lines = [line.strip() for line in (block or "").splitlines() if line.strip()]
    if not lines:
        return True
    joined = "\n".join(lines)
    without_links = re.sub(r"\[\[[^\]]+\]\]", " ", joined)
    without_markup = re.sub(r"[`*_>#\-|/:,\uFF0C\u3002.!?\uFF1F\uFF01\s\[\]\(\)]", "", without_links)
    return len(without_markup) <= 12

def clean_obsidian_content(text, *, preserve_glossary=False):
    blocks = split_markdown_blocks(text)
    if not blocks:
        return ""
    cleaned_blocks = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        first_line = lines[0]
        if is_low_signal_heading(first_line):
            continue
        if is_pure_wikilink_block(block):
            continue
        if not preserve_glossary and block.count("[[") >= 3 and len(lines) <= 6:
            continue
        cleaned_blocks.append(block.strip())
    if cleaned_blocks:
        return "\n\n".join(cleaned_blocks)
    return "\n\n".join(blocks[:2])

def parse_obsidian_doc(path_obj, wiki_root):
    raw_text, read_error = read_markdown_file(path_obj)
    if read_error:
        return None, read_error
    clean_text = strip_markdown_frontmatter(raw_text).strip()
    if not clean_text:
        return None, ""
    title = path_obj.stem
    title_match = re.search(r"^\s*#\s+(.+?)\s*$", clean_text, flags=re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()
    relative_path = path_obj.resolve().relative_to(Path(wiki_root).resolve()).as_posix()
    parts = relative_path.split("/")
    category = parts[0] if parts else ""
    wikilinks = re.findall(r"\[\[([^\]]+)\]\]", clean_text)
    rel_path_lower = relative_path.casefold()
    is_glossary = rel_path_lower.endswith("00_overviews/terms-glossary.md")
    clean_content = clean_obsidian_content(clean_text, preserve_glossary=is_glossary).strip() or clean_text
    return {
        "path": str(path_obj.resolve()),
        "relative_path": relative_path,
        "title": title,
        "category": category,
        "content": clean_text,
        "clean_content": clean_content,
        "wikilinks": wikilinks,
        "is_low_signal_doc": path_obj.name.casefold() in LOW_SIGNAL_DOC_FILENAMES,
        "modified_at": datetime.fromtimestamp(path_obj.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
    }, ""

def load_obsidian_documents(wiki_root):
    root_path = Path(wiki_root)
    documents = []
    read_errors = []
    for path_obj in sorted(root_path.rglob("*.md")):
        lower_parts = {part.casefold() for part in path_obj.parts}
        if not path_obj.is_file() or any(part in LOW_SIGNAL_DIRNAMES for part in lower_parts):
            continue
        if path_obj.name.casefold() in LOW_SIGNAL_DOC_FILENAMES:
            continue
        doc, read_error = parse_obsidian_doc(path_obj, wiki_root)
        if read_error:
            read_errors.append(read_error)
            continue
        if doc and not doc.get("is_low_signal_doc"):
            documents.append(doc)
    return documents, read_errors

def normalize_query_token(token):
    cleaned = token.strip().strip("()[]{}<>.,!?;:'\"`")
    return cleaned.casefold()

def extract_query_context(source_content):
    text = (source_content or "").strip()
    english_tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-\+]{2,}", text)
    chinese_tokens = re.findall("[\u4E00-\u9FFF]{2,12}", text)
    score_map = {}
    order_map = {}

    def bump(term, weight, order_idx):
        score_map[term] = score_map.get(term, 0) + weight
        order_map.setdefault(term, order_idx)

    for idx, token in enumerate(english_tokens):
        normalized = normalize_query_token(token)
        if not normalized or normalized in QUERY_STOPWORDS_EN:
            continue
        if len(normalized) < 4 and normalized not in ENGLISH_SIGNAL_HINTS:
            continue
        weight = 3 if normalized in ENGLISH_SIGNAL_HINTS or any(ch.isdigit() for ch in normalized) or "-" in normalized or "+" in normalized else 1
        bump(normalized, weight, idx)

    offset = len(english_tokens)
    for idx, token in enumerate(chinese_tokens, start=offset):
        normalized = normalize_query_token(token)
        if not normalized or normalized in QUERY_STOPWORDS_ZH:
            continue
        weight = 2 if len(normalized) >= 3 else 1
        bump(normalized, weight, idx)

    ranked_terms = sorted(score_map.items(), key=lambda item: (-item[1], order_map.get(item[0], 0), item[0]))
    return {"keywords": [term for term, _ in ranked_terms[:24]], "raw_text": text}

def load_terms_glossary(documents):
    glossary_map = {}
    glossary_doc = None
    for doc in documents:
        rel_path = doc.get("relative_path", "").casefold()
        title = doc.get("title", "").casefold()
        if rel_path.endswith("00_overviews/terms-glossary.md") or "terms-glossary" in title:
            glossary_doc = doc
            break
    if not glossary_doc:
        return glossary_map

    for line in glossary_doc.get("content", "").splitlines():
        cleaned = re.sub(r"[`*_>#\-|]", " ", line).strip()
        if not cleaned:
            continue
        paren_match = re.search(r"(.+?)[\(（]([^\)）]{2,80})[\)）]", cleaned)
        if paren_match:
            left = normalize_query_token(paren_match.group(1))
            right = normalize_query_token(paren_match.group(2))
            if left and right and left != right:
                glossary_map.setdefault(left, set()).add(right)
                glossary_map.setdefault(right, set()).add(left)
        if ":" in cleaned or "：" in cleaned:
            left, right = re.split(r"[:：]", cleaned, maxsplit=1)
            left_token = normalize_query_token(left)
            right_tokens = [normalize_query_token(part) for part in re.split(r"[,，/、]", right)]
            if left_token:
                for token in right_tokens:
                    if token and token != left_token:
                        glossary_map.setdefault(left_token, set()).add(token)
                        glossary_map.setdefault(token, set()).add(left_token)
    return glossary_map


def expand_query_terms(query_terms, glossary_map):
    expanded_terms = []
    seen_terms = set()
    def push_term(term):
        normalized = normalize_query_token(term)
        if not normalized or normalized in seen_terms:
            return
        seen_terms.add(normalized)
        expanded_terms.append(normalized)
    for term in query_terms:
        push_term(term)
        for alias in glossary_map.get(normalize_query_token(term), set()):
            push_term(alias)
    return expanded_terms


def get_obsidian_category_label(category):
    return OBSIDIAN_CATEGORY_LABELS.get(category, category or "未分类")

def count_wikilinks(text):
    return len(re.findall(r"\[\[[^\]]+\]\]", text or ""))


def is_definition_like_block(first_line, block, category):
    first_line_clean = re.sub(r"^[#>\-*\s]+", "", (first_line or "")).strip().casefold()
    block_clean = strip_wikilink_markup(block or "")
    if any(marker in first_line_clean for marker in DEFINITION_SECTION_MARKERS):
        return True
    if category == "03_concepts":
        if not first_line.startswith("#") and len(block_clean) >= 80 and count_wikilinks(block) <= 1:
            return True
        if re.search(r"\b(is|means|refers to|describes)\b", block_clean.casefold()):
            return True
        if any(marker in block_clean for marker in ("是指", "指的是", "通常指", "本质上", "核心在于")):
            return True
    return False


def score_excerpt_block(block, matched_terms, category):
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        return -999
    first_line = lines[0]
    if is_low_signal_heading(first_line) or is_pure_wikilink_block(block):
        return -999

    lowered = block.casefold()
    block_score = sum(14 for term in matched_terms[:6] if term in lowered)
    wikilink_count = count_wikilinks(block)
    prose_block = strip_wikilink_markup(block)

    if wikilink_count == 0:
        block_score += 4
    elif wikilink_count == 1:
        block_score += 1
    else:
        block_score -= min(6, wikilink_count)

    if re.search(r"[。！？.!?]", prose_block):
        block_score += 2
    if first_line.startswith("#"):
        block_score -= 2
    if category == "03_concepts":
        if is_definition_like_block(first_line, block, category):
            block_score += 12
        elif wikilink_count >= 2:
            block_score -= 8
    elif category == "00_overviews" and is_definition_like_block(first_line, block, category):
        block_score += 6
    elif category == "01_sources" and any(marker in first_line.casefold() for marker in ("key claims", "summary", "takeaways", "结论", "要点", "摘要", "关键主张")):
        block_score += 8

    return block_score


def extract_best_excerpt(content, matched_terms, max_chars, *, category="", title=""):
    blocks = split_markdown_blocks(content) or [(content or "").strip()]
    chosen_block = ""
    chosen_score = -1
    fallback_block = ""
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        block_score = score_excerpt_block(block, matched_terms, category)
        if block_score <= -999:
            continue
        if not fallback_block:
            fallback_block = block
        if block_score > chosen_score:
            chosen_score = block_score
            chosen_block = block

    chosen_block = chosen_block or fallback_block
    if not chosen_block:
        return ""
    chosen_block = strip_wikilink_markup(chosen_block)
    chosen_block = re.sub(r"\n{3,}", "\n\n", chosen_block).strip()
    if title and chosen_block.casefold().startswith(title.casefold()):
        chosen_block = chosen_block[len(title):].lstrip(" :\uFF1A-\n") or chosen_block
    if len(chosen_block) > max_chars:
        chosen_block = chosen_block[:max_chars].rstrip() + "..."
    return chosen_block


def score_obsidian_docs(query_context, documents, glossary_map):
    base_terms = [normalize_query_token(term) for term in query_context.get("keywords", []) if normalize_query_token(term)]
    base_term_set = set(base_terms)
    query_terms = expand_query_terms(base_terms, glossary_map)
    scored_docs = []
    for doc in documents:
        if doc.get("is_low_signal_doc"):
            continue
        title_lower = doc.get("title", "").casefold()
        content_lower = doc.get("clean_content", doc.get("content", "")).casefold()
        path_lower = doc.get("relative_path", "").casefold()
        category = doc.get("category", "")
        score = OBSIDIAN_CATEGORY_WEIGHTS.get(category, 12)
        title_matches = []
        path_matches = []
        content_matches = []
        for term in query_terms:
            is_base_term = term in base_term_set
            if term in title_lower:
                score += 18 if is_base_term else 12
                title_matches.append(term)
            elif term in path_lower:
                score += 9 if is_base_term else 6
                path_matches.append(term)
            elif term in content_lower:
                score += 5 if is_base_term else 3
                content_matches.append(term)
        rel_path = doc.get("relative_path", "")
        rel_path_lower = rel_path.casefold()
        matched_terms = []
        seen_terms = set()
        for term in title_matches + path_matches + content_matches:
            if term in seen_terms:
                continue
            seen_terms.add(term)
            matched_terms.append(term)
        if not matched_terms:
            continue
        if not title_matches and not path_matches and len(set(content_matches)) < 2:
            continue
        if rel_path_lower.endswith("00_overviews/mobile-game-trends-2025.md") and matched_terms:
            score += 8
        if rel_path_lower.endswith("00_overviews/terms-glossary.md") and matched_terms:
            score -= 28
            if title_matches or path_matches:
                score += 2
            if len(set(content_matches)) < 3 and not title_matches and not path_matches:
                continue
            if any(term in ENGLISH_SIGNAL_HINTS for term in matched_terms[:6]):
                score += 1
        if category == "03_concepts" and title_matches:
            score += 6
        if title_matches:
            score += 4
        scored_docs.append({
            "doc": doc,
            "score": score,
            "matched_terms": matched_terms,
            "title_matches": title_matches,
            "path_matches": path_matches,
            "content_matches": content_matches,
        })
    scored_docs.sort(key=lambda item: (-item["score"], OBSIDIAN_CATEGORY_ORDER.index(item["doc"].get("category")) if item["doc"].get("category") in OBSIDIAN_CATEGORY_ORDER else 999, item["doc"].get("title", "")))
    return scored_docs

def build_obsidian_hits(scored_docs, max_hits, max_chars_per_hit):
    hits = []
    for item in scored_docs:
        if len(hits) >= max_hits:
            break
        doc = item["doc"]
        matched_terms = item.get("matched_terms", [])
        excerpt = extract_best_excerpt(
            doc.get("clean_content", doc.get("content", "")),
            matched_terms,
            max_chars_per_hit,
            category=doc.get("category", ""),
            title=doc.get("title", ""),
        )
        first_excerpt_line = excerpt.splitlines()[0] if excerpt.splitlines() else ""
        if not excerpt or is_pure_wikilink_block(excerpt) or is_low_signal_heading(first_excerpt_line):
            continue
        hits.append({
            "title": doc.get("title", ""),
            "path": doc.get("relative_path", ""),
            "category": doc.get("category", ""),
            "category_label": get_obsidian_category_label(doc.get("category", "")),
            "score": item.get("score", 0),
            "excerpt": excerpt,
            "reason": "命中词：" + "、".join(matched_terms[:5]),
            "matched_terms": matched_terms,
            "modified_at": doc.get("modified_at", ""),
        })
    return hits


def build_research_brief(hits):
    if not hits:
        return ""

    def is_glossary_hit(hit):
        return hit.get("path", "").casefold().endswith("00_overviews/terms-glossary.md")

    grouped_hits = {}
    for hit in hits:
        excerpt = hit.get("excerpt", "")
        if not excerpt or is_pure_wikilink_block(excerpt):
            continue
        first_line = excerpt.splitlines()[0] if excerpt.splitlines() else ""
        if is_low_signal_heading(first_line):
            continue
        grouped_hits.setdefault(hit.get("category", ""), []).append(hit)

    brief_parts = [
        "[知识使用说明] 以下内容来自本地 Obsidian 知识库，仅可用于背景补充、概念定义、历史案例与分析框架。如果与当前素材包冲突，以当前素材包为准。"
    ]
    heading_map = {
        "00_overviews": "[相关综述]",
        "05_analyses": "[可复用分析框架]",
        "03_concepts": "[相关概念定义]",
        "01_sources": "[相关案例与来源摘要]",
    }
    for category in ("00_overviews", "05_analyses", "03_concepts", "01_sources"):
        category_hits = grouped_hits.get(category, [])
        if not category_hits:
            continue
        if category == "00_overviews":
            category_hits = sorted(category_hits, key=lambda hit: (is_glossary_hit(hit), -hit.get("score", 0), hit.get("title", "")))
        else:
            category_hits = sorted(category_hits, key=lambda hit: (-hit.get("score", 0), hit.get("title", "")))
        brief_parts.append(heading_map[category])
        for hit in category_hits[:2]:
            line = f"- {hit.get('title', '')}：{hit.get('excerpt', '')}"
            if hit.get("matched_terms"):
                line += f"（命中词：{'、'.join(hit['matched_terms'][:4])}）"
            brief_parts.append(line)
    brief = "\n".join(brief_parts).strip()
    return brief[:3200].rstrip() + ("..." if len(brief) > 3200 else "")


def build_editor_user_content(source_content, research_brief, use_images=False):
    lead = "Below is a merged source packet. Use the attached reference images together with the text for deep analysis and synthesis:" if use_images else "Below is a merged source packet. Use the text-only source packet for deep analysis and synthesis:"
    parts = [f"{lead}\n\n{(source_content or '').strip()}"]
    if research_brief:
        parts.append(
            "Below is a research brief from your local Obsidian knowledge base. It may only be used for background, concept definitions, historical cases, and analysis frameworks. If anything conflicts with the current source packet, the current source packet wins.\n\n" + research_brief.strip()
        )
    return "\n\n================\n\n".join(part for part in parts if part.strip())


def normalize_title_candidates(title_candidates):
    if isinstance(title_candidates, str):
        raw_lines = title_candidates.splitlines()
    else:
        raw_lines = []
        for item in title_candidates or []:
            raw_lines.extend(str(item).splitlines())

    normalized = []
    seen = set()
    ignored_markers = {
        ARTICLE_TITLE_MARKER,
        ARTICLE_BODY_MARKER,
        PURE_TITLE_MARKER,
        PURE_BODY_MARKER,
        HIGHLIGHT_MARKER,
    }
    for line in raw_lines:
        cleaned = str(line).strip()
        cleaned = re.sub(r"^\s*(?:[-*#]|\d+[\.)])\s*", "", cleaned)
        cleaned = cleaned.strip(" \t\"'`[]")
        if not cleaned or cleaned in ignored_markers:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized[:5]


def parse_title_candidates_block(block_text):
    return normalize_title_candidates(block_text)


def split_structured_article_sections(article_text):
    clean_text = (article_text or "").strip()
    if not clean_text:
        return [], ""

    if ARTICLE_TITLE_MARKER in clean_text:
        after_title = clean_text.split(ARTICLE_TITLE_MARKER, 1)[1]
        if ARTICLE_BODY_MARKER in after_title:
            title_block, body_block = after_title.split(ARTICLE_BODY_MARKER, 1)
            return parse_title_candidates_block(title_block), body_block.strip()

    if ARTICLE_BODY_MARKER in clean_text:
        return [], clean_text.split(ARTICLE_BODY_MARKER, 1)[1].strip()

    return [], clean_text


def build_title_candidates_block(title_candidates, marker=ARTICLE_TITLE_MARKER):
    titles = normalize_title_candidates(title_candidates)
    if not titles:
        return ""
    lines = [marker]
    lines.extend(f"{idx}. {title}" for idx, title in enumerate(titles, start=1))
    return "\n".join(lines)


def build_structured_article_text(title_candidates, article_body):
    title_block = build_title_candidates_block(title_candidates)
    clean_body = (article_body or "").strip()
    parts = []
    if title_block:
        parts.append(title_block)
    if clean_body:
        parts.append(f"{ARTICLE_BODY_MARKER}\n{clean_body}")
    return "\n\n".join(parts).strip()


def build_display_article_text(article_text, title_candidates=None):
    titles, body = split_structured_article_sections(article_text)
    resolved_titles = titles or normalize_title_candidates(title_candidates)
    clean_body = (body or article_text or "").strip()
    combined = build_structured_article_text(resolved_titles, clean_body)
    return combined or clean_body


def parse_article_generation_response(response_text, fallback_titles=None):
    titles, article_body = split_structured_article_sections(response_text)
    resolved_titles = titles or normalize_title_candidates(fallback_titles)
    clean_body = (article_body or "").strip()
    if not clean_body:
        clean_body = (response_text or "").strip()
    combined_text = build_structured_article_text(resolved_titles, clean_body)
    return resolved_titles, clean_body, combined_text or clean_body


def get_article_body_text(article_text):
    _, article_body = split_structured_article_sections(article_text)
    return (article_body or article_text or "").strip()


def build_reviewer_user_content(source_content, draft_article, research_brief="", title_candidates=None):
    resolved_titles = normalize_title_candidates(title_candidates)
    if not resolved_titles:
        resolved_titles, _ = split_structured_article_sections(draft_article)

    title_block = build_title_candidates_block(resolved_titles)
    if not title_block:
        title_block = ARTICLE_TITLE_MARKER + "\n(Missing title candidates; treat this as a structural issue.)"

    draft_body = get_article_body_text(draft_article)
    parts = [
        "Below is the merged source text (this is the only factual source for review):\n"
        + (source_content or "").strip()
        + "\n\n================\n\n"
        + "Below are the current title candidates:\n"
        + title_block
        + "\n\n================\n\n"
        + "Below is the draft body:\n"
        + draft_body
    ]
    if research_brief:
        parts.append(
            "Below is a knowledge-base research brief. It may only help you judge whether the article aligns with prior analysis frameworks. It must not be used for new fact checking or to override the source packet.\n\n"
            + research_brief.strip()
        )
    return "\n\n".join(parts)


def build_modification_user_content(review_feedback, draft_article, title_candidates=None):
    resolved_titles = normalize_title_candidates(title_candidates)
    if not resolved_titles:
        resolved_titles, _ = split_structured_article_sections(draft_article)

    title_block = build_title_candidates_block(resolved_titles)
    if not title_block:
        title_block = ARTICLE_TITLE_MARKER + "\n1. ...\n2. ...\n3. ..."

    article_body = get_article_body_text(draft_article)
    return (
        "[Current title candidates]\n"
        + title_block
        + "\n\n================\n\n"
        + "[Review feedback]\n"
        + (review_feedback or "").strip()
        + "\n\n================\n\n"
        + "[Current article body]\n"
        + article_body
        + "\n\n[Editing requirements]\n"
        + "Keep the title-candidates block alive. If the direction has not changed, preserve the current title skeleton and only improve wording, precision, and communication power."
    ).strip()


def build_chat_knowledge_context():
    research_brief = (st.session_state.get("obsidian_research_brief", "") or "").strip()
    hits = st.session_state.get("obsidian_hits", []) or []
    if not research_brief and not hits:
        return ""
    parts = []
    if research_brief:
        parts.append(f"[Obsidian 研究摘要]\n{research_brief}")
    if hits:
        hit_lines = [f"- {hit.get('title', '')} | {hit.get('category_label', '')} | {hit.get('path', '')}" for hit in hits[:8]]
        parts.append("[命中笔记清单]\n" + "\n".join(hit_lines))
    return "\n\n".join(parts)


    return "\n\n".join(parts)


def split_article_paragraphs(text):
    return [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]


def extract_obsidian_signal_terms(text):
    terms = []
    seen = set()
    english_tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-\+]{2,}", text or "")
    chinese_tokens = re.findall("[\u4E00-\u9FFF]{2,12}", text or "")
    for token in english_tokens:
        normalized = normalize_query_token(token)
        if not normalized or normalized in QUERY_STOPWORDS_EN:
            continue
        if len(normalized) < 4 and normalized not in ENGLISH_SIGNAL_HINTS:
            continue
        if normalized not in seen:
            seen.add(normalized)
            terms.append(normalized)
    for token in chinese_tokens:
        normalized = normalize_query_token(token)
        if not normalized or normalized in QUERY_STOPWORDS_ZH:
            continue
        if normalized not in seen:
            seen.add(normalized)
            terms.append(normalized)
    return terms[:24]


def score_paragraph_against_obsidian_hit(paragraph, hit):
    paragraph_terms = set(extract_obsidian_signal_terms(paragraph))
    if not paragraph_terms:
        return 0, []

    candidate_terms = []
    for term in hit.get("matched_terms", [])[:8]:
        normalized = normalize_query_token(term)
        if normalized:
            candidate_terms.append(normalized)
    candidate_terms.extend(extract_obsidian_signal_terms(hit.get("title", ""))[:6])
    candidate_terms.extend(extract_obsidian_signal_terms(hit.get("excerpt", ""))[:10])

    deduped_terms = []
    seen = set()
    for term in candidate_terms:
        if term and term not in seen:
            seen.add(term)
            deduped_terms.append(term)

    matched_terms = [term for term in deduped_terms if term in paragraph_terms]
    score = len(matched_terms) * 3
    if hit.get("title") and any(term in paragraph_terms for term in extract_obsidian_signal_terms(hit.get("title", ""))[:4]):
        score += 2
    if any(term in paragraph.casefold() for term in hit.get("matched_terms", [])[:4]):
        score += 2
    return score, matched_terms[:6]


def build_obsidian_influence_map(final_article, hits):
    paragraphs = split_article_paragraphs(final_article)
    influence_items = []
    for idx, paragraph in enumerate(paragraphs, start=1):
        scored_hits = []
        for hit in hits or []:
            score, matched_terms = score_paragraph_against_obsidian_hit(paragraph, hit)
            if score < 5:
                continue
            scored_hits.append({
                "title": hit.get("title", ""),
                "score": score,
                "matched_terms": matched_terms,
            })
        if not scored_hits:
            continue
        scored_hits.sort(key=lambda item: (-item["score"], item["title"]))
        top_hits = scored_hits[:3]
        top_score = top_hits[0]["score"]
        preview = paragraph if len(paragraph) <= 120 else paragraph[:120].rstrip() + "..."
        merged_terms = []
        seen_terms = set()
        for item in top_hits:
            for term in item.get("matched_terms", []):
                if term not in seen_terms:
                    seen_terms.add(term)
                    merged_terms.append(term)
        influence_items.append({
            "paragraph_index": idx,
            "paragraph_preview": preview,
            "influence_level": "高" if top_score >= 10 else "中",
            "score": top_score,
            "note_titles": [item.get("title", "") for item in top_hits if item.get("title")],
            "matched_terms": merged_terms[:6],
        })
    return influence_items


def refresh_obsidian_influence_map(force=False):
    final_article = get_article_body_text((st.session_state.get("final_article", "") or "").strip())
    hits = st.session_state.get("obsidian_hits", []) or []
    if not st.session_state.get("obsidian_enabled") or not final_article or not hits:
        st.session_state.obsidian_influence_map = []
        st.session_state.obsidian_influence_summary = ""
        st.session_state.obsidian_influence_signature = ""
        return
    signature_base = final_article + "\n" + json.dumps(
        [{"title": hit.get("title", ""), "excerpt": hit.get("excerpt", ""), "matched_terms": hit.get("matched_terms", [])} for hit in hits],
        ensure_ascii=False,
        sort_keys=True,
    )
    signature = hashlib.sha1(signature_base.encode("utf-8")).hexdigest()
    if not force and st.session_state.get("obsidian_influence_signature") == signature:
        return
    influence_map = build_obsidian_influence_map(final_article, hits)
    st.session_state.obsidian_influence_map = influence_map
    st.session_state.obsidian_influence_summary = (
        f"共发现 {len(influence_map)} 个段落明显受到 Obsidian 补充内容影响。"
        if influence_map else
        "当前定稿中暂无达到阈值的 Obsidian 影响段落。"
    )
    st.session_state.obsidian_influence_signature = signature


def render_obsidian_influence_panel(influence_map, summary):
    render_section_intro("Obsidian 影响地图", "按段落查看当前定稿中哪些内容明显受本地知识库补充影响。", "影响")
    if summary:
        st.caption(summary)
    if not influence_map:
        st.info("当前定稿里暂无达到阈值的 Obsidian 影响段落。")
        return
    for item in influence_map:
        note_titles = item.get("note_titles", [])
        label = f"第 {item.get('paragraph_index', 0)} 段 · {item.get('influence_level', '中')}影响"
        with st.expander(label, expanded=(item.get("paragraph_index", 0) <= 2)):
            if note_titles:
                st.caption("相关笔记：" + " / ".join(note_titles[:3]))
            if item.get("matched_terms"):
                st.caption("命中关键词：" + "、".join(item.get("matched_terms", [])[:6]))
            st.code(item.get("paragraph_preview", ""), language="markdown")


def run_obsidian_retrieval(force=False):
    if not st.session_state.get("obsidian_enabled"):
        reset_obsidian_context()
        return
    source_content = (st.session_state.get("source_content", "") or "").strip()
    if not source_content:
        reset_obsidian_context()
        return
    wiki_root, resolve_error = resolve_obsidian_wiki_root(st.session_state.get("obsidian_vault_path", ""))
    if resolve_error:
        reset_obsidian_context()
        st.session_state.obsidian_retrieval_error = resolve_error
        save_draft()
        return
    max_hits = int(st.session_state.get("obsidian_max_hits", 6) or 6)
    signature_base = f"{wiki_root}|{max_hits}|{hashlib.sha1(source_content.encode('utf-8')).hexdigest()}"
    signature = hashlib.sha1(signature_base.encode("utf-8")).hexdigest()
    has_cached_result = bool(st.session_state.get("obsidian_hits") or st.session_state.get("obsidian_research_brief") or st.session_state.get("obsidian_retrieval_error"))
    if not force and has_cached_result and st.session_state.get("obsidian_retrieval_signature") == signature:
        return
    documents, read_errors = load_obsidian_documents(wiki_root)
    query_context = extract_query_context(source_content)
    glossary_map = load_terms_glossary(documents)
    scored_docs = score_obsidian_docs(query_context, documents, glossary_map)
    hits = build_obsidian_hits(scored_docs, max_hits=max_hits, max_chars_per_hit=280)
    st.session_state.obsidian_hits = hits
    st.session_state.obsidian_research_brief = build_research_brief(hits)
    st.session_state.obsidian_query_terms = query_context.get("keywords", [])[:12]
    st.session_state.obsidian_wiki_root = wiki_root
    st.session_state.obsidian_last_indexed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.obsidian_retrieval_signature = signature
    st.session_state.obsidian_retrieval_error = "部分 Obsidian 笔记读取失败，已跳过异常文件。" if read_errors else ""
    refresh_obsidian_influence_map(force=True)
    save_draft()

def build_openai_timeout(base_url, model_name):
    base_url = (base_url or "").strip().lower()
    model_name = (model_name or "").strip().lower()

    # Yunwu Qwen models have shown billed-but-disconnected responses, so use a wider timeout window.
    if "yunwu.ai/v1" in base_url and model_name in {"qwen3.5-plus", "qwen3.6-plus"}:
        return httpx.Timeout(connect=30.0, read=1800.0, write=120.0, pool=120.0)

    return httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=60.0)


def build_openai_http_client(timeout_config):
    return httpx.Client(
        timeout=timeout_config,
        trust_env=False,
        http2=False,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=0),
    )


def should_retry_with_requests_fallback(exc):
    error_text = format_llm_exception(exc).lower()
    retry_markers = [
        "apiconnectionerror",
        "remoteprotocolerror",
        "server disconnected without sending a response",
        "remote end closed connection without response",
        "connection aborted",
        "connection reset",
        "connection refused",
        "connection error",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "502",
        "503",
        "504",
    ]
    return any(marker in error_text for marker in retry_markers)


def build_requests_timeout(timeout_config):
    connect_timeout = getattr(timeout_config, "connect", None) or 20.0
    read_timeout = getattr(timeout_config, "read", None) or 600.0
    return (connect_timeout, read_timeout)


def extract_llm_response_content(response_data, empty_choices_message, empty_body_message):
    choices = response_data.get("choices") if isinstance(response_data, dict) else None
    if not choices:
        raise ValueError(empty_choices_message)

    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
    content = normalize_llm_response_content(message.get("content"))
    if not content.strip():
        raise ValueError(empty_body_message)
    return content


def call_llm_via_requests(api_key, base_url, request_payload, timeout_config):
    endpoint = (base_url or "").rstrip("/") + "/chat/completions"
    session = requests.Session()
    session.trust_env = False
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Article-Transcription-Assistant/1.0",
    }

    try:
        response = session.post(
            endpoint,
            headers=headers,
            json=request_payload,
            timeout=build_requests_timeout(timeout_config),
        )
        response.raise_for_status()
        return extract_llm_response_content(
            response.json(),
            "Fallback request returned no choices.",
            "Fallback request returned an empty message body.",
        )
    finally:
        session.close()


def call_llm_via_httpx_raw(api_key, base_url, request_payload, timeout_config):
    endpoint = (base_url or "").rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Article-Transcription-Assistant/1.0",
    }

    with build_openai_http_client(timeout_config) as http_client:
        response = http_client.post(endpoint, headers=headers, json=request_payload)
        response.raise_for_status()
        return extract_llm_response_content(
            response.json(),
            "Raw HTTP fallback returned no choices.",
            "Raw HTTP fallback returned an empty message body.",
        )


def execute_llm_request_once(api_key, base_url, model_name, request_kwargs, timeout_config, use_stream):
    transport_errors = []

    try:
        with build_openai_http_client(timeout_config) as raw_http_client:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=0,
                timeout=timeout_config,
                http_client=raw_http_client,
            )

            if use_stream:
                parts = []
                with client.chat.completions.create(stream=True, **request_kwargs) as stream:
                    for chunk in stream:
                        if not getattr(chunk, "choices", None):
                            continue
                        for choice in chunk.choices:
                            delta = getattr(choice, "delta", None)
                            delta_content = getattr(delta, "content", None) if delta else None
                            if delta_content:
                                parts.append(delta_content)
                content = "".join(parts).strip()
            else:
                response = client.chat.completions.create(**request_kwargs)
                if not getattr(response, "choices", None):
                    raise ValueError("Model returned no choices.")
                message = response.choices[0].message if response.choices else None
                content = normalize_llm_response_content(getattr(message, "content", None))

        if content.strip():
            return content
        raise ValueError("Model returned an empty message body.")
    except Exception as sdk_error:
        transport_errors.append(f"sdk={format_llm_exception(sdk_error)}")
        if not should_retry_with_requests_fallback(sdk_error):
            raise

    for transport_name, transport_fn in (
        ("requests", call_llm_via_requests),
        ("httpx_raw", call_llm_via_httpx_raw),
    ):
        try:
            return transport_fn(api_key, base_url, request_kwargs, timeout_config)
        except Exception as transport_error:
            transport_errors.append(f"{transport_name}={format_llm_exception(transport_error)}")
            if not should_retry_with_requests_fallback(transport_error):
                raise

    raise RuntimeError("All transports failed: " + " | ".join(transport_errors))


def should_stream_llm_request(base_url, model_name):
    base_url = (base_url or "").strip().lower()
    model_name = (model_name or "").strip().lower()
    return "yunwu.ai/v1" in base_url and model_name in {"qwen3.5-plus", "qwen3.6-plus"}


BLTCY_MODEL_OPTIONS = [
    "qwen3.5-plus",
    "kimi-k2.5",
    "gpt-5.4",
    "claude-opus-4-6",
    "gpt-5.4-mini-2026-03-17",
    "gpt-5.4-nano",
    "gemini-3.1-pro-preview",
    "claude-sonnet-4-6-thinking",
    "claude-opus-4-6-thinking",
    "claude-opus-4-5-20251101-thinking",
    "gemini-3.1-pro-preview-thinking-high",
    "gemini-3.1-flash-lite-preview-thinking-high",
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed"
]
DEERAPI_MODEL_OPTIONS = [
    "gpt-5.4",
    "qwen3.5-27b",
    "qwen3.5-flash",
    "gpt-5.4-nano",
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-thinking",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview-thinking"
]
YUNWU_MODEL_OPTIONS = [
    "qwen3.6-plus",
    "qwen3.5-plus",
    "kimi-k2.5",
    "gpt-5.4",
    "claude-opus-4-6",
    "doubao-seed-2-0-lite-260215",
    "gpt-5.4-mini-2026-03-17",
    "gpt-5.4-nano",
    "gemini-3.1-pro-preview",
    "claude-sonnet-4-6-thinking",
    "claude-opus-4-6-thinking",
    "claude-opus-4-5-20251101-thinking",
    "gemini-3.1-pro-preview-thinking-high",
    "gemini-3.1-flash-lite-preview-thinking-high",
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed"
]
DE_AI_MODEL_MIGRATION = {
    "deepseek-v3-1-terminus": "deepseek-v3.1",
    "deepseek-v3-2-exp": "deepseek-v3.2",
}
DE_AI_MODELS = ["deepseek-v3.1", "deepseek-v3.2", "qwen3.5-plus", "glm-5"]
DE_AI_VARIANTS = ["\u666e\u901a\u7248", "\u793e\u533a\u6587\u7ae0\u53bbAI\u7248", "\u81ea\u7136\u5520\u55d1\u7248"]
DE_AI_VARIANT_DEFAULT = DE_AI_VARIANTS[0]
DE_AI_VARIANT_COMMUNITY = DE_AI_VARIANTS[1]
DE_AI_VARIANT_CHAT = DE_AI_VARIANTS[2]
ROLE_AUDIENCE_MAP = {
    "\u53d1\u884c\u4e3b\u7f16": "\u6e38\u620f\u884c\u4e1a\u4ece\u4e1a\u8005",
    "\u7814\u53d1\u4e3b\u7f16": "\u6e38\u620f\u5708\u540c\u884c\u548c\u786c\u6838\u73a9\u5bb6",
    "\u6e38\u620f\u5feb\u8baf\u7f16\u8f91": "\u884c\u4e1a\u4ece\u4e1a\u8005",
    "\u5ba2\u89c2\u8f6c\u5f55\u7f16\u8f91": "\u516c\u4f17\u8bfb\u8005",
    "\u6e38\u620f\u884c\u4e1a\u8bc4\u8bba\u5458": "\u6e38\u620f\u884c\u4e1a\u4ece\u4e1a\u8005",
}
ROLE_JARGON_MAP = {
    "\u53d1\u884c\u4e3b\u7f16": "ROI, LTV, \u4e70\u91cf, \u53d8\u73b0\u6548\u7387",
    "\u7814\u53d1\u4e3b\u7f16": "\u6838\u5fc3\u5faa\u73af, \u7cfb\u7edf\u8bbe\u8ba1, \u6280\u672f\u503a, \u5de5\u4e1a\u5316\u7ba1\u7ebf",
    "\u6e38\u620f\u5feb\u8baf\u7f16\u8f91": "\u7248\u53f7, \u4e0a\u7ebf\u6863\u671f, \u53d1\u884c\u8282\u594f, \u5e02\u573a\u53cd\u9988",
    "\u5ba2\u89c2\u8f6c\u5f55\u7f16\u8f91": "\u7559\u5b58, \u53d8\u73b0, \u672c\u5730\u5316, \u8fd0\u8425\u8282\u594f",
    "\u6e38\u620f\u884c\u4e1a\u8bc4\u8bba\u5458": "ROI, \u6d17\u91cf, \u8dd1\u91cf, \u5546\u4e1a\u5316\u6548\u7387",
}
ROLE_TONE_MAP = {
    "\u53d1\u884c\u4e3b\u7f16": "\u51b7\u9759\u3001\u950b\u5229\u3001\u5224\u65ad\u660e\u786e",
    "\u7814\u53d1\u4e3b\u7f16": "\u4e13\u4e1a\u3001\u76f4\u63a5\u3001\u5e26\u4e00\u70b9\u6bd2\u820c",
    "\u6e38\u620f\u5feb\u8baf\u7f16\u8f91": "\u514b\u5236\u3001\u6e05\u695a\u3001\u4fe1\u606f\u5bc6\u5ea6\u9ad8",
    "\u5ba2\u89c2\u8f6c\u5f55\u7f16\u8f91": "\u5e73\u5b9e\u3001\u5e72\u51c0\u3001\u7ed3\u6784\u6e05\u6670",
    "\u6e38\u620f\u884c\u4e1a\u8bc4\u8bba\u5458": "\u4e00\u9488\u89c1\u8840\u3001\u5f3a\u89c2\u70b9\u4f46\u4e0d\u6d6e\u5938",
}


def infer_role_persona(role_name, editor_prompt):
    prompt_text = (editor_prompt or "").strip()
    first_line = prompt_text.splitlines()[0].strip() if prompt_text else ""
    if first_line.startswith("# Role:"):
        persona = first_line.split(":", 1)[1].strip()
        if persona:
            return persona
    return role_name or "\u8d44\u6df1\u884c\u4e1a\u4f5c\u8005"


def infer_article_topic(source_content):

    clean_text = " ".join((source_content or "").split())
    if not clean_text:
        return "当前游戏行业主题"
    return clean_text[:40] + ("..." if len(clean_text) > 40 else "")


def build_de_ai_prompt_template(role_name, editor_prompt, source_content, variant=DE_AI_VARIANT_DEFAULT):
    persona = infer_role_persona(role_name, editor_prompt)
    topic = infer_article_topic(source_content)
    audience = ROLE_AUDIENCE_MAP.get(role_name, "行业读者")
    jargon = ROLE_JARGON_MAP.get(role_name, "ROI, LTV, retention, monetization")
    tone = ROLE_TONE_MAP.get(role_name, "冷静, 尖锐, 有判断")
    structure_instruction = build_article_structure_instruction()
    community_instruction = ""
    chatty_instruction = ""
    if variant == DE_AI_VARIANT_COMMUNITY:
        community_instruction = """
# Community Forum Adaptation
Apply a light community-forum rewrite for high-quality game-player discussion spaces.
- Use more natural player-facing wording and reduce newsroom stiffness, documentation tone, and report-like phrasing.
- Keep viewpoint clarity, but make sentence rhythm feel more like a strong long post in a player forum.
- Prefer shorter sentences, cleaner pauses, and more natural transitions between paragraphs.
- Allow moderate player-perspective resonance, but do not turn the article into emotional venting or fan shouting.
- Convert overly hard, media-style, or industry-report wording into expressions that ordinary players can read smoothly.
- Reduce jargon stacking. When jargon is necessary, make it understandable in plain language nearby.
- Make the conclusion land more clearly on what this means for players, expectations, or community discussion.
- Do not overdo interactivity: avoid frequent rhetorical questions, forced meme tone, tieba slang, or exaggerated emotional catchphrases.
"""
    elif variant == DE_AI_VARIANT_CHAT:
        chatty_instruction = """
# Natural Conversational Adaptation
Apply a medium-strength conversational rewrite that sounds like a real, experienced writer talking the reader through the point.
- Keep the analysis sharp and useful, but make the prose feel like a human explaining things face to face instead of filing a report.
- Allow natural transitions such as "to be honest", "actually", "in other words", "so the real issue is", or similar conversational pivots in natural Simplified Chinese.
- Use more sentence-length variation. Let some sentences land short when the point needs emphasis, instead of keeping every sentence evenly shaped.
- Allow light scene-setting, light self-aware phrasing, or a brief aside in parentheses when it helps the rhythm, but keep it restrained.
- Replace newsroom tone, report tone, and instruction-manual tone with clearer human phrasing that still respects the reader's intelligence.
- Keep professional terms when they matter, but explain them in plain language nearby instead of stacking jargon.
- Make the ending land on what the matter really means, not just that the analysis is complete.
- Keep this as high-quality long-form writing. Do not turn it into low-grade chatter, meme posting, tieba slang, dense rhetorical questions, or self-indulgent rambling.
- Do not let first-person phrasing take over the article. The writer may feel present, but the article must stay focused on the topic and argument.
- Preserve the title structure, opening background, and analytical spine. Conversational does not mean loose or messy.
"""
    return f"""# Role: {persona}

# Context
I have an article draft about [{topic}]. The information gain and analytical spine are already useful, but the language still sounds too synthetic. Rewrite it so it feels like a real human expert from the target audience [{audience}] wrote it in natural Simplified Chinese.

# Task
Keep every key fact, data point, argument, example, and reasoning step from the draft. Rewrite only the expression, cadence, and sentence texture.
If the draft opens too abruptly, add a 1-2 paragraph lede so the reader immediately knows which game, event, company, or controversy the article is about, and why this case matters now.

# Core Constraints
1. Output everything in Simplified Chinese.
2. Do not delete the candidate-title block.
3. Do not flatten the article back into one long wall of text.
4. Keep the factual order and analytical direction stable.
5. Use jargon naturally when appropriate: {jargon}.
6. Preferred tone: {tone}.

{community_instruction}
{chatty_instruction}
# Structure Preservation
{structure_instruction}
- Preserve existing heading hierarchy when it already works. You may polish heading wording, but you must not delete the hierarchy.
- Preserve the candidate-title block and keep 3-5 candidate titles in the final output. If the direction has not changed, keep the same title skeleton and only improve wording and punch.
- If the draft lacks enough background in the opening, repair that with a concise 1-2 paragraph lede before the main analysis begins.
- De-AI means rewriting expression, not rebuilding the article from scratch.

# Output Protocol
Output exactly three blocks in this order, with no extra explanation:

{PURE_TITLE_MARKER}
1. ...
2. ...
3. ...
Keep 3-5 candidate titles here.

{PURE_BODY_MARKER}
Output only the clean final article body here. Do not repeat the title block. Do not output HTML here.

{HIGHLIGHT_MARKER}
Output a reading-enhanced HTML version of the same article body.
Allowed tags: <h2>, <h3>, <p>, <strong>, <span class="highlight-positive">, <span class="highlight-risk">. Prefer <h2>/<h3> instead of Markdown `##` headings when section titles are needed.
Do not change facts. Do not add or remove content. Highlight only important learning points or risk warnings.

# Draft
[Paste the full draft here]"""

def parse_de_ai_dual_output(response_text, fallback_titles=None):
    clean_text = (response_text or "").strip()
    if not clean_text:
        return normalize_title_candidates(fallback_titles), "", ""

    resolved_titles = normalize_title_candidates(fallback_titles)
    pure_part = clean_text
    if PURE_TITLE_MARKER in clean_text and PURE_BODY_MARKER in clean_text:
        title_part = clean_text.split(PURE_TITLE_MARKER, 1)[1].split(PURE_BODY_MARKER, 1)[0]
        parsed_titles = parse_title_candidates_block(title_part)
        if parsed_titles:
            resolved_titles = parsed_titles
        pure_part = clean_text.split(PURE_BODY_MARKER, 1)[1]
    elif PURE_BODY_MARKER in clean_text:
        pure_part = clean_text.split(PURE_BODY_MARKER, 1)[1]
    else:
        parsed_titles, pure_body = split_structured_article_sections(clean_text)
        return parsed_titles or resolved_titles, pure_body or clean_text, ""

    highlighted_text = ""
    if HIGHLIGHT_MARKER in pure_part:
        pure_text, highlighted_text = pure_part.split(HIGHLIGHT_MARKER, 1)
    else:
        pure_text = pure_part

    _, pure_body = split_structured_article_sections(pure_text)
    clean_body = (pure_body or pure_text or "").strip()
    return resolved_titles, clean_body, highlighted_text.strip()


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
    clean_text = clean_text.replace("&#x1F517;", "")
    clean_text = clean_text.replace("\U0001F517", "")

    normalized_lines = []
    for raw_line in clean_text.splitlines():
        stripped = raw_line.strip()
        heading_match = re.match(r"^(#{2,3})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).replace("\U0001F517", "")
            heading_text = re.sub(r"\s*#+\s*$", "", heading_text).strip()
            normalized_lines.append(f"<h{level}>{heading_text}</h{level}>")
        else:
            normalized_lines.append(raw_line)
    clean_text = "\n".join(normalized_lines)

    clean_text = re.sub(
        r'<a[^>]*?(?:headerlink|anchor|fragment-link)[^>]*>.*?</a>',
        "",
        clean_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    clean_text = re.sub(
        r'<a[^>]*?href="#.*?"[^>]*>.*?</a>',
        "",
        clean_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
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
        .highlight-article h2,
        .highlight-article h3 {
            margin: 0 0 1rem;
            color: #16211c;
            font-weight: 700;
            line-height: 1.3;
        }
        .highlight-article h2 {
            font-size: 2.1rem;
        }
        .highlight-article h3 {
            font-size: 1.45rem;
        }
        .highlight-article p {
            margin: 0 0 1rem;
        }
        .highlight-article h1 a,
        .highlight-article h2 a,
        .highlight-article h3 a,
        .highlight-article a[href^="#"] {
            display: none !important;
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
    sanitized_html = sanitize_highlighted_article(html_text)
    render_rich_html_copy_button(sanitized_html, "highlighted_article_step6")
    st.markdown(f'<div class="highlight-article">{sanitized_html}</div>', unsafe_allow_html=True)

def generate_script_for_current_article(api_key, base_url, model_name, script_duration):
    final_article_body = get_article_body_text(st.session_state.get("final_article", ""))
    if not final_article_body:
        st.session_state.spoken_script = ""
        return
    script_sys_prompt = get_script_sys_prompt(script_duration)
    st.session_state.spoken_script = call_llm(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        system_prompt=script_sys_prompt,
        user_content=f"\u3010\u8bf7\u5c06\u4ee5\u4e0b\u6df1\u5ea6\u6587\u7ae0\u8f6c\u5316\u4e3a\u4f9b\u526a\u6620AI\u89e3\u6790\u7684{script_duration}\u53e3\u64ad\u4e0e\u5206\u955c\u811a\u672c\uff1a\u3011\n\n{final_article_body}"
    )

# ==========================================


# 1. API 与外部推送函数
# ==========================================
def call_llm(api_key, base_url, model_name, system_prompt, user_content, image_urls=None, history=None, temperature=None):
    if not api_key:
        st.error("Please enter an API key in the sidebar first.")
        st.stop()

    if image_urls is None:
        image_urls = []
    if history is None:
        history = []

    current_stage = st.session_state.get("pending_ai_stage", "")
    retry_delays = (0.8, 1.6)
    attempt_count = 0

    try:
        timeout_config = build_openai_timeout(base_url, model_name)
        use_stream = should_stream_llm_request(base_url, model_name)

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

        request_kwargs = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.3 if temperature is None else temperature,
        }

        last_error = None
        for attempt_index in range(len(retry_delays) + 1):
            attempt_count = attempt_index + 1
            try:
                content = execute_llm_request_once(
                    api_key=api_key,
                    base_url=base_url,
                    model_name=model_name,
                    request_kwargs=request_kwargs,
                    timeout_config=timeout_config,
                    use_stream=use_stream,
                )
                st.session_state.last_ai_error = ""
                return content
            except Exception as attempt_error:
                last_error = attempt_error
                if attempt_index >= len(retry_delays) or not should_retry_with_requests_fallback(attempt_error):
                    raise
                time.sleep(retry_delays[attempt_index])

        raise last_error if last_error else RuntimeError("Unknown LLM request failure.")
    except Exception as e:
        formatted_error = format_llm_exception(e)
        st.session_state.last_ai_error = formatted_error
        st.session_state.pending_ai_stage = ""
        write_ai_diagnostic_log(
            current_stage,
            base_url,
            model_name,
            formatted_error,
            user_content,
            image_count=len(image_urls),
            history_count=len(history),
            extra_details={
                **build_proxy_diagnostic_snapshot(),
                "attempt_count": attempt_count,
            },
        )
        save_draft()
        play_ai_error_sound()
        st.error(f"API call failed: {formatted_error}")
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
    if 'title_candidates' not in st.session_state:
        st.session_state.title_candidates = []
    if 'highlighted_article' not in st.session_state:
        st.session_state.highlighted_article = ""
    if 'spoken_script' not in st.session_state:
        st.session_state.spoken_script = ""
    if 'de_ai_model' not in st.session_state:
        st.session_state.de_ai_model = DE_AI_MODELS[0]
    else:
        st.session_state.de_ai_model = DE_AI_MODEL_MIGRATION.get(
            st.session_state.de_ai_model,
            st.session_state.de_ai_model,
        )
        if st.session_state.de_ai_model not in DE_AI_MODELS:
            st.session_state.de_ai_model = DE_AI_MODELS[0]
    if 'de_ai_variant' not in st.session_state:
        st.session_state.de_ai_variant = DE_AI_VARIANT_DEFAULT
    elif st.session_state.de_ai_variant not in DE_AI_VARIANTS:
        st.session_state.de_ai_variant = DE_AI_VARIANT_DEFAULT
    if 'de_ai_temperature' not in st.session_state:
        st.session_state.de_ai_temperature = 0.75
    if 'de_ai_prompt_template' not in st.session_state:
        st.session_state.de_ai_prompt_template = ""
    st.session_state.title_candidates = normalize_title_candidates(st.session_state.get("title_candidates", []))
    if not st.session_state.title_candidates:
        for article_key in ("final_article", "modified_article", "draft_article"):
            recovered_titles, _ = split_structured_article_sections(st.session_state.get(article_key, ""))
            if recovered_titles:
                st.session_state.title_candidates = recovered_titles
                break
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'image_keywords' not in st.session_state:
        st.session_state.image_keywords = ""
    if 'podcast_enabled' not in st.session_state:
        st.session_state.podcast_enabled = False
    if 'podcast_duration' not in st.session_state:
        st.session_state.podcast_duration = "5分钟"
    if 'podcast_script_raw' not in st.session_state:
        st.session_state.podcast_script_raw = ""
    if 'podcast_script_segments' not in st.session_state:
        st.session_state.podcast_script_segments = []
    if 'podcast_audio_path' not in st.session_state:
        st.session_state.podcast_audio_path = ""
    if 'podcast_audio_manifest' not in st.session_state:
        st.session_state.podcast_audio_manifest = {}
    if 'podcast_last_error' not in st.session_state:
        st.session_state.podcast_last_error = ""
    if 'podcast_voice' not in st.session_state:
        st.session_state.podcast_voice = (
            st.session_state.get("podcast_voice_a")
            or st.session_state.get("podcast_voice_b")
            or DEFAULT_TTS_VOICE
        )
    legacy_podcast_segments = st.session_state.get("podcast_script_segments", [])
    if legacy_podcast_segments:
        try:
            st.session_state.podcast_script_segments = normalize_podcast_segments(legacy_podcast_segments)
        except ValueError:
            st.session_state.podcast_script_segments = []
    legacy_podcast_manifest = st.session_state.get("podcast_audio_manifest", {}) or {}
    if legacy_podcast_manifest.get("voice_map") and not legacy_podcast_manifest.get("voice"):
        st.session_state.podcast_audio_path = ""
        st.session_state.podcast_audio_manifest = {}
    if 'podcast_tts_provider' not in st.session_state:
        st.session_state.podcast_tts_provider = "DashScope Qwen-TTS"
    if 'podcast_tts_api_key_present' not in st.session_state:
        st.session_state.podcast_tts_api_key_present = False
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
    if 'pending_ai_stage' not in st.session_state:
        st.session_state.pending_ai_stage = ""
    if 'last_completed_ai_stage' not in st.session_state:
        st.session_state.last_completed_ai_stage = ""
    if 'last_completed_ai_target_step' not in st.session_state:
        st.session_state.last_completed_ai_target_step = 0
    if 'last_ai_error' not in st.session_state:
        st.session_state.last_ai_error = ""
    if 'recovered_ai_notice' not in st.session_state:
        st.session_state.recovered_ai_notice = ""
    if 'obsidian_enabled' not in st.session_state:
        st.session_state.obsidian_enabled = False
    if 'obsidian_vault_path' not in st.session_state:
        st.session_state.obsidian_vault_path = "E:\\Obsidian\\originvault\\1\u53f7\u4ed3\u5e93\\LLM Wiki\\wiki"
    if st.session_state.get("obsidian_vault_path") == r"E:\Obsidian\originvault\1???\LLM Wiki\wiki":
        st.session_state.obsidian_vault_path = "E:\\Obsidian\\originvault\\1\u53f7\u4ed3\u5e93\\LLM Wiki\\wiki"
    if 'obsidian_max_hits' not in st.session_state:
        st.session_state.obsidian_max_hits = 6
    if 'obsidian_show_hits' not in st.session_state:
        st.session_state.obsidian_show_hits = True
    if 'obsidian_hits' not in st.session_state:
        st.session_state.obsidian_hits = []
    if 'obsidian_research_brief' not in st.session_state:
        st.session_state.obsidian_research_brief = ""
    if 'obsidian_retrieval_error' not in st.session_state:
        st.session_state.obsidian_retrieval_error = ""
    if 'obsidian_query_terms' not in st.session_state:
        st.session_state.obsidian_query_terms = []
    if 'obsidian_wiki_root' not in st.session_state:
        st.session_state.obsidian_wiki_root = ""
    if 'obsidian_last_indexed_at' not in st.session_state:
        st.session_state.obsidian_last_indexed_at = ""
    if 'obsidian_retrieval_signature' not in st.session_state:
        st.session_state.obsidian_retrieval_signature = ""
    if 'obsidian_influence_map' not in st.session_state:
        st.session_state.obsidian_influence_map = []
    if 'obsidian_influence_summary' not in st.session_state:
        st.session_state.obsidian_influence_summary = ""
    if 'obsidian_influence_signature' not in st.session_state:
        st.session_state.obsidian_influence_signature = ""
    st.session_state.target_article_words = get_target_article_words()
    st.session_state.target_article_words_slider = get_target_article_words()
    current_role = st.session_state.get('selected_role', '')
    if isinstance(current_role, str) and current_role.strip():
        st.session_state.selected_role_widget = current_role


init_state()


def mark_ai_stage_started(stage_name):
    st.session_state.pending_ai_stage = stage_name
    st.session_state.last_ai_error = ""
    save_draft()


def checkpoint_ai_stage(stage_name, target_step=None):
    st.session_state.pending_ai_stage = ""
    st.session_state.last_completed_ai_stage = stage_name
    st.session_state.last_completed_ai_target_step = target_step or 0
    st.session_state.last_ai_error = ""
    st.session_state.recovered_ai_notice = ""
    save_draft()


def clear_ai_stage_checkpoint():
    st.session_state.pending_ai_stage = ""
    st.session_state.last_completed_ai_stage = ""
    st.session_state.last_completed_ai_target_step = 0
    save_draft()


def recover_ai_progress_if_needed():
    target_step = st.session_state.get("last_completed_ai_target_step", 0) or 0
    current_step = st.session_state.get("current_step", 1)
    last_stage = st.session_state.get("last_completed_ai_stage", "")
    if target_step and current_step < target_step:
        st.session_state.current_step = target_step
        st.session_state.recovered_ai_notice = f"AI 已完成「{format_ai_stage_name(last_stage)}」，已自动恢复到第 {target_step} 步。"
        clear_ai_stage_checkpoint()
        st.rerun()
    if target_step and current_step >= target_step:
        clear_ai_stage_checkpoint()


def normalize_llm_response_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "\n".join(part for part in parts if part).strip()
    return "" if content is None else str(content)


def format_ai_stage_name(stage_name):
    return {
        "draft_generation": "写出稿",
        "review_generation": "严格审稿",
        "modification_generation": "修改稿件",
        "de_ai_generation": "去 AI 味重写",
    }.get(stage_name, stage_name or "AI 任务")


def render_ai_progress_banner():
    recovered_notice = st.session_state.get("recovered_ai_notice", "")
    if recovered_notice:
        st.success(recovered_notice)

    pending_stage = st.session_state.get("pending_ai_stage", "")
    if pending_stage:
        st.info(f"AI 正在执行「{format_ai_stage_name(pending_stage)}」。如果页面短暂重载，系统会自动尝试续上流程。")

    last_error = (st.session_state.get("last_ai_error", "") or "").strip()
    if last_error:
        st.warning(f"上一轮 AI 调用未完整收尾：{last_error}")


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


def restore_article_version_to_session(version_item, fallback_titles=None):
    if not isinstance(version_item, dict):
        return False

    selected_titles, _, selected_article_text = parse_article_generation_response(
        version_item.get("content", ""),
        fallback_titles,
    )
    st.session_state.title_candidates = selected_titles
    st.session_state.final_article = selected_article_text
    st.session_state.highlighted_article = (version_item.get("highlighted_article") or "").strip()
    st.session_state.spoken_script = ""
    reset_podcast_outputs(delete_audio=True)
    if version_item.get("id"):
        st.session_state.active_article_version_id = version_item.get("id")
    refresh_obsidian_influence_map(force=True)
    return True


def append_article_version(content, stage, role=None, model=None, parent_id=None, highlighted_article=None):
    ensure_article_version_state()
    clean_content = (content or "").strip()
    if not clean_content:
        return None

    clean_highlighted_article = (highlighted_article or "").strip()
    versions = st.session_state.article_versions
    if versions:
        last_version = versions[-1]
        if last_version.get("stage") == stage and (last_version.get("content") or "").strip() == clean_content:
            if clean_highlighted_article or "highlighted_article" not in last_version:
                last_version["highlighted_article"] = clean_highlighted_article
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
        "highlighted_article": clean_highlighted_article,
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
        append_article_version(
            final_text,
            "历史恢复定稿",
            model="",
            highlighted_article=st.session_state.get("highlighted_article", ""),
        )
        return

    draft_text = (st.session_state.get("draft_article") or "").strip()
    if draft_text:
        append_article_version(draft_text, "历史恢复初稿", model="")


bootstrap_article_versions()
recover_ai_progress_if_needed()
run_obsidian_retrieval()


def render_html_iframe(html_content, *, height=150, width=None, scrolling=False):
    iframe_html = "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body style='margin:0;padding:0;'>" + html_content + "</body></html>"
    iframe_src = "data:text/html;base64," + base64.b64encode(iframe_html.encode("utf-8")).decode("ascii")
    iframe_renderer = getattr(components, "iframe", None)
    if callable(iframe_renderer):
        try:
            iframe_renderer(iframe_src, height=height, width=width, scrolling=scrolling)
            return
        except Exception:
            pass
    html_renderer = getattr(components, "html", None)
    if callable(html_renderer):
        try:
            html_renderer(iframe_html, height=height, width=width, scrolling=scrolling)
        except Exception:
            pass

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


def play_ai_error_sound():
    render_html_iframe(
        """
        <script>
        (function () {
            try {
                const AudioContextRef = window.AudioContext || window.webkitAudioContext;
                if (!AudioContextRef) return;
                const ctx = new AudioContextRef();
                const playAlert = (startOffset, startFreq, endFreq, duration, peakGain, type) => {
                    const startAt = ctx.currentTime + startOffset;
                    const endAt = startAt + duration;
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.type = type || "sawtooth";
                    osc.frequency.setValueAtTime(startFreq, startAt);
                    osc.frequency.exponentialRampToValueAtTime(endFreq, endAt);
                    gain.gain.setValueAtTime(0.0001, startAt);
                    gain.gain.exponentialRampToValueAtTime(peakGain, startAt + 0.015);
                    gain.gain.exponentialRampToValueAtTime(0.0001, endAt);
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.start(startAt);
                    osc.stop(endAt + 0.03);
                };

                playAlert(0.00, 880.0, 440.0, 0.22, 0.09, "square");
                playAlert(0.20, 880.0, 440.0, 0.22, 0.09, "square");
                playAlert(0.42, 660.0, 330.0, 0.32, 0.08, "sawtooth");
                setTimeout(() => {
                    try { ctx.close(); } catch (e) {}
                }, 1200);
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


def _render_inline_markdown_for_clipboard(text):
    escaped = html_lib.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    return escaped.replace("\n", "<br/>")


def build_clipboard_html_document(fragment_html):
    fragment = fragment_html or "<div><br/></div>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
        "<!--StartFragment-->"
        f"{fragment}"
        "<!--EndFragment-->"
        "</body></html>"
    )


HTML_CLIPBOARD_FORMAT_NAME = "HTML Format"


def build_windows_html_clipboard_payload(document_html):
    html_text = (document_html or "").strip()
    if not html_text:
        html_text = build_clipboard_html_document("<div><br/></div>")

    html_bytes = html_text.encode("utf-8")
    start_fragment_marker = b"<!--StartFragment-->"
    end_fragment_marker = b"<!--EndFragment-->"
    start_fragment_index = html_bytes.find(start_fragment_marker)
    end_fragment_index = html_bytes.find(end_fragment_marker)
    if start_fragment_index == -1 or end_fragment_index == -1:
        raise ValueError("Clipboard HTML is missing fragment markers.")

    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_fragment:010d}\r\n"
        "EndFragment:{end_fragment:010d}\r\n"
        "StartSelection:{start_fragment:010d}\r\n"
        "EndSelection:{end_fragment:010d}\r\n"
    )

    placeholder_header = header_template.format(
        start_html=0,
        end_html=0,
        start_fragment=0,
        end_fragment=0,
    )
    start_html = len(placeholder_header.encode("ascii"))
    start_fragment = start_html + start_fragment_index + len(start_fragment_marker)
    end_fragment = start_html + end_fragment_index
    end_html = start_html + len(html_bytes)
    header = header_template.format(
        start_html=start_html,
        end_html=end_html,
        start_fragment=start_fragment,
        end_fragment=end_fragment,
    )
    return header.encode("ascii") + html_bytes


def prepare_highlighted_html_for_clipboard(html_text):
    normalized = sanitize_highlighted_article(html_text)
    if not normalized:
        return ""

    normalized = re.sub(
        r'<span\s+class="highlight-positive">(.*?)</span>',
        lambda match: '<span style="background-color:#d8e7ff;color:#1f57b8;font-weight:600;">{}</span>'.format(match.group(1)),
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    normalized = re.sub(
        r'<span\s+class="highlight-risk">(.*?)</span>',
        lambda match: '<span style="background-color:#fde1e1;color:#b3261e;font-weight:600;">{}</span>'.format(match.group(1)),
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    normalized = re.sub(r'<h2>', '<h2 style="font-size:28px;line-height:1.35;font-weight:800;margin:0 0 16px 0;color:#16211c;">', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'<h3>', '<h3 style="font-size:22px;line-height:1.4;font-weight:700;margin:0 0 14px 0;color:#16211c;">', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'<p>', '<p style="margin:0 0 16px 0;line-height:1.9;color:#16211c;">', normalized, flags=re.IGNORECASE)
    return build_clipboard_html_document(normalized)


def copy_rich_content_to_system_clipboard(plain_text, html_document=""):
    plain_text = normalize_copy_text(plain_text)
    html_document = (html_document or "").strip()
    if not plain_text:
        return False, "\u6ca1\u6709\u53ef\u590d\u5236\u7684\u5185\u5bb9\u3002"

    if os.name != "nt":
        return False, "\u5f53\u524d\u4ec5\u4e3a Windows \u672c\u673a\u526a\u8d34\u677f\u63d0\u4f9b\u4e00\u952e\u590d\u5236\u3002"

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    gm_moveable = 0x0002
    cf_unicode_text = 13
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    html_format = user32.RegisterClipboardFormatW(HTML_CLIPBOARD_FORMAT_NAME)

    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    user32.SetClipboardData.restype = ctypes.c_void_p

    def set_clipboard_block(fmt, payload):
        handle = kernel32.GlobalAlloc(gm_moveable, len(payload))
        if not handle:
            raise OSError("GlobalAlloc failed")
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            raise OSError("GlobalLock failed")
        ctypes.memmove(locked, payload, len(payload))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(fmt, handle):
            kernel32.GlobalFree(handle)
            raise OSError("SetClipboardData failed")

    plain_payload = plain_text.encode("utf-16le") + b"\\x00\\x00".decode("unicode_escape").encode("latin1")
    html_payload = build_windows_html_clipboard_payload(html_document) if html_document else b""

    if not user32.OpenClipboard(None):
        return False, "\u65e0\u6cd5\u6253\u5f00\u7cfb\u7edf\u526a\u8d34\u677f\u3002"
    try:
        if not user32.EmptyClipboard():
            return False, "\u65e0\u6cd5\u6253\u5f00\u7cfb\u7edf\u526a\u8d34\u677f\u3002"
        set_clipboard_block(cf_unicode_text, plain_payload)
        if html_payload and html_format:
            set_clipboard_block(html_format, html_payload)
    except Exception as exc:
        return False, f"\u590d\u5236\u5931\u8d25\uff1a{exc}"
    finally:
        user32.CloseClipboard()

    return True, "\u5df2\u590d\u5236\u5230\u7cfb\u7edf\u526a\u8d34\u677f\uff0c\u53ef\u76f4\u63a5\u7c98\u8d34\u5230\u5bcc\u6587\u672c\u7f16\u8f91\u5668\u3002"


def markdown_to_editor_html(markdown_text):
    normalized = normalize_copy_text(markdown_text)
    if not normalized:
        return build_clipboard_html_document("<div><br/></div>")

    paragraphs = [item.strip() for item in re.split(r"\n{2,}", normalized) if item.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    html_parts = []
    for paragraph in paragraphs:
        rendered_paragraph = _render_inline_markdown_for_clipboard(paragraph)
        # Use block-level divs for better compatibility with community editors.
        html_parts.append(f"<div>{rendered_paragraph}</div>")

    fragment = "".join(html_parts)
    return build_clipboard_html_document(fragment)


def html_fragment_to_plain_text(html_text):
    normalized = (html_text or "").strip()
    if not normalized:
        return ""

    normalized = re.sub(r"<\s*br\s*/?>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"</\s*(p|div|h1|h2|h3|li)\s*>", "\n\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<\s*li\b[^>]*>", "- ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<[^>]+>", "", normalized)
    normalized = html_lib.unescape(normalized)
    normalized = normalize_copy_text(normalized)
    return normalized


def html_fragment_to_editor_html(html_text):
    normalized = (html_text or "").strip()
    if not normalized:
        return build_clipboard_html_document("<div><br/></div>")
    return build_clipboard_html_document(normalized)


def _build_copy_button_iframe(copy_key, label, plain_text, html_payload):
    if not plain_text and not html_payload:
        return

    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", str(copy_key))
    safe_label = html_lib.escape(label)
    plain_text_b64 = base64.b64encode((plain_text or "").encode("utf-8")).decode("ascii")
    html_text_b64 = base64.b64encode((html_payload or "").encode("utf-8")).decode("ascii")

    html_renderer = getattr(components, "html", None)
    html_payload = f"""
        <div style="display:flex;align-items:center;gap:10px;margin:6px 0 0 0;">
          <button id="copy-btn-{safe_key}" onclick="window.__copyRichText_{safe_key} && window.__copyRichText_{safe_key}(); return false;" style="border:1px solid #d9d9df;background:#ffffff;padding:6px 12px;border-radius:8px;cursor:pointer;font-size:13px;">
            {safe_label}
          </button>
          <span id="copy-status-{safe_key}" style="font-size:12px;color:#667085;"></span>
        </div>
        <script>
        (function () {{
            const plainBase64 = "{plain_text_b64}";
            const htmlBase64 = "{html_text_b64}";
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

            function setStatus(message) {{
                if (status) {{
                    status.textContent = message;
                }}
            }}

            function getParentDocument() {{
                try {{
                    if (window.parent && window.parent.document) {{
                        return window.parent.document;
                    }}
                }} catch (err) {{}}
                return null;
            }}

            function getParentClipboard() {{
                try {{
                    if (window.parent && window.parent.navigator && window.parent.navigator.clipboard) {{
                        return window.parent.navigator.clipboard;
                    }}
                }} catch (err) {{}}
                return null;
            }}

            function copyViaExecCommand(targetDocument, htmlText, plainText) {{
                if (!targetDocument || !targetDocument.body || !targetDocument.execCommand) {{
                    return false;
                }}
                const listener = function (event) {{
                    event.preventDefault();
                    if (event.clipboardData) {{
                        event.clipboardData.setData("text/plain", plainText);
                        if (htmlText) {{
                            event.clipboardData.setData("text/html", htmlText);
                        }}
                    }}
                }};
                targetDocument.addEventListener("copy", listener);
                let copied = false;
                try {{
                    copied = targetDocument.execCommand("copy");
                }} catch (err) {{
                    copied = false;
                }}
                targetDocument.removeEventListener("copy", listener);
                return copied;
            }}

            async function copyViaClipboardApi(clipboard, htmlText, plainText) {{
                if (!clipboard) {{
                    return false;
                }}
                try {{
                    if (window.ClipboardItem && htmlText && clipboard.write) {{
                        const payload = new ClipboardItem({{
                            "text/plain": new Blob([plainText], {{ type: "text/plain" }}),
                            "text/html": new Blob([htmlText], {{ type: "text/html" }})
                        }});
                        await clipboard.write([payload]);
                        return true;
                    }}
                    if (clipboard.writeText) {{
                        await clipboard.writeText(plainText);
                        return true;
                    }}
                }} catch (err) {{}}
                return false;
            }}

            window.__copyRichText_{safe_key} = async function () {{
                const plainText = decodeBase64Utf8(plainBase64).replace(/\n/g, "\r\n");
                const htmlText = decodeBase64Utf8(htmlBase64);
                const parentDocument = getParentDocument();
                const parentClipboard = getParentClipboard();

                if (copyViaExecCommand(document, htmlText, plainText) || copyViaExecCommand(parentDocument, htmlText, plainText)) {{
                    setStatus("?????????????????");
                    return;
                }}
                if (await copyViaClipboardApi(parentClipboard, htmlText, plainText) || await copyViaClipboardApi(navigator.clipboard, htmlText, plainText)) {{
                    setStatus("?????????????????");
                    return;
                }}
                setStatus("???????? Ctrl+C?");
            }};
        }})();
        </script>
        """
    if callable(html_renderer):
        try:
            html_renderer(html_payload, height=56)
            return
        except TypeError:
            html_renderer(html_payload)
            return
    render_html_iframe(html_payload, height=56)


def render_editor_friendly_copy_button(text, copy_key, label="\U0001F4CB \u517c\u5bb9\u590d\u5236\uff08\u4fdd\u7559\u6bb5\u843d\uff09"):
    plain_text = normalize_copy_text(text)
    if not plain_text:
        return

    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", str(copy_key))
    status_key = f"copy_status_{safe_key}"
    if st.button(label, key=f"copy_btn_{safe_key}"):
        success, message = copy_rich_content_to_system_clipboard(plain_text, markdown_to_editor_html(plain_text))
        st.session_state[status_key] = ("success" if success else "error", message)

    status = st.session_state.get(status_key)
    if status:
        level, message = status
        if level == "success":
            st.caption(message)
        else:
            st.caption(message)


def render_rich_html_copy_button(html_text, copy_key, label="\U0001F4CB \u590d\u5236\u9ad8\u4eae\u9605\u8bfb\u7248\uff08\u4fdd\u7559\u683c\u5f0f\uff09"):
    sanitized_html = sanitize_highlighted_article(html_text)
    plain_text = html_fragment_to_plain_text(sanitized_html)
    if not plain_text:
        return

    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", str(copy_key))
    status_key = f"copy_status_{safe_key}"
    if st.button(label, key=f"copy_btn_{safe_key}"):
        success, message = copy_rich_content_to_system_clipboard(plain_text, prepare_highlighted_html_for_clipboard(sanitized_html))
        st.session_state[status_key] = ("success" if success else "error", message)

    status = st.session_state.get(status_key)
    if status:
        level, message = status
        if level == "success":
            st.caption(message)
        else:
            st.caption(message)


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
    api_provider = st.selectbox("🌐 选择 API 中转站", ["BLTCY (柏拉图次元)", "DeerAPI", "云雾API"])
    
    if api_provider == "BLTCY (柏拉图次元)":
        api_key = st.text_input("🔑 输入 BLTCY Key", type="password")
        current_base_url = "https://api.bltcy.ai/v1"
        available_models = BLTCY_MODEL_OPTIONS
    elif api_provider == "云雾API":
        api_key = st.text_input("🔑 输入云雾API Key", type="password")
        current_base_url = "https://yunwu.ai/v1"
        available_models = YUNWU_MODEL_OPTIONS
    else:
        api_key = st.text_input("🔑 输入 DeerAPI Key", type="password")
        current_base_url = "https://api.deerapi.com/v1"
        available_models = DEERAPI_MODEL_OPTIONS

    preferred_default_models = {
        "BLTCY (柏拉图次元)": "qwen3.5-plus",
        "DeerAPI": "gpt-5.4",
        "云雾API": "qwen3.6-plus",
    }
    preferred_default_model = preferred_default_models.get(api_provider, available_models[0])
    default_model_idx = available_models.index(preferred_default_model) if preferred_default_model in available_models else 0

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
    st.header("播客语音设置")
    st.toggle("启用伴生【播客解说稿】", key="podcast_enabled", on_change=save_draft)
    st.selectbox(
        "播客时长",
        ["3分钟", "5分钟", "8分钟", "12分钟"],
        key="podcast_duration",
        on_change=save_draft,
        disabled=not st.session_state.get("podcast_enabled", False)
    )
    podcast_api_key = st.text_input("输入阿里云 DashScope API Key", type="password")
    st.session_state.podcast_tts_api_key_present = bool((podcast_api_key or "").strip())
    st.caption("当前播客语音合成使用单音色 Qwen-TTS。")
    st.selectbox(
        "主播音色",
        options=list(PODCAST_VOICE_LABELS.keys()),
        format_func=lambda key: PODCAST_VOICE_LABELS.get(key, key),
        key="podcast_voice",
        on_change=save_draft,
        disabled=not st.session_state.get("podcast_enabled", False)
    )

    st.markdown("---")
    st.header("Obsidian 知识库")
    st.toggle("启用本地知识增强", key="obsidian_enabled", on_change=save_draft)
    st.text_input("知识库路径 / wiki 路径", key="obsidian_vault_path", placeholder=r"D:\Obsidian Vault", on_change=save_draft)
    st.slider("知识命中数", min_value=3, max_value=10, step=1, key="obsidian_max_hits", on_change=save_draft)
    st.toggle("显示命中详情", key="obsidian_show_hits", on_change=save_draft)
    wiki_root_preview, wiki_root_error = resolve_obsidian_wiki_root(st.session_state.get("obsidian_vault_path", ""))
    if st.session_state.get("obsidian_enabled"):
        if wiki_root_preview:
            st.caption(f"检测到 wiki 根目录：{wiki_root_preview}")
        elif wiki_root_error:
            st.caption(wiki_root_error)

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
render_ai_progress_banner()

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
                        st.session_state.obsidian_retrieval_signature = ""
                        run_obsidian_retrieval(force=True)
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
                        reset_obsidian_context()
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


        if st.session_state.get("obsidian_enabled"):
            with st.container(border=True):
                render_section_intro("Obsidian 检索简报", "在选择写作模式前，先检查本地知识库命中与自动生成的研究摘要。", "知识库")
                if st.button("刷新 Obsidian 检索", key="refresh_obsidian_hits", use_container_width=True):
                    run_obsidian_retrieval(force=True)
                    st.rerun()

                retrieval_error = (st.session_state.get("obsidian_retrieval_error", "") or "").strip()
                if retrieval_error:
                    st.warning(retrieval_error)

                wiki_root_display = st.session_state.get("obsidian_wiki_root", "")
                if wiki_root_display:
                    st.caption(f"Wiki 根目录：{wiki_root_display}")
                indexed_at = st.session_state.get("obsidian_last_indexed_at", "")
                if indexed_at:
                    st.caption(f"上次索引时间：{indexed_at}")

                query_terms = st.session_state.get("obsidian_query_terms", []) or []
                if query_terms:
                    st.caption("检索词：" + "、".join(query_terms))

                obsidian_hits = st.session_state.get("obsidian_hits", []) or []
                if obsidian_hits and st.session_state.get("obsidian_show_hits", True):
                    for idx, hit in enumerate(obsidian_hits, start=1):
                        with st.expander(f"{idx}. {hit.get('title', '')} | {hit.get('category_label', '')}", expanded=(idx <= 2)):
                            st.caption(f"{hit.get('path', '')} | {hit.get('modified_at', '')}")
                            st.markdown(hit.get("reason", ""))
                            st.code(hit.get("excerpt", ""), language="markdown")
                elif st.session_state.get("obsidian_enabled") and not retrieval_error:
                    st.info("当前这批素材没有匹配到相关 Obsidian 笔记，后续将按纯素材写作继续。")

                if st.session_state.get("obsidian_research_brief"):
                    st.markdown("#### 研究摘要预览")
                    st.code(st.session_state.obsidian_research_brief, language="markdown")

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

                        draft_content = build_editor_user_content(
                            st.session_state.source_content,
                            st.session_state.get("obsidian_research_brief", ""),
                            use_images=bool(st.session_state.source_images)
                        )
                        draft_response = call_llm(
                            api_key=api_key, base_url=current_base_url, model_name=selected_model,
                            system_prompt=final_editor_system_prompt, user_content=draft_content, image_urls=st.session_state.source_images
                        )
                        draft_titles, _, draft_article_text = parse_article_generation_response(
                            draft_response,
                            st.session_state.get("title_candidates", []),
                        )
                        st.session_state.title_candidates = draft_titles
                        st.session_state.draft_article = draft_article_text
                        append_article_version(st.session_state.draft_article, "自动驾驶初稿", role=chosen_editor, model=selected_model)
                        save_draft()

                        st.write("🧐 审稿主编介入，正在极其严苛地核对原文与逻辑...")
                        reviewer_prompt = prompts_data["reviewer"]
                        anti_hallucination_instruction = "\n\n【⚠️ 强制系统级指令：严禁幻觉】：你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】！绝对不允许使用自身知识库进行事实核对。"
                        final_reviewer_system_prompt = build_reviewer_system_prompt(reviewer_prompt, anti_hallucination_instruction)

                        combined_content = build_reviewer_user_content(
                            st.session_state.source_content,
                            st.session_state.draft_article,
                            st.session_state.get("obsidian_research_brief", ""),
                            title_candidates=st.session_state.get("title_candidates", []),
                        )
                        st.session_state.review_feedback = call_llm(
                            api_key=api_key, base_url=current_base_url, model_name=selected_model,
                            system_prompt=final_reviewer_system_prompt, user_content=combined_content, image_urls=st.session_state.source_images
                        )

                        save_draft()
                        st.write("✨ 接收修改意见，正在进行最终打磨...")
                        modification_prompt = build_modification_system_prompt(global_instruction)
                        content_to_modify = build_modification_user_content(
                    st.session_state.review_feedback,
                    st.session_state.draft_article,
                    title_candidates=st.session_state.get("title_candidates", []),
                )

                        final_response = call_llm(
                            api_key=api_key, base_url=current_base_url, model_name=selected_model,
                            system_prompt=modification_prompt, user_content=content_to_modify
                        )

                        final_titles, _, final_article_text = parse_article_generation_response(

                            final_response,

                            st.session_state.get("title_candidates", []),

                        )

                        st.session_state.title_candidates = final_titles

                        st.session_state.final_article = final_article_text
                        append_article_version(st.session_state.final_article, "自动驾驶定稿", role=chosen_editor, model=selected_model)
                        save_draft()

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
            mark_ai_stage_started("draft_generation")
            with st.spinner("编辑正在分析所有素材并奋笔疾书，请耐心等待..."):
                global_instruction = prompts_data.get("global_instruction", "")
                final_editor_system_prompt = build_editor_system_prompt(editor_prompt, global_instruction)
                editor_user_content = build_editor_user_content(
                    st.session_state.source_content,
                    st.session_state.get("obsidian_research_brief", ""),
                    use_images=bool(st.session_state.source_images)
                )

                draft_response = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=final_editor_system_prompt,
                    user_content=editor_user_content,
                    image_urls=st.session_state.source_images
                )

                draft_titles, _, draft_article_text = parse_article_generation_response(

                    draft_response,

                    st.session_state.get("title_candidates", []),

                )

                st.session_state.title_candidates = draft_titles

                st.session_state.draft_article = draft_article_text
                append_article_version(st.session_state.draft_article, "手动初稿", role=editor_role, model=selected_model)
                checkpoint_ai_stage("draft_generation", target_step=3)
                save_draft()
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
            mark_ai_stage_started("review_generation")
            with st.spinner("主编正在核对原文素材..."):
                if st.session_state.source_images:
                    anti_hallucination_instruction = """\n\n【⚠️ 强制系统级指令：严禁幻觉】：
                    你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】以及你所看到的【参考配图】！绝对不允许使用自身知识库进行事实核对。"""
                    combined_content = build_reviewer_user_content(
                        st.session_state.source_content,
                        st.session_state.draft_article,
                        st.session_state.get("obsidian_research_brief", ""),
                        title_candidates=st.session_state.get("title_candidates", []),
                    )
                else:
                    anti_hallucination_instruction = """\n\n【⚠️ 强制系统级指令：严禁幻觉】：
                    你在审查事实时，**必须且只能**基于下方提供给你的【原始素材文本】！绝对不允许使用自身知识库进行事实核对。"""
                    combined_content = build_reviewer_user_content(
                        st.session_state.source_content,
                        st.session_state.draft_article,
                        st.session_state.get("obsidian_research_brief", ""),
                        title_candidates=st.session_state.get("title_candidates", []),
                    )
                
                final_reviewer_system_prompt = build_reviewer_system_prompt(reviewer_prompt, anti_hallucination_instruction)
                
                st.session_state.review_feedback = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=final_reviewer_system_prompt, 
                    user_content=combined_content,
                    image_urls=st.session_state.source_images
                )
                checkpoint_ai_stage("review_generation", target_step=4)
                save_draft()
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
            mark_ai_stage_started("modification_generation")
            with st.spinner("编辑正在根据主编意见生成修改稿..."):
                global_instruction = prompts_data.get("global_instruction", "")
                modification_prompt = build_modification_system_prompt(global_instruction)

                content_to_modify = build_modification_user_content(
                    st.session_state.review_feedback,
                    st.session_state.draft_article,
                    title_candidates=st.session_state.get("title_candidates", []),
                )

                modified_response = call_llm(
                    api_key=api_key,
                    base_url=current_base_url,
                    model_name=selected_model,
                    system_prompt=modification_prompt,
                    user_content=content_to_modify
                )

                modified_titles, _, modified_article_text = parse_article_generation_response(

                    modified_response,

                    st.session_state.get("title_candidates", []),

                )

                st.session_state.title_candidates = modified_titles

                st.session_state.modified_article = modified_article_text
                st.session_state.final_article = ""
                st.session_state.highlighted_article = ""
                st.session_state.spoken_script = ""
                append_article_version(st.session_state.modified_article, "接受审稿修改稿", role=st.session_state.get("selected_role", ""), model=selected_model)
                checkpoint_ai_stage("modification_generation", target_step=5)
                save_draft()
                notify_step_completed(defer_until_rerun=True)
                go_to_step(5)
                st.rerun()

# --- Step 5 (manual mode) ---
elif st.session_state.current_step == 5:
    current_role = st.session_state.get("selected_role", "")
    current_editor_prompt = prompts_data["editors"].get(current_role, "") if current_role in prompts_data["editors"] else ""
    de_ai_variant = st.session_state.get("de_ai_variant", DE_AI_VARIANT_DEFAULT)
    de_ai_button_suffix = (
        "（社区版）"
        if de_ai_variant == DE_AI_VARIANT_COMMUNITY
        else "（唠嗑版）" if de_ai_variant == DE_AI_VARIANT_CHAT else ""
    )

    render_section_intro("去 AI 味", "在定稿前选择专用模型，把修改稿重写得更像真人专家输出。", "Step 05")
    render_context_strip([
        f"修改稿来源模型：{selected_model}",
        f"专用模型：{st.session_state.get('de_ai_model', DE_AI_MODELS[0])}",
        f"风格版本：{de_ai_variant}",
        f"Temperature：{st.session_state.get('de_ai_temperature', 0.75):.2f}",
        f"分镜脚本：{'开启' if enable_script else '关闭'}",
    ])

    st.markdown("### 当前修改稿")
    st.code(st.session_state.modified_article, language="markdown")
    render_editor_friendly_copy_button(st.session_state.modified_article, "modified_article_step5")

    col_variant, col_model, col_temp = st.columns([1.15, 1.2, 1])
    with col_variant:
        st.selectbox(
            "去 AI 风格版本",
            DE_AI_VARIANTS,
            key="de_ai_variant",
            on_change=save_draft,
        )
    with col_model:
        st.selectbox(
            "去 AI 味专用大模型",
            DE_AI_MODELS,
            key="de_ai_model",
            on_change=save_draft,
        )
    with col_temp:
        st.slider(
            "Temperature",
            min_value=0.70,
            max_value=0.85,
            step=0.05,
            key="de_ai_temperature",
            on_change=save_draft,
        )

    de_ai_variant = st.session_state.get("de_ai_variant", DE_AI_VARIANT_DEFAULT)
    de_ai_button_suffix = (
        "（社区版）"
        if de_ai_variant == DE_AI_VARIANT_COMMUNITY
        else "（唠嗑版）" if de_ai_variant == DE_AI_VARIANT_CHAT else ""
    )
    st.session_state.de_ai_prompt_template = build_de_ai_prompt_template(
        current_role,
        current_editor_prompt,
        st.session_state.get("source_content", ""),
        variant=de_ai_variant,
    )

    with st.expander("查看本次去 AI 味专用 Prompt 模板（只读）", expanded=False):
        st.text_area(
            "去 AI 味 Prompt 模板",
            value=st.session_state.de_ai_prompt_template,
            height=360,
            disabled=True,
            key="de_ai_prompt_template_preview",
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("返回修改稿步骤"):
            go_to_step(4)
            st.rerun()
    with col2:
        if st.button("跳过去 AI 味，直接定稿"):
            spinner_msg = f"正在将修改稿设为定稿，并生成【{script_duration}口播及分镜脚本】..." if enable_script else "正在将修改稿设为定稿..."
            with st.spinner(spinner_msg):
                skip_titles, _, skip_article_text = parse_article_generation_response(
                    st.session_state.modified_article,
                    st.session_state.get("title_candidates", []),
                )
                st.session_state.title_candidates = skip_titles
                st.session_state.final_article = skip_article_text
                st.session_state.highlighted_article = ""
                append_article_version(st.session_state.final_article, "跳过去AI味定稿", role=current_role, model=selected_model)
                if enable_script:
                    generate_script_for_current_article(api_key, current_base_url, selected_model, script_duration)
                else:
                    st.session_state.spoken_script = ""

                if st.session_state.get("podcast_enabled"):
                    generate_podcast_script_for_current_article(
                        api_key,
                        current_base_url,
                        selected_model,
                        st.session_state.get("podcast_duration", "5分钟"),
                    )
                else:
                    reset_podcast_outputs(delete_audio=True)
                save_draft()
                notify_step_completed(defer_until_rerun=True)
                go_to_step(6)
                st.rerun()
    with col3:
        if st.button(f"使用 {st.session_state.get('de_ai_model', DE_AI_MODELS[0])} 去 AI 味重写{de_ai_button_suffix}"):
            mark_ai_stage_started("de_ai_generation")
            spinner_msg = (
                f"正在使用 {st.session_state.get('de_ai_model', DE_AI_MODELS[0])} 去 AI 味{de_ai_button_suffix}，并生成【{script_duration}口播及分镜脚本】..."
                if enable_script
                else f"正在使用 {st.session_state.get('de_ai_model', DE_AI_MODELS[0])} 去 AI 味重写{de_ai_button_suffix}..."
            )
            with st.spinner(spinner_msg):
                de_ai_response = call_llm(
                    api_key=api_key,
                    base_url=current_base_url,
                    model_name=st.session_state.get('de_ai_model', DE_AI_MODELS[0]),
                    system_prompt=st.session_state.de_ai_prompt_template,
                    user_content=st.session_state.modified_article,
                    temperature=st.session_state.get('de_ai_temperature', 0.75),
                )
                pure_titles, pure_article, highlighted_article = parse_de_ai_dual_output(
                    de_ai_response,
                    fallback_titles=st.session_state.get("title_candidates", []),
                )
                st.session_state.title_candidates = pure_titles
                st.session_state.final_article = build_structured_article_text(pure_titles, pure_article) or (de_ai_response or "").strip()
                st.session_state.highlighted_article = highlighted_article
                if de_ai_variant == DE_AI_VARIANT_COMMUNITY:
                    de_ai_stage_label = "去AI味定稿（社区版）"
                elif de_ai_variant == DE_AI_VARIANT_CHAT:
                    de_ai_stage_label = "去AI味定稿（唠嗑版）"
                else:
                    de_ai_stage_label = "去AI味定稿"
                append_article_version(
                    st.session_state.final_article,
                    de_ai_stage_label,
                    role=current_role,
                    model=st.session_state.get('de_ai_model', DE_AI_MODELS[0]),
                    highlighted_article=highlighted_article,
                )
                save_draft()
                if enable_script:
                    generate_script_for_current_article(api_key, current_base_url, selected_model, script_duration)
                else:
                    st.session_state.spoken_script = ""

                if st.session_state.get("podcast_enabled"):
                    generate_podcast_script_for_current_article(
                        api_key,
                        current_base_url,
                        selected_model,
                        st.session_state.get("podcast_duration", "5分钟"),
                    )
                else:
                    reset_podcast_outputs(delete_audio=True)
                if not highlighted_article:
                    st.warning("高亮版生成失败，本次仅保留纯净定稿。")
                checkpoint_ai_stage("de_ai_generation", target_step=6)
                save_draft()
                notify_step_completed(defer_until_rerun=True)
                go_to_step(6)
                st.rerun()
                checkpoint_ai_stage("de_ai_generation", target_step=6)
                save_draft()
                notify_step_completed(defer_until_rerun=True)
                go_to_step(6)
                st.rerun()
# --- Step 6：终极版分栏 UI ---

elif st.session_state.current_step == 6:
    refresh_obsidian_influence_map()
    render_section_intro("分发工作台", "在统一界面完成定稿审阅、脚本联动、搜图建议、导出分发和后续精修。", "Step 06")
    display_final_article = build_display_article_text(
        st.session_state.get("final_article", ""),
        st.session_state.get("title_candidates", []),
    )
    render_context_strip([f"最终角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '自动路由'}", f"当前模型：{selected_model}", f"脚本状态：{'已生成' if st.session_state.spoken_script else '未生成'}"])
    st.markdown("<p class='toolbar-note'>左侧保持主稿、复制、导出与分发链路；右侧上方用于观察 Obsidian 影响，下方保留精修对话。</p>", unsafe_allow_html=True)
    left_col, right_col = st.columns([1.45, 0.98])
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
                    restore_article_version_to_session(
                        selected_version,
                        fallback_titles=st.session_state.get("title_candidates", []),
                    )
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
        st.code(display_final_article, language="markdown")
        render_editor_friendly_copy_button(display_final_article, "final_article_step6")

        st.divider()
        st.markdown("### 高亮阅读版")
        render_highlighted_article_panel(st.session_state.get("highlighted_article", ""))

        if st.session_state.spoken_script:
            st.divider()
            st.markdown(f"### 分镜脚本 · {script_duration}")
            st.code(st.session_state.spoken_script, language="markdown")
            render_editor_friendly_copy_button(st.session_state.spoken_script, "spoken_script_step6")
        if st.session_state.get("podcast_enabled"):
            st.divider()
            st.markdown(f"### 播客解说稿 · {st.session_state.get('podcast_duration', '5分钟')}")
            podcast_script_segments = st.session_state.get("podcast_script_segments", []) or []
            podcast_script_json = json.dumps(podcast_script_segments, ensure_ascii=False, indent=2) if podcast_script_segments else ""

            podcast_btn_col1, podcast_btn_col2, podcast_btn_col3 = st.columns(3)
            with podcast_btn_col1:
                if st.button("重生成播客稿", use_container_width=True):
                    with st.spinner("正在生成单人播客解说稿..."):
                        success = generate_podcast_script_for_current_article(
                            api_key,
                            current_base_url,
                            selected_model,
                            st.session_state.get("podcast_duration", "5分钟"),
                        )
                        if success:
                            st.success("播客解说稿已更新。")
                        else:
                            st.error(st.session_state.get("podcast_last_error", "播客解说稿生成失败。"))
                        st.rerun()
            with podcast_btn_col2:
                synthesize_clicked = st.button("合成播客音频", use_container_width=True)
            with podcast_btn_col3:
                if st.button("清除播客音频", use_container_width=True):
                    audio_path = st.session_state.get("podcast_audio_path", "")
                    if audio_path and os.path.exists(audio_path):
                        try:
                            os.remove(audio_path)
                        except OSError:
                            pass
                    st.session_state.podcast_audio_path = ""
                    st.session_state.podcast_audio_manifest = {}
                    st.session_state.podcast_last_error = ""
                    save_draft()
                    st.rerun()

            if podcast_script_json:
                st.code(podcast_script_json, language="json")
                render_editor_friendly_copy_button(podcast_script_json, "podcast_script_step6")
            else:
                st.info("当前还没有播客解说稿。你可以先点击“重生成播客稿”。")

            if synthesize_clicked:
                if not podcast_script_segments:
                    st.error("请先生成播客解说稿，再进行音频合成。")
                elif not (podcast_api_key or "").strip():
                    st.error("请先在侧边栏填写阿里云 DashScope API Key。")
                else:
                    with st.spinner("正在合成播客音频..."):
                        try:
                            ensure_podcast_runtime_dirs()
                            output_path, manifest = synthesize_podcast(
                                segments=podcast_script_segments,
                                voice=st.session_state.get("podcast_voice", DEFAULT_TTS_VOICE),
                                api_key=podcast_api_key.strip(),
                                output_dir=PODCAST_OUTPUT_DIR,
                                cache_dir=PODCAST_CACHE_DIR,
                                model=DEFAULT_TTS_MODEL,
                            )
                            st.session_state.podcast_audio_path = output_path
                            st.session_state.podcast_audio_manifest = manifest
                            st.session_state.podcast_last_error = ""
                            save_draft()
                            st.success("播客音频已生成。")
                            st.rerun()
                        except PodcastAudioError as exc:
                            st.session_state.podcast_last_error = str(exc)
                            save_draft()
                            st.error(str(exc))

            podcast_error = st.session_state.get("podcast_last_error", "")
            if podcast_error:
                st.error(podcast_error)

            podcast_audio_path = st.session_state.get("podcast_audio_path", "")
            if podcast_audio_path and os.path.exists(podcast_audio_path):
                with open(podcast_audio_path, "rb") as podcast_audio_file:
                    podcast_audio_bytes = podcast_audio_file.read()
                st.audio(podcast_audio_bytes, format="audio/mp3")
                st.download_button(
                    label="下载播客 MP3",
                    data=podcast_audio_bytes,
                    file_name=os.path.basename(podcast_audio_path),
                    mime="audio/mpeg",
                    use_container_width=True,
                )
                manifest = st.session_state.get("podcast_audio_manifest", {}) or {}
                if manifest:
                    voice = manifest.get("voice", "")
                    st.caption(
                        f"音色：{PODCAST_VOICE_LABELS.get(voice, voice)}｜"
                        f"片段数：{manifest.get('segment_count', 0)}｜缓存命中：{manifest.get('cache_hits', 0)}"
                    )

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
                """ + get_article_body_text(st.session_state.final_article)

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
            
        docx_data = create_docx(display_final_article, st.session_state.spoken_script if st.session_state.spoken_script else None)
        
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
                    success, msg = push_to_feishu(display_final_article, st.session_state.spoken_script if st.session_state.spoken_script else None)
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
        should_show_obsidian_influence = bool(
            st.session_state.get("obsidian_enabled")
            and (st.session_state.get("final_article", "") or "").strip()
            and (st.session_state.get("obsidian_hits", []) or [])
        )
    
        if should_show_obsidian_influence:
            with st.container(border=True):
                render_obsidian_influence_panel(
                    st.session_state.get("obsidian_influence_map", []),
                    st.session_state.get("obsidian_influence_summary", ""),
                )
    
        with st.container(border=True):
            render_section_intro("精修对话区", "保留消息历史与输入框，用于继续追问出处或改写段落。", "对话")
            chat_container = st.container(height=360)
    
            with chat_container:
                for msg in st.session_state.chat_history:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])
    
            if user_query := st.chat_input("输入修改指令、追问出处或改写要求，回车发送..."):
                st.session_state.chat_history.append({"role": "user", "content": user_query})
                with chat_container:
                    with st.chat_message("user"):
                        st.markdown(user_query)
    
                    with st.chat_message("assistant"):
                        with st.spinner("思考中..."):
                            knowledge_context = build_chat_knowledge_context()
                            chat_sys_prompt = f"""You are an expert article refinement and source-tracing assistant.
    
                            [Reference source library - the only factual source for tracing]
                            {st.session_state.source_content}
    
                            [Current final article]
                            {display_final_article}
    
                            [Background knowledge library]
                            {knowledge_context}
    
                            [Tasks]
                            1. If the user asks for sourcing, only use the reference source library to locate the original passage. Do not treat the knowledge library as a news-fact source.
                            2. If the user asks for a rewrite, output the replacement paragraph directly with no extra chatter.
                            3. If the user asks for derived analysis, combine the article and the background knowledge carefully. Mention the note title first when you rely on the knowledge library.
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







































































