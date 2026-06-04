import ast
import types
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {
    "get_reviewer_prompt_map",
    "sync_reviewer_prompt_config",
    "build_autodrive_default_config",
    "normalize_autodrive_config",
    "sanitize_editor_prompt",
    "build_modification_role_instruction",
}
TARGET_ASSIGNMENTS = {
    "DEFAULT_REVIEWER_ROLE",
    "DEFAULT_REVIEWER_PLANNED_DEEP_ROLE",
    "DEFAULT_REVIEWER_PLANNED_NEWS_ROLE",
    "DEFAULT_REVIEWER_PROMPTS",
    "DE_AI_VARIANTS",
    "DE_AI_VARIANT_DEFAULT",
    "DE_AI_VARIANT_COMMUNITY",
    "DE_AI_VARIANT_CHAT",
    "DE_AI_VARIANT_HUMANIZER",
}


class SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def load_autodrive_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("autodrive_phase1_helpers")
    module.__dict__.update(
        {
            "st": types.SimpleNamespace(session_state=SessionState()),
        }
    )

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


class AutodrivePhase1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_autodrive_helpers()

    def setUp(self):
        self.helpers.st.session_state = SessionState()

    def test_build_autodrive_default_config_prefers_current_state(self):
        prompts_data = {
            "editors": {"发行主编": "prompt-a", "深度作者": "prompt-b"},
            "reviewers": {"严审编辑": "review-a", "结构审稿": "review-b"},
            "default_reviewer_role": "结构审稿",
        }
        self.helpers.st.session_state.update(
            {
                "selected_role": "深度作者",
                "selected_reviewer": "严审编辑",
                "target_article_words": 2200,
                "de_ai_model": "glm-5",
                "de_ai_variant": self.helpers.DE_AI_VARIANT_CHAT,
            }
        )

        config = self.helpers.build_autodrive_default_config(
            prompts_data,
            available_models=["qwen3.6-plus", "gpt-5.5"],
            de_ai_models=["deepseek-v4-pro", "glm-5"],
        )

        self.assertEqual(config["target_words"], 2200)
        self.assertEqual(config["editor_role"], "深度作者")
        self.assertEqual(config["editor_model"], "qwen3.6-plus")
        self.assertEqual(config["reviewer_role"], "严审编辑")
        self.assertEqual(config["reviewer_model"], "qwen3.6-plus")
        self.assertEqual(config["revision_role"], "深度作者")
        self.assertEqual(config["revision_model"], "qwen3.6-plus")
        self.assertEqual(config["de_ai_model"], "glm-5")
        self.assertEqual(config["de_ai_variant"], self.helpers.DE_AI_VARIANT_CHAT)
        self.assertTrue(config["publish_word"])
        self.assertTrue(config["publish_feishu"])
        self.assertFalse(config["push_feishu_group"])

    def test_normalize_autodrive_config_repairs_invalid_values(self):
        prompts_data = {
            "editors": {"发行主编": "prompt-a", "深度作者": "prompt-b"},
            "reviewers": {"严审编辑": "review-a"},
            "default_reviewer_role": "严审编辑",
        }
        config = {
            "target_words": 0,
            "editor_role": "不存在",
            "editor_model": "bad-model",
            "reviewer_role": "坏审稿人",
            "reviewer_model": "bad-model",
            "revision_role": "坏改稿人",
            "revision_model": "bad-model",
            "de_ai_model": "bad-model",
            "de_ai_variant": "bad-variant",
            "publish_word": "",
            "publish_feishu": None,
            "push_feishu_group": 1,
        }

        normalized = self.helpers.normalize_autodrive_config(
            config,
            prompts_data,
            available_models=["qwen3.6-plus", "gpt-5.5"],
            de_ai_models=["deepseek-v4-pro", "glm-5"],
        )

        self.assertEqual(normalized["target_words"], 1500)
        self.assertEqual(normalized["editor_role"], "发行主编")
        self.assertEqual(normalized["editor_model"], "qwen3.6-plus")
        self.assertEqual(normalized["reviewer_role"], "严审编辑")
        self.assertEqual(normalized["reviewer_model"], "qwen3.6-plus")
        self.assertEqual(normalized["revision_role"], "发行主编")
        self.assertEqual(normalized["revision_model"], "qwen3.6-plus")
        self.assertEqual(normalized["de_ai_model"], "deepseek-v4-pro")
        self.assertEqual(normalized["de_ai_variant"], self.helpers.DE_AI_VARIANT_DEFAULT)
        self.assertFalse(normalized["publish_word"])
        self.assertFalse(normalized["publish_feishu"])
        self.assertTrue(normalized["push_feishu_group"])

    def test_build_modification_role_instruction_includes_reviser_persona(self):
        role_block = self.helpers.build_modification_role_instruction(
            "改稿主编",
            "# Role: 改稿主编\n你偏好更冷静、短句、判断先行。",
        )

        self.assertIn("当前修改稿执行角色：改稿主编", role_block)
        self.assertIn("你偏好更冷静、短句、判断先行。", role_block)


if __name__ == "__main__":
    unittest.main()
