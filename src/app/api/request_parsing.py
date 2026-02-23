"""Helpers for parsing request payloads from JSON or HTML forms."""

from __future__ import annotations

from typing import TypeVar
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError

TModel = TypeVar("TModel", bound=BaseModel)


async def parse_payload(request: Request, model_cls: type[TModel]) -> TModel:
    """Parse payload from JSON or form-encoded requests into a Pydantic model."""
    content_type = request.headers.get("content-type", "").lower()

    try:
        if "application/json" in content_type:
            raw = await request.json()
        else:
            form = await request.form()
            raw = dict(form)

        if isinstance(raw, str):
            parsed = parse_qs(raw, keep_blank_values=True)
            raw = {key: values[-1] if values else "" for key, values in parsed.items()}

        return model_cls.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid request payload.") from exc
