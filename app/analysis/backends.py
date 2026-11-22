"""Подключения к языковым моделям. Все разборщики реализуют один метод complete()."""
from __future__ import annotations

import httpx

from ..config import settings


class AnalyzerUnavailable(RuntimeError):
    """Модель недоступна (компьютер выключен, нет ключа и т.п.)."""


class LLMBackend:
    name = "base"
    model = ""

    def complete(self, system: str, user: str, want_json: bool = True) -> str:  # pragma: no cover
        raise NotImplementedError


class OllamaBackend(LLMBackend):
    """Ollama на компьютере психолога, доступная по адресу в сети Tailscale."""
    name = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None,
                 num_ctx: int | None = None, client: httpx.Client | None = None):
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.num_ctx = num_ctx or settings.ollama_num_ctx
        self.client = client or httpx.Client(timeout=httpx.Timeout(900.0, connect=10.0))

    def complete(self, system: str, user: str, want_json: bool = True) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {"num_ctx": self.num_ctx, "temperature": 0.2},
        }
        if want_json:
            body["format"] = "json"
        try:
            r = self.client.post(f"{self.url}/api/chat", json=body)
        except httpx.ConnectError as e:
            raise AnalyzerUnavailable(
                f"Не удалось подключиться к Ollama по адресу {self.url}. "
                "Включён ли компьютер и запущены ли Ollama и Tailscale?") from e
        r.raise_for_status()
        return r.json()["message"]["content"]


class YandexGPTBackend(LLMBackend):
    name = "yandex"
    endpoint = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(self, api_key: str | None = None, folder_id: str | None = None,
                 model: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key if api_key is not None else settings.yandex_api_key
        self.folder_id = folder_id if folder_id is not None else settings.yandex_folder_id
        self.model = model or settings.yandex_model
        self.client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))

    def complete(self, system: str, user: str, want_json: bool = True) -> str:
        if not self.api_key or not self.folder_id:
            raise AnalyzerUnavailable(
                "YandexGPT не настроен: укажите NAV_YANDEX_API_KEY и NAV_YANDEX_FOLDER_ID.")
        body = {
            "modelUri": f"gpt://{self.folder_id}/{self.model}",
            "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": "8000"},
            "messages": [{"role": "system", "text": system},
                         {"role": "user", "text": user}],
        }
        r = self.client.post(self.endpoint, json=body, headers={
            "Authorization": f"Api-Key {self.api_key}",
            "x-folder-id": self.folder_id,
        })
        if r.status_code >= 400:
            raise RuntimeError(f"YandexGPT ответил {r.status_code}: {r.text[:500]}")
        return r.json()["result"]["alternatives"][0]["message"]["text"]


def get_backend(name: str) -> LLMBackend:
    if name == "ollama":
        return OllamaBackend()
    if name == "yandex":
        return YandexGPTBackend()
    raise ValueError(f"Нет автоматического разборщика «{name}»")
