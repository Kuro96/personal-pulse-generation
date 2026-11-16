"""Run with the Hermes Python environment to exercise Telegram callback integration."""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Bot, Update, User
from telegram.ext import Application
from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter

spec = importlib.util.spec_from_file_location("pulse_card_actions", Path(__file__).with_name("__init__.py"))
actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actions)


class CardActionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = Bot("123456:synthetic-test-token")
        self.bot._bot_user = User(123456, "Example Bot", True)
        self.adapter = TelegramAdapter(PlatformConfig(enabled=True))
        self.adapter.set_authorization_check(lambda user_id, *args, **kwargs: user_id == "7")
        self.context = SimpleNamespace(bot=self.bot)

    def update(self, action, user_id=7, caption=False):
        message = {
            "message_id": 42, "date": 1700000000,
            "chat": {"id": -1001234567890, "type": "supergroup", "title": "Example", "is_forum": True},
            "from": self.bot._bot_user.to_dict(), "message_thread_id": 301, "is_topic_message": True,
            "caption" if caption else "text": "A specific card about tides.",
            "reply_markup": {"inline_keyboard": [[
                {"text": "展开讲讲", "callback_data": "pulse:expand"},
                {"text": "关闭按钮", "callback_data": "pulse:close"},
            ]]},
        }
        return Update.de_json({"update_id": 99, "callback_query": {
            "id": "click-99", "from": {"id": user_id, "first_name": "Reader", "is_bot": False},
            "chat_instance": "example", "data": action, "message": message,
        }}, self.bot)

    async def test_close_edits_card_without_agent_dispatch(self):
        calls = []
        async def edit(**kwargs):
            calls.append(("edit", kwargs))
        async def answer(**kwargs):
            calls.append(("answer", kwargs))
        with patch.object(Bot, "edit_message_reply_markup", side_effect=edit), \
             patch.object(Bot, "answer_callback_query", side_effect=answer), \
             patch.object(self.adapter, "handle_message", new_callable=AsyncMock) as dispatch:
            await actions.handle_callback(self.update("pulse:close"), self.context, self.adapter)
        self.assertEqual([name for name, _ in calls], ["edit", "answer"])
        self.assertEqual(calls[0][1]["message_id"], 42)
        self.assertIsNone(calls[0][1]["reply_markup"])
        dispatch.assert_not_awaited()

    async def test_expand_carries_clicked_card_and_real_user_into_same_topic(self):
        with patch.object(Bot, "answer_callback_query", new_callable=AsyncMock) as answer, \
             patch.object(self.adapter, "handle_message", new_callable=AsyncMock) as dispatch:
            await actions.handle_callback(self.update("pulse:expand", caption=True), self.context, self.adapter)
        answer.assert_awaited_once()
        event = dispatch.await_args.args[0]
        self.assertIn("展开讲讲", event.text)
        self.assertEqual(event.reply_to_text, "A specific card about tides.")
        self.assertEqual((event.source.chat_id, event.source.thread_id, event.source.user_id),
                         ("-1001234567890", "301", "7"))
        self.assertFalse(event.source.is_bot)
        self.assertFalse(event.internal)
        self.assertEqual(event.ledger_message_id, "pulse:click-99")
        self.assertEqual(event.reply_anchor_override, "42")
        factories = []
        actions.register(SimpleNamespace(register_telegram_handler=factories.append))
        app = Application.builder().bot(self.bot).build()
        factories[0](app, self.adapter)
        handler = app.handlers[0][0]
        self.assertTrue(handler.check_update(self.update("pulse:close")))
        self.assertFalse(handler.check_update(self.update("unrelated:close")))

    async def test_unauthorized_and_closed_buttons_stop_before_edit_or_dispatch(self):
        closed = self.update("pulse:expand").to_dict()
        closed["callback_query"]["message"].pop("reply_markup")
        updates = [self.update("pulse:close", user_id=8), Update.de_json(closed, self.bot)]
        for update in updates:
            with self.subTest(data=update.callback_query.data), \
                 patch.object(Bot, "answer_callback_query", new_callable=AsyncMock) as answer, \
                 patch.object(Bot, "edit_message_reply_markup", new_callable=AsyncMock) as edit, \
                 patch.object(self.adapter, "handle_message", new_callable=AsyncMock) as dispatch:
                await actions.handle_callback(update, self.context, self.adapter)
                answer.assert_awaited_once()
                edit.assert_not_awaited()
                dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
