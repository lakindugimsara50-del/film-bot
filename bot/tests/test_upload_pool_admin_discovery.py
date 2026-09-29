"""
test_upload_pool_admin_discovery.py — Tests for dynamic admin session discovery and allocation
in TelegramUploadPool for multi-quality Telegram uploads.
"""

import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = REPO_ROOT / "bot"
for p in (str(REPO_ROOT), str(BOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.upload_pool import TelegramUploadPool


class MockChatMember:
    def __init__(self, status="administrator", can_post=True):
        self.status = MagicMock()
        self.status.value = status
        self.privileges = MagicMock()
        self.privileges.can_post_messages = can_post


class MockClient:
    def __init__(self, name="session_001", is_connected=True, chat_member=None):
        self.name = name
        self.is_connected = is_connected
        self._chat_member = chat_member or MockChatMember()
        self.me = MagicMock()
        self.me.id = 12345
        self.me.username = "test_user"
        self.me.first_name = "Test"

    async def get_chat_member(self, chat_id, user_id):
        if isinstance(self._chat_member, Exception):
            raise self._chat_member
        return self._chat_member


@pytest.mark.asyncio
async def test_get_admin_sessions_filtering():
    pool = TelegramUploadPool()

    # Create 4 clients with different roles
    c_admin_ok = MockClient(name="session_001", chat_member=MockChatMember("administrator", can_post=True))
    c_regular = MockClient(name="session_002", chat_member=MockChatMember("member", can_post=False))
    c_admin_no_post = MockClient(name="session_003", chat_member=MockChatMember("administrator", can_post=False))
    c_creator = MockClient(name="session_004", chat_member=MockChatMember("creator", can_post=True))

    pool.clients = [c_admin_ok, c_regular, c_admin_no_post, c_creator]

    admin_sessions = await pool.get_admin_sessions(-100123456789, refresh=True)
    assert len(admin_sessions) == 2
    assert c_admin_ok in admin_sessions
    assert c_creator in admin_sessions
    assert c_regular not in admin_sessions
    assert c_admin_no_post not in admin_sessions


@pytest.mark.asyncio
async def test_get_client_for_quality_admin_distribution():
    pool = TelegramUploadPool()
    target_channel = -100123456789

    c1 = MockClient(name="session_001")
    c2 = MockClient(name="session_002")
    c3 = MockClient(name="session_003")

    pool.clients = [c1, c2, c3]
    pool._admin_sessions[target_channel] = [c1, c2, c3]

    # 1080p, 720p, 480p should map to distinct admin accounts
    q1080 = await pool.get_client_for_quality("1080p", target_chat=target_channel)
    q720 = await pool.get_client_for_quality("720p", target_chat=target_channel)
    q480 = await pool.get_client_for_quality("480p", target_chat=target_channel)

    assert q1080 == c1
    assert q720 == c2
    assert q480 == c3


@pytest.mark.asyncio
async def test_upload_with_pool_uses_admin_sessions():
    pool = TelegramUploadPool()
    target_channel = -100123456789

    main_bot = MockClient(name="main_bot")
    admin_bot = MockClient(name="session_admin", chat_member=MockChatMember("administrator", can_post=True))

    pool.set_main_client(main_bot)
    pool.clients = [admin_bot]
    pool._admin_sessions[target_channel] = [admin_bot]

    with patch("services.telegram_upload.upload_video_file", new_callable=AsyncMock) as mock_upload:
        mock_upload.return_value = {"file_id": "test_id", "message_id": 999}

        res = await pool.upload_with_pool(
            file_path="test.mp4",
            target_chat=target_channel,
            quality="720p",
            caption="test caption",
        )

        assert res.get("file_id") == "test_id"
        mock_upload.assert_awaited_once()
        # Verify it used admin_bot, NOT main_bot
        called_bot = mock_upload.call_args.kwargs["bot_client"]
        assert called_bot == admin_bot


@pytest.mark.asyncio
async def test_upload_with_pool_fallback_when_no_admins():
    pool = TelegramUploadPool()
    target_channel = -100123456789

    main_bot = MockClient(name="main_bot")
    non_admin_bot = MockClient(name="session_regular", chat_member=MockChatMember("member", can_post=False))

    pool.set_main_client(main_bot)
    pool.clients = [non_admin_bot]
    pool._admin_sessions[target_channel] = []

    with patch("services.telegram_upload.upload_video_file", new_callable=AsyncMock) as mock_upload:
        mock_upload.return_value = {"file_id": "main_bot_file_id", "message_id": 1000}

        res = await pool.upload_with_pool(
            file_path="test.mp4",
            target_chat=target_channel,
            quality="1080p",
            caption="test caption",
        )

        assert res.get("file_id") == "main_bot_file_id"
        mock_upload.assert_awaited_once()
        called_bot = mock_upload.call_args.kwargs["bot_client"]
        assert called_bot == main_bot


@pytest.mark.asyncio
async def test_auth_service_sync_channel_admins():
    from services.auth_service import AuthService

    auth = AuthService()
    test_user_id = 9988776655
    assert not auth.is_admin(test_user_id)
    assert not auth.is_authorized(test_user_id)

    # Mock client with get_chat_members async generator
    class AsyncGen:
        def __init__(self, items):
            self.items = items
        def __aiter__(self):
            self._iter = iter(self.items)
            return self
        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    mock_user = MagicMock()
    mock_user.id = test_user_id
    mock_user.username = "chan_admin"
    mock_user.is_bot = False

    mock_member = MagicMock()
    mock_member.user = mock_user

    mock_client = MagicMock()
    mock_client.get_chat_members.return_value = AsyncGen([mock_member])

    synced = await auth.sync_channel_admins(mock_client, -100123456789)
    assert len(synced) == 1
    assert synced[0]["id"] == test_user_id
    assert auth.is_admin(test_user_id)
    assert auth.is_authorized(test_user_id)

