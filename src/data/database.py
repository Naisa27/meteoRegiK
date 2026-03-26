from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings
from src.data.db_base import Base


class Database:
    def __init__(self):
        self._db_url = settings.SQLITE_PATH
        self._engine = create_engine(self._db_url)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        self._init_database()

    def _init_database(self) -> None:
        """Создает таблицы"""
        Base.metadata.create_all(self._engine)

    def get_engine(self):
        return self._engine

    # @contextmanager
    # def get_db( self ):
    #     """
    #     Контекстный менеджер для работы с БД.
    #     Возвращает объект DBManager, а не генератор.
    #     """
    #     # with DBManager(session_factory=self._session_factory) as db:
    #     #     yield db
    #
    #     db = DBManager( self._session_factory )
    #     try:
    #         yield db
    #     except Exception:
    #         self._session_factory.rollback()
    #         raise
    #     finally:
    #         self._session_factory.close()
