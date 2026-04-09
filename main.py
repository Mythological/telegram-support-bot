"""
Telegram-бот поддержки с использованием форумных тем (топиков).
Для запуска установите переменные окружения:
- BOT_TOKEN: Ваш токен Telegram-бота.
- ADMIN_CHAT_ID: ID группы (супергруппа с включёнными темами).
"""
import os
import json
import logging
import asyncio
import datetime
from datetime import timedelta, timezone
from typing import Tuple, List, Optional, Dict, Any, Callable, Awaitable, Union

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, TelegramObject
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from dotenv import load_dotenv

load_dotenv()

class BanlistCallback(CallbackData, prefix="banlist"):
    page: int

# --- Конфигурация ---
class Config:
    def __init__(self):
        self.bot_token = os.getenv("BOT_TOKEN")
        admin_chat_id_str = os.getenv("ADMIN_CHAT_ID")

        if not self.bot_token:
            raise ValueError("Необходимо установить переменную окружения BOT_TOKEN.")
        if not admin_chat_id_str or not admin_chat_id_str.replace('-', '').isdigit():
            raise ValueError("Переменная окружения ADMIN_CHAT_ID не установлена или имеет неверный формат.")

        self.admin_chat_id = int(admin_chat_id_str)
        self.page_size = 10

        self.bot_dir = os.path.dirname(os.path.abspath(__file__))
        self.meta_dir = os.path.join(self.bot_dir, 'meta')
        os.makedirs(self.meta_dir, exist_ok=True)

        self.users_data_file = os.path.join(self.meta_dir, 'users_data.json')
        self.messages_mapping_file = os.path.join(self.meta_dir, 'messages_mapping.json')
        self.threads_mapping_file = os.path.join(self.meta_dir, 'threads_mapping.json')  # новое
        self.log_file = os.path.join(self.meta_dir, 'admin_log.txt')
        self.messages_file = os.path.join(self.bot_dir, 'messages.json')


# --- Управление данными (без изменений) ---
class DataManager:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.file_path):
            self._save({})
            return {}
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save(self, data: Optional[Dict] = None):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(data if data is not None else self._data, f, indent=4, ensure_ascii=False)

    def get(self, key: Any, default: Any = None) -> Any:
        return self._data.get(str(key), default)

    def set(self, key: Any, value: Any):
        self._data[str(key)] = value
        self._save()

    def delete(self, key: Any):
        if str(key) in self._data:
            del self._data[str(key)]
            self._save()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def __contains__(self, key: Any) -> bool:
        return str(key) in self._data


# --- Инициализация ---
try:
    config = Config()
except ValueError as e:
    logging.critical(e)
    exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

users_data = DataManager(config.users_data_file)
messages_mapping = DataManager(config.messages_mapping_file)
threads_mapping = DataManager(config.threads_mapping_file)  # user_id -> topic_id

try:
    with open(config.messages_file, 'r', encoding='utf-8') as f:
        MESSAGES = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    logger.warning(f"Файл сообщений '{config.messages_file}' не найден. Используются значения по умолчанию.")
    MESSAGES = {}

last_cleanup_time = datetime.datetime.fromtimestamp(0)


