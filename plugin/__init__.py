"""Telegram inline actions for Personal Pulse cards."""
import asyncio
import logging
import secrets
from collections import deque
from datetime import datetime, timezone


def expansion_event(query, adapter, update_id):
    from telegram import Message
    from gateway.platforms.base import MessageType

    card = query.message
    clicked = Message.de_json({
        "message_id": card.message_id,
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": card.chat.to_dict(),
        "from": query.from_user.to_dict(),
        "message_thread_id": card.message_thread_id,
        "is_topic_message": card.is_topic_message,
        "text": "展开讲讲这张 Pulse 卡片。直接围绕卡片内容深入解释，补充必要的背景、原理和具体例子。",
    }, query.get_bot())
    event = adapter._build_message_event(clicked, MessageType.TEXT, update_id)
    event.ledger_message_id = "pulse:" + query.id
    event.edit_revision = event.ledger_message_id
    event.reply_anchor_override = str(card.message_id)
    event.reply_to_message_id = str(card.message_id)
    event.reply_to_text = card.text_html or card.caption_html
    event.reply_to_is_own_message = True
    event.auto_skill = ["cooperative-answering", "personal-pulse-generation"]
    event.allow_gateway_control = False
    event.reply_expected = True
    return event


log = logging.getLogger(__name__)
DELIVERY_TIMEOUT = 900


class CardActions:
    """One adapter's click lanes. Producer admission commits before execution or delivery;
    its reconstructed event carries the click revision through the terminal processing hook.
    An empty turn can also report SUCCESS, so require evidence of a final text send or stream.
    """
    def __init__(self, adapter):
        self.adapter = adapter
        self.cards = {}  # (chat, topic, message) -> consumed or current retry token
        self.lanes = {}
        self.waiters = {}
        complete = adapter.on_processing_complete
        send_final = adapter.send_final_ledgered

        async def observe_send(event, *args, **kwargs):
            revision = getattr(event, 'edit_revision', None)
            if revision in self.waiters:
                # Native ingress restores edit_revision and the card anchor, but not ledger_message_id.
                event.ledger_message_id = revision
            result, owner = await send_final(event, *args, **kwargs)
            if revision in self.waiters:
                event._pulse_text_delivered = result.success
            return result, owner

        async def observe_complete(event, outcome):
            from gateway.platforms.event import ProcessingOutcome
            waiter = self.waiters.get(getattr(event, 'edit_revision', None))
            if waiter is not None and not waiter.done():
                delivered = (getattr(event, '_pulse_text_delivered', False)
                             or getattr(event, '_streamed_final_response', ''))
                waiter.set_result(outcome is ProcessingOutcome.SUCCESS and bool(delivered))
            await complete(event, outcome)

        adapter.send_final_ledgered = observe_send
        adapter.on_processing_complete = observe_complete

    def enqueue(self, key, query, event, hidden):
        lane = key[:2]
        item = (key, query, event, hidden)
        if lane in self.lanes:
            self.lanes[lane].append(item)
            return
        self.lanes[lane] = deque([item])
        task = asyncio.create_task(self.drain(lane))
        self.adapter._background_tasks.add(task)
        task.add_done_callback(self.adapter._background_tasks.discard)

    async def deliver(self, event):
        from gateway.platforms.webhook_ingress import admit_producer
        waiter = asyncio.get_running_loop().create_future()
        self.waiters[event.edit_revision] = waiter
        try:
            receipt = await admit_producer(self.adapter, event)
            return receipt is not None and await waiter
        finally:
            del self.waiters[event.edit_revision]

    async def drain(self, lane):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.error import TelegramError
        queue = self.lanes[lane]
        try:
            while queue:
                key, query, event, hidden = queue.popleft()
                if not await hidden:
                    continue
                try:
                    if await asyncio.wait_for(self.deliver(event), DELIVERY_TIMEOUT):
                        continue
                except Exception:
                    log.exception('Pulse expansion failed for %s', key)
                token = 'pulse:retry:' + secrets.token_hex(8)
                self.cards[key] = token
                try:
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton('重试展开', callback_data=token)]]))
                except TelegramError:
                    log.exception('Could not restore retry on Pulse card %s', key)
        finally:
            del self.lanes[lane]


async def handle_callback(update, context, adapter):
    from telegram import Message
    from telegram.error import BadRequest

    query = update.callback_query
    message = query.message
    retry = isinstance(query.data, str) and query.data.startswith("pulse:retry:")
    if not isinstance(message, Message) or not (retry or query.data in ("pulse:expand", "pulse:close")):
        await query.answer(text="这张卡片已不可用。")
        return
    if not await adapter._callback_authorized(query, adapter._callback_ctx(query), "无权操作这张卡片。"):
        return
    adapter._accept_update()
    if message.from_user is None or message.from_user.id != context.bot.id:
        await query.answer(text="这不是当前机器人的卡片。")
        return
    buttons = message.reply_markup.inline_keyboard if message.reply_markup else ()
    if not any(button.callback_data == query.data for row in buttons for button in row):
        await query.answer(text="这张卡片的按钮已关闭。")
        return
    state = getattr(adapter, '_pulse_card_actions', None)
    if state is None:
        state = adapter._pulse_card_actions = CardActions(adapter)
    key = (message.chat.id, message.message_thread_id, message.message_id)
    if key in state.cards and query.data != state.cards[key]:
        await query.answer(text="这张卡片已处理。")
        return
    event = None if query.data == "pulse:close" else expansion_event(query, adapter, update.update_id)
    state.cards[key] = None
    hidden = asyncio.get_running_loop().create_future()
    if event is not None:
        state.enqueue(key, query, event, hidden)
    try:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        hidden.set_result(True)
    finally:
        if not hidden.done():
            hidden.set_result(False)
            if retry:
                state.cards[key] = query.data
            else:
                del state.cards[key]
    await query.answer()


def register(ctx):
    def wire(application, adapter):
        from telegram.ext import CallbackQueryHandler

        async def on_callback(update, context):
            await handle_callback(update, context, adapter)

        application.add_handler(CallbackQueryHandler(on_callback, pattern=r"^pulse:(?:expand|close|retry:[0-9a-f]{16})$"))

    ctx.register_telegram_handler(wire)
