import streamlit as st
import streamlit.components.v1 as components
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
import hashlib
import html
import base64
import csv
from datetime import datetime

try:
    from openpyxl import load_workbook
except Exception:
    load_workbook = None

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

# ==========================================
# 0. 闂傚倷绀佸﹢杈╁垝椤栫偛绀夋俊銈呮噹妗呭┑妞村摜鈧碍姘ㄩ埀绠嶉崕杈┾偓姘煎櫍閹兘濮€閵堝棛鍘鹃梺鑺ッˇ钘壩ｉ悜妯镐簻妞ゆ挾鍋熸晶锔介悙鏉戝闁瑰嘲鎳忛ˇ鐗堟償閿濆棙鐦滈梻鍌欑閹诧繝鎮уΔ鍛亱婵犲﹤鐗滈弫鍥煙闂傚鍔嶉柛?(JSON 闂備浇鐎涒晝绮欓幒妤佹櫔闂?
# ==========================================
PROMPTS_FILE = "prompts.json"
DRAFT_FILE = "draft_state.json"

DEFAULT_GLOBAL_PROMPT = """【全局强制写作规范（最高优先级）】
1. 尽量避免机械化过渡句和明显 AI 腔。
2. 专业概念请尽量用清晰的人话表达，不要堆砌黑话。
3. 结尾避免空泛升华，优先给出冷静结论或留白。
4. 多用短句，增强真实写稿时的呼吸感。"""
FINAL_POLISH_MODEL = "qwen3.5-plus"
DEFAULT_FINAL_POLISH_MODE = "微信公众号"
DEFAULT_FINAL_POLISHERS = {
    "玩家社区": "你是一位骨灰级玩家兼硬核游戏爱好者，请把输入内容改写成更适合玩家社区发布的帖子正文，语言更接地气、更有讨论感，避免公文腔和 AI 腔。",
    "微信公众号": "你是一位拥有一线经验的海外游戏发行与运营专家，请把输入内容润色成适合专业游戏行业微信公众号发布的深度推文，提升逻辑密度、专业度与可读性。"
}

def load_prompts():
    default_data = {
        "editors": {
            "发行主编": "你是一位资深的海外发行主编。请深度分析素材，重点关注买量、ROI 与发行策略。",
            "研发主编": "你是一位兼具制作人视角的研发主编。请深度拆解素材，重点关注核心循环、系统设计与研发工业化。",
            "游戏快讯编辑": "你是一名专业、客观的游戏新闻编辑。请根据输入内容提炼核心信息，写成简洁准确的新闻快讯。",
            "客观转录编辑": "你是一位客观、克制的游戏媒体编译记者。请忠实转述原始素材，不掺入主观判断。",
            "游戏行业评论人": "你是一位资深游戏行业观察主编。请从商业逻辑、组织变化与市场趋势角度，对输入事件做出深度评论。",
            "镜像翻译引擎": "你是一个严谨的镜像翻译引擎。请将输入内容准确翻译为中文，并严格保留原始结构与格式。"
        },
        "reviewer": "你是一位极其严苛的资深游戏媒体主编兼风控专家。请严格核查初稿中的事实错误、逻辑漏洞和 AI 腔表达，并给出明确的审稿结论与修改建议。",
        "global_instruction": DEFAULT_GLOBAL_PROMPT,
        "final_polishers": DEFAULT_FINAL_POLISHERS.copy()
    }

    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                data = {}

            if "editors" not in data or not isinstance(data["editors"], dict):
                data["editors"] = {}

            for role_name, role_prompt in default_data["editors"].items():
                if role_name not in data["editors"] or not data["editors"][role_name]:
                    data["editors"][role_name] = role_prompt

            if "reviewer" not in data or not data["reviewer"]:
                data["reviewer"] = default_data["reviewer"]

            if "global_instruction" not in data or not data["global_instruction"]:
                data["global_instruction"] = DEFAULT_GLOBAL_PROMPT

            if "final_polishers" not in data:
                if "final_polisher" in data and isinstance(data["final_polisher"], str):
                    data["final_polishers"] = {
                        "玩家社区": data["final_polisher"],
                        "微信公众号": data["final_polisher"]
                    }
                else:
                    data["final_polishers"] = DEFAULT_FINAL_POLISHERS.copy()
            elif isinstance(data["final_polishers"], dict):
                for polish_mode, polish_prompt in DEFAULT_FINAL_POLISHERS.items():
                    if polish_mode not in data["final_polishers"] or not data["final_polishers"][polish_mode]:
                        data["final_polishers"][polish_mode] = polish_prompt
            else:
                data["final_polishers"] = DEFAULT_FINAL_POLISHERS.copy()

            save_prompts(data)
            return data
        except Exception:
            pass

    save_prompts(default_data)
    return default_data

def save_prompts(data):
    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def save_draft():
    keys_to_save = [
        'current_step', 'article_url', 'video_url', 'source_content', 
        'source_images', 'removed_source_images', 'extraction_success', 'draft_article', 
        'review_feedback', 'final_article', 'spoken_script', 
        'chat_history', 'image_keywords', 'selected_role', 'final_polisher_mode',
        'raw_final_article', 'raw_spoken_script', 'final_polish_applied',
        'article_versions', 'source_materials'
    ]
    draft_data = {k: st.session_state[k] for k in keys_to_save if k in st.session_state}
    try:
        with open(DRAFT_FILE, "w", encoding="utf-8") as f:
            json.dump(draft_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"闂傚倷娴囨竟鍫澪涢崟鍥焼瀹ヤ讲鍋撻敃鍌氶唶闁绘棁銆€閺€鍐测攽閻樼粯娑ф俊鐩獮妤呮偐瀹稿數闂佽鍨辨竟鍡欒姳閸ф鍋? {e}")

def load_draft():
    if os.path.exists(DRAFT_FILE):
        try:
            with open(DRAFT_FILE, "r", encoding="utf-8") as f:
                draft_data = json.load(f)
            for k, v in draft_data.items():
                st.session_state[k] = v
            if "article_versions" not in st.session_state or not isinstance(st.session_state.article_versions, list):
                st.session_state.article_versions = []
            if "source_materials" not in st.session_state or not isinstance(st.session_state.source_materials, list):
                st.session_state.source_materials = []
            return True
        except Exception as e:
            print(f"闂傚倷娴囨竟鍫澪涢崟鍥焼瀹ヤ讲鍋撻敃鍌氶唶闁靛繆鈧磭褰撮梺鑽ゅТ濞诧箒銇愰崘绶ら柛鍣悢鍡涙煙椤栨稑缂併劌娼￠幃? {e}")
            return False
    return False

def clear_draft():
    if os.path.exists(DRAFT_FILE):
        try:
            os.remove(DRAFT_FILE)
        except:
            pass

def get_image_widget_key(prefix, image_url):
    digest = hashlib.md5(image_url.encode('utf-8')).hexdigest()[:12]
    return f'{prefix}_{digest}'

def clear_image_selection_state():
    for key in list(st.session_state.keys()):
        if key.startswith('remove_image_'):
            del st.session_state[key]

def render_image_preview_card(image_url, title, badge_text, badge_class=""):
    safe_url = html.escape(image_url, quote=True)
    safe_title = html.escape(title)
    safe_badge = html.escape(badge_text)
    card_html = (
        f'<div class="image-preview-card {badge_class}">'
        f'<div class="image-preview-meta">'
        f'<span class="image-preview-title">{safe_title}</span>'
        f'<span class="image-preview-badge">{safe_badge}</span>'
        f'</div>'
        f'<img src="{safe_url}" alt="{safe_title}" />'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

def uploaded_file_to_data_url(uploaded_file):
    file_bytes = uploaded_file.getvalue()
    mime_type = uploaded_file.type or "image/png"
    return bytes_to_data_url(file_bytes, mime_type)

def bytes_to_data_url(file_bytes, mime_type="image/png"):
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


STRATEGY_LABELS_ZH = {
    "direct_html": "直连网页正文",
    "reader_proxy": "代理阅读兜底",
    "browser_render": "浏览器渲染抓取",
    "screenshot_fallback": "截图兜底",
    "reddit_adapter": "Reddit 适配抓取",
    "youtube_transcript": "YouTube 字幕提取",
    "file_upload_image": "上传图片素材",
    "file_upload_text": "上传文本素材",
    "file_upload_docx": "上传 Word 素材",
    "file_upload_xlsx": "上传 Excel 素材",
    "file_upload_csv": "上传 CSV 素材",
    "file_upload_unsupported": "上传文件暂不支持",
    "file_upload_error": "上传文件解析失败",
    "unknown": "未识别策略"
}

CONFIDENCE_LABELS_ZH = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知"
}

def localize_strategy_name(strategy_used):
    return STRATEGY_LABELS_ZH.get(strategy_used, strategy_used or "未识别策略")


def localize_confidence(confidence):
    return CONFIDENCE_LABELS_ZH.get(confidence, confidence or "未知")


def localize_attempt_log(log_text):
    if not log_text:
        return "暂无抓取日志"

    localized = log_text
    replacements = [
        ("Direct HTML status", "直连网页状态"),
        ("Direct HTML content quality too low", "直连网页正文质量偏低"),
        ("Direct HTML failed:", "直连网页正文失败："),
        ("Direct HTML exception", "直连网页异常"),
        ("Reader proxy status", "代理阅读状态"),
        ("Reader proxy content quality too low", "代理阅读正文质量偏低"),
        ("Reader proxy failed:", "代理阅读兜底失败："),
        ("Reader proxy exception", "代理阅读异常"),
        ("Browser render failed:", "浏览器渲染失败："),
        ("Browser render completed", "浏览器渲染完成"),
        ("Browser render content quality too low", "浏览器渲染正文质量偏低"),
        ("Browser render unavailable", "浏览器渲染当前不可用"),
        ("Screenshot fallback unavailable", "截图兜底当前不可用"),
        ("Screenshot fallback captured", "截图兜底已捕获"),
        ("images", "图片"),
        ("success", "成功"),
        ("failed:", "失败："),
        (" failed", " 失败"),
        ("returned empty content", "返回空内容"),
        ("Playwright is not installed.", "当前环境未安装 Playwright。"),
        ("No screenshots captured", "未捕获到截图")
    ]
    for old_text, new_text in replacements:
        localized = localized.replace(old_text, new_text)
    for strategy_key, strategy_label in STRATEGY_LABELS_ZH.items():
        localized = localized.replace(strategy_key, strategy_label)
    return localized


def summarize_attempt_logs(attempt_logs):
    if not attempt_logs:
        return "暂无抓取日志"
    return " -> ".join(localize_attempt_log(log) for log in attempt_logs[:3])


def render_source_material_card(material):
    source_label = html.escape(material.get("source_label") or material.get("source_url") or "未命名来源")
    source_url = html.escape(material.get("source_url") or "")
    strategy_used = html.escape(localize_strategy_name(material.get("strategy_used") or "unknown"))
    confidence = html.escape(localize_confidence(material.get("confidence") or "unknown"))
    status_text = "抓取成功" if material.get("success") else "抓取失败"
    status_class = "success" if material.get("success") else "failed"
    title = html.escape(material.get("title") or "未识别标题")
    log_text = html.escape(summarize_attempt_logs(material.get("attempt_logs") or []))
    text_length = material.get("text_length", 0)
    image_count = material.get("image_count", 0)
    screenshot_count = material.get("screenshot_count", 0)
    error_reason = html.escape(localize_attempt_log(material.get("error_reason") or ""))

    url_markup = f'<div class="source-card-url">{source_url}</div>' if source_url else ""
    error_markup = f'<div class="source-card-error">{error_reason}</div>' if error_reason else ""
    card_html = (
        f'<div class="source-card">'
        f'<div class="source-card-head">'
        f'<div>'
        f'<div class="source-card-label">{source_label}</div>'
        f'<div class="source-card-title">{title}</div>'
        f'{url_markup}'
        f'</div>'
        f'<span class="source-card-status {status_class}">{status_text}</span>'
        f'</div>'
        f'<div class="source-card-metrics">'
        f'<span class="source-card-chip">抓取策略：{strategy_used}</span>'
        f'<span class="source-card-chip">可信度：{confidence}</span>'
        f'<span class="source-card-chip">文本长度：{text_length}</span>'
        f'<span class="source-card-chip">图片数量：{image_count}</span>'
        f'<span class="source-card-chip">截图数量：{screenshot_count}</span>'
        f'</div>'
        f'<div class="source-card-log">{log_text}</div>'
        f'{error_markup}'
        f'</div>'
    )
    st.markdown("".join(card_html), unsafe_allow_html=True)

