import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="nav-test-")
os.environ["NAV_DATA_DIR"] = _tmp
os.environ["NAV_PASSWORD"] = "secret"
os.environ["NAV_SECRET_KEY"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, init_engine  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path/'t.db'}")
    app = create_app(start_background=False)
    with TestClient(app) as c:
        r = c.post("/login", data={"password": "secret"}, follow_redirects=False)
        assert r.status_code == 303
        yield c
    from app import db as dbmod
    Base.metadata.drop_all(dbmod.engine)


class FakeBackend:
    name = "fake"
    model = "fake-1"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def complete(self, system, user, want_json=True):
        self.calls.append((system, user, want_json))
        return self.answers.pop(0)
