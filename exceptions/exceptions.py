class MeteoregikException(Exception):
    detail ="неожиданная ошибка"

    def __init__(self, *args, **kwargs):
        super().__init__(self.detail, *args, **kwargs)


class DatabaseException(MeteoregikException):
    detail = "неожиданная ошибка при работе с БД"


class ObjectNotFoundException(MeteoregikException):
    detail = "Объект не найден"


class NotAddInDBException(MeteoregikException):
    detail="Не удалось добавить методом add объект в базу данных"


class NotBulkAddInDBException(MeteoregikException):
    detail="Не удалось добавить методом bulk_add список объектов в базу данных"