def decode_uploaded_text(file_bytes):
    for encoding in ["utf-8-sig", "utf-8", "gb18030", "utf-16"]:
        try:
            return file_bytes.decode(encoding)
        except Exception:
            continue
    return None

def extract_text_from_docx_bytes(file_bytes):
    doc = Document(io.BytesIO(file_bytes))
    parts = []

    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    if paragraphs:
        parts.append("\n".join(paragraphs))

    table_blocks = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            table_blocks.append("\n".join(rows))

    if table_blocks:
        parts.append("\n\n".join(table_blocks))

    return "\n\n".join(parts).strip()

def extract_text_from_excel_bytes(file_bytes):
    if load_workbook is None:
        raise RuntimeError("当前环境未安装 openpyxl，暂时无法解析 Excel 文件。")

    workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet_blocks = []

    for sheet in workbook.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            cleaned = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
            if cleaned:
                rows.append(" | ".join(cleaned))
        if rows:
            sheet_blocks.append(f"工作表: {sheet.title}\n" + "\n".join(rows))

    return "\n\n".join(sheet_blocks).strip()

def extract_text_from_csv_bytes(file_bytes):
    decoded = decode_uploaded_text(file_bytes)
    if not decoded:
        return ""

    reader = csv.reader(io.StringIO(decoded))
    rows = []
    for row in reader:
        cleaned = [cell.strip() for cell in row if cell and cell.strip()]
        if cleaned:
            rows.append(" | ".join(cleaned))
    return "\n".join(rows).strip()

def build_uploaded_file_material(uploaded_files):
    uploaded_images = []
    uploaded_text_blocks = []
    errors = []
    success_count = 0
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    text_exts = {".txt", ".md"}
    excel_exts = {".xlsx"}
    csv_exts = {".csv"}
    doc_exts = {".docx"}

    for idx, uploaded_file in enumerate(uploaded_files, start=1):
        file_name = uploaded_file.name or f"uploaded_file_{idx}"
        suffix = os.path.splitext(file_name)[1].lower()
        file_bytes = uploaded_file.getvalue()
        mime_type = (uploaded_file.type or "").lower()

        try:
            if mime_type.startswith("image/") or suffix in image_exts:
                data_url = uploaded_file_to_data_url(uploaded_file)
                uploaded_images.append(data_url)
                uploaded_text_blocks.append(f"[上传图片素材 {idx}] 文件名：{file_name}\n这是一张用户上传的截图或图片素材，请结合图像内容参与分析。")
                success_count += 1
            elif suffix in text_exts:
                decoded_text = decode_uploaded_text(file_bytes)
                if decoded_text and decoded_text.strip():
                    uploaded_text_blocks.append(f"[上传文本素材 {idx}] 文件名：{file_name}\n{decoded_text.strip()}")
                    success_count += 1
                else:
                    errors.append(f"文本文件 {file_name} 内容为空，暂时无法用于分析。")
            elif suffix in doc_exts:
                doc_text = extract_text_from_docx_bytes(file_bytes)
                if doc_text:
                    uploaded_text_blocks.append(f"[上传 Word 素材 {idx}] 文件名：{file_name}\n{doc_text}")
                    success_count += 1
                else:
                    errors.append(f"Word 文件 {file_name} 未解析出正文内容。")
            elif suffix in excel_exts:
                excel_text = extract_text_from_excel_bytes(file_bytes)
                if excel_text:
                    uploaded_text_blocks.append(f"[上传 Excel 素材 {idx}] 文件名：{file_name}\n{excel_text}")
                    success_count += 1
                else:
                    errors.append(f"Excel 文件 {file_name} 未解析出正文内容。")
            elif suffix in csv_exts:
                csv_text = extract_text_from_csv_bytes(file_bytes)
                if csv_text:
                    uploaded_text_blocks.append(f"[上传 CSV 素材 {idx}] 文件名：{file_name}\n{csv_text}")
                    success_count += 1
                else:
                    errors.append(f"CSV 文件 {file_name} 未解析出正文内容。")
            else:
                errors.append(f"文件 {file_name} 暂不支持解析，请上传 png、jpg、webp、txt、md、docx、xlsx 或 csv。")
        except Exception as e:
            errors.append(f"文件 {file_name} 处理失败：{str(e)}")

    combined_text = "\n\n================\n\n".join(uploaded_text_blocks)
    return combined_text, uploaded_images, success_count, errors

def build_uploaded_file_material_v2(uploaded_files):
    uploaded_images = []
    uploaded_text_blocks = []
    errors = []
    success_count = 0
    source_materials = []
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    text_exts = {".txt", ".md"}
    excel_exts = {".xlsx"}
    csv_exts = {".csv"}
    doc_exts = {".docx"}

    def build_file_material(idx, file_name, strategy_used, success, text_length=0, image_count=0, error_reason=""):
        return {
            "source_label": f"上传文件 {idx}",
            "source_url": file_name,
            "source_type": "file",
            "title": file_name,
            "strategy_used": strategy_used,
            "attempt_logs": [strategy_used],
            "success": success,
            "error_reason": error_reason,
            "confidence": "high" if success else "low",
            "text_length": text_length,
            "image_count": image_count,
            "screenshot_count": 0
        }

    for idx, uploaded_file in enumerate(uploaded_files, start=1):
        file_name = uploaded_file.name or f"uploaded_file_{idx}"
        suffix = os.path.splitext(file_name)[1].lower()
        file_bytes = uploaded_file.getvalue()
        mime_type = (uploaded_file.type or "").lower()

        try:
            if mime_type.startswith("image/") or suffix in image_exts:
                data_url = uploaded_file_to_data_url(uploaded_file)
                uploaded_images.append(data_url)
                uploaded_text_blocks.append(f"[上传图片素材 {idx}] 文件名：{file_name}\n这是一张用户上传的截图或图片素材，请结合图像内容参与分析。")
                source_materials.append(build_file_material(idx, file_name, "file_upload_image", True, image_count=1))
                success_count += 1
            elif suffix in text_exts:
                decoded_text = decode_uploaded_text(file_bytes)
                if decoded_text and decoded_text.strip():
                    cleaned_text = decoded_text.strip()
                    uploaded_text_blocks.append(f"[上传文本素材 {idx}] 文件名：{file_name}\n{cleaned_text}")
                    source_materials.append(build_file_material(idx, file_name, "file_upload_text", True, text_length=len(cleaned_text)))
                    success_count += 1
                else:
                    error_message = f"文本文件 {file_name} 内容为空，暂时无法用于分析。"
                    errors.append(error_message)
                    source_materials.append(build_file_material(idx, file_name, "file_upload_text", False, error_reason=error_message))
            elif suffix in doc_exts:
                doc_text = extract_text_from_docx_bytes(file_bytes)
                if doc_text:
                    uploaded_text_blocks.append(f"[上传 Word 素材 {idx}] 文件名：{file_name}\n{doc_text}")
                    source_materials.append(build_file_material(idx, file_name, "file_upload_docx", True, text_length=len(doc_text)))
                    success_count += 1
                else:
                    error_message = f"Word 文件 {file_name} 未解析出正文内容。"
                    errors.append(error_message)
                    source_materials.append(build_file_material(idx, file_name, "file_upload_docx", False, error_reason=error_message))
            elif suffix in excel_exts:
                excel_text = extract_text_from_excel_bytes(file_bytes)
                if excel_text:
                    uploaded_text_blocks.append(f"[上传 Excel 素材 {idx}] 文件名：{file_name}\n{excel_text}")
                    source_materials.append(build_file_material(idx, file_name, "file_upload_xlsx", True, text_length=len(excel_text)))
                    success_count += 1
                else:
                    error_message = f"Excel 文件 {file_name} 未解析出正文内容。"
                    errors.append(error_message)
                    source_materials.append(build_file_material(idx, file_name, "file_upload_xlsx", False, error_reason=error_message))
            elif suffix in csv_exts:
                csv_text = extract_text_from_csv_bytes(file_bytes)
                if csv_text:
                    uploaded_text_blocks.append(f"[上传 CSV 素材 {idx}] 文件名：{file_name}\n{csv_text}")
                    source_materials.append(build_file_material(idx, file_name, "file_upload_csv", True, text_length=len(csv_text)))
                    success_count += 1
                else:
                    error_message = f"CSV 文件 {file_name} 未解析出正文内容。"
                    errors.append(error_message)
                    source_materials.append(build_file_material(idx, file_name, "file_upload_csv", False, error_reason=error_message))
            else:
                error_message = f"文件 {file_name} 暂不支持解析，请上传 png、jpg、webp、txt、md、docx、xlsx 或 csv。"
                errors.append(error_message)
                source_materials.append(build_file_material(idx, file_name, "file_upload_unsupported", False, error_reason=error_message))
        except Exception as e:
            error_message = f"文件 {file_name} 处理失败：{str(e)}"
            errors.append(error_message)
            source_materials.append(build_file_material(idx, file_name, "file_upload_error", False, error_reason=error_message))

    combined_text = "\n\n================\n\n".join(uploaded_text_blocks)
    return combined_text, uploaded_images, success_count, errors, source_materials





# ==========================================
# 1. API 婵犵數鍋為崹鍫曞箰閸涘娈介柟闂寸蹈閸ャ劎绡€婵﹩鍘鹃崝鎼佹⒑闂堟侗鐓紒鐘冲灴閹虫瑨銇愰幒鎾嫼濡炪倖鍔х槐鏇⑺囬敃鍌涚厱闁靛牆閹插墽鈧?
# ==========================================
def call_llm(api_key, base_url, model_name, system_prompt, user_content, image_urls=None, history=None):
    if not api_key:
        st.error("请先在左侧控制面板填写可用的 API Key。")
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
        st.error(f"API 调用失败：{str(e)}")
        st.stop()


def call_llm_optional(api_key, base_url, model_name, system_prompt, user_content, fallback_text=""):
    if not api_key:
        return fallback_text

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        st.warning(f"模型 {model_name} 调用失败，已回退到原始内容：{str(e)}")
        return fallback_text

def get_final_polisher_prompts(prompts_data):
    final_polishers = prompts_data.get("final_polishers", {})
    if isinstance(final_polishers, str):
        final_polishers = {
            "玩家社区": final_polishers,
            "微信公众号": final_polishers
        }
    elif not isinstance(final_polishers, dict):
        final_polishers = {}

    merged_prompts = DEFAULT_FINAL_POLISHERS.copy()
    for polish_mode, polish_prompt in final_polishers.items():
        if polish_prompt:
            merged_prompts[polish_mode] = polish_prompt
    return merged_prompts

def get_selected_final_polisher_prompt(prompts_data, polish_mode):
    final_polishers = get_final_polisher_prompts(prompts_data)
    return final_polishers.get(polish_mode, final_polishers[DEFAULT_FINAL_POLISH_MODE])

def polish_final_article(api_key, base_url, article_text, polish_prompt):
    if not article_text or not article_text.strip():
        return article_text

    polish_user_content = f"请对下面的文章初稿进行中文语气优化，严格保留核心信息与事实，不要新增未经验证的信息。\n\n{article_text}"
    polished_text = call_llm_optional(
        api_key=api_key,
        base_url=base_url,
        model_name=FINAL_POLISH_MODEL,
        system_prompt=polish_prompt,
        user_content=polish_user_content,
        fallback_text=article_text
    )
    return polished_text or article_text

def push_to_feishu(article_text, script_text=None):
    webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/a0f50778-0dd2-4963-a0b2-0c7b68e113d8"
    headers = {"Content-Type": "application/json"}

    if script_text:
        text_content = f"文章正文：\n{article_text}\n\n================\n\n分镜脚本：\n{script_text}"
    else:
        text_content = f"文章正文：\n{article_text}"

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
                return True, "已成功推送到飞书。"
            return False, f"飞书接口返回错误：{resp_json.get('msg')}"
        return False, f"飞书请求失败，HTTP 状态码：{response.status_code}"
    except Exception as e:
        return False, f"飞书推送失败：{str(e)}"

