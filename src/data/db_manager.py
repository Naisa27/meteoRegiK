from src.repositories.rp5 import Rp5FindDoubleRepository, Rp5Repository


class DBManager:
    """
    Контекстный менеджер для работы с сессией

    """

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def __enter__(self):
        self.session = self.session_factory()

        self.rp5 = Rp5Repository(self.session)
        self.rp5FindDouble = Rp5FindDoubleRepository(self.session)

        return self

    def __exit__(self, exc_type, *args):
        # if exc_type is not None:
        self.session.rollback()
        self.session.close()

    def commit(self):
        """Коммит транзакции"""
        self.session.commit()

    def rollback(self):
        """Откат транзакции"""
        self.session.rollback()

    def close(self):
        """Закрытие сессии"""
        self.session.close()
