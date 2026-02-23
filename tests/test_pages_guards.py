from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_user
from app.api.pages import router as pages_router


class DummyDB:
    async def execute(self, *args, **kwargs):
        return None


async def override_db():
    yield DummyDB()


async def guest_user():
    return None


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(pages_router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_optional_user] = guest_user
    return TestClient(app)


def test_home_for_guest_is_available():
    client = build_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "Начать бесплатно" in response.text


def test_cabinet_redirects_guest_to_home():
    client = build_client()
    response = client.get("/cabinet", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_setup_for_guest_is_available():
    client = build_client()
    response = client.get("/setup")
    assert response.status_code == 200
    assert "Единый setup" in response.text


def test_legacy_pages_are_not_found():
    client = build_client()
    assert client.get("/connect-tg").status_code == 404
    assert client.get("/connect-max").status_code == 404
    assert client.get("/channels").status_code == 404