# ==========================================
# 2. 闂傚倷绀侀幖绮旈崼鏇椻偓锕傚醇閵忋垻澶勬俊銈忕到閸熺不椤曗偓閺岀喖骞嗚閺嗚京绱掗幓鎺撳仴闁哄本鐩獮瀣晲閸涘懏鎹囬弻?
# ==========================================
# (闂傚倷鑳堕、濠囧春閺嶇婵繂琚禍褰掓煕閵夊缂佲偓閸曠厾闁瑰笒閸旀粍绻涢崼锝嗙【閼挎劙鏌涢妷锝呭缂佺姵鐗犻幃妤呭垂椤愶絺鎷圭紓浣搁ˇ婵傜宸濇い鎾跺Х琚?
def extract_youtube_transcript(url):
    try:
        parsed_url = urlparse(url)
        if "youtube.com" in parsed_url.netloc:
            video_id = parse_qs(parsed_url.query).get("v", [None])[0]
        elif "youtu.be" in parsed_url.netloc:
            video_id = parsed_url.path.lstrip("/")
        else:
            return None, [], "无法识别 YouTube 视频 ID。"

        if not video_id:
            return None, [], "未能解析到有效的 YouTube 视频 ID。"

        target_langs = ["zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh", "en"]
        try:
            if hasattr(YouTubeTranscriptApi, "get_transcript"):
                transcript_fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=target_langs)
            else:
                ytt_api = YouTubeTranscriptApi()
                if hasattr(ytt_api, "list"):
                    transcript_list = ytt_api.list(video_id)
                elif hasattr(ytt_api, "list_transcripts"):
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
                if isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
                elif hasattr(item, "text"):
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
                    return None, [], "YouTube 字幕代理兜底返回内容过短。"
                except Exception as jina_e:
                    return None, [], f"YouTube 内容抓取失败，字幕接口与代理兜底都未成功：{str(jina_e)}"
            raise inner_e

    except Exception as e:
        return None, [], f"YouTube 内容提取失败：{str(e)}"


def extract_reddit_content(url):
    blocked_markers = [
        "you've been blocked by network security",
        "use your developer token",
        "target url returned error 403",
        "log in to your reddit account",
        "reddit wants you to log in"
    ]

    def looks_like_block_page(text):
        lowered = (text or "").lower()
        return any(marker in lowered for marker in blocked_markers)

    def parse_reddit_listing(payload):
        if not isinstance(payload, list) or len(payload) < 1:
            return None, [], "Reddit 返回数据结构异常。"

        post_listing = payload[0].get("data", {}).get("children", [])
        if not post_listing:
            return None, [], "未找到 Reddit 帖子主体。"

        post_data = post_listing[0].get("data", {})
        title = (post_data.get("title") or "").strip()
        selftext = (post_data.get("selftext") or "").strip()
        subreddit = post_data.get("subreddit_name_prefixed") or post_data.get("subreddit") or "Reddit"
        author = post_data.get("author") or "未知作者"
        score = post_data.get("score")
        comment_count = post_data.get("num_comments")

        text_parts = [
            f"Reddit 标题：{title}" if title else "Reddit 标题：未识别标题",
            f"社区：{subreddit}",
            f"作者：{author}"
        ]
        if score is not None:
            text_parts.append(f"点赞数：{score}")
        if comment_count is not None:
            text_parts.append(f"评论数：{comment_count}")
        if selftext:
            text_parts.append("")
            text_parts.append("正文：")
            text_parts.append(selftext)

        comments_listing = payload[1].get("data", {}).get("children", []) if len(payload) > 1 else []
        top_comments = []
        for item in comments_listing:
            comment_data = item.get("data", {})
            body = (comment_data.get("body") or "").strip()
            comment_author = comment_data.get("author") or "未知用户"
            if body and body not in ("[deleted]", "[removed]"):
                top_comments.append(f"- u/{comment_author}: {body}")
            if len(top_comments) >= 5:
                break

        if top_comments:
            text_parts.append("")
            text_parts.append("高赞评论：")
            text_parts.extend(top_comments)

        images = []
        preview_images = post_data.get("preview", {}).get("images", [])
        for preview in preview_images:
            source = preview.get("source", {})
            image_url = source.get("url")
            if image_url:
                image_url = image_url.replace("&amp;", "&")
                if image_url not in images:
                    images.append(image_url)

        media_metadata = post_data.get("media_metadata", {})
        for meta in media_metadata.values():
            source = meta.get("s", {})
            image_url = source.get("u") or source.get("gif") or source.get("mp4")
            if image_url:
                image_url = image_url.replace("&amp;", "&")
                if image_url not in images:
                    images.append(image_url)

        text = "\n".join(part for part in text_parts if part is not None).strip()
        if text and len(text) > 50 and not looks_like_block_page(text):
            return text, images[:8], None
        return None, [], "Reddit 返回内容过短或仍被拦截。"

    parsed_url = urlparse(url)
    clean_path = parsed_url.path.rstrip("/")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 ArticleTranscriptionAssistant/1.0",
        "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8"
    }

    candidate_urls = []
    if clean_path:
        candidate_urls.extend([
            f"https://api.reddit.com{clean_path}",
            f"https://www.reddit.com{clean_path}.json?raw_json=1",
            f"https://old.reddit.com{clean_path}.json?raw_json=1"
        ])

    errors = []
    for candidate_url in candidate_urls:
        try:
            response = requests.get(candidate_url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()
            payload = response.json()
            text, images, error = parse_reddit_listing(payload)
            if text:
                return text, images, None
            errors.append(f"{candidate_url} -> {error}")
        except Exception as candidate_error:
            errors.append(f"{candidate_url} -> {str(candidate_error)[:80]}")

    try:
        jina_url = f"https://r.jina.ai/{url}"
        response = requests.get(jina_url, headers={"Accept": "text/plain"}, timeout=20)
        response.raise_for_status()
        text = response.text.strip()
        if text and len(text) > 50 and not looks_like_block_page(text):
            return text, [], None
        errors.append("r.jina.ai 返回内容仍被拦截或过短")
    except Exception as jina_error:
        errors.append(f"r.jina.ai -> {str(jina_error)[:80]}")

    return None, [], "Reddit 内容抓取失败：" + " | ".join(errors[:4])



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
            return None, [], "正文抽取为空，未识别到可用文章内容。"
    except Exception as e:
        return None, [], f"文章抓取失败：{str(e)}"

GENERIC_BLOCK_MARKERS = [
    "access denied",
    "forbidden",
    "enable javascript",
    "please verify you are human",
    "captcha",
    "blocked by network security",
    "request blocked",
    "temporarily unavailable"
]

def normalize_source_url(url):
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()

def infer_source_type(url):
    netloc = urlparse(url).netloc.lower()
    if "youtube.com" in netloc or "youtu.be" in netloc:
        return "video"
    if "reddit.com" in netloc or "redd.it" in netloc:
        return "reddit"
    return "article"

def init_source_result(url, source_type=None):
    source_type = source_type or infer_source_type(url)
    return {
        "source_url": url,
        "normalized_url": normalize_source_url(url),
        "source_type": source_type,
        "title": "",
        "text": "",
        "images": [],
        "screenshots": [],
        "strategy_used": "",
        "attempt_logs": [],
        "success": False,
        "error_reason": "",
        "confidence": "low",
        "error_code": "",
        "browser_unavailable": False
    }

def append_attempt_log(result, message):
    result["attempt_logs"].append(message)

PLAYWRIGHT_BROWSER_MISSING_CODE = "playwright_browser_missing"

def is_browser_runtime_unavailable(error_message):
    lowered = (error_message or "").lower()
    markers = [
        "playwright is not installed",
        "executable doesn't exist",
        "please run the following command to download new browsers",
        "chrome-headless-shell",
        "chromium_headless_shell"
    ]
    return any(marker in lowered for marker in markers)


def get_browser_unavailable_message(capture_screenshots=False):
    if capture_screenshots:
        return "当前环境未安装浏览器渲染组件，截图兜底暂时不可用。请在部署环境执行 playwright install chromium 后重试。"
    return "当前环境未安装浏览器渲染组件，已跳过浏览器抓取。请在部署环境执行 playwright install chromium 后重试。"


def get_browser_unavailable_summary():
    return "常规正文抓取失败，且当前环境未启用浏览器渲染组件。你可以改用可直接访问的链接、上传文件，或在部署环境补装 Chromium 后再试。"

def looks_like_generic_block(text, html_text=""):
    lowered = f"{text or ''}\n{html_text or ''}".lower()
    return any(marker in lowered for marker in GENERIC_BLOCK_MARKERS)

def is_low_quality_text(text, source_type="article"):
    cleaned = (text or "").strip()
    threshold = 400
    if source_type in ("reddit", "video"):
        threshold = 120
    return len(cleaned) < threshold

def extract_title_from_html(html_text):
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        if soup.title and soup.title.text:
            return soup.title.text.strip()
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
    except Exception:
        return ""
    return ""

def extract_article_from_html(url, html_text):
    text = trafilatura.extract(html_text)
    soup = BeautifulSoup(html_text, 'html.parser')

    noise_tags = ['aside', 'nav', 'footer', 'header']
    for noise in soup.find_all(noise_tags):
        noise.decompose()

    noise_keywords = ['author', 'related', 'comment', 'share', 'widget', 'sidebar', 'ad-container', 'popup', 'newsletter']
    for noise in soup.find_all(attrs={'class': lambda c: c and any(k in str(c).lower() for k in noise_keywords)}):
        noise.decompose()
    for noise in soup.find_all(attrs={'id': lambda i: i and any(k in str(i).lower() for k in noise_keywords)}):
        noise.decompose()

    main_content = soup.find('article') or soup.find('main') or soup.find(class_=lambda c: c and 'content' in str(c).lower()) or soup
    title = extract_title_from_html(html_text)
    images = []
    for img in main_content.find_all('img'):
        try:
            html_w = int(img.get('width', 0))
            html_h = int(img.get('height', 0))
        except Exception:
            html_w, html_h = 0, 0

        src = None
        srcset = img.get('data-srcset') or img.get('srcset')
        if srcset:
            sources = []
            for item in srcset.split(','):
                parts = item.strip().split()
                if len(parts) == 2 and parts[1].endswith('w') and parts[1][:-1].isdigit():
                    sources.append((parts[0], int(parts[1][:-1])))
            if sources:
                sources.sort(key=lambda pair: pair[1], reverse=True)
                src = sources[0][0]

        if not src:
            for attr in ['data-original', 'data-lazy-src', 'data-src', 'src']:
                value = img.get(attr)
                if value:
                    if isinstance(value, list):
                        value = value[0]
                    value = str(value).strip()
                    if not value.startswith('data:image'):
                        src = value
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
        junk_keywords = ['icon', 'spinner', 'svg', 'gif', 'button', 'tracker', 'avatar']
        if any(junk in src_lower for junk in junk_keywords):
            continue

        if html_w < 300 and html_h < 300:
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

    return (text or "").strip(), images, title

def extract_with_direct_html(url, source_type="article"):
    result = init_source_result(url, source_type=source_type)
    result["strategy_used"] = "direct_html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        append_attempt_log(result, f"Direct HTML status {response.status_code}")
        if response.status_code >= 400:
            result["error_reason"] = f"HTTP {response.status_code}"
            return result

        text, images, title = extract_article_from_html(url, response.text)
        result["title"] = title
        result["text"] = text
        result["images"] = images
        if looks_like_generic_block(text, response.text) or is_low_quality_text(text, source_type=source_type):
            result["error_reason"] = "Direct HTML content quality too low"
            return result

        result["success"] = True
        result["confidence"] = "high"
        return result
    except Exception as e:
        result["error_reason"] = f"Direct HTML failed: {str(e)}"
        append_attempt_log(result, "Direct HTML exception")
        return result

def extract_with_reader_proxy(url, source_type="article"):
    result = init_source_result(url, source_type=source_type)
    result["strategy_used"] = "reader_proxy"
    try:
        response = requests.get(f"https://r.jina.ai/{url}", headers={"Accept": "text/plain"}, timeout=20)
        append_attempt_log(result, f"Reader proxy status {response.status_code}")
        response.raise_for_status()
        result["title"] = urlparse(url).netloc
        result["text"] = response.text.strip()
        if looks_like_generic_block(result["text"]) or is_low_quality_text(result["text"], source_type=source_type):
            result["error_reason"] = "Reader proxy content quality too low"
            return result

        result["success"] = True
        result["confidence"] = "medium"
        return result
    except Exception as e:
        result["error_reason"] = f"Reader proxy failed: {str(e)}"
        append_attempt_log(result, "Reader proxy exception")
        return result

def run_browser_capture(url, capture_screenshots=False):
    if sync_playwright is None:
        return {
            "html": "",
            "title": "",
            "screenshots": [],
            "error": get_browser_unavailable_message(capture_screenshots),
            "error_code": PLAYWRIGHT_BROWSER_MISSING_CODE,
            "browser_unavailable": True
        }

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1024})
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(1200)

            total_height = page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 1200)")
            page.evaluate("() => window.scrollTo(0, 0)")
            page.wait_for_timeout(250)

            screenshots = []
            if capture_screenshots:
                viewport_height = (page.viewport_size or {}).get("height", 1024)
                section_count = min(5, max(1, int(total_height / max(viewport_height, 1)) + 1))
                max_scroll = max(int(total_height - viewport_height), 0)
                positions = []
                for idx in range(section_count):
                    if section_count == 1:
                        positions.append(0)
                    else:
                        positions.append(int((max_scroll / (section_count - 1)) * idx))
                for position in positions:
                    page.evaluate(f"() => window.scrollTo(0, {position})")
                    page.wait_for_timeout(350)
                    screenshot_bytes = page.screenshot(type="png", full_page=False)
                    screenshots.append(bytes_to_data_url(screenshot_bytes, "image/png"))
                page.evaluate("() => window.scrollTo(0, 0)")
                page.wait_for_timeout(250)

            html_text = page.content()
            title = page.title()
            browser.close()
            return {
                "html": html_text,
                "title": title,
                "screenshots": screenshots,
                "error": "",
                "error_code": "",
                "browser_unavailable": False
            }
    except Exception as e:
        raw_error = str(e)
        if is_browser_runtime_unavailable(raw_error):
            return {
                "html": "",
                "title": "",
                "screenshots": [],
                "error": get_browser_unavailable_message(capture_screenshots),
                "error_code": PLAYWRIGHT_BROWSER_MISSING_CODE,
                "browser_unavailable": True
            }
        return {
            "html": "",
            "title": "",
            "screenshots": [],
            "error": f"Browser render failed: {raw_error}",
            "error_code": "browser_render_failed",
            "browser_unavailable": False
        }

