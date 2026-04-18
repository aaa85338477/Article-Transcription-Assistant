import ast
import re
import types
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {
    "sanitize_editor_prompt",
    "build_target_length_instruction",
    "build_article_structure_instruction",
    "build_article_output_instruction",
    "build_reviewer_structure_instruction",
    "build_reviewer_system_prompt",
    "build_editor_system_prompt",
    "build_modification_system_prompt",
    "normalize_title_candidates",
    "parse_title_candidates_block",
    "split_structured_article_sections",
    "build_title_candidates_block",
    "build_structured_article_text",
    "build_display_article_text",
    "parse_article_generation_response",
    "get_article_body_text",
    "infer_role_persona",
    "infer_article_topic",
    "build_de_ai_prompt_template",
    "parse_de_ai_dual_output",
    "sanitize_highlighted_article",
}
TARGET_ASSIGNMENTS = {
    "ROLE_AUDIENCE_MAP",
    "ROLE_JARGON_MAP",
    "ROLE_TONE_MAP",
    "ARTICLE_TITLE_MARKER",
    "ARTICLE_BODY_MARKER",
    "PURE_TITLE_MARKER",
    "PURE_BODY_MARKER",
    "HIGHLIGHT_MARKER",
    "DE_AI_VARIANTS",
    "DE_AI_VARIANT_DEFAULT",
    "DE_AI_VARIANT_COMMUNITY",
    "DE_AI_VARIANT_CHAT",
}


TITLE_MARKER = "\u3010\u5907\u9009\u6807\u9898\u3011"
BODY_MARKER = "\u3010\u6b63\u6587\u3011"
PURE_TITLE_MARKER = "\u3010\u7eaf\u51c0\u6807\u9898\u7ec4\u3011"
PURE_BODY_MARKER = "\u3010\u7eaf\u51c0\u5b9a\u7a3f\u3011"
HIGHLIGHT_MARKER = "\u3010\u9ad8\u4eae\u9605\u8bfb\u7248\u3011"


def load_prompt_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("prompt_helpers")
    module.__dict__.update({"re": re})

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

    module.get_target_article_words = lambda: 1500

    missing = TARGET_FUNCTIONS.difference(module.__dict__)
    if missing:
        raise RuntimeError(f"Missing helper functions: {sorted(missing)}")
    return module


class PromptStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_prompt_helpers()

    def test_article_structure_instruction_scales_with_length(self):
        short_instruction = self.helpers.build_article_structure_instruction(900)
        medium_instruction = self.helpers.build_article_structure_instruction(1500)
        long_instruction = self.helpers.build_article_structure_instruction(2800)

        self.assertIn("3 `##` sections", short_instruction)
        self.assertIn("3-5 `##` sections", medium_instruction)
        self.assertIn("4-5 `##` sections", long_instruction)
        self.assertIn("Markdown", medium_instruction)

    def test_article_output_instruction_requires_background_and_two_blocks(self):
        instruction = self.helpers.build_article_output_instruction()

        self.assertIn("Paragraph 1 must tell the reader", instruction)
        self.assertIn("Paragraph 2 should explain", instruction)
        self.assertIn(TITLE_MARKER, instruction)
        self.assertIn(BODY_MARKER, instruction)

    def test_structured_article_helpers_round_trip_titles_and_body(self):
        response = (
            f"{TITLE_MARKER}\n"
            "1. Title A\n"
            "2. Title B\n"
            "3. Title C\n\n"
            f"{BODY_MARKER}\n"
            "????\n\n"
            "????"
        )

        titles, body, combined = self.helpers.parse_article_generation_response(response)

        self.assertEqual(titles, ["Title A", "Title B", "Title C"])
        self.assertEqual(body, "????\n\n????")
        self.assertIn(TITLE_MARKER, combined)
        self.assertIn(BODY_MARKER, combined)
        self.assertEqual(self.helpers.get_article_body_text(combined), body)

    def test_display_article_text_can_restore_saved_titles(self):
        combined = self.helpers.build_display_article_text("Body only.", ["Title A", "Title B", "Title C"])
        self.assertIn(TITLE_MARKER, combined)
        self.assertIn("Title A", combined)
        self.assertTrue(combined.endswith("Body only."))

    def test_reviewer_prompt_adds_background_and_title_checks(self):
        reviewer_prompt = self.helpers.build_reviewer_system_prompt("Base review", "Only use source packet")

        self.assertIn("Base review", reviewer_prompt)
        self.assertIn("Only use source packet", reviewer_prompt)
        self.assertIn("\u3010\u7ed3\u6784\u6027\u68c0\u67e5\u3011", reviewer_prompt)
        self.assertIn("\u3010\u80cc\u666f\u5b8c\u6574\u6027\u68c0\u67e5\u3011", reviewer_prompt)
        self.assertIn("\u3010\u6807\u9898\u7ec4\u68c0\u67e5\u3011", reviewer_prompt)

    def test_editor_and_modification_prompts_include_output_protocol(self):
        editor_prompt = self.helpers.build_editor_system_prompt("Role prompt", "Global instruction")
        modification_prompt = self.helpers.build_modification_system_prompt("Global instruction")

        self.assertIn(TITLE_MARKER, editor_prompt)
        self.assertIn(BODY_MARKER, editor_prompt)
        self.assertIn("Paragraph 1 must tell the reader", editor_prompt)

        self.assertIn(TITLE_MARKER, modification_prompt)
        self.assertIn(BODY_MARKER, modification_prompt)
        self.assertIn("candidate-title block", modification_prompt)

    def test_de_ai_prompt_template_requires_three_output_blocks(self):
        template = self.helpers.build_de_ai_prompt_template(
            "\u53d1\u884c\u4e3b\u7f16",
            "# Role: ????",
            "?????????????",
        )

        self.assertIn(PURE_TITLE_MARKER, template)
        self.assertIn(PURE_BODY_MARKER, template)
        self.assertIn(HIGHLIGHT_MARKER, template)
        self.assertIn("If the draft opens too abruptly", template)
        self.assertIn("keep 3-5 candidate titles", template)
        self.assertIn("Prefer <h2>/<h3>", template)

    def test_de_ai_prompt_template_community_and_chat_variants_add_style_rules_without_changing_output_protocol(self):
        normal_template = self.helpers.build_de_ai_prompt_template(
            "\u53d1\u884c\u4e3b\u7f16",
            "# Role: ????",
            "?????????????",
        )
        community_template = self.helpers.build_de_ai_prompt_template(
            "\u53d1\u884c\u4e3b\u7f16",
            "# Role: ????",
            "?????????????",
            variant=self.helpers.DE_AI_VARIANT_COMMUNITY,
        )
        chat_template = self.helpers.build_de_ai_prompt_template(
            "\u53d1\u884c\u4e3b\u7f16",
            "# Role: ????",
            "?????????????",
            variant=self.helpers.DE_AI_VARIANT_CHAT,
        )

        self.assertNotIn("Community Forum Adaptation", normal_template)
        self.assertNotIn("Natural Conversational Adaptation", normal_template)

        self.assertIn("Community Forum Adaptation", community_template)
        self.assertIn("player forum", community_template)
        self.assertIn("tieba slang", community_template)
        self.assertNotIn("Natural Conversational Adaptation", community_template)

        self.assertIn("Natural Conversational Adaptation", chat_template)
        self.assertIn("medium-strength conversational rewrite", chat_template)
        self.assertIn("low-grade chatter", chat_template)
        self.assertNotIn("Community Forum Adaptation", chat_template)

        self.assertEqual(
            normal_template.split("# Output Protocol", 1)[1],
            community_template.split("# Output Protocol", 1)[1],
        )
        self.assertEqual(
            normal_template.split("# Output Protocol", 1)[1],
            chat_template.split("# Output Protocol", 1)[1],
        )

    def test_sanitize_highlighted_article_converts_markdown_headings_and_removes_anchor_icons(self):
        cleaned = self.helpers.sanitize_highlighted_article("## Heading Title \U0001f517\n<p>Body</p>")

        self.assertIn("<h2>Heading Title</h2>", cleaned)
        self.assertNotIn("\U0001f517", cleaned)
        self.assertNotIn("## Heading Title", cleaned)

    def test_parse_de_ai_output_returns_titles_body_and_highlight(self):
        response = (
            f"{PURE_TITLE_MARKER}\n"
            "1. Clean Title A\n"
            "2. Clean Title B\n"
            "3. Clean Title C\n\n"
            f"{PURE_BODY_MARKER}\n"
            "??????\n\n"
            "??????\n\n"
            f"{HIGHLIGHT_MARKER}\n"
            "<p>??????</p>"
        )

        titles, body, highlighted = self.helpers.parse_de_ai_dual_output(response, fallback_titles=["Old Title"])

        self.assertEqual(titles, ["Clean Title A", "Clean Title B", "Clean Title C"])
        self.assertEqual(body, "??????\n\n??????")
        self.assertEqual(highlighted, "<p>??????</p>")


if __name__ == "__main__":
    unittest.main()
