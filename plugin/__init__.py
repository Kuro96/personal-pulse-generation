"""Telegram inline actions for Personal Pulse cards."""
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
    event.reply_anchor_override = str(card.message_id)
    event.reply_to_message_id = str(card.message_id)
    event.reply_to_text = card.text_html or card.caption_html
    event.reply_to_is_own_message = True
    event.auto_skill = ["cooperative-answering", "personal-pulse-generation"]
    event.allow_gateway_control = False
    event.reply_expected = True
    return event


async def handle_callback(update, context, adapter):
    from telegram import Message
    from telegram.error import BadRequest

    query = update.callback_query
    message = query.message
    if not isinstance(message, Message) or query.data not in {"pulse:expand", "pulse:close"}:
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
    if query.data == "pulse:close":
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        await query.answer()
        return
    await query.answer()
    await adapter.handle_message(expansion_event(query, adapter, update.update_id))


def register(ctx):
    def wire(application, adapter):
        from telegram.ext import CallbackQueryHandler

        async def on_callback(update, context):
            await handle_callback(update, context, adapter)

        application.add_handler(CallbackQueryHandler(on_callback, pattern=r"^pulse:(?:expand|close)$"))

    ctx.register_telegram_handler(wire)
