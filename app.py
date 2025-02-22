import contextlib
import requests
import datetime
import json
import asyncio
import logging
from aiogram import Dispatcher
from aiohttp import ClientTimeout

dp = Dispatcher()

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import BufferedInputFile
import os
import backoff
import aiohttp
from dotenv import load_dotenv
from typing import Dict, Any, TypedDict, List, Optional
import csv
from io import StringIO

# Настройка расширенного логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# Типизованные данные
class UserContext(TypedDict):
    messages: List[Dict[str, str]]
    message_count: int
    last_active: str
    banned: bool


# Инициализация контекстов
game_context: Dict[int, UserContext] = {}
admin_context = {}
logs_buffer = []

# Загрузка переменных окружения
logger.info("Loading environment variables")
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

if not all([BOT_TOKEN, API_URL, API_KEY, ADMIN_ID]):
    logger.critical("Missing required environment variables")
    raise ValueError("Missing required environment variables")

logger.info(f"Admin ID configured as: {ADMIN_ID}")

# Инициализация бота
router = Router(name="main")
logger.info("Initializing bot with configurations")
# Инициализация бота с увеличенными таймаутами
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    session_timeout=300,  # 5 минут
    read_timeout=300,  # 5 минут
    connect_timeout=60  # 1 минута для установки соединения
)


class APIClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        # Увеличенные таймауты для API клиента
        self.timeout = ClientTimeout(
            total=300,  # Общий таймаут 5 минут
            connect=60,  # 1 минута на подключение
            sock_read=600,  # 4 минуты на чтение
            sock_connect=30  # 30 секунд на установку сокета
        )

    @backoff.on_exception(
        backoff.expo,
        (aiohttp.ClientError, asyncio.TimeoutError),
        max_tries=3,
        max_time=300,  # Увеличенное время для повторных попыток
        giveup=lambda e: isinstance(e, aiohttp.ClientResponseError) and e.status == 429
    )
    async def ensure_session(self):
        if self.session is None:
            import aiohttp
            self.session = aiohttp.ClientSession()
        return self.session

    async def make_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        session = await self.ensure_session()
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        try:
            async with session.post(API_URL, json=data, headers=headers, timeout=self.timeout) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                logger.error("Unauthorized: Check your API_KEY")
            raise

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def __del__(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

async def maintain_typing_status(chat_id: int, stop_event: asyncio.Event):
    """Поддерживает статус печатания с увеличенным интервалом"""
    logger.debug(f"Starting typing status for chat_id: {chat_id}")
    try:
        while not stop_event.is_set():
            await bot.send_chat_action(chat_id, "typing")
            await asyncio.sleep(4.9)  # Увеличенный интервал, чуть меньше 5 секунд
    except Exception as e:
        logger.error(f"Error in typing status: {e}")
    finally:
        logger.debug(f"Stopping typing status for chat_id: {chat_id}")


async def get_ai_response(user_id: int, question: str, chat_id: int) -> str:
    api_client = APIClient()
    user_data = game_context.setdefault(user_id, init_user_context())

    if user_data["banned"]:
        return "🚫 Ваш доступ к оракулу ограничен"

    messages = user_data["messages"][-4:] + [{"role": "user", "content": safe_slice(question, 2000)}]
    data = {
        "messages": messages,
        "model": "deepseek-ai/DeepSeek-V3",
        "max_tokens": 1024,
        "temperature": 0.4,
        "top_p": 0.9
    }

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(maintain_typing_status(chat_id, stop_typing))

    try:
        for attempt in range(3):
            try:
                response_data = await api_client.make_request(data)
                if not isinstance(response_data.get("choices"), list) or not response_data["choices"]:
                    raise ValueError("Invalid API response structure")

                content = response_data["choices"][0].get("message", {}).get("content", "")
                sanitized_response = safe_slice(content.replace('\0', ''), 4000)

                user_data.update({
                    "messages": messages + [{"role": "assistant", "content": sanitized_response}],
                    "message_count": user_data["message_count"] + 1,
                    "last_active": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                game_context[user_id] = user_data

                return sanitized_response

            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    wait_time = min(2 ** attempt * 30, 240)  # Увеличенное время ожидания между попытками
                    logger.warning(f"Rate limit exceeded, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue
                return f"⚠️ Ошибка сервера ({e.status}). Пожалуйста, попробуйте позже."

            except asyncio.TimeoutError:
                if attempt < 2:
                    wait_time = min(2 ** attempt * 30, 240)
                    logger.warning(f"Request timeout, attempt {attempt + 1}/3, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue
                return "⌛ Время ожидания истекло. Пожалуйста, попробуйте позже."

    finally:
        stop_typing.set()
        try:
            await asyncio.wait_for(typing_task, timeout=10)  # Увеличенный таймаут для остановки typing
        except asyncio.TimeoutError:
            typing_task.cancel()


# Add to main() function:
async def cleanup():
    """Cleanup function to close the API client session"""
    await api_client.close()


async def main():
    try:
        dp.include_router(router)
        await dp.start_polling(bot)
    finally:
        await cleanup()


# Вспомогательные функции
def safe_slice(data: Any, max_len: int, default: str = "") -> str:
    """Безопасный срез для любых типов данных"""
    try:
        return str(data)[:max_len]
    except Exception:
        return default[:max_len]


def init_user_context() -> UserContext:
    """Инициализация контекста пользователя"""
    return {
        "messages": [],
        "message_count": 0,
        "last_active": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "banned": False
    }


async def maintain_typing_status(chat_id: int, stop_event: asyncio.Event):
    """Поддерживает статус печатания"""
    logger.debug(f"Starting typing status for chat_id: {chat_id}")
    try:
        while not stop_event.is_set():
            await bot.send_chat_action(chat_id, "typing")
            await asyncio.sleep(4)
    except Exception as e:
        logger.error(f"Error in typing status: {e}")
    finally:
        logger.debug(f"Stopping typing status for chat_id: {chat_id}")


@router.message(Command(commands=["start", "help", "menu"]))
async def cmd_start(message: Message):
    """Обработка команд старта"""
    menu_text = (
        "📜 <b>Свиток Команд:</b>\n\n"
        "/oracle - 🔮 Активировать диалог с Оракулом\n"
        "/анализ - 📊 Анализ любой темы\n"
        "/эмоции - 🌌 Расшифровать эмоциональный код\n"
        "/артефакт - 🏺 Свойства артефакта\n"
        "/предсказание - 🌠 Персональное пророчество"
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌀 Анализ", callback_data="cmd_analysis"),
        InlineKeyboardButton(text="🌌 Эмоции", callback_data="cmd_emotions")
    )
    builder.row(
        InlineKeyboardButton(text="💎 Артефакты", callback_data="cmd_artifacts"),
        InlineKeyboardButton(text="🌪 Пророчества", callback_data="cmd_prophecy")
    )

    await message.answer(menu_text, reply_markup=builder.as_markup())
#
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
async def cmd_oracle(message: Message, command: CommandObject):
    """Обработка команды /oracle с аргументами"""
    if not command.args:
        await message.answer("🔮 Я здесь! Задайте свой вопрос после команды, например:\n/oracle как пройдет мой день?")
        return

    # Создаем и запускаем задачу с typing статусом
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(maintain_typing_status(message.chat.id, stop_typing))

    try:
        response = await get_ai_response(
            message.from_user.id,
            command.args,
            message.chat.id
        )
        await message.reply(f"🔮 Ответ Оракула:\n\n{response}")
    finally:
        # Останавливаем typing статус
        stop_typing.set()
        await typing_task

@router.message(Command("анализ"))
async def cmd_analysis(message: Message, command: CommandObject):
    """Обработка команды /анализ [тема]"""
    logger.info(f"Analysis command received from user {message.from_user.id}")

    if not command.args:
        logger.warning(f"No analysis topic provided by user {message.from_user.id}")
        await message.answer("🌀 Укажите тему для анализа после команды, например:\n/анализ судьба мира")
        return

    try:
        logger.info(f"Getting analysis for topic: {command.args}")
        response = await get_ai_response(
            message.from_user.id,
            f"Сделай глубокий анализ по теме: {command.args}. Выяви закономерности, тренды и связи.",
            message.chat.id
        )

        if response:
            logger.info(f"Analysis response received for user {message.from_user.id}")
            await message.reply(f"🌀 Анализ вселенной по теме '{command.args}':\n\n{response}")
        else:
            logger.error(f"Empty analysis response for user {message.from_user.id}")
            await message.reply("⚠️ Не удалось получить анализ. Пожалуйста, попробуйте позже.")
    except Exception as e:
        logger.error(f"Error in analysis command: {e}", exc_info=True)
        await message.reply("Произошла ошибка при выполнении анализа")



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
    # Проверка блокировки
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


# Остальные обработчики остаются без изменений, но используют обновленный get_ai_response

async def main():
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())