from src.data.models.source_data_rp5 import SourceDataRp5Orm
from src.repositories.base import BaseRepository
from src.repositories.mappers.mappers import (
    SourceDataRp5DataMapper,
    SourceDataRp5FindDoubleDataMapper,
)


class Rp5Repository(BaseRepository):
    model = SourceDataRp5Orm
    mapper = SourceDataRp5DataMapper


class Rp5FindDoubleRepository(BaseRepository):
    model = SourceDataRp5Orm
    mapper = SourceDataRp5FindDoubleDataMapper
