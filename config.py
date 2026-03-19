"""Модуль загрузки конфигурации приложения из YAML-файла."""

from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class Config:
    """Параметры подключения и локального хранилища приложения."""

    homeserver_url: str
    access_token: str
    db_path: str = "users.db"

    @classmethod
    def from_yaml(cls, config_path: Path | str = DEFAULT_CONFIG_PATH) -> "Config":
        """Читает `config.yml`, валидирует поля и возвращает объект конфигурации.

        Args:
            config_path: Путь к YAML-файлу конфигурации.

        Returns:
            Инициализированный объект `Config`.

        Raises:
            FileNotFoundError: Если файл конфигурации не существует.
            ValueError: Если YAML поврежден или в нем отсутствуют обязательные поля.
        """
        path = Path(config_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Файл конфигурации не найден: {path}")

        with path.open("r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}

        if not isinstance(payload, dict):
            raise ValueError("Некорректный формат config.yml: ожидается YAML-объект")

        homeserver_url = str(payload.get("homeserver_url", "")).rstrip("/")
        access_token = str(payload.get("access_token", ""))
        db_path = str(payload.get("db_path", "users.db"))

        if not homeserver_url:
            raise ValueError("Не задан homeserver_url в config.yml")
        if not access_token:
            raise ValueError("Не задан access_token в config.yml")

        return cls(
            homeserver_url=homeserver_url,
            access_token=access_token,
            db_path=db_path,
        )
