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
    "build_autodrive_config_snapshot",
    "normalize_task_runtime_log",
    "append_task_runtime_log",
    "build_background_autodrive_launch_payload",
    "is_placeholder_task_name",
    "refresh_task_record",
    "sync_task_queue_runtime_from_disk",
    "update_task_run_state",
    "update_task_run_state_on_disk",
    "persist_task_snapshot_on_disk",
    "draft_has_meaningful_content",
    "snapshots_equivalent",
    "is_task_action_blocked",
    "build_task_interrupt_notice",
    "request_background_autodrive_cancel",
    "retry_background_autodrive_job",
    "should_cancel_background_autodrive",
    "recover_ai_progress_if_needed",
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
    "AUTODRIVE_ACTIVE_RUN_STATES",
    "AUTODRIVE_BACKGROUND_RUNTIME_LOG_LIMIT",
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
        self.helpers.read_task_queue_data = lambda: (_ for _ in ()).throw(RuntimeError("task queue file unavailable in this test"))
        self.helpers.write_task_queue_data = lambda payload: None
        self.helpers.queue_draft_restore = lambda snapshot: None
        self.helpers.get_task_by_id = lambda task_id: next(
            (task for task in self.helpers.st.session_state.get("task_queue", []) if task.get("id") == task_id),
            None,
        )

    def test_build_task_title_prefers_titles_then_url_summary(self):
        titled = self.helpers.build_task_title({"title_candidates": ["First title", "Second title"]})
        self.assertEqual(titled, "First title")
        from_brief = self.helpers.build_task_title({
            "brief_topic": "从快节奏崩塌到长线运营焦虑：当英雄射击与叙事驱动游戏遭遇生存危机"
        })
        self.assertTrue(from_brief.startswith("从快节奏崩塌到长线运营焦虑"))

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
            "selected_reviewer": "议题统稿编辑（深度版）",
            "target_article_words": 1800,
            "podcast_enabled": True,
            "final_article": "Body text",
            "review_feedback": "feedback",
            "article_versions": [{"id": "V001"}],
        }

        config = self.helpers.build_task_template_config(snapshot)
        self.assertEqual(config["selected_role"], "lead_editor")
        self.assertEqual(config["selected_reviewer"], "议题统稿编辑（深度版）")
        self.assertEqual(config["target_article_words"], 1800)
        self.assertTrue(config["podcast_enabled"])
        self.assertNotIn("final_article", config)

        updated = self.helpers.apply_task_template_config({"selected_role": "old_role", "final_article": "keep"}, config)
        self.assertEqual(updated["selected_role"], "lead_editor")
        self.assertEqual(updated["selected_reviewer"], "议题统稿编辑（深度版）")
        self.assertEqual(updated["final_article"], "keep")

        blank = self.helpers.build_blank_task_snapshot(snapshot)
        self.assertEqual(blank["selected_role"], "lead_editor")
        self.assertEqual(blank["selected_reviewer"], "议题统稿编辑（深度版）")
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
                "snapshot": {
                    "article_url": "https://gamigion.com/news",
                    "brief_topic": "英雄射击为何陷入同质化困境",
                    "writing_brief_summary": "Core topic: 英雄射击为何陷入同质化困境",
                    "brief_sources": ["https://www.gamespot.com/articles/demo"],
                },
            },
        ]

        hosts = self.helpers.build_task_source_hosts(tasks[0])
        self.assertEqual(hosts, ["pocketgamer.biz"])
        self.assertIn("pocketgamer.biz", self.helpers.build_task_search_haystack(tasks[0]))
        self.assertEqual([task["id"] for task in self.helpers.filter_tasks_by_query(tasks, "PocketGamer")], ["T001"])
        self.assertEqual([task["id"] for task in self.helpers.filter_tasks_by_query(tasks, "gamigion.com")], ["T002"])
        self.assertEqual([task["id"] for task in self.helpers.filter_tasks_by_query(tasks, "英雄射击")], ["T002"])
        self.assertEqual([task["id"] for task in self.helpers.filter_tasks_by_query(tasks, "gamespot.com")], ["T002"])
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

    def test_refresh_task_record_initializes_runtime_fields(self):
        task_record = {
            "id": "T003",
            "name": "Task Three",
            "snapshot": {
                "current_step": 2,
                "draft_article": "Draft",
            },
        }

        refreshed = self.helpers.refresh_task_record(task_record)

        self.assertEqual(refreshed["run_mode"], "manual")
        self.assertEqual(refreshed["run_state"], "idle")
        self.assertEqual(refreshed["run_stage"], "")
        self.assertEqual(refreshed["run_owner_token"], "")
        self.assertEqual(refreshed["last_run_error"], "")
        self.assertEqual(refreshed["run_cancel_requested"], False)
        self.assertEqual(refreshed["autodrive_config_snapshot"], {})
        self.assertEqual(refreshed["runtime_log"], [])

    def test_update_task_run_state_tracks_stage_and_config_snapshot(self):
        calls = []
        self.helpers.save_task_queue_state = lambda: calls.append("saved")
        self.helpers.st.session_state.update({
            "task_queue": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "snapshot": {
                        "current_step": 2,
                        "draft_article": "Draft body",
                    },
                }
            ],
            "active_task_id": "T001",
        })

        updated = self.helpers.update_task_run_state(
            "T001",
            run_mode="autodrive",
            run_state="running",
            run_stage="review",
            run_owner_token="token-123",
            started_at="2026-04-23 10:00:00",
            last_run_error="",
            runtime_log_message="Entering review stage.",
            autodrive_config_snapshot=self.helpers.build_autodrive_config_snapshot({
                "target_words": 1800,
                "editor_role": "发行主编",
                "editor_model": "qwen3.6-plus",
                "reviewer_role": "严审编辑",
                "reviewer_model": "gpt-5.5",
                "revision_role": "发行主编",
                "revision_model": "qwen3.6-plus",
                "de_ai_model": "deepseek-v4-pro",
                "de_ai_variant": "自然唠嗑版",
                "publish_word": True,
                "publish_feishu": True,
                "push_feishu_group": False,
            }),
            task_snapshot={
                "current_step": 3,
                "draft_article": "Updated draft",
            },
        )

        self.assertTrue(updated)
        task_record = self.helpers.st.session_state["task_queue"][0]
        self.assertEqual(task_record["run_mode"], "autodrive")
        self.assertEqual(task_record["run_state"], "running")
        self.assertEqual(task_record["run_stage"], "review")
        self.assertEqual(task_record["run_owner_token"], "token-123")
        self.assertEqual(task_record["run_started_at"], "2026-04-23 10:00:00")
        self.assertEqual(task_record["run_cancel_requested"], False)
        self.assertEqual(task_record["autodrive_config_snapshot"]["editor_role"], "发行主编")
        self.assertEqual(task_record["snapshot"]["current_step"], 3)
        self.assertEqual(task_record["snapshot"]["draft_article"], "Updated draft")
        self.assertEqual(task_record["runtime_log"][-1]["message"], "Entering review stage.")
        self.assertIn("saved", calls)

    def test_update_task_run_state_appends_runtime_log_entries(self):
        self.helpers.st.session_state.update({
            "task_queue": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "snapshot": {"current_step": 2, "draft_article": "Draft"},
                }
            ],
            "active_task_id": "T001",
        })

        updated = self.helpers.update_task_run_state(
            "T001",
            run_state="running",
            run_stage="draft",
            runtime_log_message="Entering draft stage.",
            save_queue=False,
        )

        self.assertTrue(updated)
        task_record = self.helpers.st.session_state["task_queue"][0]
        self.assertEqual(len(task_record["runtime_log"]), 1)
        self.assertEqual(task_record["runtime_log"][0]["stage"], "draft")
        self.assertEqual(task_record["runtime_log"][0]["state"], "running")
        self.assertEqual(task_record["runtime_log"][0]["message"], "Entering draft stage.")

    def test_normalize_task_runtime_log_enforces_limit_and_drops_empty_messages(self):
        raw_entries = [{"timestamp": "2026-04-23 10:00:00", "message": ""}]
        for index in range(85):
            raw_entries.append({
                "timestamp": f"2026-04-23 10:00:{index:02d}",
                "stage": "draft",
                "state": "running",
                "message": f"log-{index}",
            })

        normalized = self.helpers.normalize_task_runtime_log(raw_entries)

        self.assertEqual(len(normalized), 80)
        self.assertEqual(normalized[0]["message"], "log-5")
        self.assertEqual(normalized[-1]["message"], "log-84")

    def test_build_background_autodrive_launch_payload_keeps_runtime_inputs(self):
        payload = self.helpers.build_background_autodrive_launch_payload(
            " sk-test ",
            "https://yunwu.ai/v1",
            "qwen3.6-plus",
            ["qwen3.6-plus", "gpt-5.5"],
            enable_script=True,
            script_duration="5分钟",
        )

        self.assertEqual(payload["api_key"], "sk-test")
        self.assertEqual(payload["current_base_url"], "https://yunwu.ai/v1")
        self.assertEqual(payload["selected_model"], "qwen3.6-plus")
        self.assertEqual(payload["available_models"], ["qwen3.6-plus", "gpt-5.5"])
        self.assertTrue(payload["enable_script"])
        self.assertEqual(payload["script_duration"], "5分钟")

    def test_update_task_run_state_on_disk_updates_only_target_record(self):
        written_payloads = []
        queue_payload = {
            "active_task_id": "T001",
            "tasks": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "snapshot": {"current_step": 2, "draft_article": "A"},
                },
                {
                    "id": "T002",
                    "name": "Task Two",
                    "snapshot": {"current_step": 1, "draft_article": "B"},
                },
            ],
            "archived_tasks": [],
            "templates": [],
        }
        self.helpers.read_task_queue_data = lambda: json.loads(json.dumps(queue_payload, ensure_ascii=False))
        self.helpers.write_task_queue_data = lambda payload: written_payloads.append(payload)

        updated = self.helpers.update_task_run_state_on_disk(
            "T001",
            run_mode="autodrive",
            run_state="running",
            run_stage="draft",
            run_owner_token="token-bg",
            started_at="2026-04-23 10:00:00",
            autodrive_config_snapshot={"editor_role": "发行主编"},
            task_snapshot={"current_step": 3, "draft_article": "Updated A"},
        )

        self.assertTrue(updated)
        self.assertEqual(len(written_payloads), 1)
        persisted = written_payloads[0]
        task_one = next(item for item in persisted["tasks"] if item["id"] == "T001")
        task_two = next(item for item in persisted["tasks"] if item["id"] == "T002")
        self.assertEqual(task_one["run_state"], "running")
        self.assertEqual(task_one["run_stage"], "draft")
        self.assertEqual(task_one["run_cancel_requested"], False)
        self.assertEqual(task_one["snapshot"]["draft_article"], "Updated A")
        self.assertEqual(task_two["snapshot"]["draft_article"], "B")

    def test_persist_task_snapshot_on_disk_refreshes_target_snapshot(self):
        written_payloads = []
        queue_payload = {
            "active_task_id": "T001",
            "tasks": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "snapshot": {"current_step": 2, "draft_article": "A"},
                }
            ],
            "archived_tasks": [],
            "templates": [],
        }
        self.helpers.read_task_queue_data = lambda: json.loads(json.dumps(queue_payload, ensure_ascii=False))
        self.helpers.write_task_queue_data = lambda payload: written_payloads.append(payload)

        persisted = self.helpers.persist_task_snapshot_on_disk(
            "T001",
            {"current_step": 6, "final_article": "Final body"},
        )

        self.assertTrue(persisted)
        self.assertEqual(len(written_payloads), 1)
        task_one = written_payloads[0]["tasks"][0]
        self.assertEqual(task_one["snapshot"]["final_article"], "Final body")
        self.assertEqual(task_one["current_step"], 6)

    def test_request_background_autodrive_cancel_marks_running_task(self):
        saved = []
        self.helpers.save_task_queue_state = lambda: saved.append("saved")
        self.helpers.st.session_state.update({
            "task_queue": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "run_state": "running",
                    "run_owner_token": "token-1",
                    "snapshot": {"current_step": 2, "draft_article": "A"},
                }
            ],
        })
        self.helpers.get_task_by_id = lambda task_id: next(
            (task for task in self.helpers.st.session_state.get("task_queue", []) if task.get("id") == task_id),
            None,
        )

        ok, message = self.helpers.request_background_autodrive_cancel("T001")

        self.assertTrue(ok)
        self.assertIn("停止", message)
        task_record = self.helpers.st.session_state["task_queue"][0]
        self.assertTrue(task_record["run_cancel_requested"])
        self.assertIn("saved", saved)

    def test_should_cancel_background_autodrive_requires_matching_owner_token(self):
        queue_payload = {
            "tasks": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "run_state": "running",
                    "run_owner_token": "token-bg",
                    "run_cancel_requested": True,
                    "snapshot": {"current_step": 2},
                }
            ]
        }
        self.helpers.read_task_queue_data = lambda: json.loads(json.dumps(queue_payload, ensure_ascii=False))

        self.assertTrue(self.helpers.should_cancel_background_autodrive("T001", "token-bg"))
        self.assertFalse(self.helpers.should_cancel_background_autodrive("T001", "other-token"))

    def test_retry_background_autodrive_job_requeues_failed_task_with_saved_config(self):
        launches = []
        self.helpers.start_background_autodrive_job = (
            lambda task_id, launch_payload, autodrive_config_snapshot=None, task_snapshot=None:
            launches.append((task_id, launch_payload, autodrive_config_snapshot, task_snapshot)) or (True, "token-bg")
        )
        self.helpers.st.session_state.update({
            "task_queue": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "run_state": "failed",
                    "autodrive_config_snapshot": {
                        "editor_role": "发行主编",
                        "editor_model": "qwen3.6-plus",
                    },
                    "snapshot": {
                        "article_url": "https://example.com/article",
                        "source_content": "Source body",
                    },
                }
            ],
        })
        self.helpers.get_task_by_id = lambda task_id: next(
            (task for task in self.helpers.st.session_state.get("task_queue", []) if task.get("id") == task_id),
            None,
        )

        ok, message = self.helpers.retry_background_autodrive_job("T001", {"api_key": "sk-test"})

        self.assertTrue(ok)
        self.assertEqual(message, "token-bg")
        self.assertEqual(len(launches), 1)
        task_id, launch_payload, config_snapshot, task_snapshot = launches[0]
        self.assertEqual(task_id, "T001")
        self.assertEqual(launch_payload["api_key"], "sk-test")
        self.assertEqual(config_snapshot["editor_role"], "发行主编")
        self.assertEqual(task_snapshot["source_content"], "Source body")

    def test_retry_background_autodrive_job_rejects_missing_config_or_active_runs(self):
        self.helpers.start_background_autodrive_job = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not launch"))
        self.helpers.st.session_state.update({
            "task_queue": [
                {
                    "id": "T001",
                    "name": "Task One",
                    "run_state": "running",
                    "autodrive_config_snapshot": {"editor_role": "发行主编"},
                    "snapshot": {"source_content": "Source body"},
                },
                {
                    "id": "T002",
                    "name": "Task Two",
                    "run_state": "failed",
                    "autodrive_config_snapshot": {},
                    "snapshot": {"source_content": "Source body"},
                },
            ],
        })
        self.helpers.get_task_by_id = lambda task_id: next(
            (task for task in self.helpers.st.session_state.get("task_queue", []) if task.get("id") == task_id),
            None,
        )

        ok_running, message_running = self.helpers.retry_background_autodrive_job("T001", {"api_key": "sk-test"})
        ok_missing_config, message_missing_config = self.helpers.retry_background_autodrive_job("T002", {"api_key": "sk-test"})

        self.assertFalse(ok_running)
        self.assertIn("already running", message_running.lower())
        self.assertFalse(ok_missing_config)
        self.assertIn("config", message_missing_config.lower())

    def test_recover_ai_progress_if_needed_advances_step_to_last_completed_target(self):
        cleared = []
        self.helpers.is_ui_preview_mode = lambda: False
        self.helpers.clear_ai_stage_checkpoint = lambda: cleared.append("cleared")
        self.helpers.format_ai_stage_name = lambda stage_name: f"阶段:{stage_name}"
        self.helpers.st.rerun = lambda: None
        self.helpers.st.session_state.update({
            "current_step": 5,
            "last_completed_ai_stage": "de_ai_generation",
            "last_completed_ai_target_step": 6,
            "recovered_ai_notice": "",
        })

        self.helpers.recover_ai_progress_if_needed()

        self.assertEqual(self.helpers.st.session_state["current_step"], 6)
        self.assertIn("第 6 步", self.helpers.st.session_state["recovered_ai_notice"])
        self.assertEqual(cleared, ["cleared"])

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