# --- Утилиты (без изменений) ---
def log_admin_action(admin_id: int, action: str, details: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Admin ID: {admin_id} | Action: {action} | Details: {details}\n"
    try:
        with open(config.log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except IOError as e:
        logger.error(f"Ошибка записи в лог-файл: {e}")

def get_russian_month(month_number: int) -> str:
    months = {1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля', 5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа', 9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'}
    return months.get(month_number, '')

def format_datetime_for_message(dt_obj: datetime.datetime) -> str:
    if dt_obj == datetime.datetime.max:
        return "навсегда"
    moscow_tz = timezone(timedelta(hours=3))
    dt_moscow = dt_obj.astimezone(moscow_tz)
    return f"{dt_moscow.day} {get_russian_month(dt_moscow.month)} {dt_moscow.year} в {dt_moscow.strftime('%H:%M')} (мск)"

def cleanup_old_messages_if_needed():
    global last_cleanup_time
    if datetime.datetime.now() - last_cleanup_time < timedelta(hours=1):
        return
    thirty_days_ago = datetime.datetime.now() - timedelta(days=30)
    new_mapping_data = {msg_id: data for msg_id, data in messages_mapping.items()
                        if datetime.datetime.fromtimestamp(data.get('timestamp', 0)) > thirty_days_ago}
    if len(new_mapping_data) < len(messages_mapping._data):
        messages_mapping._data = new_mapping_data
        messages_mapping._save()
        logger.info(f"Очистка: удалено {len(messages_mapping._data) - len(new_mapping_data)} старых записей.")
    last_cleanup_time = datetime.datetime.now()

async def is_admin(user_id: int, chat_id: int, bot: Bot) -> bool:
    try:
        admins = await bot.get_chat_administrators(chat_id)
        return any(admin.user.id == user_id for admin in admins)
    except TelegramAPIError as e:
        logger.error(f"Ошибка при проверке статуса администратора для user_id {user_id}: {e}")
        return False

def check_ban_status_and_unban_if_expired(user_id: int) -> Tuple[bool, Optional[str]]:
    user = users_data.get(user_id)
    if not user or 'banned_until' not in user:
        return False, None
    ban_end_time_str = user['banned_until']
    ban_end_time = datetime.datetime.fromisoformat(ban_end_time_str)
    if ban_end_time > datetime.datetime.now():
        return True, format_datetime_for_message(ban_end_time)
    else:
        del user['banned_until']
        if 'ban_reason' in user: del user['ban_reason']
        users_data.set(user_id, user)
        logger.info(f"С пользователя {user_id} снят бан по истечении срока.")
        return False, None

def _parse_ban_args(args: List[str]) -> Tuple[Optional[timedelta], Optional[str]]:
    if not args: return None, None
    duration_str = args[0]
    reason_args = args[1:]
    duration = None
    if len(duration_str) > 1 and duration_str[:-1].isdigit():
        value = int(duration_str[:-1])
        unit = duration_str[-1].lower()
        if unit in ['y', 'г']: duration = timedelta(days=value * 365)
        elif unit in ['w', 'н']: duration = timedelta(weeks=value)
        elif unit in ['d', 'д']: duration = timedelta(days=value)
        elif unit in ['h', 'ч']: duration = timedelta(hours=value)
        elif unit in ['m', 'м']: duration = timedelta(minutes=value)
        else: reason_args.insert(0, duration_str)
    else:
        reason_args.insert(0, duration_str)
    reason = ' '.join(reason_args) if reason_args else None
    return duration, reason

async def _get_target_id_from_context(message: Message) -> Tuple[Optional[int], List[str]]:
    command_args = message.text.split()[1:]
    target_id = None
    remaining_args = command_args
    if message.reply_to_message:
        # В теме ответа — определяем пользователя по thread_id
        thread_id = message.message_thread_id
        if thread_id:
            # ищем user_id по thread_id
            for uid, tid in threads_mapping.items():
                if tid == thread_id:
                    target_id = int(uid)
                    break
        # Если не нашли по теме, пробуем старый метод через mapping сообщений
        if not target_id:
            mapping = messages_mapping.get(message.reply_to_message.message_id)
            if mapping:
                target_id = mapping.get('user_id')
    elif command_args and command_args[0].isdigit():
        target_id = int(command_args[0])
        remaining_args = command_args[1:]
    return target_id, remaining_args


# --- Клавиатуры (без изменений) ---
def _get_pagination_keyboard(page: int, total_pages: int) -> Optional[InlineKeyboardMarkup]:
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="<< Назад", callback_data=BanlistCallback(page=page - 1).pack()))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="Вперед >>", callback_data=BanlistCallback(page=page + 1).pack()))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


# --- Роутеры и обработчики ---
admin_router = Router()
user_router = Router()

admin_router.message.filter(F.chat.id == config.admin_chat_id)
admin_router.callback_query.filter(F.chat.id == config.admin_chat_id)


# --- Вспомогательная функция для создания темы пользователя ---
async def get_or_create_topic_for_user(user_id: int, bot: Bot, username: Optional[str] = None) -> Optional[int]:
    """Возвращает topic_id для пользователя. Если темы нет — создаёт новую."""
    topic_id = threads_mapping.get(user_id)
    if topic_id:
        return topic_id
    # Создаём тему
    topic_name = f"Поддержка пользователя {username or user_id}"
    try:
        topic = await bot.create_forum_topic(config.admin_chat_id, name=topic_name)
        threads_mapping.set(user_id, topic.message_thread_id)
        logger.info(f"Создана тема {topic.message_thread_id} для пользователя {user_id}")
        return topic.message_thread_id
    except TelegramAPIError as e:
        logger.error(f"Не удалось создать тему для {user_id}: {e}")
        return None


