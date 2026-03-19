"""CLI-утилита для отправки server notice пользователям Synapse.

Поддерживает два режима:
- `-a`: отправка только новым пользователям;
- `-n`: отправка всем пользователям из локальной БД.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, TypedDict
import aiosqlite
import httpx
import yaml

from config import Config, DEFAULT_CONFIG_PATH


LOG_PATH = Path("logs.log")
logger = logging.getLogger("synapse_notice")


def setup_logging() -> None:
    """Настраивает логирование в файл `logs.log` и в консоль."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы CLI и возвращает namespace с режимом запуска."""
    parser = argparse.ArgumentParser(
        prog="python3 -m main",
        description="Рассылка server notice пользователям Synapse",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-a",
        metavar="MESSAGE_FILE",
        help="Отправить только новым пользователям (если БД нет — только инициализировать БД и выйти)",
    )
    group.add_argument(
        "-n",
        metavar="MESSAGE_FILE",
        help="Отправить всем пользователям из БД",
    )
    return parser.parse_args()


class MessagePayload(TypedDict):
    """Структура YAML-сообщения для отправки server notice."""

    body: str
    formatted_body: str


def read_message(message_path: str) -> MessagePayload:
    """Читает YAML-файл сообщения и валидирует обязательные поля.

    Args:
        message_path: Путь к YAML-файлу с полями `body` и `formated_body`.

    Returns:
        Словарь с `body` и `formatted_body`.

    Raises:
        FileNotFoundError: Если файл не найден.
        ValueError: Если формат YAML некорректный или отсутствует `body`.
    """
    p = Path(message_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Файл сообщения не найден: {message_path}")

    with p.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    if not isinstance(payload, dict):
        raise ValueError("Некорректный формат файла сообщения: ожидается YAML-объект")

    body = str(payload.get("body", "")).strip()
    # Поддерживаем и formated_body, и formatted_body, чтобы не ломать существующие файлы.
    formatted = str(payload.get("formated_body") or payload.get("formatted_body") or body).strip()

    if not body:
        raise ValueError("В файле сообщения не задано поле body")

    return {"body": body, "formatted_body": formatted}


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Выполняет HTTP-запрос и возвращает JSON-ответ как словарь.

    Raises:
        httpx.HTTPStatusError: Если сервер вернул код ошибки.
    """
    response = await client.request(method=method, url=path, params=query, json=payload)
    response.raise_for_status()
    return response.json() if response.content else {}


async def fetch_all_users(client: httpx.AsyncClient) -> list[str]:
    """Загружает всех пользователей Synapse через пагинацию admin API."""
    result: list[str] = []
    limit = 100
    next_token: str | None = None

    while True:
        query: dict[str, Any] = {"limit": limit}
        if next_token:
            query["from"] = next_token

        data = await _request_json(
            client=client,
            method="GET",
            path="/_synapse/admin/v2/users",
            query=query,
        )

        chunk = data.get("users", [])
        for user_obj in chunk:
            user_id = user_obj.get("name")
            if user_id:
                result.append(user_id)

        next_token = data.get("next_token")
        if not next_token:
            break

    unique_users = list(set(result))
    logger.info("Получено пользователей из Synapse: %s", len(unique_users))
    return unique_users


async def send_server_notice(client: httpx.AsyncClient, user_id: str, message: MessagePayload) -> None:
    """Отправляет server notice одному пользователю."""
    payload = {
        "user_id": user_id,
        "content": {
            "msgtype": "m.text",
            "body": message["body"],
            "format": "org.matrix.custom.html",
            "formatted_body": message["formatted_body"],
        },
    }
    await _request_json(
        client=client,
        method="POST",
        path="/_synapse/admin/v1/send_server_notice",
        payload=payload,
    )
    logger.info("Отправлено уведомление пользователю %s", user_id)


async def ensure_db(conn: aiosqlite.Connection) -> None:
    """Создает таблицу `users`, если она еще не существует."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY
        )
        """
    )
    await conn.commit()


async def db_get_all_users(conn: aiosqlite.Connection) -> set[str]:
    """Возвращает множество всех user_id из локальной БД."""
    async with conn.execute("SELECT user_id FROM users") as cursor:
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def db_insert_users(conn: aiosqlite.Connection, user_ids: Iterable[str]) -> None:
    """Добавляет пользователей в БД без дубликатов."""
    await conn.executemany(
        "INSERT OR IGNORE INTO users(user_id) VALUES (?)",
        [(uid,) for uid in user_ids],
    )
    await conn.commit()


async def mode_a(config: Config, client: httpx.AsyncClient, message_file: str) -> int:
    """Режим `-a`: отправляет сообщение только новым пользователям.

    Если БД отсутствует, выполняет только первичную инициализацию списка пользователей
    без рассылки и завершает работу с кодом 0.
    """
    logger.info("Запуск режима -a, файл сообщения: %s", message_file)
    db_file_exists = Path(config.db_path).exists()

    all_users = await fetch_all_users(client)
    async with aiosqlite.connect(config.db_path) as conn:
        await ensure_db(conn)

        if not db_file_exists:
            await db_insert_users(conn, all_users)
            logger.info("БД не найдена. Загружено пользователей: %s. Рассылка не выполнялась.", len(all_users))
            return 0

        known = await db_get_all_users(conn)
        new_users = [u for u in all_users if u not in known]

        if not new_users:
            logger.info("Новых пользователей нет.")
            return 0

        message = read_message(message_file)
        sent_ok: list[str] = []
        for uid in new_users:
            try:
                await send_server_notice(client, uid, message)
                sent_ok.append(uid)
            except Exception as e:
                logger.warning("Не удалось отправить %s: %s", uid, e)

        await db_insert_users(conn, sent_ok)
        logger.info("Отправлено новым: %s из %s.", len(sent_ok), len(new_users))
    return 0


async def mode_n(config: Config, client: httpx.AsyncClient, message_file: str) -> int:
    """Режим `-n`: отправляет сообщение всем пользователям из локальной БД."""
    logger.info("Запуск режима -n, файл сообщения: %s", message_file)
    if not Path(config.db_path).exists():
        logger.error("БД не найдена. Для начала выполните режим -a.")
        return 2

    message = read_message(message_file)
    async with aiosqlite.connect(config.db_path) as conn:
        await ensure_db(conn)
        users = sorted(await db_get_all_users(conn))

    if not users:
        logger.info("В БД нет пользователей.")
        return 0

    sent = 0
    for uid in users:
        try:
            await send_server_notice(client, uid, message)
            sent += 1
        except Exception as e:
            logger.warning("Не удалось отправить %s: %s", uid, e)

    logger.info("Отправлено: %s из %s.", sent, len(users))
    return 0


async def async_main() -> int:
    """Асинхронная точка входа: загружает конфиг и запускает выбранный режим."""
    args = parse_args()
    config = Config.from_yaml(DEFAULT_CONFIG_PATH)
    logger.info("Старт приложения")

    async with httpx.AsyncClient(
        base_url=config.homeserver_url,
        headers={
            "Authorization": f"Bearer {config.access_token}",
            "Content-Type": "application/json",
        }
    ) as client:
        if args.a:
            return await mode_a(config, client, args.a)
        if args.n:
            return await mode_n(config, client, args.n)

    return 1


def main() -> int:
    """Синхронная точка входа приложения с настройкой логирования."""
    setup_logging()
    try:
        return asyncio.run(async_main())
    except Exception:
        logger.exception("Необработанная ошибка в приложении")
        raise


if __name__ == "__main__":
    sys.exit(main())

