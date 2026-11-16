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
async def test_auth_service_sync_channel_admins(tmp_path, monkeypatch):
    import services.auth_service as auth_mod
    from services.auth_service import AuthService

    test_auth_file = str(tmp_path / "authorized_users.json")
    monkeypatch.setattr(auth_mod, "_AUTH_FILE", test_auth_file)
    monkeypatch.setattr(auth_mod, "_DATA_DIR", str(tmp_path))

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


@pytest.mark.asyncio
async def test_get_admin_sessions_cached():
    pool = TelegramUploadPool()
    target_channel = -100123456789
    c1 = MockClient(name="admin_01")
    c2 = MockClient(name="admin_02")
    pool._admin_sessions[target_channel] = [c1, c2]

    cached = pool.get_admin_sessions_cached(target_channel)
    assert cached == [c1, c2]
    # Check string chat_id normalization
    cached_str = pool.get_admin_sessions_cached(str(target_channel))
    assert cached_str == [c1, c2]


@pytest.mark.asyncio
async def test_save_admin_sessions_manifest(tmp_path, monkeypatch):
    import json
    pool = TelegramUploadPool()
    target_channel = -1004325759505
    c1 = MockClient(name="94718522440")
    c1.me.id = 1967609462
    c1.me.first_name = "Dhananjaya"
    c1.me.phone_number = "+94718522440"

    test_manifest = tmp_path / "admin_sessions.json"
    monkeypatch.setattr("services.upload_pool.os.path.join", lambda *args: str(test_manifest) if "admin_sessions.json" in args else "/".join(args))

    pool.clients = [c1]
    pool.save_admin_sessions_manifest(target_channel, [c1])

    assert test_manifest.exists()
    data = json.loads(test_manifest.read_text(encoding="utf-8"))
    assert data["channel_id"] == target_channel
    assert data["admin_sessions_count"] == 1
    assert data["admin_sessions"][0]["user_id"] == 1967609462


@pytest.mark.asyncio
async def test_stream_pool_channel_media_info_prefers_main_client():
    from streaming.session_pool import TelegramStreamPool
    sp = TelegramStreamPool()
    main_bot = MockClient(name="main_bot")
    sp.set_main_client(main_bot)

    mock_msg = MagicMock()
    mock_msg.empty = False
    mock_msg.video = MagicMock()
    mock_msg.video.file_size = 50000000
    mock_msg.video.file_name = "hi_2026.mp4"
    mock_msg.video.mime_type = "video/mp4"

    main_bot.get_messages = AsyncMock(return_value=mock_msg)

    # Random non-admin client in pool
    non_admin = MockClient(name="non_admin_userbot")
    from pyrogram.errors import ChannelPrivate
    non_admin.get_messages = AsyncMock(side_effect=ChannelPrivate)
    sp.clients = [non_admin]

    # Calling get_media_info on private channel must use main_bot and succeed
    info = await sp.get_media_info(-1004325759505, 280)
    assert info["file_name"] == "hi_2026.mp4"
    assert info["file_size"] == 50000000
    main_bot.get_messages.assert_awaited_once_with(-1004325759505, 280)


@pytest.mark.asyncio
async def test_upload_with_pool_round_robin_rotation_per_tier():
    """Verify that successive uploads in the same quality tier rotate through all tier admin accounts."""
    pool = TelegramUploadPool()
    target_channel = -100123456789

    # 6 admin accounts:
    # 1080p tier (i % 3 == 0) -> c0, c3
    # 720p  tier (i % 3 == 1) -> c1, c4
    # 480p  tier (i % 3 == 2) -> c2, c5
    admins = [MockClient(name=f"admin_{i}") for i in range(6)]
    pool.clients = list(admins)
    pool._admin_sessions[target_channel] = list(admins)

    used_clients_1080p = []
    used_clients_720p = []

    async def fake_upload(*args, **kwargs):
        return {"file_id": "ok_id", "message_id": 100}

    with patch("services.telegram_upload.upload_video_file", side_effect=fake_upload) as mock_up:
        # First 1080p upload -> should pick admin_0
        await pool.upload_with_pool(file_path="m1.mp4", target_chat=target_channel, quality="1080p")
        used_clients_1080p.append(mock_up.call_args.kwargs["bot_client"].name)

        # Second 1080p upload -> should rotate to admin_3
        await pool.upload_with_pool(file_path="m2.mp4", target_chat=target_channel, quality="1080p")
        used_clients_1080p.append(mock_up.call_args.kwargs["bot_client"].name)

        # Third 1080p upload -> should wrap around to admin_0
        await pool.upload_with_pool(file_path="m3.mp4", target_chat=target_channel, quality="1080p")
        used_clients_1080p.append(mock_up.call_args.kwargs["bot_client"].name)

        # First 720p upload -> should pick admin_1
        await pool.upload_with_pool(file_path="m1_720.mp4", target_chat=target_channel, quality="720p")
        used_clients_720p.append(mock_up.call_args.kwargs["bot_client"].name)

        # Second 720p upload -> should rotate to admin_4
        await pool.upload_with_pool(file_path="m2_720.mp4", target_chat=target_channel, quality="720p")
        used_clients_720p.append(mock_up.call_args.kwargs["bot_client"].name)

    assert used_clients_1080p == ["admin_0", "admin_3", "admin_0"]
    assert used_clients_720p == ["admin_1", "admin_4"]


