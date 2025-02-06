import requests
import datetime
import asyncio
import json
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
import os

# Получаем значения из переменных окружения
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_URL = os.environ["API_URL"]
API_KEY = os.environ["API_KEY"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

# Инициализация
router = Router(name="main")
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище для контекста игры
game_context = {}

@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Панель администратора"""
    if message.from_user.id != ADMIN_ID:
        return

    stats = f"📊 Статистика:\nПользователей: {len(game_context)}"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_stats"),
        InlineKeyboardButton(text="📤 Рассылка", callback_data="broadcast")
    )

    await message.answer(stats, reply_markup=builder.as_markup())



async def get_ai_response(user_id: int, question: str) -> str:
    """Получение ответа от AI API"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    # Формируем контекст диалога
    messages = game_context.get(user_id, [])
    messages.append({"role": "user", "content": question})

    data = {
        "messages": messages,
        "model": "deepseek-ai/DeepSeek-V3",
        "max_tokens": 512,
        "temperature": 0.1,
        "top_p": 0.9
    }

    try:
        response = requests.post(API_URL, headers=headers, json=data)
        response.raise_for_status()
        ai_response = response.json()['choices'][0]['message']['content']

        # Обновляем контекст (сохраняем последние 5 сообщений)
        messages.append({"role": "assistant", "content": ai_response})
        game_context[user_id] = messages[-5:]

        return ai_response
    except Exception as e:
        logging.error(f"API Error: {e}")
        return "⚠️ Оракул временно недоступен. Попробуйте позже."


@router.message(Command(commands=["start", "help"]))
async def cmd_start(message: Message):
    """Обработка команды старт"""
    # builder = InlineKeyboardBuilder()
    # builder.add(InlineKeyboardButton(
    #     text="🌀 Открыть портал",
    #     web_app=WebAppInfo(url="https://your-game-webapp.com/")
    # ))

    await message.answer(
        "🛡️ Добро пожаловать в Игру Богов!\n\n"
        "Задайте свой вопрос оракулу или откройте портал для взаимодействия:",

    )


@router.message(F.text)
async def handle_message(message: Message):
    """Обработка всех текстовых сообщений"""
    user_id = message.from_user.id

    # Игнорируем сообщения из групп без прямого обращения
    if message.chat.type != 'private':
        if not message.text.startswith('/oracle'):
            return
        question = message.text.replace('/oracle', '').strip()
    else:
        question = message.text

    # Показываем индикатор набора сообщения
    await bot.send_chat_action(message.chat.id, "typing")

    # Получаем ответ от AI
    response = await get_ai_response(user_id, question)

    # Форматируем ответ
    formatted_response = f"🔮 Оракул провидит:\n\n{response}"
    await message.reply(formatted_response)


@router.callback_query(F.data == "refresh_stats")
async def refresh_stats(callback: CallbackQuery):
    """Обновление статистики"""
    stats = f"📊 Обновленная статистика:\nПользователей: {len(game_context)}"
    await callback.message.edit_text(stats)


async def main():
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())