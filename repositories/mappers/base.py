from typing import TypeVar

from pydantic import BaseModel

from data.db_base import Base

DBModelType = TypeVar("DBModelType", bound=Base)
SchemaType = TypeVar("SchemaType", bound=BaseModel)


class DataMapper:
    db_model: type[DBModelType] = None
    schema: type[SchemaType] = None

    @classmethod
    def map_to_schema(cls, data: type[DBModelType]) -> type[SchemaType]:
        return cls.schema.model_validate(data, from_attributes=True)

    @classmethod
    def map_to_db_model(cls, data: type[SchemaType]) -> type[DBModelType]:
        return cls.db_model(**data.model_dump())
