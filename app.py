import os
import streamlit as st

from article_localizer import generate_localized_text

st.set_page_config(page_title="Game Ops Article Localizer", page_icon="🎮", layout="wide")

st.title("海外游戏文章 · 本地化转译助手")
st.write("输入英文文章链接，一键生成中国手游行业口吻的公众号稿件。可选：附带 YouTube 视频自动章节总结作为参考。")

url = st.text_input("文章 URL", placeholder="https://example.com/article")
yt_url = st.text_input("YouTube 视频 URL (可选)", placeholder="https://www.youtube.com/watch?v=...")

with st.sidebar:
    st.header("生成设置")
    default_model = os.getenv("OPENAI_MODEL", "gemini-3.1-flash-lite-preview")
    model = st.text_input("模型名", value=default_model)
    temperature = st.slider("Temperature", 0.0, 1.0, 0.6, 0.05)
    max_tokens = st.slider("Max tokens", 600, 5000, 1800, 100)
    api_url = st.text_input(
        "中转 API URL (优先级最高)",
        value=os.getenv("AIAPI_URL", "https://api.bltcy.ai/v1/chat/completions"),
    )
    base_url = st.text_input(
        "OpenAI base_url (填根路径, 不要带 /chat/completions)",
        value=os.getenv("OPENAI_BASE_URL", ""),
        placeholder="https://api.openai.com/v1",
    )
    api_key = st.text_input("API Key (留空则用环境变量)", type="password")
    st.caption("优先使用中转 API URL；为空则走 base_url；都空则默认官方。base_url 只填根路径，如 https://api.bltcy.ai/v1。API Key 优先使用输入，其次环境变量 OPENAI_API_KEY。")

run = st.button("生成转译稿", type="primary")

if run:
    if not url.strip():
        st.error("请先输入文章 URL")
    else:
        with st.spinner("AI 正在生成公众号稿件，请稍候..."):
            try:
                api_url_final = api_url.strip() or None
                base_url_final = base_url.strip() or None
                yt_url_final = yt_url.strip() or None
                output = generate_localized_text(
                    url=url.strip(),
                    model=model.strip() or "gemini-3.1-flash-lite-preview",
                    max_tokens=max_tokens,
                    temperature=temperature,
                    api_key=api_key or None,
                    base_url=base_url_final,
                    api_url=api_url_final,
                    yt_url=yt_url_final,
                )
                st.success("生成完成，可直接复制到公众号后台。")
                st.markdown(output)
            except Exception as e:
                st.error(f"生成失败：{e}")

st.markdown("---")
st.markdown(
    "**使用说明**：填写中转 API URL 时优先走中转（已默认填入示例 https://api.bltcy.ai/v1/chat/completions）；未填则使用 base_url/官方。可通过环境变量设置 AIAPI_URL、OPENAI_BASE_URL、OPENAI_API_KEY。YouTube URL 可选，会先生成视频章节总结作为文章参考。"
)

