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
    "markdown_to_editor_html",
}


def load_copy_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("copy_helpers")
    module.__dict__.update({"re": re, "html_lib": html_lib})

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


if __name__ == "__main__":
    unittest.main()
