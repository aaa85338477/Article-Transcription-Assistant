import hashlib
import io
import os
import shutil
import subprocess
import time
from datetime import datetime

import requests

from podcast_script import normalize_podcast_segments

try:
    import dashscope
except Exception:
    dashscope = None

try:
    from dashscope.audio.qwen_tts import SpeechSynthesizer as QwenSpeechSynthesizer
except Exception:
    QwenSpeechSynthesizer = None

try:
    from pydub import AudioSegment
except Exception:
    AudioSegment = None

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None


DEFAULT_TTS_MODEL = "qwen3-tts-flash"
DEFAULT_TTS_VOICE = "Ethan"
VOICE_LABELS = {
    "Ethan": "晨煦 Ethan（阳光温暖男）",
    "Cherry": "芊悦 Cherry（亲切自然女）",
    "Serena": "苏瑶 Serena（温柔稳定女）",
    "Chelsie": "千雪 Chelsie（二次元女）",
    "Dylan": "Dylan（北京话男）",
    "Jada": "Jada（吴语女）",
    "Sunny": "Sunny（四川话女）",
}


class PodcastAudioError(RuntimeError):
    pass


def build_tts_cache_key(text, voice, model=DEFAULT_TTS_MODEL):
    payload = f"{model}|{voice}|{(text or '').strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def ensure_ffmpeg_available():
    if AudioSegment is None:
        return None

    current_converter = getattr(AudioSegment, "converter", "")
    if current_converter and os.path.exists(current_converter):
        return current_converter

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path and imageio_ffmpeg is not None:
        try:
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_path = None

    if ffmpeg_path and os.path.exists(ffmpeg_path):
        AudioSegment.converter = ffmpeg_path
        AudioSegment.ffmpeg = ffmpeg_path
        ffprobe_path = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
        if os.path.exists(ffprobe_path):
            AudioSegment.ffprobe = ffprobe_path
        return ffmpeg_path

    return None


def ensure_audio_dependencies():
    if dashscope is None:
        raise PodcastAudioError("缺少 dashscope 依赖，无法进行播客语音合成。")
    if QwenSpeechSynthesizer is None and not hasattr(dashscope, "MultiModalConversation"):
        raise PodcastAudioError("当前 dashscope SDK 不支持 Qwen-TTS。")
    if AudioSegment is None:
        raise PodcastAudioError("缺少 pydub 依赖，无法拼接播客音频。")
    if not ensure_ffmpeg_available():
        raise PodcastAudioError("缺少 ffmpeg 运行环境，无法导出播客音频。")


def _extract_response_field(node, key, default=None):
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


def format_qwen_tts_failure(response_payload):
    status_code = _extract_response_field(response_payload, "status_code", None)
    code = _extract_response_field(response_payload, "code", "")
    message = _extract_response_field(response_payload, "message", "")
    request_id = _extract_response_field(response_payload, "request_id", "")

    output = _extract_response_field(response_payload, "output", {})
    audio = _extract_response_field(output, "audio", {}) if output else {}
    audio_url = _extract_response_field(audio, "url", "") if audio else ""

    parts = []
    if status_code not in (None, ""):
        parts.append(f"status_code={status_code}")
    if code:
        parts.append(f"code={code}")
    if request_id:
        parts.append(f"request_id={request_id}")
    if message:
        parts.append(f"message={message}")
    if audio_url:
        parts.append(f"audio_url={audio_url}")
    return ", ".join(parts)


def extract_audio_url(response_payload):
    output = _extract_response_field(response_payload, "output", {})
    if not output:
        return ""
    audio = _extract_response_field(output, "audio", {})
    if not audio:
        return ""
    return str(_extract_response_field(audio, "url", "") or "").strip()


def default_qwen_tts_call(text, voice, api_key, model):
    multimodal_conversation = getattr(dashscope, "MultiModalConversation", None) if dashscope is not None else None
    if multimodal_conversation is not None:
        return multimodal_conversation.call(
            model=model,
            api_key=api_key,
            text=text,
            voice=voice,
        )
    if QwenSpeechSynthesizer is not None:
        return QwenSpeechSynthesizer.call(
            model=model,
            api_key=api_key,
            text=text,
            voice=voice,
            stream=False,
        )
    raise PodcastAudioError("当前 dashscope SDK 不支持 Qwen-TTS。")


