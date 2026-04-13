import podcast_script
import unittest


class PodcastScriptTests(unittest.TestCase):
    def test_core_prompt_includes_single_host_constraints(self):
        prompt = podcast_script.get_podcast_core_prompt()
        self.assertIn("单人播客解说", prompt)
        self.assertIn('只能包含以下一个字段', prompt)
        self.assertIn('不得直接照抄原文句子', prompt)

    def test_length_control_prompt_uses_duration_range(self):
        prompt = podcast_script.get_podcast_length_control_prompt("8分钟")
        self.assertIn("8分钟", prompt)
        self.assertIn("1800 - 2200", prompt)

    def test_parse_plain_json_segments(self):
        raw = '[{"text": "第一段"}, {"text": "第二段"}]'
        self.assertEqual(
            podcast_script.parse_podcast_script_segments(raw),
            [{"text": "第一段"}, {"text": "第二段"}],
        )

    def test_parse_fenced_json_segments(self):
        raw = '```json\n[{"text":"你好"}]\n```'
        self.assertEqual(
            podcast_script.parse_podcast_script_segments(raw),
            [{"text": "你好"}],
        )

    def test_parse_legacy_dual_host_segments(self):
        raw = '[{"speaker": "A", "text": "第一段"}, {"speaker": "B", "text": "第二段"}]'
        self.assertEqual(
            podcast_script.parse_podcast_script_segments(raw),
            [{"text": "第一段"}, {"text": "第二段"}],
        )

    def test_reject_empty_array(self):
        with self.assertRaises(ValueError):
            podcast_script.parse_podcast_script_segments('[]')

    def test_reject_missing_text(self):
        with self.assertRaises(ValueError):
            podcast_script.parse_podcast_script_segments('[{"speaker": "A"}]')


if __name__ == "__main__":
    unittest.main()
