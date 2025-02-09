import requests
import datetime
import asyncio
import json
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.formatting import Bold, Code
from aiogram.types import BufferedInputFile  # Для работы с файлами
import os

from dotenv import load_dotenv
# Добавим в начало файла
from typing import Dict, Any

# Дополнительные импорты
import csv
from io import StringIO

# Обновим game_context для хранения дополнительных данных
game_context: Dict[int, Dict[str, Any]] = {}
admin_context = {}
logs_buffer = []
load_dotenv()

# Получаем значения
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
print(ADMIN_ID)
# Инициализация
from aiogram.client.default import DefaultBotProperties

router = Router(name="main")
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    session_timeout=60,
    read_timeout=30,
    connect_timeout=30
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
game_context = {}



async def maintain_typing_status(chat_id: int, stop_event: asyncio.Event):
    """Поддерживает статус печатания до установки события остановки"""
    while not stop_event.is_set():
        await bot.send_chat_action(chat_id, "typing")
        await asyncio.sleep(4)  # Обновляем статус каждые 4 секунды


async def get_ai_response(user_id: int, question: str, chat_id: int) -> str:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    messages = game_context.get(user_id, [])
    messages.append({"role": "user", "content": question})

    data = {
        "messages": messages,
        "model": "deepseek-ai/DeepSeek-V3",
        "max_tokens": 222,
        "temperature": 0.1,
        "top_p": 0.9
    }

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(maintain_typing_status(chat_id, stop_typing))

    try:
        # Увеличиваем таймаут для requests
        response = await asyncio.to_thread(
            lambda: requests.post(
                API_URL,
                headers=headers,
                json=data,
                timeout=60  # Увеличиваем таймаут до 60 секунд
            )
        )
        response.raise_for_status()
        ai_response = response.json()['choices'][0]['message']['content']

        messages.append({"role": "assistant", "content": ai_response})
        game_context[user_id] = messages[-5:]

        return ai_response
    except requests.exceptions.Timeout:
        logging.error("API request timed out")
        return "⚠️ Оракул задумался слишком надолго. Пожалуйста, повторите вопрос."
    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        return "⚠️ Оракул временно недоступен. Пожалуйста, попробуйте позже."
    finally:
        stop_typing.set()
        await typing_task


# Добавляем новую команду в обработчик start/help
@router.message(Command(commands=["start", "help", "menu"]))
async def cmd_start(message: Message):
    """Обработка команд старта и меню"""
    menu_text = (
        "📜 <b>Священный Свиток Команд:</b>\n\n"
        "🔮 /oracle - Призвать Оракула для диалога\n"
        "🌀 /анализ [тема] - Глубинный анализ любой реальности\n"
        "🌌 /эмоции - Энергетический срез коллективного сознания\n"
        "🌠 /знак [название] - Тайны астральных символов\n"
        "💎 /артефакт [название] - Сакральные свойства объектов\n"
        "🌪 /предсказание - Пророчество на текущий квант времени\n\n"
        "✨ <i>Просто напиши вопрос - получь мудрость Вселенной</i>"
    )

    # Создаем интерактивные кнопки
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌀 Анализ", callback_data="cmd_analysis"),
        InlineKeyboardButton(text="🌌 Эмоции", callback_data="cmd_emotions"),
        InlineKeyboardButton(text="🌠 Знаки", callback_data="cmd_signs")
    )
    builder.row(
        InlineKeyboardButton(text="💎 Артефакты", callback_data="cmd_artifacts"),
        InlineKeyboardButton(text="🌪 Пророчества", callback_data="cmd_prophecy"),
        InlineKeyboardButton(text="🔮 Оракул", callback_data="cmd_oracle")
    )

    await message.answer(
        menu_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )


# Добавляем обработчики для кнопок
@router.callback_query(F.data.startswith("cmd_"))
async def handle_menu_buttons(callback: CallbackQuery):
    action = callback.data.split("_")[1]

    instructions = {
        "analysis": "🌀 Введите /анализ [тема] для глубинного исследования",
        "emotions": "🌌 Используйте /эмоции для сканирования энергополя",
        "signs": "🌠 Введите /знак [название] для расшифровки символа",
        "artifacts": "💎 Используйте /артефакт [название] для идентификации",
        "prophecy": "🌪 Пророчество доступно по команде /предсказание",
        "oracle": "🔮 Задайте любой вопрос текстом для диалога с Оракулом"
    }

    await callback.answer()
    await callback.message.answer(
        f"⚡ <b>{instructions[action]}</b>",
        parse_mode=ParseMode.HTML
    )

