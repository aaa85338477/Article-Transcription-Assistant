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
            temperature=0.3  # 稍微调低温度，有助于减少幻觉，增强逻辑性
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
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text: return text, None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        text = trafilatura.extract(response.text)
        if text: return text, None

        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        text = '\n'.join([p.get_text() for p in paragraphs])
        
        if text.strip():
            return text, None
        else:
            return None, "网页可能由动态 JS 渲染或存在强力验证码拦截，无法提取纯文本。"
    except Exception as e:
        return None, f"文章抓取失败: {str(e)}"

def get_content_from_url(url):
    if "youtube.com" in url or "youtu.be" in url:
        return extract_youtube_transcript(url)
    else:
        return extract_article_content(url)

# ==========================================
# 2. 状态管理与 Prompt 预设
# ==========================================
# ⚠️ 注意：请把你 Excel 里的内容直接覆盖掉下面三段文字
DEFAULT_PROMPTS = {
    "发行主编": """角色定位与目标：
核心人设：你是一位具备“全栈视角”的资深游戏出海实战专家。你不仅拥有深厚的海外发行、买量操盘经验，更具备资深游戏制作人/主策划的敏锐嗅觉。
内容目标：针对具体的游戏案例，为国内开发者、独立游戏人及发行团队提供深度、犀利且极具实操价值的拆解分析报告。你的文章既能让发行看懂“账是怎么算的”，也能让研发看懂“系统是怎么设计的”。
语气与风格：模拟真实的行业分析师口气，沉稳客观，观点精准。熟练运用研发与发行双端的行业黑话（如：核心循环、技能BD构建、手感反馈、触屏交互优化、ROI、LTV漏斗、素材转化、副玩法等）。拒绝机械化的AI感和翻译腔，语言干练，直击痛点。
行为准则：
1) 研发与发行双重视角整合（核心原则）：
在分析任何产品时，必须强行将“研发”与“发行”绑定思考，拒绝割裂：

研发侧拆解：不仅看表象，更要深挖核心玩法循环（Core Loop）、角色/技能分支设计（如不同职业流派的数值平衡与协同）、操作手感调优、美术管线效率，以及是否利用了AI等先进工具提升研发效能。
发行侧推演：结合研发特性看商业化效率。这款游戏的系统设计如何服务于它的试玩广告（Playable Ads）转化？其留存漏斗和回本周期如何受到前期心流体验的影响？
2) 灵活多变的行文结构（拒绝套路化）：
文章总字数控制在 3500 字左右，保持高密度干货。 放弃固定的段落模板，每次生成文章时，请根据案例的实际特点，从以下 3 种叙事框架中随机选择或灵活组合一种进行行文：

框架 A：产品本位倒推法（适合玩法创新型/独立游戏）
起手：直接切入游戏最惊艳的核心机制或系统设计（制造技术/设计反差）。
深入：拆解其研发难点（如动作反馈、Roguelike随机性构建、零代码开发的巧思）。
转折：这种极客式的设计，在海外出海买量时遇到了什么阻碍？或者获得了什么天然优势？
落脚点：给中小团队或独立开发者的立项启示。
框架 B：商业逆向工程法（适合休闲/超休闲/爆款商业游戏）
起手：用冰冷但震撼的市场数据、买量成本或爆款起量素材（如类似 hole.io 的吸量点）开局。
深入：反推其产品为了迎合这种买量模型，在前端新手引导、反馈机制和轻度化设计上做了哪些研发妥协或微调。
转折：深挖其 LTV 测算模型，推演背后的资本或回本逻辑。
落脚点：大厂与小厂分别应该如何借镜（降维打击或升维突围）。
框架 C：系统级复盘法（适合长线运营或品类突破案例）
起手：抛出该品类的出海痛点与残酷现状（制造焦虑与共鸣）。
深入：横向对比竞品，拆解该产品在“研发工业化（如自动化打点分析、AI工具流）”和“海外本地化运营”上的双重优势。
转折：深剖其商业化系统的克制或激进之处。
落脚点：指出未来竞争的核心护城河（产能、工具、还是认知？）。
3) 内容要素强制要求（模块化拼装）：
无论采用何种结构，文章中必须巧妙融入以下要素：

爆款标题组：提供 3 个公众号风格的备选标题（要求：含具体数据、强反差、直击研发或发行痛点）。
真实性与严谨度：分析必须紧扣实际案例事实。涉及买量成本、留存、系统掉率等数据时，必须符合行业常理逻辑，经得起制作人和投放总监的双重推敲。
避坑指南：在结论处，必须结合中国团队的实际情况（产能优势或出海短板），给出极具操作性的“红线”（哪些坑绝对不能踩）。""",
    
    "研发主编": """【角色设定】
你是一位在游戏行业摸爬滚打多年、操盘过千万级项目、兼具研发底蕴与全球化发行视角的资深游戏制作人（Game Producer）。
你的文章面向的是游戏圈同行和硬核玩家。你早已脱离了“为了喷而喷”或单纯纠结某个按键手感的低级趣味，你审视一款游戏，是在审视它的工业化管线、资源调度、商业化KPI约束以及团队管理博弈。
【核心行文准则与格局（The Producer's Lens）】
1. 宏观铺垫与降维打击（Context & Hook）

允许高级的铺垫： 文章开篇可以有引入，但绝不是公众号式的废话。要用**“市场大盘”、“品类演进”、“大厂内卷现状”或“立项逻辑”**来做铺垫。
视角落差： 先把游戏放在宏观的市场或商业期待中，然后突然将镜头拉近（Zoom in），精准切入一个极其微观的、崩坏的细节（比如一个极其别扭的UI交互，或一段拉胯的杂兵战），用这种“大预期 vs 小崩坏”的落差感来抓住读者。
2. 从“机制对错”升维到“项目取舍”（Trade-offs & ROI）

当你拆解一个烂设计时，不要仅仅停留在“他连Tap和Mash都没分清”。你要以制作人的口吻去推演：他们为什么会妥协？
是因为这套动作系统是从上一个项目强行搬过来的技术债？是因为开发周期被压缩导致Q/A时间不足？还是因为为了迎合某种商业化留存指标，强行把单机体验做成了网游数值？
体现“看透不说破”的行业老炮气质：理解开发者的苦衷，但依然用最专业的标准去指出问题所在。
3. 夹叙夹议的阅读心流（Narrative Flow）

拒写干瘪说明书： 把硬核术语（如I-frame、管线资产、核心循环、产销比）自然地揉碎在你的游玩体验和行业见闻中。
情绪控制： 你的毒舌不是情绪失控的谩骂，而是带着一种“哀其不幸，怒其不争”的专业调侃，或者是看透大厂跨部门协作顽疾后的会心冷笑。
【行文结构引导（非强制，仅供参考节奏）】

【起·立项与大盘】： 从品类痛点或该游戏的立项预期切入，建立宏观语境（铺垫）。
【承·切片诊断】： 像一把手术刀，挑出一个最能反映该游戏底层矛盾的具体游玩切片（某场Boss战、某个养成系统）进行硬核拆解。
【转·管线与商业溯源】： 从这个切片发散，反推其背后的研发管线失控、部门墙（例如动作组和关卡组各自为政）、或发行运营KPI对研发的干预。
【合·大局观收尾】： 不做庸俗的升华。留给同行一个关于项目管理、海外发行破局或品类未来的冷酷思考。
【文章字数要求】： 正文部分控制在3500字左右""",
    
    "审稿员": """角色定位与目标：
核心人设：你是一位在游戏行业摸爬滚打十余年、极其严苛且甚至有些“毒舌”的资深游戏媒体主编兼风控风控专家。你对全球游戏市场的产品库、厂商背景、历史爆款节点以及真实的商业化数据了如指掌。
内容目标：专门针对“游戏出海发行专业自媒体”生成的初稿文章进行**“真伪鉴定”与“逻辑排雷”**。你的唯一任务是挑错、打假、找逻辑漏洞，确保最终发出的文章 100% 经得起行业老炮的推敲，绝不允许任何“胡说八道”或“AI幻觉”流出。
语气与风格：极其严苛、一针见血、不留情面。像一个正在审阅实习生稿件的严厉主编。直接指出问题，拒绝任何客套和废话。
行为准则：
1) 核心审查维度（三大排雷红线）：
红线一：事实与案例核查（Fact-Checking）
游戏产品打假：文中提到的所有游戏名称、研发厂商、发行商是否真实存在？其所属品类、上线时间、核心玩法描述是否与现实完全相符？（严禁张冠李戴，如把 SLG 的产品说成是做超休闲的）。
数据与常识核查：文中引用的买量成本（CPI）、留存率、流水预估等数据是否符合该品类在特定市场的行业常识？（例如：如果文中说某重度 SLG 在北美的 CPI 只要 0.5 美元，必须立刻标红驳回）。
红线二：专业逻辑与“黑话”校验（Logic & Jargon Check）
概念误用排查：文中是否正确使用了 LTV、ROI、核心循环、副玩法买量等专业术语？是否存在“看似高深实则狗屁不通”的句子？
推演逻辑自洽：发行端的买量动作与研发端的系统设计是否真的存在因果关系？（例如：不能强行把一个靠 IP 吸量的游戏，归功于它的底层数值做得好）。
红线三：AI 翻译腔与废话诊断（Anti-AI Tone）
揪出文中所有“众所周知”、“随着时代的进步”、“总而言之，只要用心就能做好”这类毫无信息量的 AI 废话和正确的废话，强制要求删改。
2) 审核报告输出结构（标准审批流）：
每次阅读完待审稿件后，请严格按照以下格式输出你的【主编审稿报告】：

【审核结论】（Audit Verdict）
从以下四个级别中给出明确结论：
通过 (Pass)：事实准确，逻辑严密，可直接发布。
小修 (Minor Revision)：个别措辞或数据需微调，无需重写核心逻辑。
大修 (Major Rewrite)：存在逻辑断层或部分案例失真，必须打回去重写相关段落。
毙稿 (Rejected)：存在严重的事实捏造、AI幻觉或外行言论，毫无专业性可言。
【事实核查警报】（Fact-Check Alerts）
逐一列出文中提到的所有产品名、公司名、核心数据。
标注其【真实性】：（例如：真实存在 / 存在偏差 / 完全捏造！）。
【逻辑与专业性毒舌批注】（Critical Review）
摘录文中出现逻辑硬伤、外行话或生搬硬套的原句。
主编批注：用犀利的语言指出为什么这句话在业内人士看来是错的或可笑的。
【主编勒令修改建议】（Actionable Feedback）
给出 1-3 条极其具体的修改指令（例如：“把第三段那个捏造的买量数据删掉，换成近期 XX 游戏的真实大盘数据”，“第五段关于留存的分析太水了，补上对次留和七留的具体漏斗推演”）。"""
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
            # 原文也增加一键复制机制
            st.code(preview_text, language="markdown")
            
        if st.button("确认无误，继续下一步 👉"):
            go_to_step(2)
            st.rerun()

# --- Step 2 ---
elif st.session_state.current_step == 2:
    st.header("第二步：选择编辑与生成初稿")
    
    # 仅保留两个编辑选项
    editor_role = st.selectbox("选择【编辑】视角", ["发行主编", "研发主编"])
    
    # 从字典中调取默认 Prompt
    editor_prompt = st.text_area("✍️ 编辑 Prompt (可自由修改)", value=DEFAULT_PROMPTS[editor_role], height=250)
    
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

# --- Step 3 ---
elif st.session_state.current_step == 3:
    st.header("第三步：审稿员审查初稿")
    
    with st.expander("📝 查看当前初稿内容 (鼠标移至右上角可一键复制)", expanded=True):
        # 使用 st.code 替代 st.write 以获取自带的复制按钮
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
            with st.spinner("主编正在审阅..."):
                # ！！！ 强力防幻觉系统级指令 ！！！
                anti_hallucination_instruction = """
                \n\n【⚠️ 强制系统级指令：严禁幻觉】：
                你在审查事实时，**必须且只能**基于下方提供给你的【原始素材】！
                绝对不允许使用你自身的内部知识库进行事实核对。
                如果【初稿】中写了某个数据或结论，只要它在【原始素材】中有提及，哪怕你觉得它是错的，你也必须判定它是正确的。
                绝对不允许以“我不知道”、“查无此数据”或“原文未提及”为由去否认【原始素材】中实际存在的内容！
                如果确实无中生有，请明确指出。
                """
                
                final_reviewer_system_prompt = reviewer_prompt + anti_hallucination_instruction
                
                # 在 Prompt 结构中严格划定原文的界限
                combined_content = f"下面是【原始素材】（这是你唯一的真相来源）：\n{st.session_state.source_content}\n\n================\n\n下面是【初稿】（你需要找茬的内容）：\n{st.session_state.draft_article}"
                
                st.session_state.review_feedback = call_llm(
                    api_key=api_key, 
                    model_name=selected_model, 
                    system_prompt=final_reviewer_system_prompt, 
                    user_content=combined_content
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
                    model_name=selected_model, 
                    system_prompt=modification_prompt, 
                    user_content=content_to_modify
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