def extract_with_browser_render(url, source_type="article"):
    result = init_source_result(url, source_type=source_type)
    result["strategy_used"] = "browser_render"
    browser_payload = run_browser_capture(url, capture_screenshots=False)
    if browser_payload["error"]:
        result["error_reason"] = browser_payload["error"]
        result["error_code"] = browser_payload.get("error_code", "")
        result["browser_unavailable"] = browser_payload.get("browser_unavailable", False)
        append_attempt_log(result, "Browser render unavailable" if result["browser_unavailable"] else browser_payload["error"])
        return result

    text, images, extracted_title = extract_article_from_html(url, browser_payload["html"])
    result["title"] = extracted_title or browser_payload["title"]
    result["text"] = text
    result["images"] = images
    append_attempt_log(result, "Browser render completed")
    if looks_like_generic_block(text, browser_payload["html"]) or is_low_quality_text(text, source_type=source_type):
        result["error_reason"] = "Browser render content quality too low"
        return result

    result["success"] = True
    result["confidence"] = "medium"
    return result

def extract_with_screenshot_fallback(url, source_type="article"):
    result = init_source_result(url, source_type=source_type)
    result["strategy_used"] = "screenshot_fallback"
    browser_payload = run_browser_capture(url, capture_screenshots=True)
    if browser_payload["error"]:
        result["error_reason"] = browser_payload["error"]
        result["error_code"] = browser_payload.get("error_code", "")
        result["browser_unavailable"] = browser_payload.get("browser_unavailable", False)
        append_attempt_log(result, "Screenshot fallback unavailable" if result["browser_unavailable"] else browser_payload["error"])
        return result

    result["title"] = browser_payload["title"] or urlparse(url).netloc
    result["screenshots"] = browser_payload["screenshots"]
    result["text"] = (
        f"[截图兜底素材] 原始链接：{url}\n"
        f"页面标题：{result['title']}\n"
        "本次未能稳定抽取网页正文，已自动保留页面截图供后续模型结合视觉内容进行分析。请优先参考附带截图，不要把未验证内容当作确定事实。"
    )
    append_attempt_log(result, f"Screenshot fallback captured {len(result['screenshots'])} images")
    if result["screenshots"]:
        result["success"] = True
        result["confidence"] = "low"
        return result

    result["error_reason"] = "No screenshots captured"
    return result

def wrap_legacy_extractor(url, source_type, strategy_used, extractor):
    result = init_source_result(url, source_type=source_type)
    result["strategy_used"] = strategy_used
    try:
        text, images, error = extractor(url)
        result["text"] = text or ""
        result["images"] = images or []
        result["title"] = urlparse(url).netloc
        if text:
            result["success"] = True
            result["confidence"] = "high" if strategy_used == "youtube_transcript" else "medium"
            append_attempt_log(result, f"{strategy_used} success")
        else:
            result["error_reason"] = error or f"{strategy_used} returned empty content"
            append_attempt_log(result, result["error_reason"])
        return result
    except Exception as e:
        result["error_reason"] = f"{strategy_used} failed: {str(e)}"
        append_attempt_log(result, result["error_reason"])
        return result

def summarize_source_result(result, source_label=None):
    return {
        "source_label": source_label or urlparse(result.get("source_url", "")).netloc or "未命名来源",
        "source_url": result.get("source_url", ""),
        "source_type": result.get("source_type", "article"),
        "title": result.get("title", ""),
        "strategy_used": result.get("strategy_used", ""),
        "attempt_logs": result.get("attempt_logs", []),
        "success": result.get("success", False),
        "error_reason": result.get("error_reason", ""),
        "confidence": result.get("confidence", "low"),
        "text_length": len((result.get("text") or "").strip()),
        "image_count": len(result.get("images") or []),
        "screenshot_count": len(result.get("screenshots") or [])
    }

def get_content_from_url(url):
    source_type = infer_source_type(url)
    if source_type == "video":
        return wrap_legacy_extractor(url, "video", "youtube_transcript", extract_youtube_transcript)

    if source_type == "reddit":
        strategies = [
            lambda target_url: wrap_legacy_extractor(target_url, "reddit", "reddit_adapter", extract_reddit_content),
            lambda target_url: extract_with_reader_proxy(target_url, source_type="reddit"),
            lambda target_url: extract_with_browser_render(target_url, source_type="reddit"),
            lambda target_url: extract_with_screenshot_fallback(target_url, source_type="reddit")
        ]
    else:
        strategies = [
            lambda target_url: extract_with_direct_html(target_url, source_type="article"),
            lambda target_url: extract_with_reader_proxy(target_url, source_type="article"),
            lambda target_url: extract_with_browser_render(target_url, source_type="article"),
            lambda target_url: extract_with_screenshot_fallback(target_url, source_type="article")
        ]

    last_result = init_source_result(url, source_type=source_type)
    best_partial_result = None
    route_logs = []
    browser_unavailable = False

    for strategy in strategies:
        last_result = strategy(url)
        route_status = "success" if last_result.get("success") else "failed"
        route_logs.append(f"{last_result.get('strategy_used', 'unknown')} {route_status}")
        if last_result.get("error_reason") and not last_result.get("success"):
            route_logs.append(last_result["error_reason"])

        has_partial_content = bool((last_result.get("text") or "").strip() or (last_result.get("images") or []) or (last_result.get("screenshots") or []))
        if has_partial_content and not last_result.get("success"):
            best_partial_result = dict(last_result)

        if last_result.get("success"):
            last_result["attempt_logs"] = route_logs
            return last_result

        if last_result.get("browser_unavailable"):
            browser_unavailable = True
            break

    if browser_unavailable and best_partial_result:
        best_partial_result["success"] = True
        best_partial_result["confidence"] = "low"
        best_partial_result["attempt_logs"] = route_logs + ["浏览器策略当前不可用，已回退到前序可用结果。"]
        best_partial_result["browser_unavailable"] = True
        best_partial_result["error_code"] = PLAYWRIGHT_BROWSER_MISSING_CODE
        best_partial_result["error_reason"] = ""
        best_partial_result["user_error_summary"] = ""
        return best_partial_result

    if browser_unavailable:
        last_result["user_error_summary"] = get_browser_unavailable_summary()
        last_result["error_code"] = PLAYWRIGHT_BROWSER_MISSING_CODE

    last_result["attempt_logs"] = route_logs
    return last_result
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
    if 'source_materials' not in st.session_state:
        st.session_state.source_materials = []
    if 'removed_source_images' not in st.session_state:
        st.session_state.removed_source_images = []
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
    if 'final_polisher_mode' not in st.session_state:
        st.session_state.final_polisher_mode = DEFAULT_FINAL_POLISH_MODE
    if 'raw_final_article' not in st.session_state:
        st.session_state.raw_final_article = ""
    if 'raw_spoken_script' not in st.session_state:
        st.session_state.raw_spoken_script = ""
    if 'final_polish_applied' not in st.session_state:
        st.session_state.final_polish_applied = False
    if 'sound_notifications_enabled' not in st.session_state:
        st.session_state.sound_notifications_enabled = True
    if 'pending_completion_notice' not in st.session_state:
        st.session_state.pending_completion_notice = None
    if 'notification_counter' not in st.session_state:
        st.session_state.notification_counter = 0
    if 'article_versions' not in st.session_state or not isinstance(st.session_state.article_versions, list):
        st.session_state.article_versions = []

init_state()

def go_to_step(step):
    st.session_state.current_step = step
    save_draft()

def set_final_assets(article_text, script_text=""):
    st.session_state.raw_final_article = article_text or ""
    st.session_state.final_article = article_text or ""
    st.session_state.raw_spoken_script = script_text or ""
    st.session_state.spoken_script = script_text or ""
    st.session_state.final_polish_applied = False
    save_draft()


def record_article_version(content, version_type, source_step, note="", model_name=""):
    normalized_content = (content or "").strip()
    if not normalized_content:
        return False

    if 'article_versions' not in st.session_state or not isinstance(st.session_state.article_versions, list):
        st.session_state.article_versions = []

    existing_versions = st.session_state.article_versions
    if existing_versions:
        last_content = (existing_versions[-1].get("content") or "").strip()
        if last_content == normalized_content:
            return False

    version_entry = {
        "id": len(existing_versions) + 1,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version_type": version_type,
        "source_step": source_step,
        "note": note,
        "role": st.session_state.get("selected_role", ""),
        "model": model_name or "",
        "content": normalized_content
    }
    existing_versions.append(version_entry)
    return True

def format_article_version_label(version):
    version_id = version.get("id", "?")
    version_type = version.get("version_type", "Unnamed")
    source_step = version.get("source_step", "Unknown Step")
    created_at = version.get("created_at", "Unknown Time")
    note = version.get("note", "")
    note_suffix = f" | {note}" if note else ""
    return f"V{version_id} | {version_type} | {source_step} | {created_at}{note_suffix}"

def play_completion_sound(message):
    st.toast(f"任务完成：{message}")
    if st.session_state.get("sound_notifications_enabled", True):
        token = st.session_state.get("notification_counter", 0)
        sound_html = (
            "<script>"
            "const audioContext = new (window.AudioContext || window.webkitAudioContext)();"
            "const now = audioContext.currentTime;"
            "const master = audioContext.createGain();"
            "master.gain.value = 0.22;"
            "master.connect(audioContext.destination);"
            "const playTone = (freq, startOffset, duration, type = 'triangle') => {"
            "const osc = audioContext.createOscillator();"
            "const gain = audioContext.createGain();"
            "const t0 = now + startOffset;"
            "const t1 = t0 + duration;"
            "osc.type = type;"
            "osc.frequency.setValueAtTime(freq, t0);"
            "gain.gain.setValueAtTime(0.0001, t0);"
            "gain.gain.exponentialRampToValueAtTime(1.0, t0 + 0.02);"
            "gain.gain.exponentialRampToValueAtTime(0.0001, t1);"
            "osc.connect(gain);"
            "gain.connect(master);"
            "osc.start(t0);"
            "osc.stop(t1 + 0.01);"
            "};"
            "playTone(880, 0.00, 0.20, 'triangle');"
            "playTone(1320, 0.24, 0.26, 'square');"
            "if (navigator.vibrate) { navigator.vibrate([55, 35, 80]); }"
            "</script>"
            f'<div data-notify-token="{token}"></div>'
        )
        components.html(sound_html, height=0)

def notify_completion(message, defer_until_rerun=False):
    st.session_state.notification_counter = st.session_state.get("notification_counter", 0) + 1
    notice = {"message": message, "token": st.session_state.notification_counter}
    if defer_until_rerun:
        st.session_state.pending_completion_notice = notice
    else:
        st.session_state.pending_completion_notice = None
        play_completion_sound(message)

def render_pending_completion_notice():
    notice = st.session_state.get("pending_completion_notice")
    if notice:
        play_completion_sound(notice.get("message", "任务已完成"))
        st.session_state.pending_completion_notice = None

        st.session_state.pending_completion_notice = None

