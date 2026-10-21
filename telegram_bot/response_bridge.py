"""Send model fallback requests to Telegram and wait for a reply."""

import base64
import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from .config import TelegramConfig


class TelegramResponseBridge:
    """Correlate Telegram replies with waiting ComfyUI model requests."""

    REPLY_TIMEOUT = 3600
    MESSAGE_LIMIT = 3500

    def __init__(self, config: Optional[TelegramConfig] = None):
        self.config = config or TelegramConfig()
        self.storage_dir = Path.home() / ".comfyui_telegram_reviews"
        self.database = self.storage_dir / "model_fallbacks.sqlite3"
        self.api_base = "https://api.telegram.org/bot"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_fallbacks (
                    request_id TEXT PRIMARY KEY,
                    prompt_message_id TEXT,
                    status TEXT NOT NULL,
                    response TEXT,
                    created_at REAL NOT NULL
                )
                """
            )

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.database, timeout=30)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def request_response(
        self,
        provider: str,
        models: list[str],
        systems_prompt: str,
        user_prompt: str,
        images: list[str],
    ) -> str:
        if not self.config.is_configured():
            raise RuntimeError(
                "Telegram is not configured; set a bot token and user ID."
            )

        request_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO model_fallbacks "
                "(request_id, status, created_at) VALUES (?, 'pending', ?)",
                (request_id, time.time()),
            )

        try:
            for image in images:
                self._send_photo(image)

            text = (
                "All configured models failed. Reply directly to this message "
                "with the text to return to ComfyUI.\n\n"
                f"Provider: {provider}\n"
                f"Failed models: {', '.join(models)}\n\n"
                f"System prompt:\n{systems_prompt or '(empty)'}\n\n"
                f"User prompt:\n{user_prompt}"
            )
            chunks = [
                text[index : index + self.MESSAGE_LIMIT]
                for index in range(0, len(text), self.MESSAGE_LIMIT)
            ]
            prompt_message_id = self._send_message(chunks[0])
            with self._connection() as connection:
                connection.execute(
                    "UPDATE model_fallbacks SET prompt_message_id = ? "
                    "WHERE request_id = ? AND status = 'pending'",
                    (prompt_message_id, request_id),
                )
            for chunk in chunks[1:]:
                self._send_message(chunk)
        except Exception:
            with self._connection() as connection:
                connection.execute(
                    "DELETE FROM model_fallbacks WHERE request_id = ?",
                    (request_id,),
                )
            raise

        print(
            f"[TelegramResponseBridge] Waiting for Telegram reply "
            f"(request {request_id})"
        )
        deadline = time.monotonic() + self.REPLY_TIMEOUT
        while True:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT response FROM model_fallbacks "
                    "WHERE request_id = ? AND status = 'completed'",
                    (request_id,),
                ).fetchone()
                if row:
                    connection.execute(
                        "DELETE FROM model_fallbacks WHERE request_id = ?",
                        (request_id,),
                    )
                    return row[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1, remaining))

        with self._connection() as connection:
            row = connection.execute(
                "SELECT response FROM model_fallbacks "
                "WHERE request_id = ? AND status = 'completed'",
                (request_id,),
            ).fetchone()
            if row:
                connection.execute(
                    "DELETE FROM model_fallbacks WHERE request_id = ?",
                    (request_id,),
                )
                return row[0]
            connection.execute(
                "DELETE FROM model_fallbacks "
                "WHERE request_id = ? AND status = 'pending'",
                (request_id,),
            )
        raise TimeoutError(
            f"No Telegram reply received within {self.REPLY_TIMEOUT} seconds."
        )

    def complete_reply(self, message: dict, expected_chat_id: str) -> bool:
        reply_to = message.get("reply_to_message") or {}
        prompt_message_id = reply_to.get("message_id")
        chat_id = str((message.get("chat") or {}).get("id", ""))
        response = (message.get("text") or "").strip()
        if (
            not prompt_message_id
            or chat_id != str(expected_chat_id)
            or not response
        ):
            return False

        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE model_fallbacks SET status = 'completed', response = ? "
                "WHERE prompt_message_id = ? AND status = 'pending'",
                (response, str(prompt_message_id)),
            )
        return cursor.rowcount > 0

    def _send_message(self, text: str) -> str:
        result = self._api_request(
            "sendMessage",
            {"chat_id": self.config.get_user_id(), "text": text},
        )
        try:
            return str(result["result"]["message_id"])
        except (KeyError, TypeError) as error:
            raise RuntimeError("Telegram did not return a message ID.") from error

    def _send_photo(self, image: str) -> None:
        bot_token = self.config.get_bot_token()
        boundary = "----ComfyUI" + uuid.uuid4().hex
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
                self.config.get_user_id().encode("utf-8"),
                b"\r\n",
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="photo"; filename="input.png"\r\n',
                b"Content-Type: image/png\r\n\r\n",
                base64.b64decode(image),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        request = urllib.request.Request(
            f"{self.api_base}{bot_token}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        self._read_response(request)

    def _api_request(self, method: str, payload: dict) -> dict:
        bot_token = self.config.get_bot_token()
        request = urllib.request.Request(
            f"{self.api_base}{bot_token}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._read_response(request)

    @staticmethod
    def _read_response(request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Telegram API request failed ({error.code}): {details}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Telegram API request failed: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Telegram API returned invalid JSON.") from error

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram API request failed: {result.get('description', result)}"
            )
        return result
