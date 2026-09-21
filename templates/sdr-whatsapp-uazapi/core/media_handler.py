"""Inbound media handling — transcription/extraction + S3 storage.

Per the confirmed design: when a lead sends audio/image/video/document, we call
the MiMo v2.5 REGULAR (multimodal) model to transcribe/extract — NOT a separate
Whisper service. All external dependencies (the model caller, the S3 uploader)
are INJECTED so this module is testable without network/model access.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class MediaProcessingError(Exception):
    """Controlled error raised when transcription/extraction fails.

    Deliberately carries only a safe, non-leaking message (the media_type) so
    the caller can humanize it for the lead. The raw underlying exception is
    chained via __cause__ for logs, never exposed in the message text.
    """


async def transcribe_or_extract(
    media_url: str,
    media_type: str,
    model_caller: Callable[..., Awaitable[str]],
) -> str:
    """Transcribe (audio/video) or extract text/description (image/document).

    Routes the media to the injected multimodal `model_caller`, which wraps the
    MiMo v2.5 regular model (use AGNO_MULTIMODAL_MODEL='mimo/mimo-v2.5', the
    non-pro variant, since pro is not multimodal). `media_type` ∈
    audio|image|video|document. Returns the transcript / extracted text.
    """
    try:
        return await model_caller(media_url, media_type)
    except Exception as exc:  # noqa: BLE001 — model/transport failures
        logger.error(
            "transcribe_or_extract failed for media_type=%s", media_type,
            exc_info=True,
        )
        raise MediaProcessingError(
            f"failed to process {media_type} media"
        ) from exc


async def store_media(
    conn: Any,
    prefix: str,
    phone: str,
    media_bytes: bytes,
    media_type: str,
    s3_uploader: Callable[..., Awaitable[dict]],
    transcript: str | None = None,
) -> dict:
    """Upload media to S3 and persist a {PREFIX}media row.

    `s3_uploader(media_bytes, media_type) -> {"s3_key","s3_url"}` is injected.
    Inserts a media row (phone, s3_key, s3_url, media_type, transcript) and
    returns the inserted row as a dict. We store the S3 URL — NEVER base64 or
    raw bytes — in the DB.
    """
    upload = await s3_uploader(media_bytes, media_type)
    s3_key = upload["s3_key"]
    s3_url = upload["s3_url"]

    async with conn.cursor() as cur:
        await cur.execute(
            f'INSERT INTO "{prefix}media" '
            "(phone, s3_key, s3_url, media_type, transcript) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, phone, s3_key, s3_url, media_type, transcript",
            (phone, s3_key, s3_url, media_type, transcript),
        )
        inserted = await cur.fetchone()

    return {
        "id": inserted[0],
        "phone": inserted[1],
        "s3_key": inserted[2],
        "s3_url": inserted[3],
        "media_type": inserted[4],
        "transcript": inserted[5],
    }