render_pending_completion_notice()

def get_script_sys_prompt(duration_str):
    duration_map = {
        "1分钟": "200 - 250",
        "3分钟": "600 - 700",
        "5分钟": "1000 - 1200",
        "8分钟": "1600 - 1900"
    }
    target_words = duration_map.get(duration_str, "1000 - 1200")
    return (
        f"你是一名专业的视频分镜脚本编辑，需要根据输入内容输出适合 {duration_str} 口播时长的视频脚本。\n"
        f"目标字数：{target_words} 字。\n"
        "请使用 Markdown 输出，并按 Scene 分段。\n"
        "每个 Scene 都需要包含画面提示、口播内容和必要的转场说明。\n"
        "请确保节奏紧凑、信息完整，适合后续直接进入分镜制作。\n\n"
        "输出示例：\n"
        "### Scene 01\n- 画面：\n- 口播：\n- 转场："
    )


def inject_ui_theme():
    theme_css = (
        "<style>"
        ":root { --panel: rgba(255,255,255,0.78); --border: rgba(41,59,51,0.12); --text: #16211c; --text-muted: #5e6d66; --brand: #1f6f5f; --brand-strong: #174f44; --radius-lg: 24px; --radius-md: 18px; }"
        ".stApp { background: linear-gradient(180deg, #eef6f3 0%, #f9fcfb 100%); color: var(--text); }"
        ".toolbar-note, .source-card-url, .source-card-log { color: var(--text-muted); }"
        ".source-card { background: rgba(255,255,255,0.88); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 1rem 1.1rem; margin-bottom: 1rem; }"
        ".source-card-head { display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; margin-bottom:0.75rem; }"
        ".source-card-label { font-weight:700; color: var(--brand); }"
        ".source-card-title { font-size:1.25rem; font-weight:700; margin-top:0.25rem; }"
        ".source-card-status { display:inline-flex; padding:0.35rem 0.8rem; border-radius:999px; font-weight:700; }"
        ".source-card-status.success { background: rgba(31,111,95,0.12); color:#1f6f5f; }"
        ".source-card-status.failed { background: rgba(200,70,55,0.12); color:#c84637; }"
        ".source-card-metrics { display:flex; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.75rem; }"
        ".source-card-chip { display:inline-flex; padding:0.35rem 0.75rem; border-radius:999px; background:#edf4f1; color:#35534a; font-size:0.92rem; font-weight:600; }"
        ".source-card-error { color:#c84637; margin-top:0.5rem; }"
        ".image-preview-card { border:1px solid var(--border); border-radius:var(--radius-md); overflow:hidden; background:rgba(255,255,255,0.88); margin-bottom:0.75rem; }"
        ".image-preview-meta { display:flex; justify-content:space-between; gap:0.5rem; padding:0.75rem 0.9rem; }"
        ".image-preview-title { font-weight:700; color:var(--text); }"
        ".image-preview-badge { color:var(--brand); font-weight:600; }"
        ".image-preview-card img { width:100%; display:block; }"
        "</style>"
    )
    st.markdown(theme_css, unsafe_allow_html=True)

def render_app_hero():
    hero_html = (
        '<section class="app-hero">'
        '<div class="app-kicker">Professional Media Workspace</div>'
        '<h1>娓告垙鍐呭鐢熶骇宸ヤ綔鍙?/h1>'
        '<p>灏嗘枃绔犻摼鎺ャ€佽棰戝瓧骞曘€佷笂浼犳枃浠朵笌鍥剧墖绱犳潗缁熶竴姹囧叆鍚屼竴鏉″伐浣滄祦锛屽畬鎴愬啓绋裤€佸绋裤€佹敼绋裤€佸畾绋夸笌鍒嗗彂銆?/p>'
        '</section>'
    )
    st.markdown("".join(hero_html), unsafe_allow_html=True)

def render_stepper(current_step):
    steps = [
        (1, "素材输入", "抓取链接、字幕、文件与图片素材"),
        (2, "初稿生成", "选择编辑角色并输出第一版文章"),
        (3, "审稿校对", "主编审阅事实、逻辑与表达"),
        (4, "定稿修订", "根据审稿意见形成最终稿件"),
        (5, "分发工作台", "润色、脚本、搜图、导出与精修")
    ]
    st.markdown("<section class=\"stepper\">", unsafe_allow_html=True)
    cols = st.columns(len(steps))
    for idx, (step_id, label, desc) in enumerate(steps):
        state = "active" if current_step == step_id else ("done" if current_step > step_id else "pending")
        with cols[idx]:
            st.markdown(
                f"<div class=\"stepper-item {state}\"><div class=\"step-index\">{step_id}</div><span class=\"step-label\">{label}</span><span class=\"step-desc\">{desc}</span></div>",
                unsafe_allow_html=True
            )
    st.markdown("</section>", unsafe_allow_html=True)


def render_section_intro(title, subtitle=None, eyebrow=None):
    eyebrow_html = f'<div class="eyebrow">{eyebrow}</div>' if eyebrow else ""
    subtitle_html = f'<p class="section-subtitle">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f"<div class=\"section-head\"><div>{eyebrow_html}<h3 class=\"section-title\">{title}</h3>{subtitle_html}</div></div>",
        unsafe_allow_html=True
    )

def render_context_strip(items):
    chips = "".join([f'<span class="chip active">{item}</span>' for item in items if item])
    if chips:
        st.markdown(f'<div class="context-strip"><div class="chip-row">{chips}</div></div>', unsafe_allow_html=True)

# ==========================================
# 4. 婵犵绱曢崑鎴﹀磹濡ゅ懎鏋侀悹鍥ф▕閻掍粙鏌ｅΔ鈧悧蹇涘煝閺冨牆绠圭紒鍘滈崑鎾绘嚑椤掑倻鍘繝鐢靛仩閹诲緤閸撗冨灊鐎广儱娲ㄩ惌鍫ユ煥閺囨浜鹃梺闈涙鐢€崇暦閿濆棗绶為悘鐐舵閼?
st.set_page_config(page_title="文章转录与写作工作台", page_icon="📝", layout="wide")
inject_ui_theme()

prompts_data = load_prompts()

with st.sidebar:
    st.markdown("## 控制面板")
    st.caption("管理模型、脚本生成策略与写作 Prompt，所有改动都会直接作用到当前工作流。")
    st.session_state.sound_notifications_enabled = st.toggle("启用任务完成提示音", value=st.session_state.sound_notifications_enabled)
    st.header("引擎设置")
    api_provider = st.selectbox("选择 API 中转站", ["BLTCY (柏拉图次元)", "DeerAPI"])

    if api_provider == "BLTCY (柏拉图次元)":
        api_key = st.text_input("输入 BLTCY Key", type="password")
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
        api_key = st.text_input("输入 DeerAPI Key", type="password")
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
        
    selected_model = st.selectbox("选择驱动模型", available_models, index=default_model_idx)
    st.caption(f"当前默认润色模型会在 Step 5 使用 `{FINAL_POLISH_MODEL}`。")

    st.markdown("---")
    st.header("视频分镜设置")
    enable_script = st.toggle("启用伴生【短视频分镜脚本】", value=False)
    script_duration = st.selectbox("设定分镜脚本目标时长", ["1分钟", "3分钟", "5分钟", "8分钟"], index=2, disabled=not enable_script)

    st.markdown("---")
    st.header("Prompt 管理")
    with st.expander("打开 Prompt 配置", expanded=False):
        tab1, tab2, tab3, tab4 = st.tabs(["编辑角色", "审稿角色", "全局指令", "润色 Prompt"])

        with tab1:
            edit_role = st.selectbox("选择【编辑】视角", list(prompts_data["editors"].keys()))
            if edit_role:
                new_prompt = st.text_area("编辑 Prompt（支持临时微调）", value=prompts_data["editors"][edit_role], height=220)
                if st.button("保存当前编辑角色 Prompt", key="save_edit"):
                    prompts_data["editors"][edit_role] = new_prompt
                    save_prompts(prompts_data)
                    st.success(f"已保存角色：{edit_role}")
                    st.rerun()

            new_role_name = st.text_input("新增编辑角色名称")
            new_role_prompt = st.text_area("新增角色 Prompt", height=160)
            if st.button("新增编辑角色"):
                if new_role_name and new_role_name not in prompts_data["editors"]:
                    prompts_data["editors"][new_role_name] = new_role_prompt
                    save_prompts(prompts_data)
                    st.success(f"已新增角色：{new_role_name}")
                    st.rerun()
                else:
                    st.error("角色名称不能为空，且不能与现有角色重复。")

            del_role = st.selectbox("删除编辑角色", list(prompts_data["editors"].keys()), key="delete_editor_role")
            if st.button("删除所选编辑角色", type="primary"):
                if len(prompts_data["editors"]) > 1:
                    del prompts_data["editors"][del_role]
                    save_prompts(prompts_data)
                    st.success(f"已删除角色：{del_role}")
                    st.rerun()
                else:
                    st.error("至少需要保留一个编辑角色。")

        with tab2:
            new_reviewer_prompt = st.text_area("审稿 Prompt", value=prompts_data["reviewer"], height=260)
            if st.button("保存审稿 Prompt"):
                prompts_data["reviewer"] = new_reviewer_prompt
                save_prompts(prompts_data)
                st.success("审稿 Prompt 已保存。")
                st.rerun()

        with tab3:
            new_global_prompt = st.text_area("全局写作规范", value=prompts_data.get("global_instruction", ""), height=260)
            if st.button("保存全局写作规范"):
                prompts_data["global_instruction"] = new_global_prompt
                save_prompts(prompts_data)
                st.success("全局写作规范已保存。")
                st.rerun()

        with tab4:
            polish_prompts = get_final_polisher_prompts(prompts_data)
            polish_tab1, polish_tab2 = st.tabs(["玩家社区", "微信公众号"])

            with polish_tab1:
                player_polish_prompt = st.text_area("玩家社区润色 Prompt", value=polish_prompts.get("玩家社区", DEFAULT_FINAL_POLISHERS["玩家社区"]), height=220)
            with polish_tab2:
                wechat_polish_prompt = st.text_area("微信公众号润色 Prompt", value=polish_prompts.get("微信公众号", DEFAULT_FINAL_POLISHERS["微信公众号"]), height=220)
            if st.button("保存润色 Prompt"):
                prompts_data["final_polishers"] = {
                    "玩家社区": player_polish_prompt,
                    "微信公众号": wechat_polish_prompt
                }
                save_prompts(prompts_data)
                st.success("润色 Prompt 已保存。")
                st.rerun()

render_app_hero()
render_stepper(st.session_state.current_step)

