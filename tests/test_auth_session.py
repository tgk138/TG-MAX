from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.api.deps import get_db
from app.config import settings


class DummyDB:
    async def execute(self, *args, **kwargs):
        return None

    async def commit(self):
        return None


async def override_db():
    yield DummyDB()


def test_logout_clears_cookie_and_deletes_session(monkeypatch):
    called = {"session_key": None}

    async def fake_delete_session_by_key(db, session_key: str):
        called["session_key"] = session_key

    monkeypatch.setattr("app.api.auth.delete_session_by_key", fake_delete_session_by_key)

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.cookies.set(settings.SESSION_COOKIE_NAME, "session-abc", path="/")

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert called["session_key"] == "session-abc"
    assert settings.SESSION_COOKIE_NAME in response.headers.get("set-cookie", "")
