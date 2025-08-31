import os
import re
import logging
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Попытка импорта
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
    from telegram.error import BadRequest, TelegramError
    from telegram.constants import ParseMode
except ImportError as e:
    logger.error(f"Не установлена библиотека python-telegram-bot: {e}")
    print("Установите: pip install python-telegram-bot")
    exit(1)

# Загрузка переменных окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
    logger.info("Загружены переменные из .env")
except ImportError:
    logger.warning("Библиотека python-dotenv не установлена")

# Импорт текстов
try:
    import texts
except ImportError:
    logger.error("Файл texts.py не найден. Создайте его рядом с main.py")
    exit(1)

# Константы
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
BOT_USERNAME = os.getenv('BOT_USERNAME', '').lstrip('@')

# Парсинг ADMIN_IDS
ADMIN_IDS: List[int] = []
admin_ids_str = os.getenv('ADMIN_IDS', '')
for id_str in admin_ids_str.split(','):
    id_str = id_str.strip()
    if id_str.isdigit():
        ADMIN_IDS.append(int(id_str))
    elif id_str:
        logger.warning(f"Некорректный ID админа: {id_str}")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден")
    print("Установите BOT_TOKEN в .env или переменных окружения")
    exit(1)

# Категории
CATEGORIES = {
    'technology': '📚 ТЕХНОЛОГИИ',
    'money': '💰 ДЕНЬГИ',
    'media': '📺 МЕДИА',
    'personal': '💫 ЛИЧНОЕ',
    'culture': '🎭 КУЛЬТУРА',
    'science': '🔬 НАУКА',
    'life': '🌿 ЖИЗНЬ'
}