# --- Step 1 ---
if st.session_state.current_step == 1:
    render_section_intro("素材输入中枢", "在同一界面批量汇聚文章链接、YouTube 链接与上传文件，统一进入后续编辑与审稿流程。", "Step 01")

    with st.container(border=True):
        st.markdown("<div class='mode-grid'><div class='mode-card'><strong>批量素材输入</strong><span>支持文章、视频、文件与图片素材统一进入工作流。</span></div><div class='mode-card'><strong>两种工作流模式</strong><span>可选择手动精调或一键全自动驾驶。</span></div><div class='mode-card'><strong>统一定稿工作台</strong><span>润色、脚本、搜图、导出和精修都在最后页面集中处理。</span></div></div>", unsafe_allow_html=True)

    if os.path.exists(DRAFT_FILE):
        with st.container(border=True):
            render_section_intro("继续上次工作", "检测到本地草稿，可继续上一次的编辑流程。", "Recovery")
            st.markdown("<p class='toolbar-note'>恢复草稿会带回当前会话中的正文、图片筛选、版本记录与工作流进度。</p>", unsafe_allow_html=True)
            col_draft1, col_draft2 = st.columns(2)
            with col_draft1:
                if st.button("恢复草稿", type="primary", use_container_width=True):
                    if load_draft():
                        st.success("草稿已恢复。")
                        notify_completion("草稿恢复完成", defer_until_rerun=True)
                        st.rerun()
                    else:
                        st.error("未能恢复草稿，请检查本地草稿文件是否可用。")
            with col_draft2:
                if st.button("清空草稿", use_container_width=True):
                    clear_draft()
                    st.rerun()

    input_col1, input_col2 = st.columns(2)
    with input_col1:
        with st.container(border=True):
            render_section_intro("输入文章链接（每行一个，支持批量）", "支持批量抓取文章正文与图片素材。", "Articles")
            article_url_input = st.text_area(
                "文章链接",
                value=st.session_state.article_url,
                height=180,
                placeholder="https://example.com/article-1\nhttps://example.com/article-2"
            )
    with input_col2:
        with st.container(border=True):
            render_section_intro("输入 YouTube 视频链接（每行一个，支持批量）", "优先提取字幕；若字幕不可用，会尝试代理兜底。", "Videos")
            video_url_input = st.text_area(
                "YouTube 链接",
                value=st.session_state.video_url,
                height=180,
                placeholder="https://youtu.be/...\nhttps://www.youtube.com/watch?v=..."
            )

    with st.container(border=True):
        render_section_intro("上传文件作为文章素材", "支持上传图片、txt、md、Word、Excel 与 CSV，作为原始素材一起参与分析。", "Files")
        uploaded_source_files = st.file_uploader(
            "上传文件",
            type=["png", "jpg", "jpeg", "webp", "txt", "md", "docx", "xlsx", "csv"],
            accept_multiple_files=True,
            help="图片会作为视觉素材参与分析；txt、md、Word、Excel、CSV 会被解析为文本后进入同一工作流。"
        )
        st.markdown("<p class='toolbar-note'>当网页抓取受限时，可以直接上传截图、文本、Word、Excel 或 CSV 作为素材来源。</p>", unsafe_allow_html=True)
        if uploaded_source_files:
            st.caption(f"已选择 {len(uploaded_source_files)} 个上传文件。")

        render_section_intro("开始批量提取", "确认输入无误后，系统会批量抓取文章、字幕与上传文件内容。", "Actions")
        st.markdown("<p class='toolbar-note'>先完成素材提取，再决定进入手动精调或自动驾驶工作流。</p>", unsafe_allow_html=True)
        if st.button("开始批量提取内容", type="primary", use_container_width=True):
            article_urls = [url.strip() for url in article_url_input.split('\n') if url.strip()]
            video_urls = [url.strip() for url in video_url_input.split('\n') if url.strip()]
            uploaded_file_count = len(uploaded_source_files) if uploaded_source_files else 0

            if not article_urls and not video_urls and uploaded_file_count == 0:
                st.warning("请至少输入一条文章链接、一个 YouTube 链接，或上传一个文件。")
            else:
                total_urls = len(article_urls) + len(video_urls)
                total_materials = total_urls + uploaded_file_count
                with st.spinner(f"正在批量提取 {total_materials} 份素材，请稍候…"):
                    combined_content = ""
                    extracted_imgs = []
                    errors = []
                    success_count = 0
                    source_materials = []

                    for idx, a_url in enumerate(article_urls):
                        article_result = get_content_from_url(a_url)
                        source_materials.append(summarize_source_result(article_result, source_label=f"文章素材 {idx+1}"))
                        article_text = article_result.get("text", "")
                        article_images = (article_result.get("images") or []) + (article_result.get("screenshots") or [])
                        if article_text or article_images:
                            combined_content += f"[文章素材 {idx+1}] 来源：{a_url}\n{article_text}\n\n================\n\n"
                            extracted_imgs.extend(article_images)
                            success_count += 1
                        else:
                            article_error = article_result.get("user_error_summary") or article_result.get("error_reason", "提取失败")
                            errors.append(f"文章素材 {idx+1} 提取失败：{article_error}")

                    for idx, v_url in enumerate(video_urls):
                        video_result = get_content_from_url(v_url)
                        source_materials.append(summarize_source_result(video_result, source_label=f"视频素材 {idx+1}"))
                        video_text = video_result.get("text", "")
                        video_images = (video_result.get("images") or []) + (video_result.get("screenshots") or [])
                        if video_text or video_images:
                            combined_content += f"[视频素材 {idx+1}] 来源：{v_url}\n{video_text}\n\n================\n\n"
                            extracted_imgs.extend(video_images)
                            success_count += 1
                        else:
                            video_error = video_result.get("user_error_summary") or video_result.get("error_reason", "提取失败")
                            errors.append(f"视频素材 {idx+1} 提取失败：{video_error}")


                    if uploaded_source_files:
                        uploaded_content, uploaded_images, uploaded_success_count, uploaded_errors, uploaded_materials = build_uploaded_file_material_v2(uploaded_source_files)
                        if uploaded_content:
                            if combined_content:
                                combined_content += f"{uploaded_content}\n\n================\n\n"
                            else:
                                combined_content = uploaded_content + "\n\n================\n\n"
                        extracted_imgs = uploaded_images + extracted_imgs
                        success_count += uploaded_success_count
                        errors.extend(uploaded_errors)
                        source_materials.extend(uploaded_materials)

                    extracted_imgs = extracted_imgs[:15]

                    if combined_content or extracted_imgs:
                        if (not combined_content) and extracted_imgs:
                            combined_content = f"[上传图片素材说明]\n本次共提取到 {len(extracted_imgs)} 张图片素材，后续请结合图片内容参与分析。"
                        st.session_state.article_url = article_url_input
                        st.session_state.video_url = video_url_input
                        st.session_state.source_content = combined_content
                        st.session_state.source_images = extracted_imgs
                        st.session_state.source_materials = source_materials
                        st.session_state.removed_source_images = []
                        st.session_state.article_versions = []
                        st.session_state.extraction_success = True
                        clear_image_selection_state()
                        save_draft()

                        if errors:
                            st.warning("部分素材提取失败，但已保留成功提取的内容。\n" + "\n".join(errors))
                            notify_completion(f"素材提取完成，共成功处理 {success_count} 份素材。")
                        else:
                            st.success(f"素材提取完成：共成功处理 {success_count} 份素材，并提取到 {len(extracted_imgs)} 张图片。")
                            notify_completion(f"素材提取完成，共获得 {len(extracted_imgs)} 张图片。")
                    else:
                        st.session_state.extraction_success = False
                        st.session_state.source_materials = source_materials
                        st.error("所有素材提取均失败，请检查链接、上传文件内容或网络状态。" + ("\n" + "\n".join(errors) if errors else ""))

            render_section_intro("聚合素材预览", "先快速检查抓取结果，再决定进入手动精调还是全自动驾驶。", "Preview")
            source_cols = st.columns(2)
            for idx, material in enumerate(st.session_state.source_materials):
                with source_cols[idx % 2]:
                    render_source_material_card(material)

    if st.session_state.extraction_success:
        with st.container(border=True):
            removed_image_count = len(st.session_state.removed_source_images)
            active_image_count = len(st.session_state.source_images)
            render_section_intro("聚合素材预览", "先快速检查抓取结果，再决定进入手动精调还是全自动驾驶。", "Preview")
            st.caption(f"当前参与分析的图片：{active_image_count} 张；已移除图片：{removed_image_count} 张。")

            if st.session_state.source_materials:
                st.markdown("#### 来源抓取概览")
                source_cols = st.columns(2)
                for idx, material in enumerate(st.session_state.source_materials):
                    with source_cols[idx % 2]:
                        render_source_material_card(material)
                st.divider()

            if st.session_state.source_images:
                st.markdown("#### 当前参与分析的图片")
                img_cols = st.columns(3)
                for idx, img_url in enumerate(st.session_state.source_images):
                    with img_cols[idx % 3]:
                        remove_key = get_image_widget_key("remove_image", img_url)
                        is_marked = st.session_state.get(remove_key, False)
                        render_image_preview_card(
                            img_url,
                            f"图片 {idx + 1}",
                            "已标记移除" if is_marked else "参与分析中",
                            "selected" if is_marked else ""
                        )
                        st.checkbox("标记移除", key=remove_key)

                selected_for_removal = [
                    img_url for img_url in st.session_state.source_images
                    if st.session_state.get(get_image_widget_key("remove_image", img_url), False)
                ]
                if st.button("移除已勾选图片", use_container_width=True):
                    if selected_for_removal:
                        st.session_state.source_images = [
                            img_url for img_url in st.session_state.source_images
                            if img_url not in selected_for_removal
                        ]
                        for img_url in selected_for_removal:
                            if img_url not in st.session_state.removed_source_images:
                                st.session_state.removed_source_images.append(img_url)
                        clear_image_selection_state()
                        save_draft()
                        st.rerun()
                    else:
                        st.info("当前没有勾选待移除的图片。")
            else:
                st.info("当前没有可参与分析的图片，系统会退化为纯文本模式继续工作。")

            if st.session_state.removed_source_images:
                st.divider()
                with st.expander(f"已移除图片（{len(st.session_state.removed_source_images)}）", expanded=False):
                    removed_cols = st.columns(3)
                    for idx, img_url in enumerate(st.session_state.removed_source_images):
                        with removed_cols[idx % 3]:
                            render_image_preview_card(img_url, f"已移除图片 {idx + 1}", "已移除", "removed")
                            if st.button("恢复这张图片", key=get_image_widget_key("restore_image", img_url), use_container_width=True):
                                st.session_state.removed_source_images = [
                                    existing_url for existing_url in st.session_state.removed_source_images
                                    if existing_url != img_url
                                ]
                                if img_url not in st.session_state.source_images:
                                    st.session_state.source_images.append(img_url)
                                clear_image_selection_state()
                                save_draft()
                                st.rerun()

            st.divider()
            st.markdown("#### 素材文本预览")
            preview_text = st.session_state.source_content[:1800] + ("\n\n......(内容已截断)" if len(st.session_state.source_content) > 1800 else "")
            st.code(preview_text, language="markdown")

        with st.container(border=True):
            st.markdown("#### 选择工作流模式")
            st.caption(f"当前共有 {len(st.session_state.source_images)} 张图片会参与后续分析。")
            col_flow1, col_flow2 = st.columns(2)

            with col_flow1:
                st.markdown("<p class='toolbar-note'>手动精调适合逐步把关：先定编辑角色，再看初稿、审稿意见和最终定稿。</p>", unsafe_allow_html=True)
                if st.button("进入手动精调模式", use_container_width=True):
                    go_to_step(2)
                    st.rerun()

            with col_flow2:
                st.markdown("<p class='toolbar-note'>全自动驾驶会自动完成角色匹配、初稿、审稿、改稿，并直接跳转到 Step 5 工作台。</p>", unsafe_allow_html=True)
                if st.button("启动全自动驾驶", type="primary", use_container_width=True):
                    if not api_key:
                        st.error("请先在左侧边栏输入 API Key。")
                        st.stop()

                    with st.status("全自动驾驶已启动，AI 正在接管工作流...", expanded=True) as status:
                        st.write("正在分析素材内容，为你匹配最合适的编辑角色...")
                        editor_names = list(prompts_data["editors"].keys())
                        routing_prompt = (
                            "你是一个智能路由系统。请阅读以下素材，判断哪种编辑角色最适合将其改写为深度文章。"
                            f"请只输出角色的完整名称，不要包含任何解释。可选角色：{', '.join(editor_names)}"
                        )
                        chosen_editor_raw = call_llm(
                            api_key=api_key,
                            base_url=current_base_url,
                            model_name=selected_model,
                            system_prompt=routing_prompt,
                            user_content=st.session_state.source_content[:5000]
                        )
                        chosen_editor = chosen_editor_raw.strip() if chosen_editor_raw else ""
                        if chosen_editor not in editor_names:
                            chosen_editor = editor_names[0]
                        st.session_state.selected_role = chosen_editor
                        st.write(f"已匹配编辑角色：{chosen_editor}")

                        status.update(label="正在生成第一版初稿...", state="running")
                        editor_prompt = prompts_data["editors"][chosen_editor]
                        global_instruction = prompts_data.get("global_instruction", "")
                        final_editor_system_prompt = f"{editor_prompt}\n\n{global_instruction}"
                        draft_content = st.session_state.source_content
                        st.session_state.draft_article = call_llm(
                            api_key=api_key,
                            base_url=current_base_url,
                            model_name=selected_model,
                            system_prompt=final_editor_system_prompt,
                            user_content=draft_content,
                            image_urls=st.session_state.source_images
                        )
                        record_article_version(
                            st.session_state.draft_article,
                            version_type="Auto Draft",
                            source_step="Auto",
                            note=f"Routed Role: {chosen_editor}",
                            model_name=selected_model
                        )

                        status.update(label="正在生成审稿意见...", state="running")
                        reviewer_prompt = prompts_data["reviewer"]
                        anti_hallucination_instruction = "\n\n请严格基于提供的素材写作。不要编造不存在的事实、数据、人物表态或公司动作；若素材不足，请明确说明信息有限。"
                        final_reviewer_system_prompt = reviewer_prompt + anti_hallucination_instruction
                        review_input = (
                            f"请基于以下原始素材与初稿给出审稿意见。\n\n原始素材：\n{st.session_state.source_content}\n\n"
                            f"================\n初稿：\n{st.session_state.draft_article}"
                        )
                        st.session_state.review_feedback = call_llm(
                            api_key=api_key,
                            base_url=current_base_url,
                            model_name=selected_model,
                            system_prompt=final_reviewer_system_prompt,
                            user_content=review_input,
                            image_urls=st.session_state.source_images
                        )

                        status.update(label="正在生成最终定稿...", state="running")
                        final_input = (
                            f"请根据以下审稿意见修改文章，输出最终定稿。\n\n原始素材：\n{st.session_state.source_content}\n\n"
                            f"初稿：\n{st.session_state.draft_article}\n\n审稿意见：\n{st.session_state.review_feedback}"
                        )
                        st.session_state.final_article = call_llm(
                            api_key=api_key,
                            base_url=current_base_url,
                            model_name=selected_model,
                            system_prompt=final_editor_system_prompt + anti_hallucination_instruction,
                            user_content=final_input,
                            image_urls=st.session_state.source_images
                        )
                        record_article_version(
                            st.session_state.final_article,
                            version_type="Auto Final",
                            source_step="Auto",
                            note=f"Routed Role: {chosen_editor}",
                            model_name=selected_model
                        )

                        if enable_script:
                            status.update(label="正在生成分镜脚本...", state="running")
                            st.session_state.spoken_script = call_llm(
                                api_key=api_key,
                                base_url=current_base_url,
                                model_name=selected_model,
                                system_prompt=get_script_sys_prompt(script_duration),
                                user_content=st.session_state.final_article,
                                image_urls=st.session_state.source_images
                            )
                        else:
                            st.session_state.spoken_script = ""

                        save_draft()
                        status.update(label="自动驾驶完成，正在跳转到定稿工作台…", state="complete", expanded=False)

                    notify_completion("自动驾驶流程完成", defer_until_rerun=True)
                    go_to_step(5)
                    st.rerun()

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("返回素材输入"):
            go_to_step(1)
            st.rerun()
    with col2:
        if st.button(f"使用 {selected_model} 生成初稿"):
            with st.spinner("缂傚倸鍊搁崐鎼佸磹瑜版帗鍋嬮柣鎰仛椤愯姤銇勯幇鈺佺労婵為棿鍗抽弻銊モ攽閸℃ê娅ら梻濠庡墻閸撶喖寮诲☉銏犵闁瑰鍎愬Λ锟犳⒑鐠囪尙鍑圭紒鐟╅悰婢跺﹦鍔﹀銈嗗笒鐎氣偓姘…璺ㄦ崉閾忓湱浼囧銈忕畵娴滃爼寮婚埄鍐ㄧ窞閻庯綆浜滈～宀勬⒑閼规澘鍚归柛鐘冲姉缂傛捇鎮剧仦绋夸壕闁挎繂楠告晶浼存煙閸愬樊妯€闁哄矉缍佹俊鍫曞川椤撶姷鈼ら梻浣告啞濮婂宕伴弽鏋佺€广儱鎳愰弳鍡涙煕閹板姢婵炲牊鐓″娲嚃閳轰緡鏆梺鍛婃⒐濞叉粎绱撻幘璇茬鐟滃繑銇欓崘宸唵閻犺櫣灏ㄩ崝鐔虹磼?.."):
                global_instruction = prompts_data.get("global_instruction", "")
                final_editor_system_prompt = f"{editor_prompt}\n\n{global_instruction}"
                
                if st.session_state.source_images:
                    editor_user_content = f"婵犵數鍋涢鎱ㄦ导鏉戠？闁规鍠氱粻鏃堟煕濞戞瑦缍戦柛娆忥攻閵囧嫰寮介妸銉ユ瘓濠碘槅鍨伴鍛村煡婢舵劕绠奸柛鎰屽懎鍤繝鐢靛仜閵堢瑜旈崺鐐哄礃椤旇偐鐤€濡炪倖姊婚弲寮抽悩鐢电＝濞达絾鍨濋崥鈹戦鈧褔鈥﹂崶鏃堝川椤斿吋鐣繝娈垮枟閿曗晠宕滃璺虹９闁靛牆閻撴洟鏌熼柇锕€澧柛锝堥妵鍕籍閳у礉濞嗘挸鏋佺€广儱鎳愰弳鍡涙煕閹板姢婵炲牐灏欑槐鎾诲磼濞嗘垵濡介梺鐟版啞閹倿鐛崘鈺傚劅闁靛鑵归幐鍐⒑閸涘﹥澶勯柛瀣瀵Ω閳哄倻鍘介梺闈涢崕閬嶉埡鍐ｅ亾濞堝灝娅橀柛鎾跺枛楠炲﹪骞樼拠鑼梺闈涙俊鍥€呴銏♀拺缂備焦锕╁▓鏃€绻涚拠褏鎮肩紒鏅濋埀娼ч幉锛勪焊鎼淬劍鈷戦柛鍣崕鎴犵磼閻樺啿娴鐐寸墬濞煎繘宕滆閺嗙娀姊洪幖鐐插闂佸府绲介锝夊箹娴ｅ摜鐤€濡炪倕绻愬Λ娑㈠箟閸ф鈷戦柟瑙勫姦閸ゆ瑧绱掓径瀣唉妤犵偛鍟幆鏃堝Ω閿旇姤鐝繝娈垮枟缁诲啫螣閸濈湏{st.session_state.source_content}"
                else:
                    editor_user_content = f"婵犵數鍋涢鎱ㄦ导鏉戠？闁规鍠氱粻鏃堟煕濞戞瑦缍戦柛娆忥攻閵囧嫰寮介妸銉ユ瘓濠碘槅鍨伴鍛村煡婢舵劕绠奸柛鎰屽懎鍤繝鐢靛仜閵堢瑜旈崺鐐哄礃椤旇偐鐤€濡炪倖姊婚弲寮抽悩鐢电＝濞达絾鍨濋崥鈹戦鈧褔鈥﹂崶鏃堝川椤斿吋鐣繝娈垮枟閿曗晠宕滃璺虹９闁靛牆閻撴洟鏌熼柇锕€澧柛锝堥妵鍕籍閳у礉濞嗘挸鏋佺€广儱鎳愰弳鍡涙煕閹板姢婵炲牊鐓″娲传閸曘儲銇勯銏╂Ц闁伙絽鐏氶幏鍛存惞鐟欏嫸绱℃俊鐐€栭悧妤冪矙閹烘鍊垫い鏇楀亾闁哄矉绻濆畷濂搁姀鐘嫟缂傚倷鑳剁划鈥﹂崶绠栭柟娈垮枙濞屾煕閹炬鎳愰弳銏ゆ⒑绾懎闁规椿浜炵划濠氬箣閿曗偓閸ㄥ倿骞栧ǎ濡奸柛鎴犲█閺岀喖鏌囬敂璺ㄦ晼闂佽崵鍠嗛崹浠嬪蓟瑜忛幏鐘绘嚑椤戝灲閺岀喖婵傜寮伴梺杞扮缁夋挳锝為姀銈呭強缂佸灁{st.session_state.source_content}"
                
                st.session_state.draft_article = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=final_editor_system_prompt,
                    user_content=editor_user_content,
                    image_urls=st.session_state.source_images
                )
                record_article_version(
                    st.session_state.draft_article,
                    version_type="Manual Draft",
                    source_step="Step 02",
                    note=f"Editor Role: {editor_role}",
                    model_name=selected_model
                )
                notify_completion("初稿生成完成", defer_until_rerun=True)
                go_to_step(3)
                st.rerun()