# --- Команды пользователя ---
@user_router.message(CommandStart())
async def start_command(message: Message):
    user_id = message.chat.id
    if check_ban_status_and_unban_if_expired(user_id)[0]: return

    now = datetime.datetime.now()
    if user_id not in users_data:
        users_data.set(user_id, {
            'first_launch': now.isoformat(),
            'total_messages': 0,
            'last_message_date': now.isoformat(),
            'username': message.from_user.username if message.from_user else "unknown"
        })
        await message.reply(MESSAGES.get("welcome_user", "Здравствуйте! Отправьте ваше сообщение, и администратор скоро ответит."))
    else:
        user = users_data.get(user_id)
        first_launch_dt = datetime.datetime.fromisoformat(user['first_launch'])
        await message.reply(MESSAGES.get("already_started", "С возвращением! Вы с нами с {date}.").format(date=format_datetime_for_message(first_launch_dt)))

@user_router.message(Command("help"))
async def help_command(message: Message):
    await message.reply(MESSAGES.get("help_message", "Просто отправьте ваше сообщение в этот чат."))


# --- Команды администратора (с поддержкой тем) ---
@admin_router.message(Command("msg"))
async def msg_admin_command(message: Message, bot: Bot):
    args = message.text.split(maxsplit=2)
    if len(args) < 3 or not args[1].isdigit():
        await message.reply(MESSAGES.get("msg_usage", "Использование: /msg <user_id> <текст>"))
        return
    _, user_id_str, text = args
    try:
        await bot.send_message(int(user_id_str), text)
        await message.reply(MESSAGES.get("msg_sent_success", "Сообщение пользователю {user_id} отправлено.").format(user_id=user_id_str))
        log_admin_action(message.from_user.id, "SEND_MSG", f"To user {user_id_str}")
    except TelegramAPIError as e:
        await message.reply(MESSAGES.get("msg_send_error", "Ошибка при отправке: {error}").format(error=e))

@admin_router.message(Command("who"))
async def who_admin_command(message: Message):
    target_user_id, _ = await _get_target_id_from_context(message)
    if not target_user_id:
        await message.reply(MESSAGES.get("who_usage", "Использование: /who <user_id> или ответом на сообщение в теме."))
        return
    user_info = users_data.get(target_user_id)
    if not user_info:
        await message.reply(MESSAGES.get("user_not_found", "Пользователь не найден в базе данных."))
        return
    first_launch_dt = datetime.datetime.fromisoformat(user_info['first_launch'])
    text = MESSAGES.get("user_info_template",
                        "ID: <code>{id}</code>\nUsername: @{uname}\nПервый запуск: {date}").format(
                            id=target_user_id,
                            uname=user_info.get('username', 'н/у'),
                            date=format_datetime_for_message(first_launch_dt)
                        )
    await message.reply(text)
    log_admin_action(message.from_user.id, "GET_USER_INFO", f"For user {target_user_id}")

@admin_router.message(Command("stats"))
async def stats_admin_command(message: Message):
    now = datetime.datetime.now()
    all_users = list(users_data.values())
    active_weekly = sum(1 for u in all_users if 'last_message_date' in u and now - datetime.datetime.fromisoformat(u['last_message_date']) < timedelta(days=7))
    active_monthly = sum(1 for u in all_users if 'last_message_date' in u and now - datetime.datetime.fromisoformat(u['last_message_date']) < timedelta(days=30))
    total_messages = sum(u.get('total_messages', 0) for u in all_users)
    text = MESSAGES.get("stats_template",
                        ("📊 <b>Статистика бота</b>\n\n"
                         "Всего пользователей: {total}\n"
                         "Активных за неделю: {weekly}\n"
                         "Активных за месяц: {monthly}\n"
                         "Всего сообщений: {messages}")).format(
                             total=len(all_users),
                             weekly=active_weekly,
                             monthly=active_monthly,
                             messages=total_messages
                         )
    await message.reply(text)

