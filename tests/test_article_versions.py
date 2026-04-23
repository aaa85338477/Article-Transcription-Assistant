import ast
import types
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {
    "ensure_article_version_state",
    "append_article_version",
    "restore_article_version_to_session",
}


class FakeDateTimeNow:
    def strftime(self, fmt):
        return "2026-04-18 12:34:56"


class FakeDateTime:
    @staticmethod
    def now():
        return FakeDateTimeNow()


class SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def load_version_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("version_helpers")
    module.__dict__.update({
        "datetime": FakeDateTime,
        "st": types.SimpleNamespace(session_state=SessionState()),
        "parse_article_generation_response": lambda content, fallback_titles=None: ([], content, content),
        "reset_podcast_outputs": lambda delete_audio=False: None,
        "refresh_obsidian_influence_map": lambda force=False: None,
        "refresh_evidence_map": lambda force=False: None,
    })

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            function_module = ast.Module(body=[node], type_ignores=[])
            compiled = compile(function_module, filename=str(APP_PATH), mode="exec")
            exec(compiled, module.__dict__)

    missing = TARGET_FUNCTIONS.difference(module.__dict__)
    if missing:
        raise RuntimeError(f"Missing helper functions: {sorted(missing)}")
    return module


class ArticleVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_version_helpers()

    def setUp(self):
        self.helpers.st.session_state = SessionState({
            "article_versions": [],
            "active_article_version_id": None,
            "selected_role": "editor",
            "title_candidates": ["old title"],
            "highlighted_article": "",
            "spoken_script": "",
            "final_article": "",
        })

    def test_append_article_version_persists_highlighted_article(self):
        version_id = self.helpers.append_article_version(
            "[Body]\nBody content",
            "final stage",
            model="deepseek-v3.2",
            highlighted_article="<h2>Heading</h2><p>Highlighted body</p>",
        )

        self.assertEqual(version_id, "V001")
        saved_version = self.helpers.st.session_state["article_versions"][0]
        self.assertEqual(saved_version["highlighted_article"], "<h2>Heading</h2><p>Highlighted body</p>")
        self.assertEqual(saved_version["created_at"], "2026-04-18 12:34:56")

    def test_restore_article_version_to_session_restores_highlighted_article(self):
        calls = {"reset": None, "refresh": None, "evidence_refresh": None, "fallback_titles": None}

        def fake_parse(content, fallback_titles=None):
            calls["fallback_titles"] = fallback_titles
            return (["new title"], "restored article body", "[Body]\nrestored article body")

        def fake_reset(delete_audio=False):
            calls["reset"] = delete_audio

        def fake_refresh(force=False):
            calls["refresh"] = force

        def fake_evidence_refresh(force=False):
            calls["evidence_refresh"] = force

        self.helpers.parse_article_generation_response = fake_parse
        self.helpers.reset_podcast_outputs = fake_reset
        self.helpers.refresh_obsidian_influence_map = fake_refresh
        self.helpers.refresh_evidence_map = fake_evidence_refresh

        restored = self.helpers.restore_article_version_to_session(
            {
                "id": "V007",
                "content": "[Body]\nprevious article body",
                "highlighted_article": "<p>restored highlight</p>",
            },
            fallback_titles=["old title"],
        )

        self.assertTrue(restored)
        self.assertEqual(self.helpers.st.session_state["title_candidates"], ["new title"])
        self.assertEqual(self.helpers.st.session_state["final_article"], "[Body]\nrestored article body")
        self.assertEqual(self.helpers.st.session_state["highlighted_article"], "<p>restored highlight</p>")
        self.assertEqual(self.helpers.st.session_state["active_article_version_id"], "V007")
        self.assertEqual(calls["fallback_titles"], ["old title"])
        self.assertTrue(calls["reset"])
        self.assertTrue(calls["refresh"])
        self.assertTrue(calls["evidence_refresh"])


if __name__ == "__main__":
    unittest.main()
