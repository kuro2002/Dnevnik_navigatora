import json

import httpx
import pytest

from app.analysis.backends import AnalyzerUnavailable, OllamaBackend, YandexGPTBackend


def test_ollama_request_shape():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message": {"content": "{\"a\":1}"}})

    be = OllamaBackend(url="http://pc:11434/", model="qwen3:14b", num_ctx=16384,
                       client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert be.complete("sys", "usr") == "{\"a\":1}"
    assert seen["url"] == "http://pc:11434/api/chat"
    assert seen["body"]["format"] == "json"
    assert seen["body"]["options"]["num_ctx"] == 16384
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_ollama_unreachable():
    def handler(req):
        raise httpx.ConnectError("down")

    be = OllamaBackend(url="http://pc:11434", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AnalyzerUnavailable):
        be.complete("s", "u")


def test_yandex_requires_config():
    with pytest.raises(AnalyzerUnavailable):
        YandexGPTBackend(api_key="", folder_id="").complete("s", "u")


def test_yandex_request_shape():
    seen = {}

    def handler(req):
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"result": {"alternatives": [{"message": {"role": "assistant", "text": "ответ"}}]}})

    be = YandexGPTBackend(api_key="k", folder_id="f1", model="yandexgpt/latest",
                          client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert be.complete("sys", "usr") == "ответ"
    assert seen["headers"]["authorization"] == "Api-Key k"
    assert seen["body"]["modelUri"] == "gpt://f1/yandexgpt/latest"
    assert seen["body"]["messages"][1] == {"role": "user", "text": "usr"}