@admin_router.message(Command("ban"))
async def ban_admin_command(message: Message, bot: Bot):
    target_user_id, ban_args = await _get_target_id_from_context(message)
    if not target_user_id:
        await message.reply(MESSAGES.get("ban_usage", "Использование: /ban <user_id> [срок] [причина] или ответом на сообщение."))
        return
    user_info = users_data.get(target_user_id)
    if not user_info:
        await message.reply(MESSAGES.get("user_not_found", "Пользователь не найден."))
        return
    is_banned, ban_until = check_ban_status_and_unban_if_expired(target_user_id)
    if is_banned:
        await message.reply(MESSAGES.get("user_already_banned", "Пользователь уже заблокирован до {until}.").format(until=ban_until))
        return
    duration, reason = _parse_ban_args(ban_args)
    ban_end_time = datetime.datetime.now() + duration if duration else datetime.datetime.max
    user_info['banned_until'] = ban_end_time.isoformat()
    if reason: user_info['ban_reason'] = reason
    users_data.set(target_user_id, user_info)
    duration_text = str(duration) if duration else "навсегда"
    try:
        await bot.send_message(target_user_id, MESSAGES.get("user_ban_notification", "Вы были заблокированы на {duration}. Причина: {reason}").format(duration=duration_text, reason=reason or 'не указана'))
    except TelegramAPIError:
        pass
    await message.reply(MESSAGES.get("admin_ban_success", "Пользователь {user_id} успешно заблокирован.").format(user_id=target_user_id))
    log_admin_action(message.from_user.id, "BAN", f"User {target_user_id}, duration: {duration_text}, reason: {reason or 'N/A'}")

@admin_router.message(Command("unban"))
async def unban_admin_command(message: Message, bot: Bot):
    target_user_id, _ = await _get_target_id_from_context(message)
    if not target_user_id:
        await message.reply(MESSAGES.get("unban_usage", "Использование: /unban <user_id> или ответом на сообщение."))
        return
    user_info = users_data.get(target_user_id)
    if not user_info or 'banned_until' not in user_info:
        await message.reply(MESSAGES.get("user_not_banned", "Пользователь не заблокирован."))
        return
    del user_info['banned_until']
    if 'ban_reason' in user_info: del user_info['ban_reason']
    users_data.set(target_user_id, user_info)
    try:
        await bot.send_message(target_user_id, MESSAGES.get("user_unbanned_notification", "Вы были разблокированы администратором."))
    except TelegramAPIError: pass
    await message.reply(MESSAGES.get("admin_unban_success", "Пользователь {user_id} разблокирован.").format(user_id=target_user_id))
    log_admin_action(message.from_user.id, "UNBAN", f"User {target_user_id}")

@admin_router.message(Command("banlist"))
async def banlist_admin_command(message: Message, bot: Bot):
    await _send_banlist_page(message, bot, 1)

async def _send_banlist_page(m: Union[Message, CallbackQuery], bot: Bot, page: int):
    banned_users = []
    for uid, uinfo in users_data.items():
        is_banned, until = check_ban_status_and_unban_if_expired(int(uid))
        if is_banned:
            banned_users.append({'id': uid, 'uname': uinfo.get('username', 'н/у'), 'reason': uinfo.get('ban_reason', 'н/у'), 'until': until})
    if not banned_users:
        text = MESSAGES.get("no_banned_users", "Заблокированных пользователей нет.")
        if isinstance(m, CallbackQuery): await m.answer(text, show_alert=True)
        else: await m.reply(text)
        return
    total_pages = (len(banned_users) + config.page_size - 1) // config.page_size
    page = max(1, min(page, total_pages))
    paginated = banned_users[(page - 1) * config.page_size : page * config.page_size]
    user_lines = [MESSAGES.get("banned_user_line", "ID: <code>{id}</code> @{uname} - до {until} (Причина: {reason})").format(**u) for u in paginated]
    text = MESSAGES.get("banned_list_header", "<b>Список заблокированных (стр. {p}/{tp}):</b>\n\n").format(p=page, tp=total_pages) + "\n".join(user_lines)
    markup = _get_pagination_keyboard(page, total_pages)
    if isinstance(m, CallbackQuery):
        await m.message.edit_text(text, reply_markup=markup)
    else:
        await m.reply(text, reply_markup=markup)
    log_admin_action(m.from_user.id, "GET_BANLIST", f"Page {page}")

@admin_router.callback_query(BanlistCallback.filter())
async def banlist_navigation_handler(cq: CallbackQuery, bot: Bot, callback_data: BanlistCallback):
    await cq.answer()
    await _send_banlist_page(cq, bot, callback_data.page)

