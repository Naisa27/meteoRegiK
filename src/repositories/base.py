from pydantic import BaseModel
from sqlalchemy import insert, select
from sqlalchemy.exc import NoResultFound

from src.exceptions.exceptions import (
    DatabaseException,
    NotAddInDBException,
    NotBulkAddInDBException,
    ObjectNotFoundException,
)
from src.repositories.mappers.base import DataMapper


class BaseRepository:
    model = None
    mapper: DataMapper = None

    def __init__(self, session):
        self.session = session

    def add(self, data: BaseModel):
        add_data_stmt = insert(self.model).values(**data.model_dump())
        print(f"{data.model_dump()}")
        try:
            self.session.execute(add_data_stmt)
        except Exception as e:
            raise NotAddInDBException from e

    def bulk_add(self, data: list[BaseModel]):
        if not data:
            return
        add_data_stmt = insert(self.model)
        values = [item.model_dump() for item in data]
        try:
            self.session.execute(add_data_stmt, values)
        except Exception as e:
            raise NotBulkAddInDBException from e

    def chunked_bulk_add(self, data: list[BaseModel], chunk_size: int = 5000):
        total = len(data)
        for start in range(0, total, chunk_size):
            chunk = data[start : start + chunk_size]
            self.bulk_add(chunk)
            print(f"Inserted {min(start + chunk_size, total)}/{total} rows")

    def get_filtered(self, *filter, **filter_by):
        query = select(self.model).filter(*filter).filter_by(**filter_by)
        try:
            result = self.session.execute(query)
        except NoResultFound as e:
            raise ObjectNotFoundException from e
        except Exception as e:
            raise DatabaseException from e

        return [self.mapper.map_to_schema(model) for model in result.scalars().all()]

    def get_all(self):
        return self.get_filtered()

    def get_one_or_none(self, **filter_by):
        query = select(self.model).filter_by(**filter_by)
        result = self.session.execute(query)
        model = result.scalars().one_or_none()

        if model is None:
            return None

        return self.mapper.map_to_schema(model)

    def get_one(self, **filter_by):
        query = select(self.model).filter_by(**filter_by)
        result = self.session.execute(query)
        try:
            model = result.scalars().one()
        except NoResultFound as e:
            raise ObjectNotFoundException from e
        except Exception as e:
            raise DatabaseException from e

        return self.mapper.map_to_schema(model)
