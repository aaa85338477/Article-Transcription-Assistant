import ast
import types
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {"get_default_api_provider_config"}
TARGET_ASSIGNMENTS = {"YUNWU_MODEL_OPTIONS"}


def load_provider_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("provider_helpers")

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
        raise RuntimeError(f"Missing provider helper functions: {sorted(missing)}")
    return module


class ApiProviderConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_provider_helpers()

    def test_default_provider_config_is_locked_to_yunwu(self):
        config = self.helpers.get_default_api_provider_config()

        self.assertEqual(config["provider_name"], "云雾API")
        self.assertEqual(config["api_key_label"], "🔑 输入云雾API Key")
        self.assertEqual(config["base_url"], "https://yunwu.ai/v1")
        self.assertEqual(config["preferred_default_model"], "qwen3.6-plus")
        self.assertEqual(config["available_models"], self.helpers.YUNWU_MODEL_OPTIONS)

    def test_default_provider_config_returns_copy_of_model_options(self):
        config = self.helpers.get_default_api_provider_config()
        config["available_models"].append("temporary-model")

        self.assertNotIn("temporary-model", self.helpers.YUNWU_MODEL_OPTIONS)


if __name__ == "__main__":
    unittest.main()
