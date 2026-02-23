"""MAX Bot API client (httpx-based, async).

Base URL: https://platform-api.max.ru
Auth: Authorization header with bot token.
Docs: https://dev.max.ru/docs-api
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE = 4 * 1024 * 1024 * 1024  # 4 GB


class MaxApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"MAX API error {code}: {message}")


@dataclass
class UploadResult:
    url: str
    token: str | None = None


def _find_first_str_by_key(payload: Any, keys: set[str]) -> str | None:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _find_first_str_by_key(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_str_by_key(item, keys)
            if found:
                return found
    return None


def _find_first_str(payload: Any) -> str | None:
    if isinstance(payload, str) and payload:
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            found = _find_first_str(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_str(item)
            if found:
                return found
    return None


def _extract_upload_url(payload: Any) -> str | None:
    url = _find_first_str_by_key(payload, {"url", "upload_url", "uploadUrl"})
    if url:
        return url
    candidate = _find_first_str(payload)
    if candidate and candidate.startswith(("http://", "https://")):
        return candidate
    return None


def _extract_upload_token(payload: Any) -> str | None:
    return _find_first_str_by_key(payload, {"token", "file_token", "photo_token", "attachment_token"})


class MaxClient:
    def __init__(self, token: str, base_url: str | None = None):
        self.token = token
        self.base_url = (base_url or settings.MAX_API_BASE).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": self.token},
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise MaxApiError(
                code=body.get("code", f"http_{resp.status_code}"),
                message=body.get("message", resp.text),
                status=resp.status_code,
            )
        return resp.json()

    # --- API methods ---

    async def get_me(self) -> dict[str, Any]:
        """GET /me — validate token, return bot info."""
        return await self._request("GET", "/me")

    async def get_chats(self, count: int = 100, marker: int | None = None) -> dict[str, Any]:
        """GET /chats — list chats where bot is a member."""
        params: dict[str, Any] = {"count": count}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/chats", params=params)

    async def get_chat(self, chat_id: int) -> dict[str, Any]:
        """GET /chats/{chatId} — chat details."""
        return await self._request("GET", f"/chats/{chat_id}")

    async def get_membership(self, chat_id: int) -> dict[str, Any]:
        """GET /chats/{chatId}/members/me — check bot permissions."""
        return await self._request("GET", f"/chats/{chat_id}/members/me")

    async def request_upload(self, upload_type: str) -> UploadResult:
        """POST /uploads — get upload URL (token may be absent until file upload).

        upload_type: image | video | audio | file
        """
        data = await self._request("POST", "/uploads", params={"type": upload_type})
        url = _extract_upload_url(data)
        if not url:
            raise MaxApiError(
                code="uploads.invalid_response",
                message=f"Missing upload URL in response: {data!r}",
            )
        return UploadResult(url=url, token=_extract_upload_token(data))

    async def upload_file(self, upload_url: str, file_path: str) -> str | None:
        """Upload file to the URL obtained from request_upload."""
        client = await self._get_client()
        path = Path(file_path)
        retries = 3
        resp: httpx.Response | None = None
        for attempt in range(retries):
            try:
                with open(path, "rb") as f:
                    resp = await client.post(
                        upload_url,
                        files={"data": (path.name, f)},
                        timeout=httpx.Timeout(300.0, connect=10.0),
                    )
                break
            except (httpx.TimeoutException, httpx.TransportError) as e:
                error_text = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                if attempt >= retries - 1:
                    raise MaxApiError(
                        code="upload.transport_error",
                        message=error_text,
                    ) from e
                delay = 0.5 * (2 ** attempt)
                logger.warning(
                    "Upload transport error (attempt %d/%d): %s; retry in %.1fs",
                    attempt + 1,
                    retries,
                    error_text,
                    delay,
                )
                await asyncio.sleep(delay)

        if resp is None:
            return None
        if resp.status_code >= 400:
            raise MaxApiError(
                code=f"upload_{resp.status_code}",
                message=resp.text,
                status=resp.status_code,
            )
        if not resp.headers.get("content-type", "").startswith("application/json"):
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        return _extract_upload_token(body)

    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        attachments: list[dict] | None = None,
        format_: str | None = None,
        notify: bool = True,
    ) -> dict[str, Any]:
        """POST /messages — send a message to chat.

        attachments: list of attachment dicts (e.g. {"type": "image", "payload": {"token": "..."}})
        format_: "markdown" or "html" or None
        """
        params: dict[str, Any] = {"chat_id": chat_id}
        if not notify:
            params["disable_link_preview"] = True

        body: dict[str, Any] = {}
        if text:
            body["text"] = text[:4000]
        if attachments:
            body["attachments"] = attachments
        if format_:
            body["format"] = format_

        return await self._request("POST", "/messages", params=params, json=body)

    async def send_message_with_retry(
        self,
        chat_id: int,
        text: str | None = None,
        attachments: list[dict] | None = None,
        format_: str | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Send message with retry on 'attachment.not.ready' error."""
        retries = max_retries or settings.SEND_RETRY
        backoff = [0.5, 1.0, 2.0, 4.0, 8.0]

        for attempt in range(retries):
            try:
                return await self.send_message(
                    chat_id=chat_id,
                    text=text,
                    attachments=attachments,
                    format_=format_,
                )
            except MaxApiError as e:
                if e.code == "attachment.not.ready" and attempt < retries - 1:
                    delay = backoff[min(attempt, len(backoff) - 1)]
                    logger.warning(
                        "attachment.not.ready, retry %d/%d in %.1fs",
                        attempt + 1,
                        retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """GET /messages/{messageId} — check message delivery."""
        return await self._request("GET", f"/messages/{message_id}")
