import ast
import json
import re
import types
import unittest
from pathlib import Path
from urllib.parse import urlparse


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {
    "clone_json_data",
    "build_task_fallback_name",
    "normalize_title_candidates",
    "split_structured_article_sections",
    "get_article_body_text",
    "build_task_title",
    "derive_task_status",
    "build_task_queue_metrics",
    "build_task_metrics",
    "build_task_template_config",
    "apply_task_template_config",
    "build_blank_task_snapshot",
    "build_batch_export_markdown",
    "is_placeholder_task_name",
    "refresh_task_record",
    "draft_has_meaningful_content",
    "snapshots_equivalent",
    "is_task_action_blocked",
    "build_task_interrupt_notice",
}
TARGET_ASSIGNMENTS = {
    "TASK_TEMPLATE_CONFIG_KEYS",
    "TASK_STATUS_LABELS",
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


def load_task_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("task_helpers")
    module.__dict__.update({
        "json": json,
        "re": re,
        "urlparse": urlparse,
        "st": types.SimpleNamespace(session_state=SessionState()),
        "datetime": types.SimpleNamespace(now=lambda: types.SimpleNamespace(strftime=lambda fmt: "2026-04-23 10:00:00")),
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


class TaskQueueHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_task_helpers()

    def test_build_task_title_prefers_titles_then_url_summary(self):
        titled = self.helpers.build_task_title({"title_candidates": ["First title", "Second title"]})
        self.assertEqual(titled, "First title")

        from_urls = self.helpers.build_task_title({
            "article_url": "https://example.com/a\nhttps://example.com/b"
        })
        self.assertEqual(from_urls, "example.com 等2条")

    def test_derive_task_status_distinguishes_core_states(self):
        self.assertEqual(self.helpers.derive_task_status({"last_ai_error": "boom"}), "failed")
        self.assertEqual(self.helpers.derive_task_status({"final_article": "done"}), "completed")
        self.assertEqual(self.helpers.derive_task_status({"review_feedback": "needs work"}), "needs_review")
        self.assertEqual(self.helpers.derive_task_status({"current_step": 3}), "in_progress")
        self.assertEqual(self.helpers.derive_task_status({}), "pending")

    def test_template_config_and_blank_snapshot_preserve_only_config(self):
        snapshot = {
            "selected_role": "lead_editor",
            "target_article_words": 1800,
            "podcast_enabled": True,
            "final_article": "Body text",
            "review_feedback": "feedback",
            "article_versions": [{"id": "V001"}],
        }

        config = self.helpers.build_task_template_config(snapshot)
        self.assertEqual(config["selected_role"], "lead_editor")
        self.assertEqual(config["target_article_words"], 1800)
        self.assertTrue(config["podcast_enabled"])
        self.assertNotIn("final_article", config)

        updated = self.helpers.apply_task_template_config({"selected_role": "old_role", "final_article": "keep"}, config)
        self.assertEqual(updated["selected_role"], "lead_editor")
        self.assertEqual(updated["final_article"], "keep")

        blank = self.helpers.build_blank_task_snapshot(snapshot)
        self.assertEqual(blank["selected_role"], "lead_editor")
        self.assertEqual(blank["target_article_words"], 1800)
        self.assertEqual(blank["current_step"], 1)
        self.assertEqual(blank["final_article"], "")
        self.assertEqual(blank["review_feedback"], "")
        self.assertEqual(blank["article_versions"], [])

    def test_draft_restore_helpers_detect_meaningful_changes(self):
        self.assertFalse(self.helpers.draft_has_meaningful_content({"current_step": 1, "article_url": "", "draft_article": ""}))
        self.assertTrue(self.helpers.draft_has_meaningful_content({"current_step": 2}))
        self.assertTrue(self.helpers.draft_has_meaningful_content({"draft_article": "some text"}))
        self.assertTrue(self.helpers.snapshots_equivalent({"a": 1, "b": [2, 3]}, {"b": [2, 3], "a": 1}))
        self.assertFalse(self.helpers.snapshots_equivalent({"a": 1}, {"a": 2}))

    def test_interrupt_guard_requires_confirmation_before_risky_actions(self):
        self.assertTrue(self.helpers.is_task_action_blocked("draft_generation", False))
        self.assertFalse(self.helpers.is_task_action_blocked("draft_generation", True))
        self.assertFalse(self.helpers.is_task_action_blocked("", False))

        notice = self.helpers.build_task_interrupt_notice("任务 001", "draft_generation")
        self.assertIn("任务 001", notice)
        self.assertIn("写出稿", notice)

    def test_refresh_task_record_replaces_placeholder_task_names(self):
        task_record = {
            "id": "T002",
            "name": "?? 002",
            "snapshot": {
                "current_step": 1,
                "title_candidates": [],
                "article_url": "",
                "video_url": "",
                "source_content": "",
                "draft_article": "",
                "modified_article": "",
                "final_article": "",
                "review_feedback": "",
                "review_actions": [],
                "last_ai_error": "",
                "article_versions": [],
                "highlighted_article": "",
                "podcast_script_raw": "",
                "podcast_audio_path": "",
            },
        }

        refreshed = self.helpers.refresh_task_record(task_record)

        self.assertEqual(refreshed["name"], self.helpers.build_task_fallback_name("T002"))
        self.assertEqual(refreshed["status"], "pending")

    def test_queue_metrics_and_batch_export_include_completed_artifacts(self):
        tasks = [
            {
                "id": "T001",
                "name": "Task One",
                "status": "completed",
                "updated_at": "2026-04-23 10:00:00",
                "snapshot": {
                    "final_article": "Final body",
                    "highlighted_article": "<p>Highlight</p>",
                },
            },
            {
                "id": "T002",
                "name": "Task Two",
                "status": "failed",
                "updated_at": "2026-04-23 10:05:00",
                "snapshot": {
                    "draft_article": "Draft body",
                },
            },
        ]

        metrics = self.helpers.build_task_queue_metrics(tasks)
        self.assertEqual(metrics["total"], 2)
        self.assertEqual(metrics["completed"], 1)
        self.assertEqual(metrics["failed"], 1)

        export_text = self.helpers.build_batch_export_markdown([tasks[0]])
        self.assertIn("# Task One", export_text)
        self.assertIn("- 任务 ID：T001", export_text)
        self.assertIn("- 状态：已完成", export_text)
        self.assertIn("## 正文", export_text)
        self.assertIn("Final body", export_text)
        self.assertIn("## 高亮阅读版", export_text)
        self.assertIn("<p>Highlight</p>", export_text)



if __name__ == "__main__":
    unittest.main()
