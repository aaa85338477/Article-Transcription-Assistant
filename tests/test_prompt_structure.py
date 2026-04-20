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
    "parse_banned_terms_text",
    "parse_replacement_terms_text",
    "merge_term_rules",
    "merge_replacement_terms",
    "build_term_rules_instruction",
    "build_term_rules_preview_text",
    "scan_article_terms",
    "summarize_term_scan",
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


TITLE_MARKER = "【备选标题】"
BODY_MARKER = "【正文】"
PURE_TITLE_MARKER = "【纯净标题组】"
PURE_BODY_MARKER = "【纯净定稿】"
HIGHLIGHT_MARKER = "【高亮阅读版】"


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
            "Body paragraph 1.\n\n"
            "Body paragraph 2."
        )

        titles, body, combined = self.helpers.parse_article_generation_response(response)

        self.assertEqual(titles, ["Title A", "Title B", "Title C"])
        self.assertEqual(body, "Body paragraph 1.\n\nBody paragraph 2.")
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
        self.assertIn("【结构性检查】", reviewer_prompt)
        self.assertIn("【背景完整性检查】", reviewer_prompt)
        self.assertIn("【标题组检查】", reviewer_prompt)

    def test_editor_and_modification_prompts_include_output_protocol(self):
        editor_prompt = self.helpers.build_editor_system_prompt("Role prompt", "Global instruction")
        modification_prompt = self.helpers.build_modification_system_prompt("Global instruction")

        self.assertIn(TITLE_MARKER, editor_prompt)
        self.assertIn(BODY_MARKER, editor_prompt)
        self.assertIn("Paragraph 1 must tell the reader", editor_prompt)
        self.assertIn(TITLE_MARKER, modification_prompt)
        self.assertIn(BODY_MARKER, modification_prompt)
        self.assertIn("title group", modification_prompt.lower())

    def test_de_ai_prompt_template_requires_three_output_blocks(self):
        template = self.helpers.build_de_ai_prompt_template("发行主编", "# Role: 示例", "示例素材")

        self.assertIn(PURE_TITLE_MARKER, template)
        self.assertIn(PURE_BODY_MARKER, template)
        self.assertIn(HIGHLIGHT_MARKER, template)
        self.assertIn("如果原稿开头太突兀", template)
        self.assertIn("保留 3-5 个备选标题", template)
        self.assertIn("优先使用 <h2>/<h3>", template)

    def test_de_ai_prompt_template_community_and_chat_variants_add_style_rules_without_changing_output_protocol(self):
        normal_template = self.helpers.build_de_ai_prompt_template("发行主编", "# Role: 示例", "示例素材")
        community_template = self.helpers.build_de_ai_prompt_template(
            "发行主编",
            "# Role: 示例",
            "示例素材",
            variant=self.helpers.DE_AI_VARIANT_COMMUNITY,
        )
        chat_template = self.helpers.build_de_ai_prompt_template(
            "发行主编",
            "# Role: 示例",
            "示例素材",
            variant=self.helpers.DE_AI_VARIANT_CHAT,
        )

        self.assertNotIn("社区文章去AI版附加要求", normal_template)
        self.assertNotIn("自然唠嗑版附加要求", normal_template)
        self.assertIn("社区文章去AI版附加要求", community_template)
        self.assertIn("玩家社区里的高质量长帖", community_template)
        self.assertIn("贴吧口癖", community_template)
        self.assertNotIn("自然唠嗑版附加要求", community_template)
        self.assertIn("自然唠嗑版附加要求", chat_template)
        self.assertIn("中强度口语化改写", chat_template)
        self.assertIn("低质水贴", chat_template)
        self.assertNotIn("社区文章去AI版附加要求", chat_template)
        self.assertEqual(
            normal_template.split("# 输出格式要求", 1)[1],
            community_template.split("# 输出格式要求", 1)[1],
        )
        self.assertEqual(
            normal_template.split("# 输出格式要求", 1)[1],
            chat_template.split("# 输出格式要求", 1)[1],
        )

    def test_term_rule_parsers_and_merge_keep_rules_stable(self):
        banned_terms = self.helpers.parse_banned_terms_text("值得关注\n\n值得关注\n不难发现\n")
        replacement_terms, invalid_lines = self.helpers.parse_replacement_terms_text(
            "值得关注 => 更值得细看的是\n坏格式\n进一步来说 -> 往下看\n显而易见 → 这点其实很清楚"
        )
        merged_banned_terms, merged_default_replacements = self.helpers.merge_term_rules(
            banned_terms,
            {"值得关注": "更值得细看的是"},
            ["进一步来说"],
            {"不难发现": "直接能看出来"},
        )
        merged_suggested_replacements = self.helpers.merge_replacement_terms(
            {"核心优势在于": "真正有优势的地方在于"},
            {"显而易见": "这点其实很清楚"},
        )

        self.assertEqual(banned_terms, ["值得关注", "不难发现"])
        self.assertEqual(invalid_lines, ["坏格式"])
        self.assertEqual(replacement_terms["进一步来说"], "往下看")
        self.assertEqual(replacement_terms["显而易见"], "这点其实很清楚")
        self.assertIn("进一步来说", merged_banned_terms)
        self.assertEqual(merged_default_replacements["值得关注"], "更值得细看的是")
        self.assertEqual(merged_default_replacements["不难发现"], "直接能看出来")
        self.assertEqual(merged_suggested_replacements["核心优势在于"], "真正有优势的地方在于")
        self.assertEqual(merged_suggested_replacements["显而易见"], "这点其实很清楚")

    def test_term_rules_instruction_and_prompt_injection_use_only_default_replacements(self):
        instruction = self.helpers.build_term_rules_instruction(
            ["值得关注"],
            {"从某种意义上说": "换个更直接的说法"},
        )
        preview = self.helpers.build_term_rules_preview_text(
            ["值得关注"],
            {"从某种意义上说": "换个更直接的说法"},
            {"核心优势在于": "真正有优势的地方在于"},
        )
        modification_prompt = self.helpers.build_modification_system_prompt("Global instruction", term_rules_instruction=instruction)
        de_ai_prompt = self.helpers.build_de_ai_prompt_template(
            "发行主编",
            "# Role: 示例",
            "示例素材",
            term_rules_instruction=instruction,
        )

        self.assertIn("【个人词表约束】", instruction)
        self.assertIn("以下表达请尽量不要出现", instruction)
        self.assertIn("【建议替换词表】", preview)
        self.assertIn("值得关注", modification_prompt)
        self.assertIn("换个更直接的说法", modification_prompt)
        self.assertIn("【个人词表约束】", de_ai_prompt)
        self.assertIn("从某种意义上说", de_ai_prompt)
        self.assertNotIn("核心优势在于", de_ai_prompt)

    def test_scan_article_terms_uses_body_paragraphs_and_counts_hits_by_level(self):
        article_text = (
            f"{TITLE_MARKER}\n"
            "1. 标题A\n"
            "2. 标题B\n"
            "3. 标题C\n\n"
            f"{BODY_MARKER}\n"
            "第一段值得关注。\n\n"
            "第二段从某种意义上说，值得关注。\n\n"
            "第三段核心优势在于节奏稳定。"
        )
        scan_result = self.helpers.scan_article_terms(
            article_text,
            ["值得关注"],
            {"从某种意义上说": "换个更直接的说法"},
            {"核心优势在于": "真正有优势的地方在于"},
        )
        summary = self.helpers.summarize_term_scan(scan_result)
        term_map = {item["term"]: item for item in scan_result}

        self.assertEqual(len(scan_result), 3)
        self.assertEqual(summary["matched_terms"], 3)
        self.assertEqual(summary["total_hits"], 4)
        self.assertEqual(summary["banned_terms"], 1)
        self.assertEqual(summary["default_replacement_terms"], 1)
        self.assertEqual(summary["suggested_replacement_terms"], 1)
        self.assertEqual(term_map["值得关注"]["paragraph_indexes"], [1, 2])
        self.assertEqual(term_map["从某种意义上说"]["level"], "default")
        self.assertEqual(term_map["核心优势在于"]["level"], "suggested")

    def test_sanitize_highlighted_article_converts_markdown_headings_and_removes_anchor_icons(self):
        cleaned = self.helpers.sanitize_highlighted_article("## Heading Title 🔗\n<p>Body</p>")

        self.assertIn("<h2>Heading Title</h2>", cleaned)
        self.assertNotIn("🔗", cleaned)
        self.assertNotIn("## Heading Title", cleaned)

    def test_parse_de_ai_output_returns_titles_body_and_highlight(self):
        response = (
            f"{PURE_TITLE_MARKER}\n"
            "1. Clean Title A\n"
            "2. Clean Title B\n"
            "3. Clean Title C\n\n"
            f"{PURE_BODY_MARKER}\n"
            "正文段落一。\n\n"
            "正文段落二。\n\n"
            f"{HIGHLIGHT_MARKER}\n"
            "<p>正文段落一。</p>"
        )

        titles, body, highlighted = self.helpers.parse_de_ai_dual_output(response, fallback_titles=["Old Title"])

        self.assertEqual(titles, ["Clean Title A", "Clean Title B", "Clean Title C"])
        self.assertEqual(body, "正文段落一。\n\n正文段落二。")
        self.assertEqual(highlighted, "<p>正文段落一。</p>")


if __name__ == "__main__":
    unittest.main()
