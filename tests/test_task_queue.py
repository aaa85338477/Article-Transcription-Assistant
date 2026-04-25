from datetime import datetime as RealDateTime, timedelta
from pathlib import Path
import ast
import json
import re
import types
import unittest
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
    "get_next_entity_id",
    "build_task_source_hosts",
    "build_task_search_haystack",
    "filter_tasks_by_query",
    "build_blank_task_snapshot",
    "build_batch_export_markdown",
    "is_placeholder_task_name",
    "refresh_task_record",
    "draft_has_meaningful_content",
    "snapshots_equivalent",
    "is_task_action_blocked",
    "build_task_interrupt_notice",
    "delete_task",
    "bulk_delete_tasks",
    "auto_archive_completed_tasks",
    "restore_archived_task",
    "resume_task",
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


class FrozenDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 4, 23, 10, 0, 0, tzinfo=tz)


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
        "datetime": FrozenDateTime,
        "timedelta": timedelta,
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

    def setUp(self):
        self.helpers.st.session_state.clear()
        self.helpers.TASK_QUEUE_NOTICE_KEY = "_task_queue_notice"
        self.helpers.init_task_queue_state = lambda: None
        self.helpers.save_task_queue_state = lambda: None
        self.helpers.queue_draft_restore = lambda snapshot: None
        self.helpers.get_task_by_id = lambda task_id: next(
            (task for task in self.helpers.st.session_state.get("task_queue", []) if task.get("id") == task_id),
            None,
        )

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

        notice = self.helpers.build_task_interrupt_notice("Task 001", "draft_generation")
        self.assertIn("Task 001", notice)
        self.assertTrue(len(notice) > len("Task 001"))

    def test_search_helpers_cover_name_host_and_status_filter(self):
        tasks = [
            {
                "id": "T001",
                "name": "PocketGamer interview",
                "status": "completed",
                "snapshot": {"article_url": "https://www.pocketgamer.biz/feature"},
            },
            {
                "id": "T002",
                "name": "Gamigion roundup",
                "status": "pending",
                "snapshot": {"article_url": "https://gamigion.com/news"},
            },
        ]

        hosts = self.helpers.build_task_source_hosts(tasks[0])
        self.assertEqual(hosts, ["pocketgamer.biz"])
        self.assertIn("pocketgamer.biz", self.helpers.build_task_search_haystack(tasks[0]))
        self.assertEqual([task["id"] for task in self.helpers.filter_tasks_by_query(tasks, "PocketGamer")], ["T001"])
        self.assertEqual([task["id"] for task in self.helpers.filter_tasks_by_query(tasks, "gamigion.com")], ["T002"])
        self.assertEqual([task["id"] for task in self.helpers.filter_tasks_by_query(tasks, "pocketgamer", status_filter="completed")], ["T001"])
        self.assertEqual(self.helpers.filter_tasks_by_query(tasks, "pocketgamer", status_filter="pending"), [])

    def test_resume_task_restores_saved_snapshot_before_returning_to_step(self):
        calls = []
        self.helpers.queue_draft_restore = lambda snapshot: calls.append(snapshot)
        self.helpers.st.session_state.update({
            "active_task_id": "T001",
            "last_ai_error": "boom",
            "task_queue": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "current_step": 3,
                    "snapshot": {
                        "current_step": 2,
                        "draft_article": "saved draft",
                        "review_feedback": "",
                        "last_ai_error": "old error",
                    },
                },
            ],
        })

        resumed = self.helpers.resume_task("T001")

        self.assertTrue(resumed)
        self.assertEqual(self.helpers.st.session_state["active_task_id"], "T001")
        self.assertEqual(self.helpers.st.session_state["last_ai_error"], "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["draft_article"], "saved draft")
        self.assertEqual(calls[0]["current_step"], 3)
        self.assertEqual(calls[0]["last_ai_error"], "")

    def test_delete_task_keeps_queue_alive_and_switches_when_deleting_active_task(self):
        calls = []
        self.helpers.save_task_queue_state = lambda: calls.append("saved")
        self.helpers.queue_draft_restore = lambda snapshot: calls.append(("restored", snapshot.get("marker")))
        self.helpers.st.session_state.update({
            "active_task_id": "T001",
            "task_queue": [
                {"id": "T001", "name": "Task One", "snapshot": {"marker": "one"}},
                {"id": "T002", "name": "Task Two", "snapshot": {"marker": "two"}},
            ],
        })

        deleted = self.helpers.delete_task("T001")

        self.assertTrue(deleted)
        self.assertEqual(self.helpers.st.session_state["active_task_id"], "T002")
        self.assertEqual([task["id"] for task in self.helpers.st.session_state["task_queue"]], ["T002"])
        self.assertTrue(self.helpers.st.session_state["_task_queue_notice"].endswith("Task One"))
        self.assertIn("saved", calls)
        self.assertIn(("restored", "two"), calls)

    def test_delete_task_rejects_removing_last_task(self):
        self.helpers.save_task_queue_state = lambda: (_ for _ in ()).throw(AssertionError("should not save"))
        self.helpers.queue_draft_restore = lambda snapshot: (_ for _ in ()).throw(AssertionError("should not restore"))
        self.helpers.st.session_state.update({
            "active_task_id": "T001",
            "task_queue": [
                {"id": "T001", "name": "Solo Task", "snapshot": {"marker": "one"}},
            ],
        })

        deleted = self.helpers.delete_task("T001")

        self.assertFalse(deleted)
        self.assertEqual([task["id"] for task in self.helpers.st.session_state["task_queue"]], ["T001"])

    def test_bulk_delete_tasks_cleans_targets_and_creates_blank_when_queue_would_empty(self):
        calls = []
        self.helpers.save_task_queue_state = lambda: calls.append("saved")
        self.helpers.queue_draft_restore = lambda snapshot: calls.append(("restored", snapshot.get("current_step"), snapshot.get("final_article", "")))
        self.helpers.st.session_state.update({
            "active_task_id": "T001",
            "task_queue": [
                {"id": "T001", "name": "Done", "status": "completed", "snapshot": {"current_step": 6, "final_article": "done"}},
                {"id": "T002", "name": "Failed", "status": "failed", "snapshot": {"current_step": 3, "last_ai_error": "boom"}},
            ],
        })

        removed = self.helpers.bulk_delete_tasks(["T001", "T002"])

        self.assertEqual(removed, 2)
        self.assertEqual(len(self.helpers.st.session_state["task_queue"]), 1)
        placeholder = self.helpers.st.session_state["task_queue"][0]
        self.assertTrue(placeholder["id"].startswith("T"))
        self.assertEqual(placeholder["status"], "pending")
        self.assertEqual(self.helpers.st.session_state["active_task_id"], placeholder["id"])
        self.assertIn("saved", calls)
        self.assertTrue(any(call[0] == "restored" for call in calls if isinstance(call, tuple)))

    def test_auto_archive_completed_tasks_moves_old_items_but_skips_active(self):
        self.helpers.save_task_queue_state = lambda: None
        self.helpers.st.session_state.update({
            "active_task_id": "T003",
            "task_queue": [
                {"id": "T001", "name": "Old complete", "status": "completed", "updated_at": "2026-04-10 09:00:00", "snapshot": {"final_article": "old"}},
                {"id": "T002", "name": "Recent complete", "status": "completed", "updated_at": "2026-04-22 09:00:00", "snapshot": {"final_article": "recent"}},
                {"id": "T003", "name": "Active complete", "status": "completed", "updated_at": "2026-04-01 09:00:00", "snapshot": {"final_article": "active"}},
                {"id": "T004", "name": "In progress", "status": "in_progress", "updated_at": "2026-04-01 09:00:00", "snapshot": {"draft_article": "draft"}},
            ],
            "archived_task_queue": [],
        })

        moved = self.helpers.auto_archive_completed_tasks(now=FrozenDateTime(2026, 4, 23, 10, 0, 0), retention_days=7)

        self.assertEqual(moved, 1)
        self.assertEqual([task["id"] for task in self.helpers.st.session_state["task_queue"]], ["T002", "T003", "T004"])
        self.assertEqual([task["id"] for task in self.helpers.st.session_state["archived_task_queue"]], ["T001"])
        self.assertEqual(self.helpers.st.session_state["archived_task_queue"][0]["archive_reason"], "auto_completed_retention")

    def test_restore_archived_task_moves_item_back_and_restores_snapshot(self):
        calls = []
        self.helpers.save_task_queue_state = lambda: calls.append("saved")
        self.helpers.queue_draft_restore = lambda snapshot: calls.append(("restored", snapshot.get("marker")))
        self.helpers.st.session_state.update({
            "active_task_id": "T001",
            "task_queue": [{"id": "T001", "name": "Active", "snapshot": {"marker": "active"}, "status": "pending"}],
            "archived_task_queue": [{"id": "T009", "name": "Archived", "snapshot": {"marker": "archived"}, "status": "completed", "updated_at": "2026-04-01 09:00:00", "archived_at": "2026-04-23 10:00:00", "archive_reason": "auto_completed_retention"}],
        })

        restored = self.helpers.restore_archived_task("T009")

        self.assertTrue(restored)
        self.assertEqual(self.helpers.st.session_state["active_task_id"], "T009")
        self.assertEqual(sorted(task["id"] for task in self.helpers.st.session_state["task_queue"]), ["T001", "T009"])
        self.assertEqual(self.helpers.st.session_state["archived_task_queue"], [])
        self.assertIn(("restored", "archived"), calls)

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
        self.assertIn("Final body", export_text)
        self.assertIn("<p>Highlight</p>", export_text)


if __name__ == "__main__":
    unittest.main()