# --- Step 3（审稿准备） ---
elif st.session_state.current_step == 3:
    render_section_intro("审稿准备", "查看当前初稿，决定是否跳过审稿，或先生成审稿意见后再进入定稿。", "Step 03")
    render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"分镜脚本：{'已启用' if enable_script else '未启用'}"])
    
    with st.expander("查看当前初稿（可折叠）", expanded=True):
        st.code(st.session_state.draft_article, language="markdown")
    
    st.divider()
    
    reviewer_prompt = st.text_area("审稿 Prompt（支持临时微调）", value=prompts_data["reviewer"], height=200)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("返回上一步"):
            go_to_step(2)
            st.rerun()
    with col2:
        if st.button("跳过审稿，直接进入定稿"):
            spinner_msg = "正在跳过审稿并准备定稿..."
            with st.spinner(spinner_msg):
                st.session_state.final_article = st.session_state.draft_article
                record_article_version(
                    st.session_state.final_article,
                    version_type="Fast Final",
                    source_step="Step 03",
                    note="Skipped strict review",
                    model_name=selected_model
                )

                if enable_script:
                    script_sys_prompt = get_script_sys_prompt(script_duration)
                    st.session_state.spoken_script = call_llm(
                        api_key=api_key, base_url=current_base_url, model_name=selected_model,
                        system_prompt=script_sys_prompt,
                        user_content=f"请基于下面的最终稿，生成一份适合 {script_duration} 的短视频分镜脚本，要求口语化、镜头清晰、便于直接执行。\n\n{st.session_state.final_article}"
                    )
                else:
                    st.session_state.spoken_script = ""

                set_final_assets(st.session_state.final_article, st.session_state.spoken_script)
                notify_completion("已跳过审稿并进入定稿", defer_until_rerun=True)
                go_to_step(5)
                st.rerun()
    with col3:
        if st.button(f"使用 {selected_model} 生成审稿意见"):
            with st.spinner("正在生成审稿意见..."):
                anti_hallucination_instruction = "\n\n【强制审稿约束】你在核查事实时，只能基于用户提供的原始素材文本与附带图片，不得调用外部知识补充事实。"
                combined_content = (
                    f"下面是聚合的【原始素材文本】（这是唯一的真相来源）：\n{st.session_state.source_content}\n\n================\n"
                    f"下面是【初稿】：\n{st.session_state.draft_article}"
                )


                final_reviewer_system_prompt = reviewer_prompt + anti_hallucination_instruction

                st.session_state.review_feedback = call_llm(
                    api_key=api_key,
                    base_url=current_base_url,
                    model_name=selected_model,
                    system_prompt=final_reviewer_system_prompt,
                    user_content=combined_content,
                    image_urls=st.session_state.source_images
                )
                notify_completion("审稿意见生成完成", defer_until_rerun=True)
                go_to_step(4)
                st.rerun()

# --- Step 4（审稿结果） ---
elif st.session_state.current_step == 4:
    render_section_intro("审稿结果", "查看模型给出的审稿意见，并决定是沿用初稿还是根据反馈生成定稿。", "Step 04")
    render_context_strip([f"当前模型：{selected_model}", f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"分镜脚本：{'已启用' if enable_script else '未启用'}"])
    
    st.info("**下面是模型给出的审稿意见。你可以返回上一步继续修改，也可以直接沿用初稿，或根据审稿意见生成定稿。**")
    st.code(st.session_state.review_feedback, language="markdown")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("返回审稿"):
            go_to_step(3)
            st.rerun()
    with col2:
        if st.button("忽略审稿意见，直接沿用初稿"):
            spinner_msg = "正在沿用初稿并进入定稿..."
            with st.spinner(spinner_msg):
                st.session_state.final_article = st.session_state.draft_article
                record_article_version(
                    st.session_state.final_article,
                    version_type="Forced Final",
                    source_step="Step 04",
                    note="Ignored review feedback",
                    model_name=selected_model
                )
                
                if enable_script:
                    script_sys_prompt = get_script_sys_prompt(script_duration)
                    st.session_state.spoken_script = call_llm(
                        api_key=api_key, base_url=current_base_url, model_name=selected_model,
                        system_prompt=script_sys_prompt,
                        user_content=f"请基于下面的最终稿，生成一份适合 {script_duration} 的短视频分镜脚本，要求口语化、镜头清晰、便于直接执行。\n\n{st.session_state.final_article}"
                    )
                else:
                    st.session_state.spoken_script = ""
                    
                set_final_assets(st.session_state.final_article, st.session_state.spoken_script)
                notify_completion("已沿用初稿作为当前定稿", defer_until_rerun=True)
                go_to_step(5)
                st.rerun()
    with col3:
        if st.button(f"根据审稿意见生成定稿（{selected_model}）"):
            spinner_msg = "正在根据审稿意见生成定稿..."
            with st.spinner(spinner_msg):
                global_instruction = prompts_data.get("global_instruction", "")
                modification_prompt = f"你是一名资深主编，请根据审稿意见修改文章。\n要求：\n1. 优先修正事实、逻辑和表述问题。\n2. 保留原文有效信息，不要凭空新增未经验证的事实。\n3. 输出完整可直接发布的定稿。\n\n{global_instruction}"
                
                content_to_modify = f"【审稿意见】\n{st.session_state.review_feedback}\n\n================\n\n【待修改初稿】\n{st.session_state.draft_article}"
                
                st.session_state.final_article = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt=modification_prompt, 
                    user_content=content_to_modify
                )
                record_article_version(
                    st.session_state.final_article,
                    version_type="Reviewed Final",
                    source_step="Step 04",
                    note="Applied review feedback",
                    model_name=selected_model
                )
                
                if enable_script:
                    script_sys_prompt = get_script_sys_prompt(script_duration)
                    st.session_state.spoken_script = call_llm(
                        api_key=api_key, base_url=current_base_url, model_name=selected_model,
                        system_prompt=script_sys_prompt,
                        user_content=f"请基于下面的最终稿，生成一份适合 {script_duration} 的短视频分镜脚本，要求口语化、镜头清晰、便于直接执行。\n\n{st.session_state.final_article}"
                    )
                else:
                    st.session_state.spoken_script = ""
                
                set_final_assets(st.session_state.final_article, st.session_state.spoken_script)
                notify_completion("定稿生成完成", defer_until_rerun=True)
                go_to_step(5)
                st.rerun()

