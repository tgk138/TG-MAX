from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.deps import get_db
from app.api.telegram import AUTH_FLOW_COOKIE, router as tg_router


class DummyDB:
    async def execute(self, *args, **kwargs):
        class DummyResult:
            def scalar_one_or_none(self):
                return None

        return DummyResult()


async def override_db():
    yield DummyDB()


class _SentCodeTypeApp:
    pass


class _SentCode:
    def __init__(self):
        self.type = _SentCodeTypeApp()
        self.timeout = 30


class FakeTelegramService:
    def __init__(self, *args, **kwargs):
        pass

    async def send_code(self, phone: str):
        return _SentCode()

    async def disconnect(self):
        return None


@pytest.fixture(autouse=True)
def clear_auth_flows():
    from app.api import telegram

    telegram._auth_flows.clear()
    yield
    telegram._auth_flows.clear()


def build_client(monkeypatch) -> TestClient:
    monkeypatch.setattr("app.api.telegram.TelegramService", FakeTelegramService)
    app = FastAPI()
    app.include_router(tg_router)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_send_code_works_without_pre_auth_and_sets_flow_cookie(monkeypatch):
    client = build_client(monkeypatch)
    response = client.post("/api/tg/send_code", json={"phone": "+7 (999) 123-45-67"})

    assert response.status_code == 200
    data = response.json()
    assert data["needs_code"] is True
    assert data["phone_normalized"] == "+79991234567"
    assert AUTH_FLOW_COOKIE in response.headers.get("set-cookie", "")


def test_channels_endpoint_requires_auth(monkeypatch):
    client = build_client(monkeypatch)
    response = client.get("/api/tg/channels")
    assert response.status_code == 401
