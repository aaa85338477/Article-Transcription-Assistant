import json
import re


PODCAST_DURATION_WORD_RANGE = {
    "3分钟": "700 - 900",
    "5分钟": "1200 - 1500",
    "8分钟": "1800 - 2200",
    "12分钟": "2600 - 3200",
}

PODCAST_CORE_PROMPT = """你是一位资深的中文播客编导。你的任务是：读取我随后提供的一篇深度文章，提取其中最重要的信息，并改写成适合单人播客解说的结构化脚本。

【唯一输出要求】
你只能输出一个合法、完整、可直接被 JSON 解析器读取的 JSON 数组。
除了这个 JSON 数组本身，不允许输出任何其他内容。
严禁输出：
- Markdown 标记
- ```json 或 ```
- 标题
- 前言后语
- 注释
- 解释文字
- 旁白说明
- 舞台提示
- 过渡提示

【JSON 结构要求】
JSON 数组中的每个元素都必须是对象，且只能包含以下一个字段：
- "text": 主播要直接朗读的具体台词

额外要求：
1. 禁止输出任何额外字段。
2. 禁止输出尾逗号。
3. 每个对象都必须完整闭合。
4. "text" 必须是合法 JSON 字符串。
5. 如果台词中需要出现双引号，必须正确转义，确保整个 JSON 可被程序直接解析。

【主播人设】
- 主播定位：资深行业观察者。
- 语气要求：专业、自然、清楚、有判断，但不要端着。
- 表达任务：把原文里的关键信息，转写成适合单人口播的讲述方式，让听众像在听一位懂行的人做清晰解读。

【结构要求】
1. 整体必须是连续的单人解说，不要写成问答，不要写成双人对谈。
2. 每段都要自然承接上一段，形成完整口播节奏。
3. 每段 "text" 建议控制在 30 到 120 个汉字之间。
4. 不要让单条台词长到像整段文章，也不要短到只剩一句空话。
5. 分段要服务于口播节奏和信息推进，不要机械拆句。

【口语化与 TTS 友好要求】
1. 台词必须是适合直接朗读的中文口语，不要像书面文章。
2. 句子要有呼吸感。优先用短句，复杂信息拆开说。
3. 多使用自然停顿，适当使用逗号和句号，减少过长整句。
4. 英文缩写或行业术语前后要留出自然语境，避免整句生硬。
5. 每段都要尽量一口气讲清一个小点，但不要密得发闷。

【改写原则】
1. 不得直接照抄原文句子。
2. 必须忠于原文事实、判断和信息边界。
3. 允许把书面表达改写成更自然的口头表达。
4. 要把“文章里的信息”变成“主播嘴里的人话”。
5. 不要编造原文没有提供的新事实、新数据、新案例、新观点。

【质量要求】
1. 每段台词都要有信息量，避免空泛寒暄。
2. 不要频繁重复同一个观点。
3. 不要使用旁白口吻。
4. 不要写“接下来我们聊下一部分”这类生硬过渡。
5. 要让整篇稿子适合被一个主播连续读出来，而不是像提纲。

【输出示例】
[
  {"text": "今天想聊一个挺有意思的现象。很多产品在外层用休闲玩法吸量，里面却塞了重度留存结构。"},
  {"text": "这套模式为什么还在被反复使用？核心原因其实不复杂。前端副玩法确实能把点击和转化先拉起来。"},
  {"text": "但真正决定这门生意能不能成立的，还是后面的重度系统能不能接住用户，能不能把留存和付费做起来。"}
]

请在我发送文章后，直接输出 JSON 数组。"""


def get_podcast_target_words(duration_str):
    return PODCAST_DURATION_WORD_RANGE.get(duration_str, PODCAST_DURATION_WORD_RANGE["5分钟"])


def get_podcast_core_prompt():
    return PODCAST_CORE_PROMPT


def get_podcast_length_control_prompt(duration_str):
    target_words = get_podcast_target_words(duration_str)
    return "\n".join(
        [
            "【长度控制】",
            f"1. 本次目标时长：{duration_str}",
            f"2. 总字数控制在 {target_words} 字左右。",
            "3. 总体保持信息密度稳定，不要为了拉长时长而重复观点。",
            "4. 在满足时长的前提下，优先保证口播顺畅、结构清晰和听感自然。",
        ]
    )


def get_podcast_sys_prompt(duration_str):
    return "\n\n".join([get_podcast_core_prompt(), get_podcast_length_control_prompt(duration_str)])


def extract_json_array_text(raw_text):
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("播客脚本为空")

    fenced_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text, flags=re.IGNORECASE)
    if fenced_match:
        return fenced_match.group(1).strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("未找到合法的 JSON 数组")
    return text[start : end + 1].strip()


def try_repair_json_array_text(json_text):
    repaired = (json_text or "").strip()
    if not repaired:
        return repaired

    repaired = repaired.replace("\ufeff", "")
    repaired = re.sub(r",(\s*[\]}])", r"\1", repaired)
    repaired = re.sub(r"([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', repaired)
    repaired = re.sub(
        r":\s*'([^'\\]*(?:\\.[^'\\]*)*)'",
        lambda m: ': "' + m.group(1).replace('"', '\\"') + '"',
        repaired,
    )
    repaired = re.sub(r'"\s*"\s*:', '", "', repaired)
    repaired = re.sub(r"\}\s*\{", "}, {", repaired)
    repaired = re.sub(r'"\s*\n\s*"', '",\n"', repaired)
    return repaired


def normalize_podcast_segments(data):
    if not isinstance(data, list) or not data:
        raise ValueError("播客脚本必须是非空数组")

    normalized = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 段不是对象")
        text = str(item.get("text", "")).strip()
        if not text:
            raise ValueError(f"第 {index} 段的 text 不能为空")
        normalized.append({"text": text})

    return normalized


def parse_podcast_script_segments(raw_text):
    json_text = extract_json_array_text(raw_text)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        repaired_json_text = try_repair_json_array_text(json_text)
        if repaired_json_text != json_text:
            try:
                data = json.loads(repaired_json_text)
            except json.JSONDecodeError:
                raise ValueError(f"播客脚本 JSON 解析失败: {exc}") from exc
        else:
            raise ValueError(f"播客脚本 JSON 解析失败: {exc}") from exc

    return normalize_podcast_segments(data)