# --- Step 5（定稿工作台 UI） ---
elif st.session_state.current_step == 5:
    render_section_intro("定稿工作台", "在这里集中处理最终稿、语气优化、分镜脚本、搜图关键词、导出与飞书推送。", "Step 05")
    polish_status = f"已执行「{st.session_state.final_polisher_mode}」润色" if st.session_state.final_polish_applied else "当前未执行语气优化"
    render_context_strip([f"编辑角色：{st.session_state.selected_role if 'selected_role' in st.session_state else '未选择'}", f"当前模型：{selected_model}", f"分镜脚本：{'已生成' if st.session_state.spoken_script else '未生成'}", f"润色状态：{polish_status}"])
    st.markdown("<p class='toolbar-note'>你可以在这里继续做语气优化、版本回看、搜图关键词提取、Word 导出、飞书推送和精修追问，所有操作都会基于当前定稿进行。</p>", unsafe_allow_html=True)
    left_col, right_col = st.columns([1.45, 0.95])
    
    with left_col:
        with st.container(border=True):
            render_section_intro("语气优化", "你可以在当前定稿基础上调用 qwen3.5-plus 做中文语气优化，让文章更适合阅读与发布。", "Polish")
            polish_prompts = get_final_polisher_prompts(prompts_data)
            polish_options = list(polish_prompts.keys())
            if st.session_state.final_polisher_mode not in polish_options:
                st.session_state.final_polisher_mode = DEFAULT_FINAL_POLISH_MODE if DEFAULT_FINAL_POLISH_MODE in polish_options else polish_options[0]
            st.session_state.final_polisher_mode = st.selectbox("选择润色模式", polish_options, index=polish_options.index(st.session_state.final_polisher_mode), key="step5_final_polisher_mode")
            if st.session_state.final_polish_applied:
                st.success(f"已切换润色模式：{st.session_state.final_polisher_mode}")
            else:
                st.info("语气优化会在当前最终稿基础上进行，不会改动原始素材内容。")
            polish_col1, polish_col2 = st.columns([1.2, 1])
            with polish_col1:
                if st.button(f"使用 {FINAL_POLISH_MODEL} 执行语气优化", use_container_width=True):
                    with st.spinner("正在使用 qwen3.5-plus 进行语气优化..."):
                        raw_article = st.session_state.raw_final_article or st.session_state.final_article
                        polished_article = polish_final_article(
                            api_key=api_key,
                            base_url=current_base_url,
                            article_text=raw_article,
                            polish_prompt=get_selected_final_polisher_prompt(prompts_data, st.session_state.final_polisher_mode)
                        )
                        st.session_state.raw_final_article = raw_article
                        st.session_state.final_article = polished_article
                        record_article_version(
                            st.session_state.final_article,
                            version_type="Tone Polished",
                            source_step="Step 05",
                            note=f"Polish Mode: {st.session_state.final_polisher_mode}",
                            model_name=FINAL_POLISH_MODEL
                        )
                        if st.session_state.raw_spoken_script:
                            st.session_state.spoken_script = call_llm(
                                api_key=api_key,
                                base_url=current_base_url,
                                model_name=selected_model,
                                system_prompt=get_script_sys_prompt(script_duration),
                                user_content=f"请基于下面的最终稿，生成一份适合 {script_duration} 的短视频分镜脚本，要求口语化、镜头清晰、便于直接执行。\n\n{st.session_state.final_article}"
                            )
                        st.session_state.final_polish_applied = True
                        save_draft()
                        notify_completion(f"{st.session_state.final_polisher_mode} 润色完成", defer_until_rerun=True)
                        st.rerun()
            with polish_col2:
                restore_disabled = not st.session_state.final_polish_applied or not st.session_state.raw_final_article
                if st.button("恢复润色前版本", use_container_width=True, disabled=restore_disabled):
                    st.session_state.final_article = st.session_state.raw_final_article
                    if st.session_state.raw_spoken_script:
                        st.session_state.spoken_script = st.session_state.raw_spoken_script
                    st.session_state.final_polish_applied = False
                    save_draft()
                    st.rerun()

        st.markdown("### 最终稿预览")
        st.code(st.session_state.final_article, language="markdown")

        with st.expander(f"稿件版本记录（{len(st.session_state.article_versions)}）", expanded=False):
            if st.session_state.article_versions:
                version_indices = list(range(len(st.session_state.article_versions) - 1, -1, -1))
                selected_version_idx = st.selectbox(
                    "选择要预览的版本",
                    version_indices,
                    format_func=lambda idx: format_article_version_label(st.session_state.article_versions[idx]),
                    key="article_version_selector"
                )
                selected_version = st.session_state.article_versions[selected_version_idx]
                role_info = selected_version.get("role", "")
                model_info = selected_version.get("model", "")
                meta_parts = []
                if role_info:
                    meta_parts.append(f"角色：{role_info}")
                if model_info:
                    meta_parts.append(f"模型：{model_info}")
                if meta_parts:
                    st.caption(" | ".join(meta_parts))
                st.code(selected_version.get("content", ""), language="markdown")

                selected_version_id = selected_version.get("id", selected_version_idx + 1)
                if st.button("恢复为当前稿件", key=f"use_article_version_{selected_version_id}", use_container_width=True):
                    restored_article = selected_version.get("content", "")
                    st.session_state.final_article = restored_article
                    st.session_state.raw_final_article = restored_article
                    st.session_state.final_polish_applied = False
                    record_article_version(
                        restored_article,
                        version_type="Version Rollback",
                        source_step="Step 05",
                        note=f"Rollback to V{selected_version_id}",
                        model_name=selected_model
                    )
                    save_draft()
                    notify_completion("已恢复选中版本", defer_until_rerun=True)
                    st.rerun()
            else:
                st.caption("当前会话里还没有版本记录。")


        if st.session_state.spoken_script:
            st.divider()
            st.markdown(f"### 分镜脚本（{script_duration}）")
            st.code(st.session_state.spoken_script, language="markdown")

        st.divider()
        st.markdown("### 搜图关键词")
        st.info("可基于当前最终稿提取 10 组适合 Google 图片搜索的关键词。")

        if st.button("生成 Google 图片搜索关键词", use_container_width=True):
            with st.spinner("正在生成 Google 图片搜索关键词..."):
                keyword_prompt = (
                    "请根据以下文章内容，提取 10 个最适合在 Google 图片（Google Images）中搜索配图的精准关键词组合。\n\n"
                    "要求：\n"
                    "1. 关键词要具体，适合搜索新闻配图、产品图、人物图或场景图。\n"
                    "2. 尽量包含公司名、产品名、事件名、平台名、人物名等核心检索词。\n"
                    "3. 直接输出 10 行关键词，不要加解释。\n\n"
                    + st.session_state.final_article
                )
                st.session_state.image_keywords = call_llm(
                    api_key=api_key, 
                    base_url=current_base_url,
                    model_name=selected_model, 
                    system_prompt="你是一个擅长提取图片搜索关键词的助手。请输出适合 Google 图片搜索的精准关键词组合。",
                    user_content=keyword_prompt
                )
                save_draft()
                notify_completion("搜图关键词已生成")

        if st.session_state.image_keywords:
            st.success("已生成 Google 图片搜索关键词。")
            st.code(st.session_state.image_keywords, language="markdown")
            
        st.divider()

        def create_docx(article_text, script_text=None):
            doc = Document()
            doc.add_heading('最终稿正文', level=1)
            doc.add_paragraph(article_text)
            
            if script_text:
                doc.add_heading('分镜脚本', level=1)
                doc.add_paragraph(script_text)
            
            bio = io.BytesIO()
            doc.save(bio)
            return bio.getvalue()
            
        docx_data = create_docx(st.session_state.final_article, st.session_state.spoken_script if st.session_state.spoken_script else None)
        
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
             st.download_button(
                label="下载 Word 稿件" if not st.session_state.spoken_script else "下载 Word 稿件（含脚本）",
                data=docx_data,
                file_name="最终稿.docx" if not st.session_state.spoken_script else "最终稿_含分镜脚本.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True
            )
            
        with btn_col2:
            if st.button("推送到飞书", use_container_width=True):
                with st.spinner("正在推送到飞书..."):
                    success, msg = push_to_feishu(st.session_state.final_article, st.session_state.spoken_script if st.session_state.spoken_script else None)
                    if success:
                        st.success("已成功推送到飞书。")
                        notify_completion("飞书推送完成")
                    else:
                        st.error(f"飞书推送失败：{msg}")
                            
        with btn_col3:
            if st.button("清空当前会话", use_container_width=True):
                clear_draft()
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

    with right_col:
        with st.container(border=True):
            render_section_intro("精修助手", "继续追问事实出处、判断依据和上下文细节，也可以把某段文字交给助手直接重写。", "Assistant")
            st.markdown("<p class='toolbar-note'>如果你想核查某一句话的出处、让助手补强某段表达，或者进一步挖掘素材里的细节，这里就是最后一道编辑工作台。</p>", unsafe_allow_html=True)
            quick_col1, quick_col2 = st.columns(2)
            with quick_col1:
                st.markdown("- 可以继续追问事实出处、关键判断和上下文背景。")
                st.markdown("- 可以结合版本记录快速回滚并比较不同稿件。")
            with quick_col2:
                st.markdown("- 图片删选后再生成稿件，能减少干扰图影响。")
                st.markdown("- 抓取失败时可直接上传截图或文件继续分析。")

        with st.container(border=True):
            render_section_intro("快捷建议", "这里放的是当前工作台里最常用的两个方向：继续精修，或快速回看版本。", "Shortcuts")
            shortcut_col1, shortcut_col2 = st.columns(2)
            with shortcut_col1:
                st.caption("常用操作")
                st.markdown("- 继续追问事实出处与关键判断。")
                st.markdown("- 快速回滚历史版本，比较不同稿件。")
            with shortcut_col2:
                st.caption("使用建议")
                st.markdown("- 图片删选后再生成稿件，能减少干扰。")
                st.markdown("- 抓取失败时可直接上传截图或文件继续分析。")

        with st.container(border=True):
            render_section_intro("精修对话", "你可以在这里继续追问出处、要求补充论据，或把某一段交给助手直接重写。", "Chat")
            chat_container = st.container(height=500)

            with chat_container:
                for msg in st.session_state.chat_history:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

            if user_query := st.chat_input("例如：这段判断的出处在哪？帮我把导语改得更利落一些。"):
                st.session_state.chat_history.append({"role": "user", "content": user_query})
                with chat_container:
                    with st.chat_message("user"):
                        st.markdown(user_query)

                    with st.chat_message("assistant"):
                        with st.spinner("正在生成精修建议..."):
                            chat_sys_prompt = (
                                "你是一个极其专业的文章精修与溯源助手。\n\n"
                                "【你的参考资料库（唯一真相来源）】\n"
                                f"{st.session_state.source_content}\n\n"
                                "【当前正在精修的定稿文章】\n"
                                f"{st.session_state.final_article}\n\n"
                                "【你的任务】\n"
                                "1. 如果用户要求溯源，请尽量定位到参考资料库中的原文片段并客观回答。\n"
                                "2. 如果用户要求重写某段，请直接给出可无缝替换的修改后段落。\n"
                                "3. 如果用户提出衍生问题，请基于已有素材给出专业回答，不得虚构来源。"
                            )
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

















