@pytest.mark.asyncio
async def test_upload_pool_init_parses_session_strings(monkeypatch, tmp_path):
    """Verify that upload_pool.init parses SESSION_STRINGS / MULTI_ACCOUNT_SESSIONS from environment."""
    pool = TelegramUploadPool()
    dummy_dir = str(tmp_path / "empty_sessions")
    Path(dummy_dir).mkdir(parents=True, exist_ok=True)

    fake_session_strings = "BQAAAFakeSessionString1, BQAAAFakeSessionString2"
    monkeypatch.setenv("MULTI_ACCOUNT_SESSIONS", fake_session_strings)

    created_clients = []

    def mock_client_init(*args, **kwargs):
        mc = MagicMock()
        mc.name = kwargs.get("name", "client")
        mc.is_connected = True
        mc.is_initialized = True
        mc.connect = AsyncMock(return_value=True)
        mc.initialize = AsyncMock(return_value=None)
        mc.get_me = AsyncMock(return_value=MagicMock(id=99901, is_premium=False, first_name="Userbot"))
        created_clients.append(mc)
        return mc

    with patch("services.upload_pool.Client", side_effect=mock_client_init):
        await pool.init(api_id=12345, api_hash="abcdef", sessions_dir=dummy_dir)

    assert len(pool.clients) == 2
    assert any("upload_pool_worker_1" in getattr(c, "name", "") for c in pool.clients)
    assert any("upload_pool_worker_2" in getattr(c, "name", "") for c in pool.clients)


@pytest.mark.asyncio
async def test_upload_with_pool_empty_candidates_fallback_no_index_error():
    """Verify that when candidates is empty (e.g. non-channel chat with disconnected sessions),
    upload_with_pool cleanly falls back to the main bot without raising IndexError."""
    pool = TelegramUploadPool()
    private_chat = 987654321  # Non-channel chat ID (doesn't start with -100)

    # Disconnected client in pool -> candidates will be empty
    disc_client = MockClient(name="disconnected_userbot")
    disc_client.is_connected = False
    pool.clients = [disc_client]

    main_bot = MockClient(name="main_bot")
    pool.set_main_client(main_bot)

    with patch("services.telegram_upload.upload_video_file", new_callable=AsyncMock) as mock_up:
        mock_up.return_value = {"file_id": "fallback_ok", "message_id": 999}
        res = await pool.upload_with_pool(
            file_path="movie.mp4",
            target_chat=private_chat,
            quality="1080p",
            fallback_client=main_bot
        )
        assert res.get("file_id") == "fallback_ok"
        mock_up.assert_awaited_once()
        assert mock_up.call_args.kwargs["bot_client"] == main_bot


@pytest.mark.asyncio
async def test_upload_with_pool_quality_case_insensitivity():
    """Verify that quality strings like '1080P', '1080p', and '1080' share the same rotation tier."""
    pool = TelegramUploadPool()
    target_channel = -100123456789

    admins = [MockClient(name=f"admin_{i}") for i in range(6)]
    pool.clients = list(admins)
    pool._admin_sessions[target_channel] = list(admins)

    used = []
    async def fake_upload(*args, **kwargs):
        return {"file_id": "ok_id", "message_id": 100}

    with patch("services.telegram_upload.upload_video_file", side_effect=fake_upload) as mock_up:
        # First 1080p upload with lowercase
        await pool.upload_with_pool(file_path="m1.mp4", target_chat=target_channel, quality="1080p")
        used.append(mock_up.call_args.kwargs["bot_client"].name)

        # Second 1080p upload with uppercase '1080P' -> should advance rotation to admin_3
        await pool.upload_with_pool(file_path="m2.mp4", target_chat=target_channel, quality="1080P")
        used.append(mock_up.call_args.kwargs["bot_client"].name)

    assert used == ["admin_0", "admin_3"]




