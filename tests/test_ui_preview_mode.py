from pathlib import Path
import ast
import json
import types
import unittest
from copy import deepcopy


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {
    "is_ui_preview_mode",
    "build_ui_preview_review_feedback",
    "build_ui_preview_step_data",
    "build_ui_preview_runtime",
    "apply_ui_preview_runtime",
    "apply_ui_preview_snapshot",
    "clear_ui_preview_snapshot",
    "sync_ui_preview_mode",
    "build_ui_preview_chat_reply",
}


class SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class FrozenDateTime:
    @staticmethod
    def now(tz=None):
        from datetime import datetime

        return datetime(2026, 5, 6, 9, 0, 0, tzinfo=tz)


def clone_json_data(value):
    return deepcopy(value)


def load_preview_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("preview_helpers")
    module.__dict__.update(
        {
            "json": json,
            "st": types.SimpleNamespace(session_state=SessionState()),
            "datetime": FrozenDateTime,
            "clone_json_data": clone_json_data,
            "DEFAULT_TTS_VOICE": "Sambert",
            "DEFAULT_REVIEWER_ROLE": "发行主编",
            "DE_AI_VARIANT_HUMANIZER": "Humanizer-zh",
            "prompts_data": {"editors": {"发行主编": {}}, "reviewers": {"发行主编": {}}},
            "build_structured_article_text": lambda title_candidates, body: body.strip(),
            "build_ui_preview_review_feedback": lambda: "示例审稿反馈：结构可再聚焦，表达可以更直接。",
            "parse_review_actions": lambda feedback: [{"id": "review_1", "label": "示例审稿项"}] if feedback else [],
            "build_blank_task_snapshot": lambda base_snapshot=None: clone_json_data(base_snapshot or {}),
            "refresh_task_record": lambda task_record, task_snapshot=None: task_record.update(
                {
                    "snapshot": clone_json_data(task_snapshot or {}),
                    "status": "completed" if (task_snapshot or {}).get("final_article") else "pending",
                    "current_step": int((task_snapshot or {}).get("current_step", 1) or 1),
                    "metrics": {"total": 1},
                }
            ),
            "apply_draft_data": lambda data: module.st.session_state.__setitem__("draft_data", clone_json_data(data)),
            "build_draft_data": lambda: clone_json_data(module.st.session_state.get("draft_data", {})),
        }
    )

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            function_module = ast.Module(body=[node], type_ignores=[])
            compiled = compile(function_module, filename=str(APP_PATH), mode="exec")
            exec(compiled, module.__dict__)

    missing = TARGET_FUNCTIONS.difference(module.__dict__)
    if missing:
        raise RuntimeError(f"Missing preview helper functions: {sorted(missing)}")
    return module


class UiPreviewModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_preview_helpers()

    def setUp(self):
        self.helpers.st.session_state.clear()

    def test_build_ui_preview_runtime_steps_are_distinct(self):
        step2 = self.helpers.build_ui_preview_runtime(2)
        self.assertIn("draft", step2)
        self.assertEqual(step2["active_task_id"], "P001")
        self.assertEqual(step2["draft"]["current_step"], 2)
        self.assertEqual(step2["draft"].get("review_feedback", ""), "")
        self.assertEqual(step2["draft"].get("final_article", ""), "")

        step6 = self.helpers.build_ui_preview_runtime(6)
        self.assertEqual(step6["draft"]["current_step"], 6)
        self.assertTrue(step6["draft"]["final_article"])
        self.assertTrue(step6["draft"]["highlighted_article"])
        self.assertTrue(step6["archived_task_queue"])
        self.assertTrue(step6["task_templates"])

    def test_sync_preview_mode_applies_and_clears_preview_snapshot(self):
        live_draft = {
            "current_step": 1,
            "live_marker": "keep",
            "title_candidates": ["Live title"],
        }
        self.helpers.apply_draft_data(live_draft)
        self.helpers.st.session_state.update(
            {
                "task_queue": [{"id": "L001", "name": "Live task", "status": "pending"}],
                "archived_task_queue": [{"id": "LA01", "name": "Archived live task", "status": "completed"}],
                "task_templates": [{"id": "TPL-LIVE", "name": "Live template"}],
                "active_task_id": "L001",
                "task_filter_status": "失败",
                "task_search_query": "live",
                "task_template_apply_targets": ["L001"],
                "ui_preview_mode_enabled": True,
                "ui_preview_step": 6,
                "_ui_preview_applied": False,
            }
        )

        self.helpers.sync_ui_preview_mode()
        self.assertEqual(self.helpers.st.session_state["draft_data"]["current_step"], 6)
        self.assertEqual(self.helpers.st.session_state["task_queue"][0]["id"], "P001")
        self.assertEqual(self.helpers.st.session_state["active_task_id"], "P001")
        self.assertEqual(self.helpers.st.session_state["task_filter_status"], "全部")
        self.assertEqual(self.helpers.st.session_state["task_search_query"], "")
        self.assertIn("live_marker", self.helpers.st.session_state["ui_preview_snapshot"]["live_backup"])

        self.helpers.st.session_state["ui_preview_mode_enabled"] = False
        self.helpers.sync_ui_preview_mode()
        self.assertEqual(self.helpers.st.session_state["draft_data"]["live_marker"], "keep")
        self.assertEqual(self.helpers.st.session_state["task_queue"][0]["id"], "L001")
        self.assertEqual(self.helpers.st.session_state["archived_task_queue"][0]["id"], "LA01")
        self.assertEqual(self.helpers.st.session_state["task_templates"][0]["id"], "TPL-LIVE")
        self.assertEqual(self.helpers.st.session_state["active_task_id"], "L001")
        self.assertEqual(self.helpers.st.session_state["ui_preview_snapshot"], {})
        self.assertFalse(self.helpers.st.session_state["_ui_preview_applied"])

    def test_preview_chat_reply_stays_on_preview_path(self):
        reply = self.helpers.build_ui_preview_chat_reply("把结论再压短一点")
        self.assertIn("测试模式", reply)
        self.assertIn("把结论再压短一点", reply)


if __name__ == "__main__":
    unittest.main()