def download_audio_bytes(audio_url, requests_get=None):
    if not audio_url:
        raise PodcastAudioError("Qwen-TTS 未返回音频下载地址。")
    getter = requests_get or requests.get
    response = getter(audio_url, timeout=60)
    response.raise_for_status()
    audio_bytes = response.content
    if not audio_bytes:
        raise PodcastAudioError("下载到的音频数据为空。")
    return audio_bytes


def synthesize_segment(
    text,
    voice,
    api_key,
    cache_dir,
    model=DEFAULT_TTS_MODEL,
    retries=2,
    synthesizer_callable=None,
    requests_get=None,
):
    ensure_audio_dependencies()
    clean_text = (text or "").strip()
    if not clean_text:
        raise PodcastAudioError("待合成文本不能为空。")
    if len(clean_text) > 600:
        raise PodcastAudioError("单段播客文本超过 600 字符，建议进一步切分。")
    if not api_key:
        raise PodcastAudioError("未提供 DashScope API Key。")

    os.makedirs(cache_dir, exist_ok=True)
    cache_key = build_tts_cache_key(clean_text, voice, model=model)
    cache_path = os.path.join(cache_dir, f"{cache_key}.wav")
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as cached_file:
            return cached_file.read(), True, cache_key

    caller = synthesizer_callable or default_qwen_tts_call
    last_error = None
    for attempt in range(retries + 1):
        try:
            response_payload = caller(
                text=clean_text,
                voice=voice,
                api_key=api_key,
                model=model,
            )
            status_code = _extract_response_field(response_payload, "status_code", 200)
            if status_code not in (200, "200", None):
                raise PodcastAudioError(
                    f"Qwen-TTS 请求失败: {format_qwen_tts_failure(response_payload) or status_code}"
                )

            audio_url = extract_audio_url(response_payload)
            if not audio_url:
                raise PodcastAudioError(
                    f"Qwen-TTS 未返回音频下载地址: {format_qwen_tts_failure(response_payload) or 'unknown error'}"
                )

            audio_bytes = download_audio_bytes(audio_url, requests_get=requests_get)
            with open(cache_path, "wb") as cache_file:
                cache_file.write(audio_bytes)
            return audio_bytes, False, cache_key
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(0.25 * (2 ** attempt), 1.0))

    raise PodcastAudioError(f"语音片段生成失败: {last_error}") from last_error


def normalize_segment_volume(segment, target_dbfs=-18.0):
    current_dbfs = getattr(segment, "dBFS", float("-inf"))
    if current_dbfs == float("-inf"):
        return segment
    return segment.apply_gain(target_dbfs - current_dbfs)


def synthesize_podcast(
    segments,
    voice,
    api_key,
    output_dir,
    cache_dir,
    model=DEFAULT_TTS_MODEL,
    pause_ms=350,
    retries=2,
    synthesizer_callable=None,
    requests_get=None,
):
    ensure_audio_dependencies()
    normalized_segments = normalize_podcast_segments(segments)

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    combined = AudioSegment.silent(duration=0)
    pause = AudioSegment.silent(duration=pause_ms)
    cache_hits = 0
    segment_hashes = []

    for index, item in enumerate(normalized_segments, start=1):
        text = item["text"]
        try:
            audio_bytes, cache_hit, cache_key = synthesize_segment(
                text=text,
                voice=voice,
                api_key=api_key,
                cache_dir=cache_dir,
                model=model,
                retries=retries,
                synthesizer_callable=synthesizer_callable,
                requests_get=requests_get,
            )
        except Exception as exc:
            preview = text[:30]
            raise PodcastAudioError(f"第 {index} 段语音合成失败（{preview}）: {exc}") from exc

        if cache_hit:
            cache_hits += 1
        segment_hashes.append(cache_key)

        segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")
        segment = normalize_segment_volume(segment)
        combined += segment + pause

    fingerprint_source = "|".join(segment_hashes)
    filename = f"podcast_{hashlib.sha1(fingerprint_source.encode('utf-8')).hexdigest()[:16]}.mp3"
    output_path = os.path.join(output_dir, filename)
    combined.export(output_path, format="mp3")

    manifest = {
        "provider": "DashScope Qwen-TTS",
        "model": model,
        "voice": voice,
        "segment_count": len(normalized_segments),
        "cache_hits": cache_hits,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "duration_ms": len(combined),
        "output_path": output_path,
    }
    return output_path, manifest
