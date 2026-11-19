"""Run with the Hermes Python environment to exercise Telegram callback integration."""
import asyncio
import importlib.util
from dataclasses import replace
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Bot, Update, User
from telegram.ext import Application
from gateway.config import PlatformConfig
from gateway.platforms.base import ProcessingOutcome, SendResult
from gateway.session_ingress import deliver_settled
from gateway.session_producer_lifecycle import mark_producer_admission, producer_turn
from plugins.platforms.telegram.adapter import TelegramAdapter

spec = importlib.util.spec_from_file_location("pulse_card_actions", Path(__file__).with_name("__init__.py"))
actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actions)


class CardActionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch('telegram.request.HTTPXRequest.do_request',
                                side_effect=AssertionError('Unexpected Telegram transport call')))
        self.bot = Bot("123456:synthetic-test-token")
        self.bot._bot_user = User(123456, "Example Bot", True)
        self.adapter = TelegramAdapter(PlatformConfig(enabled=True))
        self.adapter.set_authorization_check(lambda user_id, *args, **kwargs: user_id == "7")
        self.context = SimpleNamespace(bot=self.bot)

    def update(self, action, user_id=7, caption=False, card=42, topic=301, click="click-99"):
        message = {
            "message_id": card, "date": 1700000000,
            "chat": {"id": -1001234567890, "type": "supergroup", "title": "Example", "is_forum": True},
            "from": self.bot._bot_user.to_dict(), "message_thread_id": topic, "is_topic_message": True,
            "caption" if caption else "text": ("A specific card about tides." if card == 42 else f"Card {card}."),
            "reply_markup": {"inline_keyboard": [[
                {"text": "展开讲讲", "callback_data": "pulse:expand"},
                {"text": "关闭按钮", "callback_data": "pulse:close"},
            ]]},
        }
        if action.startswith("pulse:retry:"):
            message["reply_markup"]["inline_keyboard"] = [[{"text": "重试展开", "callback_data": action}]]
        return Update.de_json({"update_id": 99, "callback_query": {
            "id": click, "from": {"id": user_id, "first_name": "Reader", "is_bot": False},
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
             patch("gateway.platforms.webhook_ingress.admit_producer", new_callable=AsyncMock) as dispatch:
            await actions.handle_callback(self.update("pulse:close"), self.context, self.adapter)
        self.assertEqual([name for name, _ in calls], ["edit", "answer"])
        self.assertEqual(calls[0][1]["message_id"], 42)
        self.assertIsNone(calls[0][1]["reply_markup"])
        dispatch.assert_not_awaited()

    async def test_expand_carries_clicked_card_and_real_user_into_same_topic(self):
        event = actions.expansion_event(self.update("pulse:expand", caption=True).callback_query,
                                        self.adapter, 99)
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

    async def test_delivery_fifo_and_retry(self):
        admitted = asyncio.Queue()
        async def admit(adapter, event):
            await admitted.put(replace(event))  # durable ingress reconstructs the event
            return SimpleNamespace(status='queued')

        async def click(card, *, topic=301, action='pulse:expand', ident=None):
            update = self.update(action, card=card, topic=topic, click=ident or f'{topic}-{card}')
            await actions.handle_callback(update, self.context, self.adapter)

        async def take():
            return await asyncio.wait_for(admitted.get(), 2)

        with (
            patch('gateway.platforms.webhook_ingress.admit_producer', side_effect=admit),
            patch.object(Bot, 'answer_callback_query', new_callable=AsyncMock),
            patch.object(Bot, 'edit_message_reply_markup', new_callable=AsyncMock) as edit,
            patch.object(self.adapter, '_reactions_enabled', return_value=False),
            patch.object(self.adapter, '_start_typing_refresh', return_value=None),
            patch('gateway.delivery_ledger.ledger_enabled', return_value=False),
            patch.object(self.adapter, '_send_with_retry', return_value=SendResult(success=True)) as send,
        ):
            await click(42)
            await click(42, ident='second-tap')
            await click(43)
            await click(44, topic=302)
            events = {e.message_id: e for e in [await take(), await take()]}
            self.assertEqual(set(events), {'42', '44'})
            self.assertEqual([c.kwargs['message_id'] for c in edit.await_args_list], [42, 43, 44])
            self.assertTrue(all(c.kwargs['reply_markup'] is None for c in edit.await_args_list))
            self.assertTrue(admitted.empty())
            first = events['42']
            runner = SimpleNamespace(_adapter_for_source=lambda source: self.adapter)
            authority = SimpleNamespace(profile_id="test", runner=runner, pending_deliveries={}, native_waiters=set())
            row = {'status': 'queued', 'admission_id': 'a'}
            first._producer_delivery = True
            mark_producer_admission(authority, first, row)
            async with producer_turn(authority, row, first, {}):
                authority.pending_deliveries['a'] = self.adapter, first, 'lane', 'Tides explained', None
            self.assertTrue(admitted.empty())
            await deliver_settled(authority, 'a')
            send.assert_awaited_once()
            self.assertEqual(send.await_args.kwargs['reply_to'], '42')
            second = await take()
            self.assertEqual((second.reply_to_message_id, second.reply_to_text), ('43', 'Card 43.'))
            await self.adapter.on_processing_complete(second, ProcessingOutcome.FAILURE)
            other = events['44']
            other._streamed_final_response = 'Streamed answer'
            await self.adapter.on_processing_complete(other, ProcessingOutcome.SUCCESS)
            await asyncio.gather(*self.adapter._background_tasks)
            self.assertEqual(edit.await_count, 4)
            retry = edit.await_args.kwargs
            self.assertEqual(retry['message_id'], 43)
            token = retry['reply_markup'].inline_keyboard[0][0].callback_data
            await click(43)  # stale original keyboard
            await click(43, action=token, ident='retry-click')
            await click(43, action=token, ident='duplicate-retry')
            retried = await take()
            self.assertNotEqual(retried.edit_revision, second.edit_revision)
            await self.adapter.on_processing_complete(second, ProcessingOutcome.FAILURE)
            retried._streamed_final_response = 'Done'
            await self.adapter.on_processing_complete(retried, ProcessingOutcome.SUCCESS)
            await asyncio.gather(*self.adapter._background_tasks)
            self.assertEqual(edit.await_count, 5)
            self.assertTrue(admitted.empty())

    async def test_timeout_releases_next_even_if_retry_edit_fails(self):
        from telegram.error import BadRequest
        calls = []
        async def admit(adapter, event):
            calls.append(event.message_id)
            if len(calls) == 2:
                await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)  # no delivery
            return object()
        with (
            patch('gateway.platforms.webhook_ingress.admit_producer', side_effect=admit),
            patch.object(actions, 'DELIVERY_TIMEOUT', .05),
            patch.object(self.adapter, '_reactions_enabled', return_value=False),
            patch.object(Bot, 'answer_callback_query', new_callable=AsyncMock),
            patch.object(Bot, 'edit_message_reply_markup', side_effect=[None, None, BadRequest('gone'), None]) as edit,
            self.assertLogs(actions.log, 'ERROR'),
        ):
            for card in (42, 43):
                await actions.handle_callback(self.update('pulse:expand', card=card, click=str(card)),
                                              self.context, self.adapter)
            await asyncio.wait_for(asyncio.gather(*self.adapter._background_tasks), 2)
        self.assertEqual(calls, ['42', '43'])
        self.assertEqual([c.kwargs['message_id'] for c in edit.await_args_list], [42, 43, 42, 43])

    async def test_unauthorized_and_closed_buttons_stop_before_edit_or_dispatch(self):
        closed = self.update("pulse:expand").to_dict()
        closed["callback_query"]["message"].pop("reply_markup")
        updates = [self.update("pulse:close", user_id=8), Update.de_json(closed, self.bot)]
        for update in updates:
            with self.subTest(data=update.callback_query.data), \
                 patch.object(Bot, "answer_callback_query", new_callable=AsyncMock) as answer, \
                 patch.object(Bot, "edit_message_reply_markup", new_callable=AsyncMock) as edit, \
                 patch("gateway.platforms.webhook_ingress.admit_producer", new_callable=AsyncMock) as dispatch:
                await actions.handle_callback(update, self.context, self.adapter)
                answer.assert_awaited_once()
                edit.assert_not_awaited()
                dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
