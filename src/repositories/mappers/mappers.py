from src.data.models.source_data_rp5 import SourceDataRp5Orm
from src.repositories.mappers.base import DataMapper
from src.schemas.source_data_rp5 import SourceDataRp5, SourceDataRp5FindDouble


class SourceDataRp5DataMapper(DataMapper):
    db_model = SourceDataRp5Orm
    schema = SourceDataRp5


class SourceDataRp5FindDoubleDataMapper(DataMapper):
    db_model = SourceDataRp5Orm
    schema = SourceDataRp5FindDouble
