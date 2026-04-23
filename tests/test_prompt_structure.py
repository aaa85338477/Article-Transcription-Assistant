import ast
import hashlib
import json
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
    "build_modification_user_content",
    "parse_review_actions",
    "build_selected_review_feedback",
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
    "get_expected_h2_range",
    "split_body_paragraphs",
    "extract_markdown_h2_sections",
    "build_publish_quality_gate_report",
    "extract_de_ai_raw_title_candidates",
    "detect_auto_retry_issues",
    "build_auto_retry_instruction",
    "build_auto_retry_notice",
    "ensure_highlighted_article_context",
    "parse_de_ai_dual_output",
    "sanitize_highlighted_article",
    "normalize_query_token",
    "split_article_paragraphs",
    "extract_obsidian_signal_terms",
    "score_paragraph_against_obsidian_hit",
    "parse_source_packet_segments",
    "extract_claim_sentences",
    "score_paragraph_against_source_segment",
    "build_article_evidence_map",
    "refresh_evidence_map",
    "normalize_evidence_excerpt",
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
    "DE_AI_VARIANT_HUMANIZER",
    "QUERY_STOPWORDS_EN",
    "QUERY_STOPWORDS_ZH",
    "ENGLISH_SIGNAL_HINTS",
}


TITLE_MARKER = "【备选标题】"
BODY_MARKER = "【正文】"
PURE_TITLE_MARKER = "【纯净标题组】"
PURE_BODY_MARKER = "【纯净定稿】"
HIGHLIGHT_MARKER = "【高亮阅读版】"


class SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def load_prompt_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("prompt_helpers")
    module.__dict__.update({
        "re": re,
        "json": json,
        "hashlib": hashlib,
        "st": types.SimpleNamespace(session_state=SessionState()),
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
        self.assertIn("\u3010\u7ed3\u6784\u6027\u68c0\u67e5\u3011", reviewer_prompt)
        self.assertIn("\u3010\u80cc\u666f\u5b8c\u6574\u6027\u68c0\u67e5\u3011", reviewer_prompt)
        self.assertIn("\u3010\u6807\u9898\u7ec4\u68c0\u67e5\u3011", reviewer_prompt)
        self.assertIn("[Humanizer Review Requirements]", reviewer_prompt)
        self.assertIn("AI writing patterns", reviewer_prompt)
        self.assertIn("Flag formulaic sentence shapes", reviewer_prompt)
        self.assertIn("three-part parallel structures", reviewer_prompt)
        self.assertIn("top-level Markdown bullet item", reviewer_prompt)
        self.assertIn("\u95ee\u9898\u7c7b\u578b\uff1a...", reviewer_prompt)

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
        template = self.helpers.build_de_ai_prompt_template("\u53d1\u884c\u4e3b\u7f16", "# Role: \u793a\u4f8b", "\u793a\u4f8b\u7d20\u6750")

        self.assertIn(PURE_TITLE_MARKER, template)
        self.assertIn(PURE_BODY_MARKER, template)
        self.assertIn(HIGHLIGHT_MARKER, template)
        self.assertIn("\u5982\u679c\u539f\u7a3f\u5f00\u5934\u592a\u7a81\u5140", template)
        self.assertIn("\u4fdd\u7559 3-5 \u4e2a\u5907\u9009\u6807\u9898", template)
        self.assertIn("\u4f18\u5148\u4f7f\u7528 <h2>/<h3>", template)

    def test_de_ai_prompt_template_variants_add_style_rules_without_changing_output_protocol(self):
        role_name = "\u53d1\u884c\u4e3b\u7f16"
        editor_prompt = "# Role: \u793a\u4f8b"
        source_content = "\u793a\u4f8b\u7d20\u6750"
        normal_template = self.helpers.build_de_ai_prompt_template(role_name, editor_prompt, source_content)
        community_template = self.helpers.build_de_ai_prompt_template(
            role_name,
            editor_prompt,
            source_content,
            variant=self.helpers.DE_AI_VARIANT_COMMUNITY,
        )
        chat_template = self.helpers.build_de_ai_prompt_template(
            role_name,
            editor_prompt,
            source_content,
            variant=self.helpers.DE_AI_VARIANT_CHAT,
        )
        humanizer_template = self.helpers.build_de_ai_prompt_template(
            role_name,
            editor_prompt,
            source_content,
            variant=self.helpers.DE_AI_VARIANT_HUMANIZER,
        )

        community_marker = "\u793e\u533a\u6587\u7ae0\u53bbAI\u7248\u9644\u52a0\u8981\u6c42"
        chat_marker = "\u81ea\u7136\u5520\u55d1\u7248\u9644\u52a0\u8981\u6c42"
        humanizer_marker = "Humanizer-zh\u7248\u9644\u52a0\u8981\u6c42"

        self.assertNotIn(community_marker, normal_template)
        self.assertNotIn(chat_marker, normal_template)
        self.assertNotIn(humanizer_marker, normal_template)
        self.assertIn(community_marker, community_template)
        self.assertIn("\u73a9\u5bb6\u793e\u533a\u91cc\u7684\u9ad8\u8d28\u91cf\u957f\u5e16", community_template)
        self.assertIn("\u8d34\u5427\u53e3\u7656", community_template)
        self.assertNotIn(chat_marker, community_template)
        self.assertNotIn(humanizer_marker, community_template)
        self.assertIn(chat_marker, chat_template)
        self.assertIn("\u4e2d\u5f3a\u5ea6\u53e3\u8bed\u5316\u6539\u5199", chat_template)
        self.assertIn("\u4f4e\u8d28\u6c34\u8d34", chat_template)
        self.assertNotIn(community_marker, chat_template)
        self.assertNotIn(humanizer_marker, chat_template)
        self.assertIn(humanizer_marker, humanizer_template)
        self.assertIn("\u53bb\u6a21\u677f\u5316", humanizer_template)
        self.assertIn("\u5ba3\u4f20\u8154", humanizer_template)
        self.assertNotIn(community_marker, humanizer_template)
        self.assertNotIn(chat_marker, humanizer_template)
        self.assertEqual(
            normal_template.split("# \u8f93\u51fa\u683c\u5f0f\u8981\u6c42", 1)[1],
            community_template.split("# \u8f93\u51fa\u683c\u5f0f\u8981\u6c42", 1)[1],
        )
        self.assertEqual(
            normal_template.split("# \u8f93\u51fa\u683c\u5f0f\u8981\u6c42", 1)[1],
            chat_template.split("# \u8f93\u51fa\u683c\u5f0f\u8981\u6c42", 1)[1],
        )
        self.assertEqual(
            normal_template.split("# \u8f93\u51fa\u683c\u5f0f\u8981\u6c42", 1)[1],
            humanizer_template.split("# \u8f93\u51fa\u683c\u5f0f\u8981\u6c42", 1)[1],
        )

    def test_parse_review_actions_handles_markdown_bullet_tasks(self):
        review_feedback = (
            "\u3010\u603b\u4f53\u5224\u65ad\u3011\n\u5408\u683c\n\n"
            "\u3010\u4fee\u6539\u4efb\u52a1\u6e05\u5355\u3011\n"
            "- \u4efb\u52a1 1\uff5c\u8865\u8db3\u80cc\u666f\u4ea4\u4ee3\n"
            "\u95ee\u9898\u7c7b\u578b\uff1a\u80cc\u666f\u7f3a\u5931\n"
            "\u5bf9\u5e94\u4f4d\u7f6e\u6216\u539f\u53e5\uff1a\u5f00\u5934\u7b2c\u4e00\u6bb5\n"
            "\u4e3a\u4ec0\u4e48\u8981\u6539\uff1a\u8bfb\u8005\u4e0d\u5bb9\u6613\u770b\u61c2\u6765\u9f99\u53bb\u8109\n"
            "\u5e94\u8be5\u600e\u4e48\u6539\uff1a\u8865\u4e00\u53e5\u80cc\u666f\u8bf4\u660e\n\n"
            "- \u4efb\u52a1 2\uff5c\u7f29\u77ed\u6807\u9898\n"
            "\u95ee\u9898\u7c7b\u578b\uff1a\u6807\u9898\u5197\u957f\n"
            "\u5bf9\u5e94\u4f4d\u7f6e\u6216\u539f\u53e5\uff1a\u6807\u9898\u7ec4\u7b2c\u4e00\u6761\n"
            "\u4e3a\u4ec0\u4e48\u8981\u6539\uff1a\u4fe1\u606f\u5bc6\u5ea6\u8fc7\u9ad8\n"
            "\u5e94\u8be5\u600e\u4e48\u6539\uff1a\u4fdd\u7559\u6838\u5fc3\u5224\u65ad\u5373\u53ef\n\n"
            "\u3010\u5176\u4ed6\u5efa\u8bae\u3011\n1. \u7ef4\u6301\u8282\u594f"
        )

        actions = self.helpers.parse_review_actions(review_feedback)

        self.assertEqual([action["id"] for action in actions], ["review_action_1", "review_action_2"])
        self.assertEqual(actions[0]["title"], "\u4efb\u52a1 1\uff5c\u8865\u8db3\u80cc\u666f\u4ea4\u4ee3")
        self.assertIn("\u95ee\u9898\u7c7b\u578b\uff1a\u80cc\u666f\u7f3a\u5931", actions[0]["body"])
        self.assertEqual(actions[1]["summary"], "\u4efb\u52a1 2\uff5c\u7f29\u77ed\u6807\u9898")

    def test_parse_review_actions_falls_back_to_numbered_tasks(self):
        review_feedback = (
            "\u3010\u603b\u4f53\u5224\u65ad\u3011\n\u5408\u683c\n\n"
            "\u3010\u4fee\u6539\u4efb\u52a1\u6e05\u5355\u3011\n"
            "1. \u8865\u8db3\u80cc\u666f\u4ea4\u4ee3\n"
            "\u95ee\u9898\u7c7b\u578b\uff1a\u80cc\u666f\u7f3a\u5931\n"
            "\u5e94\u8be5\u600e\u4e48\u6539\uff1a\u5f00\u5934\u5148\u4ea4\u4ee3\u4e8b\u4ef6\u7f18\u7531\n\n"
            "2. \u538b\u7f29\u603b\u7ed3\n"
            "\u95ee\u9898\u7c7b\u578b\uff1a\u6536\u675f\u8fc7\u6162\n"
            "\u5e94\u8be5\u600e\u4e48\u6539\uff1a\u76f4\u63a5\u843d\u5230\u7ed3\u8bba\n"
        )

        actions = self.helpers.parse_review_actions(review_feedback)

        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["title"], "\u8865\u8db3\u80cc\u666f\u4ea4\u4ee3")
        self.assertEqual(actions[1]["title"], "\u538b\u7f29\u603b\u7ed3")

    def test_build_selected_review_feedback_only_keeps_accepted_actions(self):
        review_actions = [
            {"id": "review_action_1", "title": "\u4efb\u52a1 1", "summary": "\u4efb\u52a1 1", "body": "- \u4efb\u52a1 1\n\u95ee\u9898\u7c7b\u578b\uff1a\u80cc\u666f\u7f3a\u5931"},
            {"id": "review_action_2", "title": "\u4efb\u52a1 2", "summary": "\u4efb\u52a1 2", "body": "- \u4efb\u52a1 2\n\u95ee\u9898\u7c7b\u578b\uff1a\u6807\u9898\u5197\u957f"},
        ]

        filtered = self.helpers.build_selected_review_feedback(review_actions, ["review_action_2"])

        self.assertIn("\u3010\u4fee\u6539\u4efb\u52a1\u6e05\u5355\u3011", filtered)
        self.assertIn("\u4efb\u52a1 2", filtered)
        self.assertNotIn("\u4efb\u52a1 1", filtered)

    def test_build_modification_user_content_only_injects_selected_review_feedback(self):
        selected_feedback = "\u3010\u4fee\u6539\u4efb\u52a1\u6e05\u5355\u3011\n- \u4efb\u52a1 2\uff5c\u7f29\u77ed\u6807\u9898\n\u95ee\u9898\u7c7b\u578b\uff1a\u6807\u9898\u5197\u957f"
        content = self.helpers.build_modification_user_content(
            selected_feedback,
            f"{BODY_MARKER}\n\u793a\u4f8b\u6b63\u6587",
            title_candidates=["\u6807\u9898A", "\u6807\u9898B", "\u6807\u9898C"],
        )

        self.assertIn("[Review feedback to apply]", content)
        self.assertIn("\u4efb\u52a1 2\uff5c\u7f29\u77ed\u6807\u9898", content)
        self.assertNotIn("\u4efb\u52a1 1\uff5c\u8865\u8db3\u80cc\u666f\u4ea4\u4ee3", content)
        self.assertIn("Do not absorb review suggestions", content)

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

    def test_parse_de_ai_dual_output_restores_titles_and_intro_for_highlighted_article(self):
        response = (
            f"{PURE_TITLE_MARKER}\n"
            "1. 标题A\n"
            "2. 标题B\n"
            "3. 标题C\n\n"
            f"{PURE_BODY_MARKER}\n"
            "导语第一段。\n\n"
            "导语第二段。\n\n"
            "## 第一节\n\n"
            "正文第一节。\n\n"
            f"{HIGHLIGHT_MARKER}\n"
            "## 第一节\n\n"
            "<p>高亮正文第一节。</p>"
        )

        titles, body, highlighted = self.helpers.parse_de_ai_dual_output(response)

        self.assertEqual(titles, ["标题A", "标题B", "标题C"])
        self.assertIn("导语第一段", body)
        self.assertIn(TITLE_MARKER, highlighted)
        self.assertIn("标题A", highlighted)
        self.assertIn(BODY_MARKER, highlighted)
        self.assertIn("导语第一段", highlighted)
        self.assertIn("## 第一节", highlighted)
        self.assertIn("高亮正文第一节", highlighted)

    def test_parse_de_ai_dual_output_does_not_duplicate_intro_when_highlight_already_contains_it(self):
        response = (
            f"{PURE_TITLE_MARKER}\n"
            "1. ??A\n"
            "2. ??B\n"
            "3. ??C\n\n"
            f"{PURE_BODY_MARKER}\n"
            "???????????Atlas Pro?????????????????????????????????\n\n"
            "PCGamesN????????????????????????????????\n\n"
            "## ???????????????\n\n"
            "??????????????\n\n"
            f"{HIGHLIGHT_MARKER}\n"
            "<h1>???????????Atlas Pro?????????????????????????????????</h1>\n"
            "<p>PCGamesN????????????????????????????????</p>\n"
            "<h2>???????????????</h2>\n"
            "<p>??????????????</p>"
        )

        titles, body, highlighted = self.helpers.parse_de_ai_dual_output(response)

        self.assertEqual(titles, ["??A", "??B", "??C"])
        self.assertIn("?????????????", body)
        self.assertIn(TITLE_MARKER, highlighted)
        self.assertEqual(highlighted.count("???????????Atlas Pro???????"), 1)
        self.assertEqual(highlighted.count("PCGamesN????????????????????????????????"), 1)
        self.assertNotIn(f"{BODY_MARKER}\n???????????Atlas Pro???????", highlighted)

    def test_publish_quality_gate_passes_when_structure_is_complete(self):
        article_text = (
            f"{TITLE_MARKER}\n"
            "1. Title A\n"
            "2. Title B\n"
            "3. Title C\n\n"
            f"{BODY_MARKER}\n"
            "Intro paragraph one.\n\n"
            "Intro paragraph two.\n\n"
            "## Section One\n\n"
            "Section one paragraph one.\n\n"
            "Section one paragraph two.\n\n"
            "## Section Two\n\n"
            "Section two paragraph one.\n\n"
            "Section two paragraph two.\n\n"
            "## Section Three\n\n"
            "Section three paragraph one.\n\n"
            "Section three paragraph two."
        )
        report = self.helpers.build_publish_quality_gate_report(
            article_text,
            title_candidates=["Title A", "Title B", "Title C"],
            highlighted_article="<p>Highlighted</p>",
            term_scan_summary={
                "banned_terms": 0,
                "default_replacement_terms": 0,
                "suggested_replacement_terms": 0,
            },
            target_words=1500,
        )

        self.assertEqual(report["overall_status"], "pass")
        self.assertEqual(report["fail_count"], 0)
        self.assertEqual(report["warn_count"], 0)
        self.assertEqual(report["h2_count"], 3)
        self.assertEqual(report["expected_h2_range"], (3, 5))

    def test_publish_quality_gate_blocks_missing_structure_and_banned_terms(self):
        article_text = (
            f"{BODY_MARKER}\n"
            "Direct analysis without enough setup.\n\n"
            "This paragraph still keeps a banned phrase."
        )
        report = self.helpers.build_publish_quality_gate_report(
            article_text,
            title_candidates=["Only one title"],
            highlighted_article="",
            term_scan_summary={
                "banned_terms": 1,
                "default_replacement_terms": 0,
                "suggested_replacement_terms": 0,
            },
            target_words=1500,
        )
        item_map = {item["key"]: item for item in report["items"]}

        self.assertEqual(report["overall_status"], "fail")
        self.assertGreaterEqual(report["fail_count"], 3)
        self.assertEqual(item_map["titles"]["status"], "fail")
        self.assertEqual(item_map["h2_count"]["status"], "fail")
        self.assertEqual(item_map["term_rules"]["status"], "fail")
        self.assertEqual(item_map["highlight"]["status"], "warn")


    def test_publish_quality_gate_flags_humanizer_risk_when_ai_patterns_are_obvious(self):
        article_text = (
            f"{TITLE_MARKER}\n"
            "1. Title A\n"
            "2. Title B\n"
            "3. Title C\n\n"
            f"{BODY_MARKER}\n"
            "导语第一段。\n\n"
            "导语第二段。\n\n"
            "## 第一节\n\n"
            "这不仅仅是一次更新，而是一场革命。此外，专家认为它至关重要。\n\n"
            "## 第二节\n\n"
            "众所周知，这也提醒我们未来可期。\n\n"
            "## 第三节\n\n"
            "最后一节把结构补齐。"
        )
        report = self.helpers.build_publish_quality_gate_report(
            article_text,
            title_candidates=["Title A", "Title B", "Title C"],
            highlighted_article="<p>Highlighted</p>",
            term_scan_summary={
                "banned_terms": 0,
                "default_replacement_terms": 0,
                "suggested_replacement_terms": 0,
            },
            target_words=1500,
        )
        item_map = {item["key"]: item for item in report["items"]}

        self.assertEqual(item_map["humanizer_risk"]["status"], "fail")
        self.assertIn("AI", item_map["humanizer_risk"]["detail"])

    def test_detect_auto_retry_issues_flags_missing_titles_intro_h2_and_highlight(self):
        article_text = (
            f"{BODY_MARKER}\\n"
            "Direct analysis without enough setup.\\n\\n"
            "Still no headings here."
        )
        issues = self.helpers.detect_auto_retry_issues(
            article_text,
            explicit_title_candidates=[],
            highlighted_article="",
            require_highlight=True,
            target_words=1500,
        )

        self.assertIn("titles", issues)
        self.assertIn("h2_count", issues)
        self.assertIn("highlight", issues)

    def test_auto_retry_instruction_and_notice_reflect_requested_repairs(self):
        instruction = self.helpers.build_auto_retry_instruction(
            ["titles", "h2_count", "highlight"],
            target_words=1500,
            require_highlight=True,
        )
        notice = self.helpers.build_auto_retry_notice("de_ai_generation", ["titles", "highlight"])

        self.assertIn("3-5", instruction)
        self.assertIn("\u9ad8\u4eae\u9605\u8bfb\u7248", instruction)
        self.assertIn("`##`", instruction)
        self.assertIn("\u81ea\u52a8\u8865\u8dd1", notice)
        self.assertIn("\u6807\u9898\u7ec4", notice)
        self.assertIn("\u9ad8\u4eae\u9605\u8bfb\u7248", notice)

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


        self.assertIn(TITLE_MARKER, highlighted)
        self.assertIn("Clean Title A", highlighted)
        self.assertIn("<p>", highlighted)
        self.assertIn("</p>", highlighted)

    def test_normalize_evidence_excerpt_flattens_markdown_shape(self):
        raw_excerpt = "### \u4e09\u7ef4\u627e\u7269\u6d88\u9664\uff08Match 3D\uff09\n\n- \u7b2c\u4e00\u6761\u89c2\u5bdf\n- \u7b2c\u4e8c\u6761\u89c2\u5bdf"

        normalized = self.helpers.normalize_evidence_excerpt(raw_excerpt, max_chars=200)

        self.assertNotIn("###", normalized)
        self.assertIn("\u4e09\u7ef4\u627e\u7269\u6d88\u9664", normalized)
        self.assertIn("? \u7b2c\u4e00\u6761\u89c2\u5bdf", normalized)

    def test_parse_source_packet_segments_extracts_labels_and_locators(self):
        source_content = (
            "\u3010\u6587\u7ae0\u7d20\u6750 1\u3011\u6765\u6e90\u4e8e: https://example.com/a\n"
            "\u7b2c\u4e00\u6bb5\u5185\u5bb9\u3002\n\n\u7b2c\u4e8c\u6bb5\u5185\u5bb9\u3002\n\n================\n"
            "\u3010\u89c6\u9891\u7d20\u6750 2\u3011\u6765\u6e90\u4e8e: https://example.com/video\n"
            "\u89c6\u9891\u6458\u8981\u3002\n\n================\n"
            "\u3010\u4e0a\u4f20\u6587\u4ef6\u7d20\u6750 3\u3011\u6587\u4ef6\u540d: notes.txt\n"
            "\u4e0a\u4f20\u6587\u4ef6\u6b63\u6587\u3002\n\n================\n"
            "\u3010\u4e0a\u4f20\u56fe\u7247\u7d20\u6750 4\u3011\u6587\u4ef6\u540d: chart.png\n"
            "\u56fe\u7247\u8bf4\u660e\u3002"
        )

        segments = self.helpers.parse_source_packet_segments(source_content)

        self.assertEqual([segment["source_type"] for segment in segments], ["article", "video", "upload_text", "upload_image"])
        self.assertEqual(segments[0]["source_label"], "\u6587\u7ae0\u7d20\u6750 1")
        self.assertEqual(segments[1]["source_locator"], "https://example.com/video")
        self.assertEqual(segments[2]["paragraphs"], ["\u4e0a\u4f20\u6587\u4ef6\u6b63\u6587\u3002"])

    def test_extract_claim_sentences_prefers_numeric_and_judgment_sentences(self):
        paragraph = "\u8fd9\u662f\u80cc\u666f\u94fa\u57ab\u3002Atlas Pro \u539a\u5ea6\u4ece 3 \u6beb\u7c73\u964d\u5230 1.9 \u6beb\u7c73\uff0c\u8bf4\u660e\u5b83\u5728\u8f7b\u8584\u65b9\u5411\u7ee7\u7eed\u63a8\u8fdb\u3002\u6700\u540e\u8865\u4e00\u53e5\u3002"

        claims = self.helpers.extract_claim_sentences(paragraph)

        self.assertGreaterEqual(len(claims), 1)
        self.assertIn("1.9", claims[0])

    def test_build_article_evidence_map_keeps_source_packet_and_obsidian_support(self):
        final_article = "Atlas Pro thickness drops from 3 mm to 1.9 mm, showing Razer is pushing a thinner glide experience."
        source_segments = [
            {
                "source_type": "article",
                "source_label": "\u6587\u7ae0\u7d20\u6750 1",
                "source_locator": "https://example.com/a",
                "raw_block": "",
                "paragraphs": ["Atlas Pro thickness drops from 3 mm to 1.9 mm and focuses on thinner glide experience."],
            }
        ]
        obsidian_hits = [
            {
                "title": "Glass pad trend notes",
                "path": "hardware/glass-pads.md",
                "excerpt": "Writers keep linking thinner glide hardware to a premium experience trend.",
                "matched_terms": ["thinner", "glide", "experience"],
            }
        ]

        evidence_map = self.helpers.build_article_evidence_map(final_article, source_segments, obsidian_hits)

        self.assertEqual(len(evidence_map), 1)
        source_types = evidence_map[0]["source_types"]
        self.assertIn("source_packet", source_types)
        self.assertIn("obsidian", source_types)
        self.assertLessEqual(len(evidence_map[0]["support_items"]), 3)

    def test_build_article_evidence_map_skips_low_confidence_matches(self):
        final_article = "This paragraph stays generic and does not overlap with the packet evidence."
        source_segments = [
            {
                "source_type": "article",
                "source_label": "\u6587\u7ae0\u7d20\u6750 1",
                "source_locator": "https://example.com/a",
                "raw_block": "",
                "paragraphs": ["A totally different sports recap about teams and league standings."],
            }
        ]

        evidence_map = self.helpers.build_article_evidence_map(final_article, source_segments, [])

        self.assertEqual(evidence_map, [])

    def test_refresh_evidence_map_ignores_highlighted_article_changes(self):
        session_state = SessionState({
            "final_article": "\u3010\u6b63\u6587\u3011\nAtlas Pro thickness drops from 3 mm to 1.9 mm.",
            "source_content": "\u3010\u6587\u7ae0\u7d20\u6750 1\u3011\u6765\u6e90\u4e8e: https://example.com/a\nAtlas Pro thickness drops from 3 mm to 1.9 mm.",
            "obsidian_hits": [],
            "highlighted_article": "<p>old highlight</p>",
            "evidence_map": [],
            "evidence_summary": "",
            "evidence_signature": "",
        })
        self.helpers.st.session_state = session_state

        self.helpers.refresh_evidence_map(force=True)
        first_signature = session_state["evidence_signature"]
        first_map = list(session_state["evidence_map"])

        session_state["highlighted_article"] = "<p>new highlight</p>"
        self.helpers.refresh_evidence_map(force=False)

        self.assertEqual(session_state["evidence_signature"], first_signature)
        self.assertEqual(session_state["evidence_map"], first_map)

    def test_refresh_evidence_map_works_without_obsidian_hits(self):
        session_state = SessionState({
            "final_article": "\u3010\u6b63\u6587\u3011\nAtlas Pro thickness drops from 3 mm to 1.9 mm.",
            "source_content": "\u3010\u6587\u7ae0\u7d20\u6750 1\u3011\u6765\u6e90\u4e8e: https://example.com/a\nAtlas Pro thickness drops from 3 mm to 1.9 mm.",
            "obsidian_hits": [],
            "evidence_map": [],
            "evidence_summary": "",
            "evidence_signature": "",
        })
        self.helpers.st.session_state = session_state

        self.helpers.refresh_evidence_map(force=True)

        self.assertEqual(len(session_state["evidence_map"]), 1)
        self.assertEqual(session_state["evidence_map"][0]["source_types"], ["source_packet"])
        self.assertIn("\u547d\u4e2d\u6bb5\u843d", session_state["evidence_summary"])

if __name__ == "__main__":
    unittest.main()