# Новая команда для закрытия темы (удалить или закрыть)
@admin_router.message(Command("closetopic"))
async def closetopic_command(message: Message, bot: Bot):
    """Закрывает текущую тему (удаляет её)."""
    thread_id = message.message_thread_id
    if not thread_id:
        await message.reply("Эта команда должна использоваться внутри темы.")
        return
    # Находим пользователя, связанного с этой темой
    user_id = None
    for uid, tid in threads_mapping.items():
        if tid == thread_id:
            user_id = int(uid)
            break
    if user_id:
        threads_mapping.delete(user_id)
    try:
        await bot.delete_forum_topic(config.admin_chat_id, thread_id)
        await message.reply("Тема закрыта и удалена.")
        log_admin_action(message.from_user.id, "CLOSE_TOPIC", f"Topic {thread_id}, user {user_id}")
    except TelegramAPIError as e:
        await message.reply(f"Ошибка при удалении темы: {e}")


# --- Обработка сообщений пользователя (с созданием темы) ---
@user_router.message(F.chat.type == 'private')
async def handle_user_message(message: Message, bot: Bot):
    user_id = message.chat.id
    is_banned, until = check_ban_status_and_unban_if_expired(user_id)
    if is_banned:
        user = users_data.get(user_id)
        reason = user.get('ban_reason', 'не указана')
        await message.reply(MESSAGES.get("user_is_banned_message", "Вы заблокированы до {until}. Причина: {reason}.").format(until=until, reason=reason))
        return

    now = datetime.datetime.now()
    user_data = users_data.get(user_id, {})
    user_data['total_messages'] = user_data.get('total_messages', 0) + 1
    user_data['last_message_date'] = now.isoformat()
    users_data.set(user_id, user_data)

    # Получаем или создаём тему для этого пользователя
    username = message.from_user.username
    topic_id = await get_or_create_topic_for_user(user_id, bot, username)
    if not topic_id:
        await message.reply("Не удалось создать тему для обращения. Пожалуйста, попробуйте позже.")
        return

    try:
        # Пересылаем сообщение в группу, указывая тему
        fwded = await bot.forward_message(
            chat_id=config.admin_chat_id,
            from_chat_id=user_id,
            message_id=message.message_id,
            message_thread_id=topic_id
        )
        messages_mapping.set(fwded.message_id, {'user_id': user_id, 'user_message_id': message.message_id, 'timestamp': now.timestamp()})
        cleanup_old_messages_if_needed()
    except TelegramAPIError as e:
        logger.error(f"Не удалось переслать сообщение от {user_id} в тему {topic_id}: {e}")
        await message.reply(MESSAGES.get("forward_error", "Не удалось доставить сообщение администратору. Пожалуйста, попробуйте позже."))


# --- Ответ администратора в теме ---
@admin_router.message(F.reply_to_message)
async def handle_admin_reply(message: Message, bot: Bot):
    # Определяем user_id либо через thread_id, либо через mapping
    user_id = None
    thread_id = message.message_thread_id
    if thread_id:
        for uid, tid in threads_mapping.items():
            if tid == thread_id:
                user_id = int(uid)
                break
    if not user_id:
        # fallback: ищем по mapping сообщения, на которое отвечаем
        mapping = messages_mapping.get(message.reply_to_message.message_id)
        if mapping:
            user_id = mapping.get('user_id')
    if not user_id:
        await message.reply(MESSAGES.get("reply_to_unknown", "Не удалось определить пользователя для ответа."))
        return

    try:
        # Копируем сообщение администратора в личку пользователя
        await bot.copy_message(user_id, message.chat.id, message.message_id)
        log_admin_action(message.from_user.id, "REPLY", f"To user {user_id}")
    except TelegramAPIError as e:
        logger.error(f"Ошибка ответа пользователю {user_id}: {e}")
        await message.reply(MESSAGES.get("reply_error", "Не удалось отправить ответ. Ошибка: {error}").format(error=e))


# --- Middleware для проверки прав администратора (без изменений) ---
class AdminAuthMiddleware(BaseMiddleware):
    def __init__(self, admin_chat_id: int):
        self.admin_chat_id = admin_chat_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        bot = data.get('bot')
        if not bot or not await is_admin(event.from_user.id, self.admin_chat_id, bot):
            logger.warning(f"Попытка несанкционированного доступа от user_id {event.from_user.id}")
            if isinstance(event, CallbackQuery):
                await event.answer("У вас нет прав для этого действия.", show_alert=True)
            return
        return await handler(event, data)


# --- Запуск бота ---
async def main():
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    admin_router.message.middleware(AdminAuthMiddleware(config.admin_chat_id))
    admin_router.callback_query.middleware(AdminAuthMiddleware(config.admin_chat_id))

    dp.include_router(admin_router)
    dp.include_router(user_router)

    try:
        logger.info("Бот запущен и готов к работе.")
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Критическая ошибка при работе бота: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
