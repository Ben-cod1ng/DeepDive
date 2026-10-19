from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from deepdive.api import create_app
from deepdive.config import Settings, LOCAL_HOSTS
from deepdive.hosting import prepare_storage
from .test_workflow import service


def test_railway_host_and_auth_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr("deepdive.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DEEPDIVE_API_TOKEN_FILE", raising=False)
    monkeypatch.setenv("DEEPDIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEEPDIVE_API_TOKEN", "a" * 32)
    monkeypatch.setenv("RAILWAY_PRIVATE_DOMAIN", "deepdive.railway.internal")
    settings = Settings.load()
    s = service(tmp_path)
    try:
        with TestClient(create_app(s, settings), base_url="http://deepdive.railway.internal") as client:
            assert client.get("/health").status_code == 200
            assert client.get("/health", headers={"Host": "healthcheck.railway.app"}).status_code == 200
            assert client.get("/health", headers={"Host": "unexpected.example"}).status_code == 400
            assert client.get("/sessions").status_code == 401
            assert client.get("/sessions", headers={"Authorization": "Bearer " + "a" * 32}).status_code == 200
            assert client.get("/sessions", headers={"Authorization": "Bearer " + "a" * 32,
                                                     "Origin": "https://unexpected.example"}).status_code == 403
    finally:
        s.close()
    monkeypatch.delenv("RAILWAY_PRIVATE_DOMAIN")
    assert Settings.load().allowed_hosts == LOCAL_HOSTS
    monkeypatch.setenv("RAILWAY_PRIVATE_DOMAIN", "*")
    with pytest.raises(ValueError, match="Invalid RAILWAY_PRIVATE_DOMAIN"):
        Settings.load()


def test_railway_requires_token_and_persistent_volume(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "test")
    monkeypatch.setenv("RAILWAY_PRIVATE_DOMAIN", "deepdive.railway.internal")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setattr("deepdive.hosting.os.geteuid", lambda: 10001, raising=False)
    settings = Settings(Path("/data"))
    with pytest.raises(ValueError, match="DEEPDIVE_API_TOKEN"):
        prepare_storage(settings)
    settings.api_token = "a" * 32
    with pytest.raises(ValueError, match="Attach a Railway volume"):
        prepare_storage(settings)
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    prepare_storage(settings)


def test_root_startup_relinquishes_privileges_before_serving(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    monkeypatch.setattr("deepdive.hosting.os.geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(Path, "is_symlink", lambda _: False)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    calls = []
    for name in ("chown", "setgroups", "setgid", "setuid"):
        monkeypatch.setattr("deepdive.hosting.os." + name,
                            lambda *args, operation=name: calls.append((operation, args)), raising=False)
    with pytest.raises(ValueError, match="only for the /data"):
        prepare_storage(Settings(Path("/app")))
    assert calls == []
    prepare_storage(Settings(Path("/data")))
    assert calls == [("chown", (Path("/data"), 10001, 10001)), ("setgroups", ([],)),
                     ("setgid", (10001,)), ("setuid", (10001,))]


@pytest.mark.parametrize("code", ["", "too-short"])
def test_hosted_ui_fails_closed_without_demo_code(monkeypatch, code):
    monkeypatch.setattr("deepdive.ui_access.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "test")
    monkeypatch.setenv("DEEPDIVE_DEMO_ACCESS_CODE", code)
    def forbidden(*a, **k):
        pytest.fail("Unauthenticated browser must not initialize the API client")
    monkeypatch.setattr("deepdive.client.Client.__init__", forbidden)
    app = AppTest.from_file("streamlit_app.py", default_timeout=30).run()
    assert not app.exception
    assert "not configured" in app.error[0].value


def test_demo_login_logout_and_code_rotation(monkeypatch):
    monkeypatch.setattr("deepdive.ui_access.load_dotenv", lambda *a, **k: None)
    code = "test-demo-code-" + "x" * 24
    monkeypatch.setenv("DEEPDIVE_DEMO_ACCESS_CODE", code)
    app = AppTest.from_string("""
import streamlit as st
from deepdive.ui_access import require_demo_access
require_demo_access()
st.success('Workspace unlocked')
""").run()
    assert not app.success
    app.text_input[0].set_value("incorrect")
    app.button[0].click().run()
    assert not app.success and "incorrect" in app.error[0].value
    app.text_input[0].set_value(code)
    app.button[0].click().run()
    assert not app.exception and app.success[0].value == "Workspace unlocked"
    next(b for b in app.button if b.label == "Sign out").click().run()
    assert not app.success
    app.text_input[0].set_value(code)
    app.button[0].click().run()
    assert app.success
    monkeypatch.setenv("DEEPDIVE_DEMO_ACCESS_CODE", code + "-rotated")
    app.run()
    assert not app.success and app.text_input[0].label == "Demo access code"
