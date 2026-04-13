import io
import types
import unittest
from unittest import mock

import podcast_audio


class FakeAudioSegment:
    def __init__(self, duration=100, dbfs=-20.0):
        self.duration = duration
        self.dBFS = dbfs

    @classmethod
    def silent(cls, duration=0):
        return cls(duration=duration, dbfs=float("-inf"))

    @classmethod
    def from_file(cls, file_obj, format="wav"):
        return cls(duration=120, dbfs=-20.0)

    def apply_gain(self, gain):
        new_dbfs = self.dBFS if self.dBFS == float("-inf") else self.dBFS + gain
        return FakeAudioSegment(duration=self.duration, dbfs=new_dbfs)

    def export(self, path, format="mp3"):
        return path

    def __add__(self, other):
        return FakeAudioSegment(duration=self.duration + other.duration, dbfs=self.dBFS)

    def __len__(self):
        return self.duration


class MemoryFile(io.BytesIO):
    def __init__(self, storage, path, mode):
        self._storage = storage
        self._path = path
        self._mode = mode
        initial = storage.get(path, b"") if "r" in mode else b""
        super().__init__(initial)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if "w" in self._mode:
            self._storage[self._path] = self.getvalue()
        self.close()
        return False


class FakeHttpResponse:
    def __init__(self, content=b"wav-bytes"):
        self.content = content

    def raise_for_status(self):
        return None


class PodcastAudioTests(unittest.TestCase):
    def setUp(self):
        self.original_dashscope = podcast_audio.dashscope
        self.original_qwen_synth = podcast_audio.QwenSpeechSynthesizer
        self.original_audio_segment = podcast_audio.AudioSegment
        podcast_audio.dashscope = types.SimpleNamespace(MultiModalConversation=object())
        podcast_audio.QwenSpeechSynthesizer = object
        podcast_audio.AudioSegment = FakeAudioSegment

    def tearDown(self):
        podcast_audio.dashscope = self.original_dashscope
        podcast_audio.QwenSpeechSynthesizer = self.original_qwen_synth
        podcast_audio.AudioSegment = self.original_audio_segment

    def test_build_tts_cache_key_changes_with_voice(self):
        first = podcast_audio.build_tts_cache_key("hello", "voice-a")
        second = podcast_audio.build_tts_cache_key("hello", "voice-b")
        self.assertNotEqual(first, second)

    def test_extract_audio_url(self):
        payload = {"status_code": 200, "output": {"audio": {"url": "https://example.com/a.wav"}}}
        self.assertEqual(podcast_audio.extract_audio_url(payload), "https://example.com/a.wav")

    def test_synthesize_segment_uses_cache_after_first_call(self):
        calls = []
        downloads = []
        storage = {}

        def fake_caller(**kwargs):
            calls.append(kwargs)
            return {"status_code": 200, "output": {"audio": {"url": "https://example.com/a.wav"}}}

        def fake_get(url, timeout=60):
            downloads.append((url, timeout))
            return FakeHttpResponse(content=b"wav-bytes")

        def fake_exists(path):
            return path in storage

        def fake_open(path, mode="rb"):
            return MemoryFile(storage, path, mode)

        with mock.patch.object(podcast_audio, "ensure_audio_dependencies"), \
             mock.patch("podcast_audio.os.makedirs"), \
             mock.patch("podcast_audio.os.path.exists", side_effect=fake_exists), \
             mock.patch("builtins.open", side_effect=fake_open):
            first = podcast_audio.synthesize_segment(
                text="hello",
                voice="Ethan",
                api_key="token",
                cache_dir="cache-dir",
                synthesizer_callable=fake_caller,
                requests_get=fake_get,
            )
            second = podcast_audio.synthesize_segment(
                text="hello",
                voice="Ethan",
                api_key="token",
                cache_dir="cache-dir",
                synthesizer_callable=fake_caller,
                requests_get=fake_get,
            )

        self.assertFalse(first[1])
        self.assertTrue(second[1])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(downloads), 1)

    def test_synthesize_podcast_uses_single_voice_manifest(self):
        def fake_synthesize_segment(**kwargs):
            return b"wav-bytes", True, "abc123"

        with mock.patch.object(podcast_audio, "ensure_audio_dependencies"), \
             mock.patch.object(podcast_audio, "synthesize_segment", side_effect=fake_synthesize_segment), \
             mock.patch("podcast_audio.os.makedirs"):
            output_path, manifest = podcast_audio.synthesize_podcast(
                segments=[
                    {"text": "第一段"},
                    {"speaker": "A", "text": "第二段"},
                ],
                voice="Ethan",
                api_key="token",
                output_dir="output-dir",
                cache_dir="cache-dir",
            )

        self.assertTrue(output_path.endswith(".mp3"))
        self.assertEqual(manifest["segment_count"], 2)
        self.assertEqual(manifest["voice"], "Ethan")

    def test_synthesize_podcast_surfaces_failed_segment(self):
        with mock.patch.object(podcast_audio, "ensure_audio_dependencies"), \
             mock.patch.object(podcast_audio, "synthesize_segment", side_effect=podcast_audio.PodcastAudioError("boom")), \
             mock.patch("podcast_audio.os.makedirs"):
            with self.assertRaises(podcast_audio.PodcastAudioError) as ctx:
                podcast_audio.synthesize_podcast(
                    segments=[{"text": "第一段失败"}],
                    voice="Ethan",
                    api_key="token",
                    output_dir="output-dir",
                    cache_dir="cache-dir",
                )

        self.assertIn("第 1 段语音合成失败", str(ctx.exception))
        self.assertNotIn("A:", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
