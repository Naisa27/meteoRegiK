from pydantic import BaseModel


class ImportResult(BaseModel):
    success: bool
    table_name: str | None = None
    rows_imported: int = 0
    columns_count: int = 0
    errors: list[str] | None = []
    error_row: int | None = None
    rows_doubles: int | None = None

    def __str__(self):
        return str(self.__dict__)
