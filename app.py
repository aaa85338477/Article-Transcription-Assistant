import os
import streamlit as st

from article_localizer import generate_localized_text

st.set_page_config(page_title="Game Ops Article Localizer", page_icon="🎮", layout="wide")

st.title("海外游戏文章 · 本地化转译助手")
st.write("输入英文文章链接，一键生成中国手游行业口吻的公众号稿件。")

url = st.text_input("文章 URL", placeholder="https://example.com/article")

with st.sidebar:
    st.header("生成设置")
    default_model = os.getenv("OPENAI_MODEL", "gpt-4o")
    model = st.text_input("模型名", value=default_model)
    temperature = st.slider("Temperature", 0.0, 1.0, 0.6, 0.05)
    max_tokens = st.slider("Max tokens", 600, 2400, 1800, 100)
    base_url = st.text_input("自定义 Base URL (可选)", placeholder="https://api.openai.com/v1")
    api_key = st.text_input("API Key (留空则用环境变量)", type="password")
    st.caption("提示：可在部署平台上通过环境变量设置 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL。")

run = st.button("生成转译稿", type="primary")

if run:
    if not url.strip():
        st.error("请先输入文章 URL")
    else:
        with st.spinner("AI 正在生成公众号稿件，请稍候..."):
            try:
                output = generate_localized_text(
                    url=url.strip(),
                    model=model.strip() or "gpt-4o",
                    max_tokens=max_tokens,
                    temperature=temperature,
                    api_key=api_key or None,
                    base_url=base_url.strip() or None,
                )
                st.success("生成完成，可直接复制到公众号后台。")
                st.markdown(output)
            except Exception as e:
                st.error(f"生成失败：{e}")

st.markdown("---")
st.markdown("**使用说明**：左侧可调整模型、temperature 和 tokens；如需自建网关请填写 Base URL；未填 API Key 时优先读取环境变量 OPENAI_API_KEY。")
