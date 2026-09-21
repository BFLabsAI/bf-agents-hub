"""Tests for core.media_handler — multimodal transcription/extraction + S3 store.

All externals (model_caller, s3_uploader, db) are injected and faked here; no
network/S3/model access. Each DB test uses a UNIQUE table prefix.
"""
from __future__ import annotations

import pytest

from core import media_handler


async def test_transcribe_or_extract_routes_audio_to_model_and_returns_text():
    seen: dict = {}

    async def model_caller(media_url, media_type, **kwargs):
        seen["media_url"] = media_url
        seen["media_type"] = media_type
        return "ola tudo bem"

    result = await media_handler.transcribe_or_extract(
        "https://s3/audio.ogg", "audio", model_caller
    )

    assert result == "ola tudo bem"
    assert seen["media_url"] == "https://s3/audio.ogg"
    assert seen["media_type"] == "audio"


async def test_transcribe_or_extract_routes_all_media_types():
    routed: list[str] = []

    async def model_caller(media_url, media_type, **kwargs):
        routed.append(media_type)
        return f"extracted from {media_type}"

    for mt in ("image", "video", "document"):
        result = await media_handler.transcribe_or_extract(
            f"https://s3/x.{mt}", mt, model_caller
        )
        assert result == f"extracted from {mt}"

    assert routed == ["image", "video", "document"]


async def test_transcribe_or_extract_surfaces_controlled_error_on_model_failure():
    secret = "boom: OPENAI_API_KEY=sk-leak raw stacktrace"

    async def failing_model_caller(media_url, media_type, **kwargs):
        raise RuntimeError(secret)

    with pytest.raises(media_handler.MediaProcessingError) as exc_info:
        await media_handler.transcribe_or_extract(
            "https://s3/audio.ogg", "audio", failing_model_caller
        )

    # The controlled error must NOT leak the raw exception text as the message
    # (the caller will humanize it). It also must never be returned as a
    # transcript string.
    assert secret not in str(exc_info.value)
    assert "audio" in str(exc_info.value)


async def test_store_media_uploads_and_inserts_row(schema_factory, db_conn):
    prefix = "test_mediahdl_1_"
    await schema_factory(prefix)

    uploaded: dict = {}

    async def s3_uploader(media_bytes, media_type):
        uploaded["bytes"] = media_bytes
        uploaded["media_type"] = media_type
        return {
            "s3_key": "media/abc.ogg",
            "s3_url": "https://bucket.s3.amazonaws.com/media/abc.ogg",
        }

    row = await media_handler.store_media(
        db_conn, prefix, "5585999998888", b"\x00\x01raw-bytes", "audio", s3_uploader
    )

    # Uploader received the raw bytes (not base64) and media_type.
    assert uploaded["bytes"] == b"\x00\x01raw-bytes"
    assert uploaded["media_type"] == "audio"

    # Returned dict reflects the stored row with the S3 URL (not base64/bytes).
    assert row["phone"] == "5585999998888"
    assert row["s3_key"] == "media/abc.ogg"
    assert row["s3_url"] == "https://bucket.s3.amazonaws.com/media/abc.ogg"
    assert row["media_type"] == "audio"
    assert "id" in row

    # Row persisted in {prefix}media.
    async with db_conn.cursor() as cur:
        await cur.execute(
            f'SELECT phone, s3_key, s3_url, media_type FROM "{prefix}media" WHERE id = %s',
            (row["id"],),
        )
        db_row = await cur.fetchone()
    assert db_row == (
        "5585999998888",
        "media/abc.ogg",
        "https://bucket.s3.amazonaws.com/media/abc.ogg",
        "audio",
    )


async def test_store_media_persists_transcript_when_provided(schema_factory, db_conn):
    prefix = "test_mediahdl_2_"
    await schema_factory(prefix)

    async def s3_uploader(media_bytes, media_type):
        return {"s3_key": "media/x.jpg", "s3_url": "https://bucket/media/x.jpg"}

    row = await media_handler.store_media(
        db_conn, prefix, "5511", b"img", "image", s3_uploader,
        transcript="a photo of an invoice",
    )

    assert row["transcript"] == "a photo of an invoice"

    async with db_conn.cursor() as cur:
        await cur.execute(
            f'SELECT transcript FROM "{prefix}media" WHERE id = %s', (row["id"],)
        )
        assert (await cur.fetchone())[0] == "a photo of an invoice"