# Хранилище с сохранением в файл
class JsonStorage:
    def __init__(self, filename: str = "data.json"):
        self.filename = filename
        self.backup_filename = filename + ".backup"
        data = self._load_data()
        self.data = data
        self.next_post_id = data.get('next_post_id', 1)

    def _load_data(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
            # Создаем резервную копию при ошибке
            if os.path.exists(self.filename):
                try:
                    import shutil
                    shutil.copy(self.filename, self.backup_filename)
                    logger.info(f"Создана резервная копия: {self.backup_filename}")
                except Exception as backup_error:
                    logger.error(f"Ошибка создания резервной копии: {backup_error}")
        return {'posts': [], 'users': {}, 'user_states': {}, 'next_post_id': 1}

    def _save_data(self) -> bool:
        try:
            # Создаем временную копию для безопасного сохранения
            temp_filename = self.filename + ".tmp"
            self.data['next_post_id'] = self.next_post_id
            
            with open(temp_filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            
            # Заменяем старый файл новым
            if os.path.exists(self.filename):
                os.replace(temp_filename, self.filename)
            else:
                os.rename(temp_filename, self.filename)
                
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
            # Пытаемся удалить временный файл
            try:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
            except:
                pass
            return False

    @property
    def posts(self) -> List[Dict[str, Any]]:
        return self.data.get('posts', [])

    @property
    def users(self) -> Dict[str, Any]:
        return self.data.get('users', {})

    @property
    def user_states(self) -> Dict[str, Any]:
        return self.data.get('user_states', {})

    def save_user(self, user_id: int, username: Optional[str] = None) -> None:
        if username:
            self.users[str(user_id)] = {
                'username': username,
                'last_active': datetime.now(timezone.utc).isoformat()
            }
            self._save_data()

    def can_user_post(self, user_id: int) -> bool:
        user_posts = [p for p in self.posts if p.get('user_id') == user_id]
        if not user_posts:
            return True

        try:
            latest_post = max(user_posts, key=lambda x: x.get('created_at', ''))
            created_at = latest_post.get('created_at', '')
            
            if not created_at:
                return True
                
            # Нормализуем формат даты
            if created_at.endswith('Z'):
                created_at = created_at[:-1] + '+00:00'
                
            post_dt = datetime.fromisoformat(created_at)
            if post_dt.tzinfo is None:
                post_dt = post_dt.replace(tzinfo=timezone.utc)

            today = datetime.now(timezone.utc).date()
            return post_dt.date() < today
            
        except (ValueError, KeyError) as e:
            logger.warning(f"Ошибка проверки даты поста: {e}")
            return True

    def save_post(self, user_id: int, category: str, title: str, url: str) -> Optional[int]:
        try:
            post = {
                'user_id': user_id,
                'category': category,
                'title': title,
                'url': url,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'id': self.next_post_id
            }
            self.next_post_id += 1
            self.posts.append(post)
            
            if self._save_data():
                username = self.users.get(str(user_id), {}).get('username', 'unknown')
                logger.info(f"NEW_POST | UserID: {user_id} | Username: @{username} | "
                           f"Category: {category} | Title: {title}")
                return post['id']
            return None
            
        except Exception as e:
            logger.error(f"Ошибка сохранения поста: {e}")
            return None

    def delete_post(self, post_id: int) -> bool:
        try:
            for i, post in enumerate(self.posts):
                if post.get('id') == post_id:
                    self.posts.pop(i)
                    return self._save_data()
            return False
        except Exception as e:
            logger.error(f"Ошибка удаления поста #{post_id}: {e}")
            return False

    def delete_all_posts(self) -> int:
        try:
            count = len(self.posts)
            self.data['posts'] = []
            if self._save_data():
                logger.info(f"Удалено {count} постов (все)")
                return count
            return 0
        except Exception as e:
            logger.error(f"Ошибка удаления всех постов: {e}")
            return 0

    def get_recent_posts(self, limit_per_category: int = 5, max_total: int = 50) -> Dict[str, List[Dict[str, Any]]]:
        try:
            posts_by_category = {}
            all_posts = sorted(self.posts, key=lambda x: x.get('created_at', ''), reverse=True)

            for category in CATEGORIES.keys():
                filtered = [p for p in all_posts if p.get('category') == category]
                posts_by_category[category] = filtered[:limit_per_category]

            return {k: v for k, v in posts_by_category.items() if v}
        except Exception as e:
            logger.error(f"Ошибка получения постов: {e}")
            return {}

    def get_user_state(self, user_id: int) -> Dict[str, Any]:
        return self.user_states.get(str(user_id), {'state': 'start', 'data': {}})

    def set_user_state(self, user_id: int, state: str, data: Optional[dict] = None) -> None:
        if data is None:
            data = {}
        user_id_str = str(user_id)
        current = self.user_states.get(user_id_str, {}).get('data', {})
        current.update(data)
        self.user_states[user_id_str] = {'state': state, 'data': current}
        self._save_data()

    def clear_user_state(self, user_id: int) -> None:
        user_id_str = str(user_id)
        if user_id_str in self.user_states:
            del self.user_states[user_id_str]
            self._save_data()


storage = JsonStorage()

# Валидация URL
def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url.strip())
        return all([result.scheme in ['http', 'https'], result.netloc])
    except Exception:
        return False

# Проверка подписки
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not CHANNEL_ID:
        return True

    try:
        if CHANNEL_ID.startswith('@'):
            chat = await context.bot.get_chat(CHANNEL_ID)
            chat_id = chat.id
        else:
            try:
                chat_id = int(CHANNEL_ID)
            except ValueError:
                logger.error(f"CHANNEL_ID должен быть @username или числовой ID: {CHANNEL_ID}")
                return False

        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False

# === АДМИНСКИЕ ФУНКЦИИ ===
async def admin_show_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text(texts.ERROR_ACCESS_DENIED, parse_mode=ParseMode.HTML)
        return

    posts = storage.posts
    if not posts:
        await update.message.reply_text(texts.ADMIN_NO_POSTS, parse_mode=ParseMode.HTML)
        return

    count = min(10, len(posts))
    text = texts.ADMIN_POSTS_HEADER.format(total=len(posts), count=count)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    for post in posts[-count:]:
        username = storage.users.get(str(post['user_id']), {}).get('username', 'unknown')
        category = CATEGORIES.get(post['category'], post['category'])

        message_text = texts.ADMIN_POST_FORMAT.format(
            post_id=post['id'],
            username=username,
            user_id=post['user_id'],
            category=category,
            title=post['title'],
            url=post['url'],
            date=post['created_at'][:16].replace('T', ' ')
        )

        keyboard = [[InlineKeyboardButton(texts.ADMIN_DELETE_BUTTON, callback_data=f"admin_delete_{post['id']}")]]
        try:
            await update.message.reply_text(
                text=message_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Ошибка отправки поста: {e}")

async def admin_delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        await query.message.reply_text(texts.ERROR_ACCESS_DENIED, parse_mode=ParseMode.HTML)
        return

    try:
        post_id = int(query.data.replace('admin_delete_', ''))
    except ValueError:
        await query.edit_message_text("❌ Неверный ID", parse_mode=ParseMode.HTML)
        return

    if storage.delete_post(post_id):
        await query.edit_message_text(f"✅ Пост #{post_id} удалён", parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(texts.ERROR_POST_NOT_FOUND, parse_mode=ParseMode.HTML)

async def admin_delete_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text(texts.ERROR_ACCESS_DENIED, parse_mode=ParseMode.HTML)
        return

    keyboard = [[InlineKeyboardButton(texts.ADMIN_DELETE_ALL_BUTTON, callback_data="confirm_delete_all")]]
    await update.message.reply_text(
        texts.ADMIN_DELETE_ALL_CONFIRM,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def admin_delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        await query.message.reply_text(texts.ERROR_ACCESS_DENIED, parse_mode=ParseMode.HTML)
        return

    count = storage.delete_all_posts()
    await query.edit_message_text(f"✅ Удалено {count} постов", parse_mode=ParseMode.HTML)

# === ОСНОВНЫЕ ОБРАБОТЧИКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        user_id = user.id
        storage.save_user(user_id, user.username)

        if not await check_subscription(user_id, context):
            await show_subscription_required(update, context)
            return

        await show_welcome(update, context)
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")
        await error_handler(update, context)

async def show_subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not CHANNEL_ID:
            await show_welcome(update, context)
            return

        if CHANNEL_ID.startswith('@'):
            link = f"https://t.me/{CHANNEL_ID.lstrip('@')}"
        else:
            clean_id = CHANNEL_ID.replace('-100', '')
            link = f"https://t.me/c/{clean_id}"

        buttons = []
        for btn in texts.SUBSCRIPTION_CHECK_BUTTONS:
            button_text = btn["text"].format(channel_link=link)
            if "url" in btn:
                buttons.append([InlineKeyboardButton(button_text, url=btn["url"].format(channel_link=link))])
            else:
                buttons.append([InlineKeyboardButton(button_text, callback_data=btn["callback_data"])])

        reply_markup = InlineKeyboardMarkup(buttons)

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=texts.SUBSCRIPTION_REQUIRED,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
            except BadRequest:
                pass
        else:
            await update.message.reply_text(
                text=texts.SUBSCRIPTION_REQUIRED,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка показа подписки: {e}")
        await error_handler(update, context)

async def show_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        # Проверяем подписку перед показом основного меню
        user_id = update.effective_user.id
        if not await check_subscription(user_id, context):
            await show_subscription_required(update, context)
            return

        buttons = []
        for row in texts.WELCOME_BUTTONS:
            if not isinstance(row, list):
                continue
            button_row = []
            for btn in row:
                if isinstance(btn, dict) and "text" in btn and "callback_data" in btn:
                    button_row.append(InlineKeyboardButton(btn["text"], callback_data=btn["callback_data"]))
            if button_row:
                buttons.append(button_row)

        reply_markup = InlineKeyboardMarkup(buttons)

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=texts.WELCOME_MESSAGE,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
            except BadRequest:
                pass
        else:
            await update.message.reply_text(
                text=texts.WELCOME_MESSAGE,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка приветствия: {e}")
        await error_handler(update, context)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()

        # Проверяем подписку
        user_id = update.effective_user.id
        if not await check_subscription(user_id, context):
            await show_subscription_required(update, context)
            return

        keyboard = []
        for key, value in CATEGORIES.items():
            keyboard.append([InlineKeyboardButton(value, callback_data=f"category_{key}")])

        await query.edit_message_text(
            text=texts.CHOOSE_CATEGORY,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        storage.set_user_state(update.effective_user.id, 'awaiting_category')
    except Exception as e:
        logger.error(f"Ошибка показа категорий: {e}")
        await error_handler(update, context)

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        
        # Проверяем подписку
        if not await check_subscription(user_id, context):
            await show_subscription_required(update, context)
            return

        category_key = query.data.replace('category_', '')

        if category_key not in CATEGORIES:
            await query.edit_message_text("❌ Ошибка выбора", parse_mode=ParseMode.HTML)
            return

        storage.set_user_state(user_id, 'awaiting_title', {'category': category_key})
        await query.edit_message_text(
            text=texts.AWAITING_TITLE.format(category=CATEGORIES[category_key]),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к категориям", callback_data="back_to_categories")]]),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка выбора категории: {e}")
        await error_handler(update, context)

async def handle_title_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not update.message:
            return

        user_id = update.effective_user.id
        
        # Проверяем подписку
        if not await check_subscription(user_id, context):
            await show_subscription_required(update, context)
            return

        title = update.message.text.strip()

        if len(title) > 50:
            await update.message.reply_text(texts.TITLE_TOO_LONG, parse_mode=ParseMode.HTML)
            return

        user_data = storage.get_user_state(user_id)['data']
        category = user_data.get('category')

        if not category:
            await update.message.reply_text("⚠️ Ошибка. Начнём сначала.", parse_mode=ParseMode.HTML)
            await start(update, context)
            return

        if not storage.can_user_post(user_id):
            await update.message.reply_text(texts.ERROR_LIMIT_REACHED, parse_mode=ParseMode.HTML)
            await show_other_posts(update, context)
            return

        storage.set_user_state(user_id, 'awaiting_url', {'category': category, 'title': title})
        await update.message.reply_text(
            text=texts.AWAITING_URL,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к заголовку", callback_data="back_to_title")]]),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка ввода заголовка: {e}")
        await error_handler(update, context)

async def handle_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not update.message:
            return

        user_id = update.effective_user.id
        
        # Проверяем подписку
        if not await check_subscription(user_id, context):
            await show_subscription_required(update, context)
            return

        url = update.message.text.strip()
        user_data = storage.get_user_state(user_id)['data']

        if 'category' not in user_data or 'title' not in user_data:
            await update.message.reply_text("⚠️ Данные утеряны. Начнём сначала.", parse_mode=ParseMode.HTML)
            await start(update, context)
            return

        if not is_valid_url(url):
            await update.message.reply_text(texts.INVALID_URL, parse_mode=ParseMode.HTML)
            return

        post_id = storage.save_post(user_id, user_data['category'], user_data['title'], url)
        if post_id:
            await update.message.reply_text(texts.POST_ADDED, parse_mode=ParseMode.HTML)
            await show_other_posts(update, context)
        else:
            await update.message.reply_text("❌ Ошибка сохранения. Попробуйте позже.", parse_mode=ParseMode.HTML)

        storage.clear_user_state(user_id)
    except Exception as e:
        logger.error(f"Ошибка ввода URL: {e}")
        await error_handler(update, context)

async def show_other_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        # Проверяем подписку
        user_id = update.effective_user.id
        if not await check_subscription(user_id, context):
            await show_subscription_required(update, context)
            return

        posts_by_cat = storage.get_recent_posts()
        if not posts_by_cat:
            text = texts.NO_OTHER_POSTS
            if update.callback_query:
                await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML)
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            return

        text = texts.OTHER_POSTS_HEADER
        for cat, posts in posts_by_cat.items():
            text += f"\n<b>{CATEGORIES[cat]}</b>:\n"
            for post in posts:
                text += f'• <a href="{post["url"]}">{post["title"]}</a>\n'
            text += "\n"
        text += texts.OTHER_POSTS_FOOTER

        # Проверяем длину сообщения
        if len(text) > 4096:
            text = texts.OTHER_POSTS_HEADER + "\n\n⚠️ Слишком много постов для отображения. Используйте /admin для просмотра всех."

        keyboard = [
            [InlineKeyboardButton("✅ Я поддержал авторов", callback_data="support_done")],
            [InlineKeyboardButton("📣 Пригласить друзей", callback_data="invite_friends")]
        ]

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
            except BadRequest:
                pass
        else:
            await update.message.reply_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

        storage.set_user_state(update.effective_user.id, 'awaiting_support_confirmation')
    except Exception as e:
        logger.error(f"Ошибка показа постов: {e}")
        await error_handler(update, context)

async def handle_support_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    storage.clear_user_state(user_id)
    await query.edit_message_text(
        text=texts.SUPPORT_DONE,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Начать заново", callback_data="start_over")],
            [InlineKeyboardButton("📣 Пригласить друзей", callback_data="invite_friends")]
        ]),
        parse_mode=ParseMode.HTML
    )

async def handle_invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    username = user.username or "автор"
    share_url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}&text={username}+приглашает+в+сообщество+взаимной+поддержки+авторов!"
    await query.edit_message_text(
        text=texts.INVITE_FRIENDS,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Поделиться приглашением", url=share_url)],
            [InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]
        ]),
        parse_mode=ParseMode.HTML
    )

async def handle_back_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "back_to_categories":
        await show_categories(update, context)
    elif data == "back_to_title":
        user_data = storage.get_user_state(user_id)['data']
        if 'category' not in user_data:
            await start(update, context)
            return
        await query.edit_message_text(
            text=texts.AWAITING_TITLE.format(category=CATEGORIES[user_data['category']]),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к категориям", callback_data="back_to_categories")]]),
            parse_mode=ParseMode.HTML
        )
    elif data == "start_over":
        await start(update, context)
    elif data == "back_to_main":
        await start(update, context)

async def handle_check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if await check_subscription(user_id, context):
        # Пользователь подписан - показываем основное меню
        try:
            await query.edit_message_text(
                text="✅ Отлично! Теперь вы можете пользоваться ботом.",
                parse_mode=ParseMode.HTML
            )
            await show_welcome(update, context)
        except BadRequest:
            # Если не удалось редактировать сообщение, отправляем новое
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Отлично! Теперь вы можете пользоваться ботом.",
                parse_mode=ParseMode.HTML
            )
            await show_welcome(update, context)
    else:
        # Пользователь все еще не подписан
        try:
            await query.edit_message_text(
                text="❌ Вы еще не подписались на канал. Пожалуйста, подпишитесь и нажмите проверку снова.",
                parse_mode=ParseMode.HTML
            )
        except BadRequest:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Вы еще не подписались на канал.",
                parse_mode=ParseMode.HTML
            )
        await show_subscription_required(update, context)

