import ast
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TARGET_FUNCTIONS = {"extract_article_content_with_fallback"}


def load_article_helpers():
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    module = types.ModuleType("article_helpers")
    module.__dict__.update({
        "requests": requests,
        "trafilatura": types.SimpleNamespace(extract=lambda html: "direct body"),
        "BeautifulSoup": BeautifulSoup,
        "urlparse": urlparse,
        "re": __import__("re"),
    })

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            function_module = ast.Module(body=[node], type_ignores=[])
            compiled = compile(function_module, filename=str(APP_PATH), mode="exec")
            exec(compiled, module.__dict__)

    missing = TARGET_FUNCTIONS.difference(module.__dict__)
    if missing:
        raise RuntimeError(f"Missing helper functions: {sorted(missing)}")
    return module


class FakeResponse:
    def __init__(self, *, status_code=200, text="", content=None, headers=None, url="https://example.com/article"):
        self.status_code = status_code
        self.text = text
        self.content = content if content is not None else (text or "").encode("utf-8")
        self.headers = headers or {}
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Client Error: Unsupported Media Type for url: {self.url}"
            )


class ArticleExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_article_helpers()

    def test_extract_article_content_falls_back_when_origin_returns_415(self):
        url = "https://www.gamigion.com/traditional-casual-games-are-having-a-tough-time/"
        origin_response = FakeResponse(status_code=415, url=url)
        fallback_response = FakeResponse(
            status_code=200,
            text="Fallback article body " * 10,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            url=f"https://r.jina.ai/{url}",
        )

        with mock.patch.object(self.helpers.requests, "get", side_effect=[origin_response, fallback_response]) as mocked_get:
            text, images, error = self.helpers.extract_article_content_with_fallback(url)

        self.assertIsNone(error)
        self.assertEqual(images, [])
        self.assertIn("Fallback article body", text)
        self.assertEqual(mocked_get.call_count, 2)
        self.assertEqual(mocked_get.call_args_list[0].kwargs["timeout"], 15)
        self.assertEqual(mocked_get.call_args_list[1].args[0], f"https://r.jina.ai/{url}")
        self.assertEqual(mocked_get.call_args_list[1].kwargs["headers"]["Accept"], "text/plain")

    def test_extract_article_content_prefers_utf8_bytes_for_jina_fallback(self):
        url = "https://www.gamigion.com/how-royal-kingdom-is-scaling-with-interstitials-and-rewarded-ads/"
        origin_response = FakeResponse(status_code=415, url=url)
        correct_text = "Loader\n这是一段正常中文内容。" * 6
        mojibake_text = correct_text.encode("utf-8").decode("latin-1")
        fallback_response = FakeResponse(
            status_code=200,
            text=mojibake_text,
            content=correct_text.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            url=f"https://r.jina.ai/{url}",
        )

        with mock.patch.object(self.helpers.requests, "get", side_effect=[origin_response, fallback_response]):
            text, images, error = self.helpers.extract_article_content_with_fallback(url)

        self.assertIsNone(error)
        self.assertEqual(images, [])
        self.assertIn("正常中文内容", text)
        self.assertNotIn("è¿™", text)


if __name__ == "__main__":
    unittest.main()
