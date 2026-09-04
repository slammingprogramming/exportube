import pytest

from exportube.storage.db import Database


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(tmp_path / "test.sqlite3")
    yield database
    database.close()
