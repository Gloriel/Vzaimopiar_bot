import os
import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

# Настройка основного логирования в файл и в консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Настройка отдельного логгера для постов
post_logger = logging.getLogger('post_logger')
post_logger.setLevel(logging.INFO)
post_handler = logging.FileHandler("posts.log", encoding="utf-8")
post_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
post_logger.addHandler(post_handler)
post_logger.propagate = False  # Предотвращаем дублирование в основном логе

# Попытка импорта Telegram библиотеки
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
    from telegram.error import BadRequest, Forbidden
except ImportError as e:
    logger.error(f"Не установлена библиотека python-telegram-bot: {e}")
    print("Установите библиотеку: pip install python-telegram-bot")
    exit(1)

# Попытка загрузить переменные из .env файла
try:
    from dotenv import load_dotenv
    load_dotenv()
    logger.info("Загружены переменные из .env файла")
except ImportError:
    logger.warning("Библиотека python-dotenv не установлена, используем системные переменные окружения")

# Константы
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')  # может быть числом или @username
BOT_USERNAME = os.getenv('BOT_USERNAME', '').lstrip('@')  # без @

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден в переменных окружения")
    print("Установите BOT_TOKEN в .env или переменных окружения")
    exit(1)

# Категории контента
CATEGORIES = {
    'technology': '📚 ТЕХНОЛОГИИ',
    'money': '💰 ДЕНЬГИ', 
    'media': '📺 МЕДИА',
    'personal': '💫 ЛИЧНОЕ',
    'culture': '🎭 КУЛЬТУРА',
    'science': '🔬 НАУКА',
    'life': '🌿 ЖИЗНЬ'
}

# Простое хранилище данных в памяти
class MemoryStorage:
    def __init__(self):
        self.posts = []
        self.users = {}
        self.user_states = {}
    
    def save_user(self, user_id: int, username: str = None):
        self.users[user_id] = {
            'username': username,
            'last_active': datetime.now(timezone.utc).isoformat()
        }
    
    def save_post(self, user_id: int, category: str, title: str, url: str):
        post = {
            'user_id': user_id,
            'category': category,
            'title': title,
            'url': url,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'id': len(self.posts) + 1
        }
        self.posts.append(post)
        
        # Логируем добавление поста в отдельный файл
        try:
            username = self.users.get(user_id, {}).get('username', 'unknown')
            post_logger.info(f"NEW_POST | UserID: {user_id} | Username: @{username} | "
                           f"Category: {category} | Title: {title} | URL: {url}")
        except Exception as e:
            logger.error(f"Ошибка при логировании поста: {e}")
        
        return post['id']
    
    def get_recent_posts(self, limit_per_category: int = 5, max_total: int = 50):
        posts_by_category = {}
        for category in CATEGORIES.keys():
            category_posts = [p for p in self.posts if p['category'] == category]
            category_posts.sort(key=lambda x: x['created_at'], reverse=True)
            if category_posts:
                posts_by_category[category] = category_posts[:limit_per_category]
        
        total_posts = sum(len(posts) for posts in posts_by_category.values())
        if total_posts > max_total:
            reduction_factor = max_total / total_posts
            for category in list(posts_by_category.keys()):
                new_limit = max(1, int(len(posts_by_category[category]) * reduction_factor))
                posts_by_category[category] = posts_by_category[category][:new_limit]
        
        return {k: v for k, v in posts_by_category.items() if v}

    def get_user_state(self, user_id: int) -> Dict[str, Any]:
        return self.user_states.get(user_id, {'state': 'start', 'data': {}})
    
    def set_user_state(self, user_id: int, state: str, data: dict = None):
        """Устанавливает состояние пользователя"""
        if data is None:
            data = {}
        
        if user_id in self.user_states:
            # Сохраняем существующие данные и обновляем новыми
            current_data = self.user_states[user_id]['data'].copy()
            current_data.update(data)
            data = current_data
        
        self.user_states[user_id] = {'state': state, 'data': data}
    
    def update_user_data(self, user_id: int, **kwargs):
        """Обновляет данные пользователя"""
        if user_id not in self.user_states:
            self.user_states[user_id] = {'state': 'start', 'data': {}}
        self.user_states[user_id]['data'].update(kwargs)
    
    def clear_user_state(self, user_id: int):
        """Очищает состояние пользователя"""
        if user_id in self.user_states:
            del self.user_states[user_id]

# Инициализация хранилища
storage = MemoryStorage()