async def handle_view_posts_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    storage.save_user(user_id, query.from_user.username)
    if not await check_subscription(user_id, context):
        await show_subscription_required(update, context)
        return
    await show_other_posts(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    state = storage.get_user_state(user_id)['state']

    if state == 'awaiting_title':
        await handle_title_input(update, context)
    elif state == 'awaiting_url':
        await handle_url_input(update, context)
    elif not update.message.text.startswith('/'):
        await update.message.reply_text("Используйте /start", parse_mode=ParseMode.HTML)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Ошибка в боте: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(texts.ERROR_GENERIC, parse_mode=ParseMode.HTML)
        except:
            pass

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    
    # Админские команды
    if ADMIN_IDS:
        application.add_handler(CommandHandler("admin", admin_show_posts))
        application.add_handler(CommandHandler("delete_all", admin_delete_all_command))
        application.add_handler(CallbackQueryHandler(admin_delete_all_callback, pattern=r"^confirm_delete_all$"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Обработчики callback-запросов
    application.add_handler(CallbackQueryHandler(handle_category_selection, pattern=r"^category_"))
    application.add_handler(CallbackQueryHandler(handle_support_done, pattern=r"^support_done$"))
    application.add_handler(CallbackQueryHandler(handle_invite_friends, pattern=r"^invite_friends$"))
    application.add_handler(CallbackQueryHandler(handle_check_subscription, pattern=r"^check_subscription$"))
    application.add_handler(CallbackQueryHandler(handle_view_posts_only, pattern=r"^view_posts_only$"))
    application.add_handler(CallbackQueryHandler(handle_back_navigation, pattern=r"^(back_|start_over|back_to_)"))
    application.add_handler(CallbackQueryHandler(admin_delete_post, pattern=r"^admin_delete_"))
    application.add_handler(CallbackQueryHandler(show_categories, pattern=r"^next_to_categories$"))

    application.add_error_handler(error_handler)

    logger.info("Бот запущен. Ожидание обновлений...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()