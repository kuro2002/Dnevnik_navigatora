import httpx
import pytest

from app import transcription
from app.config import settings


def test_parse_json_text():
    r = httpx.Response(200, json={"text": "привет"})
    assert transcription.parse_transcript_response(r) == "привет"


def test_parse_segments_with_timestamps_and_speakers():
    r = httpx.Response(200, json={"segments": [{"start": 65.2, "speaker": "S1", "text": " да "},
                                               {"text": "нет"}]})
    assert transcription.parse_transcript_response(r) == "[00:01:05] S1: да\nнет"


def test_parse_plain_text():
    r = httpx.Response(200, text="просто текст", headers={"content-type": "text/plain"})
    assert transcription.parse_transcript_response(r) == "просто текст"


def test_transcribe_posts_wav(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF....")
    monkeypatch.setattr(settings, "asr_url", "http://pc:8080")
    monkeypatch.setattr(settings, "asr_model", "gigaam")
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = req.content
        return httpx.Response(200, json={"text": "расшифровка"})

    text = transcription.transcribe_file(wav, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert text == "расшифровка"
    assert seen["url"] == "http://pc:8080/v1/audio/transcriptions"
    assert b'name="model"' in seen["body"] and b"gigaam" in seen["body"]
    assert b'name="file"' in seen["body"]


def test_transcribe_unreachable(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr(settings, "asr_url", "http://pc:8080")

    def handler(req):
        raise httpx.ConnectError("off")

    with pytest.raises(transcription.TranscriberUnavailable):
        transcription.transcribe_file(wav, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_transcribe_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "asr_url", "")
    with pytest.raises(transcription.TranscriberUnavailable):
        transcription.transcribe_file(tmp_path / "x.wav")