# Валидация URL
def is_valid_url(url: str) -> bool:
    """Проверяет валидность URL"""
    url = url.strip()
    pattern = re.compile(
        r'^https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+'
        r'(?::\d+)?(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(pattern.match(url))

# Проверка подписки на канал
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    if not CHANNEL_ID:
        return True
        
    try:
        # CHANNEL_ID может быть числом или @username
        chat_id = CHANNEL_ID
        try:
            chat_id = int(chat_id)
        except ValueError:
            if chat_id.startswith('@'):
                chat_id = chat_id
            else:
                chat_id = f"@{chat_id}"
        
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except (BadRequest, Forbidden) as e:
        if "CHAT_NOT_FOUND" in str(e) or "not found" in str(e).lower():
            logger.error(f"Канал не найден. Проверьте CHANNEL_ID: {CHANNEL_ID}")
        else:
            logger.error(f"Ошибка проверки подписки: {e}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при проверке подписки: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
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

async def show_subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает сообщение о необходимости подписки"""
    try:
        if not CHANNEL_ID:
            await show_welcome(update, context)
            return
            
        # Поддержка числового ID
        try:
            chat_link = int(CHANNEL_ID)
            channel_link = f"https://t.me/c/{str(chat_link).replace('-100', '')}"
        except ValueError:
            clean_id = CHANNEL_ID.lstrip('@')
            channel_link = f"https://t.me/{clean_id}"
        
        keyboard = [
            [InlineKeyboardButton("✅ Подписаться", url=channel_link)],
            [InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            "🔐 <b>Подписка на канал «Коллектиум»</b> — ваш билет в сообщество взаимной поддержки!\n\n"
            "Закрытое сообщество авторов, где мы помогаем друг другу расти.\n"
            "Подпишитесь, чтобы получить доступ к системе взаимопиара."
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка в show_subscription_required: {e}")
        await error_handler(update, context)

async def show_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает приветственное сообщение"""
    try:
        welcome_text = (
            "🚀 <b>Добро пожаловать в бот взаимопиара!</b>\n\n"
            "Здесь авторы помогают друг другу бесплатно выводить посты в топ.\n\n"
            "📌 <b>Как это работает:</b>\n"
            "• Добавляешь свои статьи — получаешь живых читателей\n"
            "• Поддерживаешь других авторов — твой контент тоже увидят\n"
            "• Растишь аудиторию без бюджета, через взаимную лояльность\n\n"
            "Всё просто: помогаешь другим — помогают тебе! 🤝"
        )
        
        keyboard = [[InlineKeyboardButton("✅ Начать работу", callback_data="next_to_categories")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=welcome_text, reply_markup=reply_markup, parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                text=welcome_text, reply_markup=reply_markup, parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Ошибка в show_welcome: {e}")
        await error_handler(update, context)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран выбора категории"""
    try:
        query = update.callback_query
        await query.answer()
        
        keyboard = []
        row = []
        for i, (key, value) in enumerate(CATEGORIES.items()):
            callback_data = f"category_{key}"
            row.append(InlineKeyboardButton(value, callback_data=callback_data))
            if len(row) == 3 or i == len(CATEGORIES) - 1:
                keyboard.append(row)
                row = []
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="🎯 <b>На какую тему твое сообщение?</b>\n\nВыбери наиболее подходящую категорию:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        storage.set_user_state(update.effective_user.id, 'awaiting_category')
    except Exception as e:
        logger.error(f"Ошибка в show_categories: {e}")
        await error_handler(update, context)

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор категории"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        category_key = query.data.replace('category_', '')
        
        if category_key not in CATEGORIES:
            await query.edit_message_text("Пожалуйста, выберите корректную категорию.", parse_mode="HTML")
            return
        
        category_name = CATEGORIES[category_key]
        storage.set_user_state(user_id, 'awaiting_title', {'category': category_key})
        
        await query.edit_message_text(
            text=f"🏷️ <b>Отлично! Выбрана категория: {category_name}</b>\n\n"
                 "Теперь введи заголовок своего поста 📝\n"
                 "<i>(не более 50 символов)</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к категориям", callback_data="back_to_categories")]]),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в handle_category_selection: {e}")
        await error_handler(update, context)

async def handle_title_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод заголовка"""
    try:
        if not update.message:
            return
        
        user_id = update.effective_user.id
        title = update.message.text.strip()
        
        if len(title) > 50:
            await update.message.reply_text(
                "❌ <b>Слишком длинный заголовок!</b>\n\n"
                "Заголовок должен быть не более 50 символов. Попробуй короче и ёмче ✍️",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к категориям", callback_data="back_to_categories")]]),
                parse_mode="HTML"
            )
            return
        
        # Получаем категорию из текущего состояния
        user_data = storage.get_user_state(user_id)['data']
        category = user_data.get('category')
        if not category:
            await update.message.reply_text(
                "⚠️ Произошла ошибка. Давай начнем сначала.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="HTML"
            )
            await start(update, context)
            return
        
        # Сохраняем и переходим, не теряя данные
        storage.set_user_state(user_id, 'awaiting_url', {'category': category, 'title': title})
        
        await update.message.reply_text(
            "🔗 <b>Отлично! Теперь пришли ссылку на свой материал</b>\n\n"
            "Убедись, что ссылка начинается с http:// или https://",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к заголовку", callback_data="back_to_title")]]),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в handle_title_input: {e}")
        await error_handler(update, context)

async def handle_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод URL"""
    try:
        if not update.message:
            return
        
        user_id = update.effective_user.id
        url = update.message.text.strip()
        user_data = storage.get_user_state(user_id)['data']
        
        if 'category' not in user_data or 'title' not in user_data:
            await update.message.reply_text(
                "⚠️ Данные утеряны. Давай начнем сначала.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="HTML"
            )
            await start(update, context)
            return
        
        if not is_valid_url(url):
            await update.message.reply_text(
                "❌ <b>Некорректная ссылка!</b>\n\n"
                "Пожалуйста, введите валидный URL, начинающийся с http:// или https://\n"
                "Пример: https://example.com/my-article",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к заголовку", callback_data="back_to_title")]]),
                parse_mode="HTML"
            )
            return
        
        post_id = storage.save_post(user_id, user_data['category'], user_data['title'], url)
        
        if post_id:
            await update.message.reply_text(
                "✅ <b>Отлично! Твой пост успешно добавлен!</b>\n\n"
                "Теперь другие участники увидят твой материал и поддержат его 🤝",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="HTML"
            )
            await show_other_posts(update, context)
        else:
            await update.message.reply_text(
                "❌ <b>Произошла ошибка при сохранении поста.</b>\n\n"
                "Попробуй позже или обратись к администратору.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="HTML"
            )
            storage.clear_user_state(user_id)
    except Exception as e:
        logger.error(f"Ошибка в handle_url_input: {e}")
        await error_handler(update, context)

async def show_other_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает посты других авторов с гиперссылками"""
    try:
        user_id = update.effective_user.id
        posts_by_category = storage.get_recent_posts()
        
        if not posts_by_category:
            no_posts_text = (
                "📭 <b>Пока нет постов для оценки</b>\n\n"
                "К сожалению, других авторов еще нет или их посты на модерации.\n"
                "Попробуй зайти позже — сообщество быстро растет! 🌱"
            )
            if hasattr(update, 'message'):
                await update.message.reply_text(no_posts_text, parse_mode="HTML")
            else:
                await update.callback_query.edit_message_text(no_posts_text, parse_mode="HTML")
            return
        
        message_text = "🎯 <b>Время помочь другим — и получить поддержку в ответ!</b>\n\n"
        message_text += "👇 <i>Здесь свежие посты других авторов:</i>\n\n"
        
        for category_key, posts in posts_by_category.items():
            message_text += f"<b>{CATEGORIES[category_key]}:</b>\n"
            for i, post in enumerate(posts, 1):
                # Создаем гиперссылку вместо простого текста
                message_text += f'{i}. <a href="{post["url"]}">{post["title"]}</a>\n'
            message_text += "\n"
        
        message_text += (
            "💫 <b>Как поддержать авторов:</b>\n"
            "• Поставь лайк 👍\n"
            "• Напиши комментарий 💬\n"
            "• Сделай репост 🔄\n\n"
            "После поддержки нажми «✅ Я сделал»\n"
            "<i>Если понравилось несколько постов — благодарность только приветствуется!</i>"
        )
        
        keyboard = [
            [InlineKeyboardButton("✅ Я поддержал авторов", callback_data="support_done")],
            [InlineKeyboardButton("📣 Пригласить друзей", callback_data="invite_friends")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update, 'message'):
            await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        
        storage.set_user_state(user_id, 'awaiting_support_confirmation')
    except Exception as e:
        logger.error(f"Ошибка в show_other_posts: {e}")
        await error_handler(update, context)

async def handle_support_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает подтверждение выполнения поддержки"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        storage.clear_user_state(user_id)
        
        final_text = (
            "🎉 <b>Отлично! Ты выполнил дневную норму взаимоподдержки.</b>\n\n"
            "Теперь другие авторы сделают то же самое — для тебя.\n"
            "Сила сообщества в взаимности! 🤝\n\n"
            "💡 <i>Увидимся завтра для новой порции поддержки!</i>"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔄 Начать заново", callback_data="start_over")],
            [InlineKeyboardButton("📣 Пригласить друзей", callback_data="invite_friends")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=final_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в handle_support_done: {e}")
        await error_handler(update, context)

async def handle_invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает сообщение для приглашения друзей"""
    try:
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        username = user.username or user.first_name or "автор"
        
        invite_text = (
            f"📣 <b>Пригласи друзей в наше сообщество!</b>\n\n"
            f"Добрый день. Пишу к Вам с предложением. Меня зовут {username}. "
            f"Все мы размещаем свои материалы в разных платформах. И все знаем, как тяжело пробиться в топ статей. "
            f"Если вы считаете, что Ваш голос и Ваши мысли должны увидеть больше людей, добро пожаловать в группу единомышленников!\n\n"
            f"<b>Проект «Взаимопиар»</b> создан для того, чтобы помогать увеличивать число читателей, "
            f"выводить статьи в топ своими лайками, комментами и репостами.\n\n"
            f"Всё абсолютно бесплатно! Вы мою статью чекните, — я Вашу. "
            f"А когда набирается десятки и тем более сотни человек, уже виден реальный результат.\n\n"
            f"💫 <b>Присоединяйся к нашему сообществу!</b>\n"
            f"Просто зайди в этого бота и следуй инструкциям.\n\n"
            f"<i>Вместе мы сможем больше!</i> 🤝"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔗 Поделиться приглашением", url=f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}&text={username} приглашает вас в сообщество взаимной поддержки авторов!")],
            [InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=invite_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в handle_invite_friends: {e}")
        await error_handler(update, context)

async def handle_back_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает навигацию назад"""
    try:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        back_action = query.data

        if back_action == "back_to_categories":
            await show_categories(update, context)
        elif back_action == "back_to_title":
            user_data = storage.get_user_state(user_id)['data']
            if 'category' not in user_data:
                await start(update, context)
                return
            storage.set_user_state(user_id, 'awaiting_title', user_data)
            await query.edit_message_text(
                text="🏷️ <b>Введи заголовок своего поста</b>\n\n<i>(не более 50 символов)</i>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад к категориям", callback_data="back_to_categories")]]),
                parse_mode="HTML"
            )
        elif back_action == "start_over":
            await start(update, context)
        elif back_action == "back_to_main":
            # Возврат из приглашения друзей
            user_state = storage.get_user_state(user_id)
            if user_state['state'] == 'awaiting_support_confirmation':
                await show_other_posts(update, context)
            else:
                await start(update, context)
    except Exception as e:
        logger.error(f"Ошибка в handle_back_navigation: {e}")
        await error_handler(update, context)

async def handle_check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет подписку после нажатия кнопки"""
    try:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id

        if await check_subscription(user_id, context):
            await show_welcome(update, context)
        else:
            await show_subscription_required(update, context)
    except Exception as e:
        logger.error(f"Ошибка в handle_check_subscription: {e}")
        await error_handler(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения"""
    try:
        if not update.message:
            return
        
        user_id = update.effective_user.id
        state = storage.get_user_state(user_id)['state']
        
        if state == 'awaiting_title':
            await handle_title_input(update, context)
        elif state == 'awaiting_url':
            await handle_url_input(update, context)
        else:
            if not update.message.text.startswith('/'):
                await update.message.reply_text(
                    "🤔 <b>Я не понимаю эту команду.</b>\n\nИспользуй /start чтобы начать заново.",
                    parse_mode="HTML"
                )
    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}")
        await error_handler(update, context)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ <b>Произошла ошибка.</b>\n\nПожалуйста, попробуй еще раз или используй /start чтобы начать заново.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

def main():
    """Основная функция запуска бота"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрируем все обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CallbackQueryHandler(show_categories, pattern="^next_to_categories$"))
    application.add_handler(CallbackQueryHandler(handle_category_selection, pattern="^category_"))
    application.add_handler(CallbackQueryHandler(handle_back_navigation, pattern="^back_to_"))
    application.add_handler(CallbackQueryHandler(handle_support_done, pattern="^support_done$"))
    application.add_handler(CallbackQueryHandler(handle_invite_friends, pattern="^invite_friends$"))
    application.add_handler(CallbackQueryHandler(handle_check_subscription, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(start, pattern="^start_over$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    logger.info("Bot is starting...")
    print(f"BOT_TOKEN: {'установлен' if BOT_TOKEN else 'не установлен'}")
    print(f"CHANNEL_ID: {CHANNEL_ID}")
    print(f"BOT_USERNAME: {BOT_USERNAME}")
    
    try:
        application.run_polling()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        print(f"Критическая ошибка: {e}")

if __name__ == '__main__':
    main()