import uuid

import pytest

from app.api.migrations import SelectPostsRequest, select_posts


class _UpdateResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _CountResult:
    def __init__(self, value: int):
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeDB:
    def __init__(self, user_id: uuid.UUID):
        self._user_id = user_id
        self.first_update_sql = ""

    async def get(self, model, entity_id):
        return type("MigrationStub", (), {"user_id": self._user_id})()

    async def execute(self, statement):
        sql = str(statement)
        if "UPDATE tg_posts" in sql:
            self.first_update_sql = sql
            # Emulate DB effect: with an id IN clause on empty list, update touches 0 rows.
            rowcount = 0 if "tg_posts.id IN" in sql else 42
            return _UpdateResult(rowcount)
        if "count(tg_posts.id)" in sql:
            return _CountResult(0)
        return _UpdateResult(1)

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_select_posts_with_empty_post_ids_updates_nothing():
    user_id = uuid.uuid4()
    migration_id = uuid.uuid4()
    user = type("UserStub", (), {"id": user_id})()
    db = _FakeDB(user_id)

    payload = SelectPostsRequest(selected=True, post_ids=[])
    result = await select_posts(migration_id, payload, db=db, user=user)

    assert "tg_posts.id IN" in db.first_update_sql
    assert result["updated"] == 0
