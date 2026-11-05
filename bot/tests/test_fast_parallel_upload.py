import asyncio
import io
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

_bot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _bot_dir not in sys.path:
    sys.path.insert(0, _bot_dir)

from pyrogram import Client, raw
from services import telegram_upload


@pytest.mark.asyncio
async def test_fast_save_file_patch_installed():
    """Verify fast_save_file is globally hooked into Pyrogram Client and SaveFile."""
    assert Client.save_file == telegram_upload.fast_save_file


@pytest.mark.asyncio
async def test_fast_save_file_small_file():
    """Verify small file (<10MB) routes through fast_save_file correctly."""
    mock_client = MagicMock(spec=Client)
    mock_client.storage = MagicMock()
    mock_client.storage.dc_id = AsyncMock(return_value=2)
    mock_client.storage.auth_key = AsyncMock(return_value=b"0" * 256)
    mock_client.storage.test_mode = AsyncMock(return_value=False)
    mock_client.rnd_id = MagicMock(return_value=999)
    mock_client.loop = asyncio.get_event_loop()
    mock_client.me = MagicMock(is_premium=False)

    fake_data = b"Hello Telegram World! " * 100
    bio = io.BytesIO(fake_data)
    bio.name = "test_small.txt"

    with patch("services.telegram_upload.Session") as mock_session_cls:
        mock_sess = MagicMock()
        mock_sess.start = AsyncMock()
        mock_sess.stop = AsyncMock()
        mock_sess.invoke = AsyncMock(return_value=True)
        mock_session_cls.return_value = mock_sess

        res = await telegram_upload.fast_save_file(mock_client, bio)
        assert isinstance(res, raw.types.InputFile)
        assert res.id == 999
        assert res.parts == 1
        assert res.name == "test_small.txt"
        assert mock_sess.start.called
        assert mock_sess.stop.called


@pytest.mark.asyncio
async def test_fast_save_file_large_file_multi_session():
    """Verify large file (>50MB) initiates parallel MTProto sessions."""
    mock_client = MagicMock(spec=Client)
    mock_client.storage = MagicMock()
    mock_client.storage.dc_id = AsyncMock(return_value=2)
    mock_client.storage.auth_key = AsyncMock(return_value=b"0" * 256)
    mock_client.storage.test_mode = AsyncMock(return_value=False)
    mock_client.rnd_id = MagicMock(return_value=123456789)
    mock_client.loop = asyncio.get_event_loop()
    mock_client.me = MagicMock(is_premium=True)

    # 60 MB file simulated
    chunk_512k = b"X" * (512 * 1024)
    file_size_60m = 60 * 1024 * 1024

    class MockLargeFile(io.RawIOBase):
        def __init__(self):
            self.name = "movie_1080p.mp4"
            self.pos = 0

        def seek(self, offset, whence=io.SEEK_SET):
            if whence == io.SEEK_END:
                self.pos = file_size_60m
            else:
                self.pos = offset
            return self.pos

        def tell(self):
            return self.pos

        def read(self, size=-1):
            if self.pos >= file_size_60m:
                return b""
            rem = file_size_60m - self.pos
            sz = min(size if size > 0 else rem, rem)
            self.pos += sz
            return chunk_512k[:sz]

    large_fp = MockLargeFile()

    with patch("services.telegram_upload.Session") as mock_session_cls:
        sessions_created = []

        def _make_session(*args, **kwargs):
            s = MagicMock()
            s.start = AsyncMock()
            s.stop = AsyncMock()
            s.invoke = AsyncMock(return_value=True)
            sessions_created.append(s)
            return s

        mock_session_cls.side_effect = _make_session

        res = await telegram_upload.fast_save_file(mock_client, large_fp)
        assert isinstance(res, raw.types.InputFileBig)
        assert res.id == 123456789
        assert res.name == "movie_1080p.mp4"
        # For >50MB, exactly 8 parallel sessions were instantiated and started!
        assert len(sessions_created) == 8
        for s in sessions_created:
            assert s.start.called
            assert s.stop.called
