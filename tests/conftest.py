import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.demo import DemoSource  # noqa: E402
from app.main import create_app  # noqa: E402

AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BASIC_AUTH_PASSWORD", "pw")
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    return Settings(
        github_token="",
        owner="h-takaha",
        lab_git_dir=tmp_path / "git",
        lab_run_dir=tmp_path / "run",
        agent_ops_dir=tmp_path / "agent-ops",
        data_dir=tmp_path / "data",
        cache_ttl=60,
    )


@pytest.fixture
def source():
    return DemoSource()


@pytest.fixture
def client(settings, source):
    from fastapi.testclient import TestClient

    app = create_app(settings, source)
    return TestClient(app, headers=AUTH)


@pytest.fixture
def csrf(client):
    import re

    html = client.get("/guide").text
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
