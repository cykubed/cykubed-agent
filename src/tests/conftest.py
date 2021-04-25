import pytest
from fastapi_auth0 import Auth0User
from sqlalchemy.orm import Session

from db_engine import TestingSessionLocal, test_engine
from models import Base


def override_get_db():
    db = None
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        if db:
            db.close()


def override_get_user():
    yield Auth0User(sub=1)


# use this in standalone CRUD tests
@pytest.fixture(scope='function')
def db() -> Session:
    session: Session = override_get_db().__next__()
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield session
    session.rollback()



