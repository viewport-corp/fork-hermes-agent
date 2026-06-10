"""Bot API 10.0 bot-to-bot handling and loop protection.

Telegram's Bot-to-Bot Communication Mode (Bot API 10.0, 2026-05-08) delivers
group messages authored by other bots (``from.is_bot=True``). Telegram
requires bots to implement their own loop protection:
https://core.telegram.org/bots/features#bot-to-bot-communication

These tests cover ``TelegramAdapter._should_process_bot_message`` and its
integration with ``_should_process_message``.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig


def _make_adapter(bot_to_bot=None, require_mention=False, bot_username="hermes_bot"):
    from gateway.platforms.telegram import TelegramAdapter

    extra = {
        "require_mention": require_mention,
        # Keep unit tests isolated from TELEGRAM_* env in the parent
        # environment (same convention as test_telegram_group_gating.py).
        "allowed_topics": [],
        "allowed_chats": [],
        "group_allowed_chats": [],
    }
    if bot_to_bot is not None:
        extra["bot_to_bot"] = bot_to_bot

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter._bot = SimpleNamespace(id=999, username=bot_username)
    adapter._message_handler = AsyncMock()
    adapter._mention_patterns = adapter._compile_mention_patterns()
    adapter._forum_lock = asyncio.Lock()
    adapter._forum_command_registered = set()
    return adapter


def _group_message(
    text="hello",
    *,
    chat_id=-100,
    from_user_id=111,
    is_bot=False,
    message_id=42,
):
    return SimpleNamespace(
        message_id=message_id,
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        is_topic_message=False,
        chat=SimpleNamespace(
            id=chat_id, type="group", title="Test Group", is_forum=False
        ),
        from_user=SimpleNamespace(
            id=from_user_id,
            full_name="Peer Bot" if is_bot else "Alice Example",
            first_name="Peer" if is_bot else "Alice",
            is_bot=is_bot,
        ),
        reply_to_message=None,
        date=None,
    )


def test_bot_message_processed_by_default():
    """from.is_bot=True group messages reach the trigger rules (not filtered)."""
    adapter = _make_adapter()
    msg = _group_message(is_bot=True, from_user_id=555, message_id=1)
    assert adapter._should_process_message(msg) is True


def test_human_message_unaffected():
    adapter = _make_adapter()
    msg = _group_message(is_bot=False, message_id=2)
    assert adapter._should_process_message(msg) is True


def test_bot_to_bot_disabled_drops_bot_messages():
    adapter = _make_adapter(bot_to_bot={"enabled": False})
    bot_msg = _group_message(is_bot=True, from_user_id=555, message_id=3)
    human_msg = _group_message(is_bot=False, message_id=4)
    assert adapter._should_process_message(bot_msg) is False
    assert adapter._should_process_message(human_msg) is True


def test_own_messages_ignored():
    adapter = _make_adapter()
    msg = _group_message(is_bot=True, from_user_id=999, message_id=5)
    assert adapter._should_process_message(msg) is False


def test_duplicate_bot_message_ids_deduplicated():
    adapter = _make_adapter()
    first = _group_message(is_bot=True, from_user_id=555, message_id=6)
    duplicate = _group_message(is_bot=True, from_user_id=555, message_id=6)
    assert adapter._should_process_message(first) is True
    assert adapter._should_process_message(duplicate) is False


def test_bot_reply_depth_cap_and_human_reset():
    adapter = _make_adapter(bot_to_bot={"max_reply_depth": 2})
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, message_id=10)
        )
        is True
    )
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=556, message_id=11)
        )
        is True
    )
    # Third consecutive bot message exceeds the depth cap.
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, message_id=12)
        )
        is False
    )
    # A human message resets the chain.
    assert (
        adapter._should_process_message(_group_message(is_bot=False, message_id=13))
        is True
    )
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, message_id=14)
        )
        is True
    )


def test_per_chat_rate_limit():
    adapter = _make_adapter(
        bot_to_bot={"rate_limit_per_minute": 2, "max_reply_depth": 50}
    )
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, message_id=20)
        )
        is True
    )
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=556, message_id=21)
        )
        is True
    )
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, message_id=22)
        )
        is False
    )
    # Other chats keep their own budget.
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, chat_id=-200, message_id=23)
        )
        is True
    )


def test_depth_cap_is_per_chat():
    adapter = _make_adapter(bot_to_bot={"max_reply_depth": 1})
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, message_id=30)
        )
        is True
    )
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, message_id=31)
        )
        is False
    )
    assert (
        adapter._should_process_message(
            _group_message(is_bot=True, from_user_id=555, chat_id=-300, message_id=32)
        )
        is True
    )