@router.message(Command("oracle"))
async def cmd_oracle(message: Message):
    """Обработка команды /oracle"""
    await message.answer("🔮 Я здесь! Задайте свой вопрос вселенной:")

@router.message(Command("анализ"))
async def cmd_analysis(message: Message, command: CommandObject):
    """Обработка команды /анализ [тема]"""
    if not command.args:
        await message.answer("🌀 Укажите тему для анализа после команды, например:\n/анализ судьба мира")
        return

    response = await get_ai_response(
        message.from_user.id,
        f"Сделай глубокий анализ по теме: {command.args}. Выяви закономерности, тренды и связи.",
        message.chat.id
    )
    await message.reply(f"🌀 Анализ вселенной по теме '{command.args}':\n\n{response}")

@router.message(Command("эмоции"))
async def cmd_emotions(message: Message):
    """Обработка команды /эмоции"""
    response = await get_ai_response(
        message.from_user.id,
        "Проанализируй текущую эмоциональную динамику в этом чате. Учти последние сообщения.",
        message.chat.id
    )
    await message.reply(f"🌌 Эмоциональный срез реальности:\n\n{response}")

@router.message(Command("знак"))
async def cmd_sign(message: Message, command: CommandObject):
    """Обработка команды /знак [название]"""
    if not command.args:
        await message.answer("🌠 Укажите название знака после команды, например:\n/знак скорпион")
        return

    response = await get_ai_response(
        message.from_user.id,
        f"Объясни значение и влияние знака: {command.args}. Добавь мифологический контекст.",
        message.chat.id
    )
    await message.reply(f"🌠 Тайна знака '{command.args}':\n\n{response}")

@router.message(Command("артефакт"))
async def cmd_artifact(message: Message, command: CommandObject):
    """Обработка команды /артефакт [название]"""
    if not command.args:
        await message.answer("💎 Укажите название артефакта после команды, например:\n/артефакт меч судьбы")
        return

    response = await get_ai_response(
        message.from_user.id,
        f"Опиши свойства и историю артефакта: {command.args}. Если его нет в игре - предложи концепцию.",
        message.chat.id
    )
    await message.reply(f"💊 Тайны артефакта '{command.args}':\n\n{response}")

@router.message(Command("предсказание"))
async def cmd_prophecy(message: Message):
    """Обработка команды /предсказание"""
    response = await get_ai_response(
        message.from_user.id,
        "Дай символическое предсказание для текущего момента. Используй метафоры и архетипы.",
        message.chat.id
    )
    await message.reply(f"🌪 Пророчество на сейчас:\n\n{response}")


@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Главное меню администратора"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Недостаточно прав!")
        return  # Явный возврат вместо неявного

    # Обновленная статистика
    active_users = sum(1 for u in game_context.values() if u.get('last_active'))
    banned_users = sum(1 for u in game_context.values() if u.get('banned', False))

    stats = (
        f"📊 <b>Статистика системы:</b>\n"
        f"👥 Всего пользователей: {len(game_context)}\n"
        f"💬 Активных (7 дней): {active_users}\n"
        f"🚫 Заблокированных: {banned_users}"
    )

    # Обновленная клавиатура
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📤 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton(text="📝 Логи", callback_data="admin_logs")
    )
    builder.row(
        InlineKeyboardButton(text="👤 Поиск", callback_data="admin_search"),
        InlineKeyboardButton(text="📦 Экспорт", callback_data="admin_export")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh")
    )

    await message.answer(
        stats,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("admin_"))
