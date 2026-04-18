import ast
import html as html_lib
import re
import types
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {
    "normalize_copy_text",
    "_render_inline_markdown_for_clipboard",
    "build_clipboard_html_document",
    "build_windows_html_clipboard_payload",
    "prepare_highlighted_html_for_clipboard",
    "markdown_to_editor_html",
    "html_fragment_to_plain_text",
    "html_fragment_to_editor_html",
}


def load_copy_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("copy_helpers")
    module.__dict__.update({"re": re, "html_lib": html_lib, "sanitize_highlighted_article": lambda value: value, "build_clipboard_html_document": None})

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            function_module = ast.Module(body=[node], type_ignores=[])
            compiled = compile(function_module, filename=str(APP_PATH), mode="exec")
            exec(compiled, module.__dict__)

    missing = TARGET_FUNCTIONS.difference(module.__dict__)
    if missing:
        raise RuntimeError(f"Missing helper functions: {sorted(missing)}")
    return module


class CopyFormattingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_copy_helpers()

    def test_normalize_copy_text_preserves_double_newlines(self):
        source = "第一段\r\n第二行\r\n\r\n\r\n第三段\u2028第四行"
        normalized = self.helpers.normalize_copy_text(source)
        self.assertEqual(normalized, "第一段\n第二行\n\n第三段\n第四行")


    def test_build_windows_html_clipboard_payload_contains_offsets_and_fragment(self):
        fragment = self.helpers.build_clipboard_html_document("<div>??</div>")
        payload = self.helpers.build_windows_html_clipboard_payload(fragment)
        payload_text = payload.decode("utf-8")
        self.assertIn("Version:0.9", payload_text)
        self.assertIn("StartHTML:", payload_text)
        self.assertIn("StartFragment:", payload_text)
        self.assertIn("StartSelection:", payload_text)
        self.assertIn("EndSelection:", payload_text)
        self.assertIn("<!--StartFragment--><div>??</div><!--EndFragment-->", payload_text)


    def test_prepare_highlighted_html_for_clipboard_inlines_styles(self):
        source = '<h2>??</h2><p>?? <span class="highlight-positive">???</span></p>'
        html = self.helpers.prepare_highlighted_html_for_clipboard(source)
        self.assertIn('font-size:28px', html)
        self.assertIn('background-color:#d8e7ff', html)
        self.assertIn('???', html)
        self.assertNotIn('', html)
        self.assertIn('<!--StartFragment-->', html)

    def test_markdown_to_editor_html_wraps_each_paragraph_as_block(self):
        source = "第一段第一行\n第一段第二行\n\n第二段 **加粗**"
        html = self.helpers.markdown_to_editor_html(source)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<!--StartFragment-->", html)
        self.assertIn("<div>第一段第一行<br/>第一段第二行</div>", html)
        self.assertIn("<div>第二段 <strong>加粗</strong></div>", html)
        self.assertIn("<!--EndFragment-->", html)

    def test_markdown_to_editor_html_returns_placeholder_for_empty_text(self):
        html = self.helpers.markdown_to_editor_html("\n\n")
        self.assertIn("<div><br/></div>", html)

    def test_html_fragment_to_plain_text_preserves_headings_and_highlights(self):
        source = "<h2>???</h2><p>??? <strong>??</strong></p><p>???</p>"
        plain = self.helpers.html_fragment_to_plain_text(source)
        self.assertEqual(plain, "???\n\n??? ??\n\n???")

    def test_html_fragment_to_editor_html_wraps_original_fragment(self):
        source = "<h2>???</h2><p>??</p>"
        html = self.helpers.html_fragment_to_editor_html(source)
        self.assertIn("<!--StartFragment-->", html)
        self.assertIn(source, html)
        self.assertIn("<!--EndFragment-->", html)


if __name__ == "__main__":
    unittest.main()
