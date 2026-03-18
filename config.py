# noinspection PyUnresolvedReferences
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DB_DIR: str
    DB_NAME: str

    APP_NAME: str
    EXPORT_DIR: str
    LOG_FILE: str
    DEBUG: bool

    @property
    def SQLITE_PATH(self) -> str:
        db_path = Path(self.DB_DIR) / self.DB_NAME
        return f"sqlite:///{db_path}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def get_export_path(self, filename: str) -> Path:
        """
        Получить полный путь для экспортируемого файла

        Args:
            filename: Имя файла

        Returns:
            Path: Полный путь к файлу
        """
        return Path(self.EXPORT_DIR) / filename

    def get_log_path(self) -> Path:
        """
        Получить путь к файлу логов

        Returns:
            Path: Путь к файлу логов
        """
        return Path(self.LOG_FILE)

    def ensure_directories(self) -> None:
        """Создать необходимые директории"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.export_dir).mkdir(parents=True, exist_ok=True)
        Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()

# Создание директорий при импорте
# settings.ensure_directories()
