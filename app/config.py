"""Настройки приложения из переменных окружения (префикс NAV_)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"NAV_{name}", default)


@dataclass
class Settings:
    password: str = field(default_factory=lambda: _env("PASSWORD", ""))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-secret"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    worker_token: str = field(default_factory=lambda: _env("WORKER_TOKEN", ""))

    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen3:14b"))
    ollama_num_ctx: int = field(default_factory=lambda: int(_env("OLLAMA_NUM_CTX", "16384")))

    yandex_api_key: str = field(default_factory=lambda: _env("YANDEX_API_KEY", ""))
    yandex_folder_id: str = field(default_factory=lambda: _env("YANDEX_FOLDER_ID", ""))
    yandex_model: str = field(default_factory=lambda: _env("YANDEX_MODEL", "yandexgpt/latest"))

    # Транскрибация: сервер audio.cpp (audiocpp_server) на компьютере психолога, адрес в Tailscale
    asr_url: str = field(default_factory=lambda: _env("ASR_URL", ""))
    asr_model: str = field(default_factory=lambda: _env("ASR_MODEL", ""))
    asr_language: str = field(default_factory=lambda: _env("ASR_LANGUAGE", "ru"))
    asr_file_field: str = field(default_factory=lambda: _env("ASR_FILE_FIELD", "file"))
    asr_retry_minutes: int = field(default_factory=lambda: int(_env("ASR_RETRY_MINUTES", "10")))
    ffmpeg: str = field(default_factory=lambda: _env("FFMPEG", "ffmpeg"))

    chunk_chars: int = field(default_factory=lambda: int(_env("CHUNK_CHARS", "24000")))

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'navigator.db'}"

    @property
    def audio_dir(self) -> Path:
        p = self.data_dir / "audio"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