async def handle_admin_actions(callback: CallbackQuery, state: FSMContext):
    """Обработка действий администратора"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Доступ запрещен!")
        return

    action = callback.data.split("_")[1]

    if action == "broadcast":
        await callback.message.answer("📝 Введите текст рассылки:")
        await state.set_state("admin_broadcast_text")

    elif action == "logs":
        log_file = StringIO()
        log_file.write("\n".join(logs_buffer[-100:]))
        log_file.seek(0)
        await callback.message.answer_document(
            document=BufferedInputFile(log_file.read().encode(), filename="logs.txt"))

    elif action == "export":
        # Генерация CSV файла
        csv_file = StringIO()
        writer = csv.writer(csv_file)
        writer.writerow(["ID", "Last Active", "Messages", "Banned"])

        for user_id, data in game_context.items():
            writer.writerow([
                user_id,
                data.get('last_active', 'N/A'),
                data.get('message_count', 0),
                data.get('banned', False)
            ])

        csv_file.seek(0)
        await callback.message.answer_document(
            document=BufferedInputFile(csv_file.read().encode(), filename="users_export.csv")
        )

    elif action == "search":
        await callback.message.answer("🔍 Введите ID пользователя или имя:")
        await state.set_state("admin_search_user")

    await callback.answer()


@router.message(F.text, StateFilter("admin_broadcast_text"))
async def process_broadcast(message: Message, state: FSMContext):
    """Обработка текста рассылки"""
    success = 0
    failed = 0

    for user_id in game_context:
        try:
            await bot.send_message(user_id, f"📢 Рассылка:\n\n{message.text}")
            success += 1
        except Exception as e:
            failed += 1
            logs_buffer.append(f"Failed to send to {user_id}: {str(e)}")

    await message.answer(
        f"✅ Рассылка завершена:\n"
        f"• Успешно: {success}\n"
        f"• Не удалось: {failed}"
    )
    await state.clear()


@router.message(F.text, StateFilter("admin_search_user"))
async def process_user_search(message: Message):
    """Поиск информации о пользователе"""
    search_query = message.text.lower()
    found_users = []

    for user_id, data in game_context.items():
        user = await bot.get_chat(user_id)
        if (search_query in str(user_id) or
                search_query in user.first_name.lower() or
                search_query in (user.last_name or "").lower()):
            found_users.append(
                f"👤 {user.first_name} {user.last_name or ''}\n"
                f"🆔 ID: {user_id}\n"
                f"📅 Последняя активность: {data.get('last_active', 'N/A')}\n"
                f"📩 Сообщений: {data.get('message_count', 0)}\n"
                f"🚫 Статус: {'Заблокирован' if data.get('banned') else 'Активен'}"
            )

    response = "🔍 Результаты поиска:\n\n" + "\n\n".join(found_users[:5]) if found_users else "❌ Пользователи не найдены"
    await message.answer(response)


# Обновим функцию get_ai_response для сбора статистики
async def get_ai_response(user_id: int, question: str, chat_id: int) -> str:
    # Обновляем статистику
    user_data = game_context.setdefault(user_id, {
        'message_count': 0,
        'last_active': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'banned': False
    })

    if user_data.get('banned'):
        return "🚫 Ваш доступ к оракулу ограничен"

    user_data['message_count'] += 1
    user_data['last_active'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Остальная часть функции без изменений



@router.message(Command("ban"))
async def ban_user(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Неавторизованный доступ!")
        return

    try:
        user_id = int(command.args)
        if user_id not in game_context:
            await message.answer("❌ Пользователь не найден")
            return

        game_context[user_id]['banned'] = True
        await message.answer(f"✅ Пользователь {Code(str(user_id))} заблокирован", parse_mode=ParseMode.HTML)

    except ValueError:
        await message.answer("❌ Неверный формат ID. Пример: /ban 123456789")


@router.message(Command("unban"))
async def unban_user(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        user_id = int(command.args)
        game_context[user_id]['banned'] = False
        await message.answer(f"✅ Пользователь {user_id} разблокирован")
    except:
        await message.answer("❌ Использование: /unban <user_id>")


# Обновим обработчик сообщений для проверки блокировки
@router.message(F.text & ~F.text.startswith('/'))
async def handle_general_message(message: Message):
    user_data = game_context.get(message.from_user.id, {})

    if user_data.get('banned'):
        await message.answer("🚫 Ваш доступ к оракулу ограничен")
        return

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