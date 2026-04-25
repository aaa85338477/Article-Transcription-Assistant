import ast
import html as html_lib
import re
import types
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {
    "normalize_title_candidates",
    "parse_title_candidates_block",
    "split_structured_article_sections",
    "get_article_body_text",
    "build_feishu_doc_title",
    "sanitize_highlighted_article",
    "feishu_html_to_plain_text",
    "build_feishu_elements_from_html",
    "build_feishu_blocks_from_highlighted_html",
    "build_feishu_text_elements",
    "make_feishu_block",
    "build_feishu_doc_blocks",
}
TARGET_ASSIGNMENTS = {
    "ARTICLE_TITLE_MARKER",
    "ARTICLE_BODY_MARKER",
    "PURE_TITLE_MARKER",
    "PURE_BODY_MARKER",
    "HIGHLIGHT_MARKER",
}


class SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def load_feishu_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("feishu_helpers")
    module.__dict__.update({
        "re": re,
        "html_lib": html_lib,
        "st": types.SimpleNamespace(session_state=SessionState()),
        "get_task_by_id": lambda task_id: None,
        "sanitize_highlighted_article": lambda value: value,
    })

    for node in tree.body:
        if isinstance(node, ast.Assign):
            target_names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if target_names & TARGET_ASSIGNMENTS:
                assign_module = ast.Module(body=[node], type_ignores=[])
                compiled = compile(assign_module, filename=str(APP_PATH), mode="exec")
                exec(compiled, module.__dict__)
        elif isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            function_module = ast.Module(body=[node], type_ignores=[])
            compiled = compile(function_module, filename=str(APP_PATH), mode="exec")
            exec(compiled, module.__dict__)

    missing = TARGET_FUNCTIONS.difference(module.__dict__)
    if missing:
        raise RuntimeError(f"Missing helper functions: {sorted(missing)}")
    return module


class FeishuDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_feishu_helpers()

    def test_build_feishu_doc_title_prefers_title_candidates(self):
        article = (
            "\u3010\u5907\u9009\u6807\u9898\u3011\n"
            "1. \u6807\u9898 A\n"
            "2. \u6807\u9898 B\n\n"
            "\u3010\u6b63\u6587\u3011\n"
            "\u6b63\u6587\u7b2c\u4e00\u6bb5\u3002"
        )
        title = self.helpers.build_feishu_doc_title(article)
        self.assertEqual(title, "\u6807\u9898 A")

    def test_build_feishu_text_elements_marks_bold_segments(self):
        elements = self.helpers.build_feishu_text_elements("\u666e\u901a **\u91cd\u70b9** \u6536\u5c3e")
        self.assertEqual(elements[0]["text_run"]["content"], "\u666e\u901a ")
        self.assertEqual(elements[1]["text_run"]["content"], "\u91cd\u70b9")
        self.assertTrue(elements[1]["text_run"]["text_element_style"]["bold"])
        self.assertEqual(elements[2]["text_run"]["content"], " \u6536\u5c3e")

    def test_build_feishu_doc_blocks_keeps_title_candidates_section(self):
        article = (
            "\u3010\u5907\u9009\u6807\u9898\u3011\n"
            "1. \u6807\u9898 A\n"
            "2. \u6807\u9898 B\n\n"
            "\u3010\u6b63\u6587\u3011\n"
            "\u6b63\u6587\u7b2c\u4e00\u6bb5\u3002"
        )

        blocks = self.helpers.build_feishu_doc_blocks(article)

        self.assertEqual(blocks[0]["heading2"]["elements"][0]["text_run"]["content"], "\u5907\u9009\u6807\u9898")
        self.assertEqual(blocks[1]["text"]["elements"][0]["text_run"]["content"], "1. \u6807\u9898 A")
        self.assertEqual(blocks[2]["text"]["elements"][0]["text_run"]["content"], "2. \u6807\u9898 B")
        self.assertEqual(blocks[3]["text"]["elements"][0]["text_run"]["content"], "\u6b63\u6587\u7b2c\u4e00\u6bb5\u3002")


    def test_build_feishu_doc_blocks_keeps_headings_paragraphs_and_highlights(self):
        article = (
            "\u3010\u5907\u9009\u6807\u9898\u3011\n"
            "1. \u6807\u9898 A\n\n"
            "\u3010\u6b63\u6587\u3011\n"
            "\u5bfc\u8bed\u7b2c\u4e00\u6bb5\u3002\n\n"
            "## \u5c0f\u8282\u4e00\n"
            "\u8fd9\u91cc\u6709 **\u91cd\u70b9** \u5185\u5bb9\u3002"
        )
        highlighted = "<h2>\u5bfc\u8bfb</h2><p>\u8fd9\u662f <strong>\u9ad8\u4eae</strong> \u91cd\u70b9\u3002</p>"

        blocks = self.helpers.build_feishu_doc_blocks(article, highlighted_html=highlighted)

        self.assertEqual(blocks[0]["heading2"]["elements"][0]["text_run"]["content"], "\u5907\u9009\u6807\u9898")
        self.assertEqual(blocks[1]["text"]["elements"][0]["text_run"]["content"], "1. \u6807\u9898 A")
        self.assertEqual(blocks[2]["text"]["elements"][0]["text_run"]["content"], "\u5bfc\u8bed\u7b2c\u4e00\u6bb5\u3002")
        self.assertEqual(blocks[3]["heading2"]["elements"][0]["text_run"]["content"], "\u5c0f\u8282\u4e00")
        paragraph_elements = blocks[4]["text"]["elements"]
        self.assertEqual(paragraph_elements[1]["text_run"]["content"], "\u91cd\u70b9")
        self.assertTrue(paragraph_elements[1]["text_run"]["text_element_style"]["bold"])
        self.assertEqual(blocks[5]["heading2"]["elements"][0]["text_run"]["content"], "\u9ad8\u4eae\u91cd\u70b9")
        self.assertEqual(blocks[6]["heading3"]["elements"][0]["text_run"]["content"], "\u5bfc\u8bfb")
        highlight_elements = blocks[7]["text"]["elements"]
        self.assertEqual(highlight_elements[0]["text_run"]["content"], "\u8fd9\u662f ")
        self.assertFalse(highlight_elements[0]["text_run"]["text_element_style"].get("bold", False))
        self.assertEqual(highlight_elements[1]["text_run"]["content"], "\u9ad8\u4eae")
        self.assertTrue(highlight_elements[1]["text_run"]["text_element_style"].get("bold"))
        self.assertEqual(highlight_elements[2]["text_run"]["content"], " \u91cd\u70b9\u3002")
        self.assertFalse(highlight_elements[2]["text_run"]["text_element_style"].get("bold", False))

    def test_sanitize_highlighted_article_unwraps_highlight_spans_inside_headings(self):
        source = '<h2><span class="highlight-positive">\u5bfc\u8bfb\u6807\u9898</span></h2><p><span class="highlight-risk">\u98ce\u9669\u63d0\u793a</span></p>'
        sanitized = self.helpers.sanitize_highlighted_article(source)

        self.assertIn('<h3>\u5bfc\u8bfb\u6807\u9898</h3>', sanitized)
        self.assertIn('<span class="highlight-risk">\u98ce\u9669\u63d0\u793a</span>', sanitized)
        self.assertNotIn('<h2><span class="highlight-positive">', sanitized)

    def test_build_feishu_blocks_from_highlighted_html_keeps_structure_and_partial_emphasis(self):
        highlighted = '<h2>\u5bfc\u8bfb</h2><p>\u6bb5\u843d <span class="highlight-positive">\u91cd\u70b9</span> \u6536\u5c3e</p>'
        blocks = self.helpers.build_feishu_blocks_from_highlighted_html(highlighted)

        self.assertEqual(blocks[0]["heading3"]["elements"][0]["text_run"]["content"], "\u5bfc\u8bfb")
        paragraph_elements = blocks[1]["text"]["elements"]
        self.assertEqual(paragraph_elements[0]["text_run"]["content"], "\u6bb5\u843d ")
        self.assertFalse(paragraph_elements[0]["text_run"]["text_element_style"].get("bold", False))
        self.assertEqual(paragraph_elements[1]["text_run"]["content"], "\u91cd\u70b9")
        self.assertTrue(paragraph_elements[1]["text_run"]["text_element_style"].get("bold"))
        self.assertEqual(paragraph_elements[1]["text_run"]["text_element_style"].get("text_color"), 5)
        self.assertEqual(paragraph_elements[1]["text_run"]["text_element_style"].get("background_color"), 5)
        self.assertEqual(paragraph_elements[2]["text_run"]["content"], " \u6536\u5c3e")
        self.assertFalse(paragraph_elements[2]["text_run"]["text_element_style"].get("bold", False))

    def test_build_feishu_blocks_from_highlighted_html_keeps_risk_highlight_colors(self):
        highlighted = '<p><span class="highlight-risk">\u98ce\u9669\u70b9</span></p>'
        blocks = self.helpers.build_feishu_blocks_from_highlighted_html(highlighted)

        risk_style = blocks[0]["text"]["elements"][0]["text_run"]["text_element_style"]
        self.assertTrue(risk_style.get("bold"))
        self.assertEqual(risk_style.get("text_color"), 1)
        self.assertEqual(risk_style.get("background_color"), 1)


if __name__ == "__main__":
    unittest.main()
