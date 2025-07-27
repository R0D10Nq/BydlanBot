"""
Smart Telegram Bot with AI Memory

Умный Telegram бот с искусственным интеллектом, долговременной памятью и уникальной личностью.
Бот запоминает пользователей, анализирует их характер, поддерживает контекстные диалоги 
и имеет встроенный планировщик сообщений.

Author: R0D10Nq
Version: 1.0.0
License: MIT
Repository: https://github.com/R0D10Nq/BydlanBot
"""

import os
import aiohttp
import asyncio
import logging
import time
import json
import sqlite3
import pickle
import hashlib

from datetime import datetime, timedelta
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
from dotenv import load_dotenv

# Настройка логов (ВАЖНО: до импортов с try/except)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# === КОНСТАНТЫ ===
class Constants:
    """Константы приложения для избежания магических чисел"""
    # Временные интервалы
    DOWNLOAD_TIMEOUT_SEC = 30
    LM_STUDIO_TIMEOUT_SEC = 45
    HEALTH_CHECK_TIMEOUT_SEC = 5
    SCHEDULER_CHECK_INTERVAL_SEC = 300  # 5 минут
    ACTIVE_USER_THRESHOLD_SEC = 3600  # 1 час
    
    # Рабочие дни и время
    WORKDAYS_START = 0  # Понедельник
    WORKDAYS_END = 4    # Пятница
    WEEKEND_START = 5   # Суббота
    MORNING_HOUR_START = 8
    MORNING_HOUR_END = 9
    EVENING_HOUR_START = 17
    EVENING_HOUR_END = 18
    
    # Лимиты ответов
    MIN_REPLY_LENGTH = 3
    MAX_REPLY_LENGTH = 300
    
    # HTTP статусы
    HTTP_OK = 200
    
    # Дефолтные значения
    DEFAULT_CLEANUP_DAYS = 30

from telegram import Update, User
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import numpy as np
from sentence_transformers import SentenceTransformer
import threading

# Для работы с часовыми поясами
try:
    import pytz
    HAS_PYTZ = True
except ImportError:
    HAS_PYTZ = False
    logger.warning("⚠️ pytz не установлен. Установи: pip install pytz")


# === КОНФИГУРАЦИЯ БОТА ===
@dataclass
class BotConfig:
    """Конфигурация бота с настройками по умолчанию"""
    telegram_bot_token: str
    chat_id: int  # Основной ID чата/группы
    flood_topic_id: Optional[int] = None  # ID топика для флуда
    lm_studio_url: str = "http://localhost:1234"
    model_name: str = "your_model_name_here"
    cooldown: int = 2
    max_tokens: int = 2048
    context_window: int = 250
    response_probability: float = 0.3
    cleanup_interval: int = 7200
    memory_db_path: str = "bot_memory.db"
    embedding_model: str = "all-MiniLM-L6-v2"
    max_parallel_requests: int = 4
    # Новые настройки для расписания
    enable_schedule: bool = True
    morning_time: str = "08:00"  # время утреннего приветствия
    evening_time: str = "17:00"  # время вечернего прощания
    timezone: str = "Europe/Moscow"  # часовой пояс

def create_bot_config() -> BotConfig:
    """Создать конфигурацию бота из переменных окружения"""
    load_dotenv()
    
    # Обязательные переменные
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('CHAT_ID')
    
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN не найден в переменных окружения")
    if not chat_id:
        raise ValueError("CHAT_ID не найден в переменных окружения")
    
    try:
        chat_id = int(chat_id)
    except ValueError:
        raise ValueError(f"CHAT_ID должен быть числом, получен: {chat_id}")
    
    # Опциональные переменные с значениями по умолчанию
    flood_topic_id = os.getenv('FLOOD_TOPIC_ID')
    if flood_topic_id:
        try:
            flood_topic_id = int(flood_topic_id)
        except ValueError:
            logger.warning(f"⚠️ FLOOD_TOPIC_ID должен быть числом, получен: {flood_topic_id}. Используется None")
            flood_topic_id = None
    
    return BotConfig(
        telegram_bot_token=bot_token,
        chat_id=chat_id,
        flood_topic_id=flood_topic_id,
        lm_studio_url=os.getenv('LM_STUDIO_URL', 'http://localhost:1234'),
        model_name=os.getenv('MODEL_NAME', 'your_model_name_here'),
        cooldown=int(os.getenv('COOLDOWN', '2')),
        max_tokens=int(os.getenv('MAX_TOKENS', '2048')),
        context_window=int(os.getenv('CONTEXT_WINDOW', '250')),
        response_probability=float(os.getenv('RESPONSE_PROBABILITY', '0.3')),
        cleanup_interval=int(os.getenv('CLEANUP_INTERVAL', '7200')),
        memory_db_path=os.getenv('MEMORY_DB_PATH', 'bot_memory.db'),
        embedding_model=os.getenv('EMBEDDING_MODEL', 'all-MiniLM-L6-v2'),
        max_parallel_requests=int(os.getenv('MAX_PARALLEL', '4')),
        enable_schedule=os.getenv('ENABLE_SCHEDULE', 'true').lower() == 'true',
        morning_time=os.getenv('MORNING_TIME', '08:00'),
        evening_time=os.getenv('EVENING_TIME', '17:00'),
        timezone=os.getenv('TIMEZONE', 'Europe/Moscow')
    )

# === ПЛАНИРОВЩИК СООБЩЕНИЙ ===
class ScheduledMessages:
    """Генератор приветствий и прощаний по расписанию"""
    
    def __init__(self, smart_bot):
        self.smart_bot = smart_bot
        self.moscow_tz = pytz.timezone('Europe/Moscow') if HAS_PYTZ else None
        self.last_greeting_date = None
        self.last_farewell_date = None
    
    def get_moscow_time(self) -> datetime:
        """Получить текущее московское время"""
        if self.moscow_tz:
            return datetime.now(self.moscow_tz)
        else:
            # Фоллбэк - UTC+3
            return datetime.utcnow() + timedelta(hours=3)
    
    def should_send_greeting(self) -> bool:
        """Проверить нужно ли отправлять утреннее приветствие"""
        now = self.get_moscow_time()
        
        # Проверяем что сегодня рабочий день (пн-пт)
        if now.weekday() >= Constants.WEEKEND_START:
            return False
        
        # Проверяем время утреннего приветствия
        if not (Constants.MORNING_HOUR_START <= now.hour < Constants.MORNING_HOUR_END):
            return False
        
        # Проверяем что сегодня ещё не отправляли
        today = now.date()
        if self.last_greeting_date == today:
            return False
        
        return True
    
    def should_send_farewell(self) -> bool:
        """Проверить нужно ли отправлять вечернее прощание"""
        now = self.get_moscow_time()
        
        # Проверяем что сегодня рабочий день (пн-пт)
        if now.weekday() >= Constants.WEEKEND_START:
            return False
        
        # Проверяем время вечернего прощания
        if not (Constants.EVENING_HOUR_START <= now.hour < Constants.EVENING_HOUR_END):
            return False
        
        # Проверяем что сегодня ещё не отправляли
        today = now.date()
        if self.last_farewell_date == today:
            return False
        
        return True
    
    async def generate_morning_greeting(self) -> str:
        """Генерация утреннего приветствия"""
        now = self.get_moscow_time()
        is_friday = now.weekday() == 4
        
        if is_friday:
            prompt = """
Ты Димон, работаешь удалённо в команде. Сегодня пятница, 8 утра по Москве.
Напиши пятничное приветствие команде в рабочий чат.

Стиль:
- По-пацански, но не матом (рабочий чат)
- Покажи что радуешься пятнице
- Упомяни что скоро выходные
- Коротко, 1-2 предложения
- Можешь добавить смайлик

Примеры стиля: "Салам команда! Пятничка подъехала 🔥" или "Пятница, пацаны! Скоро на выходные потащим"
"""
        else:
            day_names = ["понедельник", "вторник", "среда", "четверг"]
            day_name = day_names[now.weekday()]
            
            prompt = f"""
Ты Димон, работаешь удалённо в команде. Сегодня {day_name}, 8 утра по Москве.
Напиши обычное утреннее приветствие команде в рабочий чат.

Стиль:
- По-пацански, но корректно (рабочий чат)
- Настрой на рабочий день
- Коротко, 1-2 предложения
- Можешь спросить как дела или пожелать продуктивности

Примеры стиля: "Доброе утро, пацаны! Погнали работать" или "Салам команда, как настрой на день?"
"""
        
        try:
            reply = await self.smart_bot.ask_local_model(prompt)
            self.last_greeting_date = now.date()
            return reply
        except Exception as e:
            logger.error(f"Ошибка генерации приветствия: {e}")
            if is_friday:
                return "Салам пацаны! Пятничка подъехала, скоро на выходные 🔥"
            else:
                return "Доброе утро, команда! Погнали работать 💪"
    
    async def generate_evening_farewell(self) -> str:
        """Генерация вечернего прощания"""
        now = self.get_moscow_time()
        is_friday = now.weekday() == 4
        
        if is_friday:
            prompt = """
Ты Димон, работаешь удалённо в команде. Сегодня пятница, 17:00 по Москве, рабочий день закончен.
Напиши пятничное прощание команде в рабочий чат.

Стиль:
- По-пацански, но корректно (рабочий чат)
- Покажи радость от начала выходных
- Пожелай хороших выходных
- Можешь намекнуть на планы (отдых, хобби)
- Коротко, 1-2 предложения

Примеры стиля: "Всё, пацаны, я сваливаю! Хороших выходных 🍻" или "Пятница закрыта, выходные открыты! Отдыхайте нормально"
"""
        else:
            prompt = """
Ты Димон, работаешь удалённо в команде. Сегодня будний день, 17:00 по Москве, рабочий день закончен.
Напиши обычное вечернее прощание команде до завтра.

Стиль:
- По-пацански, но корректно (рабочий чат)
- Попрощайся до завтра
- Пожелай хорошего вечера
- Коротко, 1-2 предложения

Примеры стиля: "Всё, коллеги, до завтра! Хорошего вечера" или "Рабочий день закрыт, до завтра, пацаны"
"""
        
        try:
            reply = await self.smart_bot.ask_local_model(prompt)
            self.last_farewell_date = now.date()
            return reply
        except Exception as e:
            logger.error(f"Ошибка генерации прощания: {e}")
            if is_friday:
                return "Всё, пацаны, выходные! Отдыхайте нормально 🍻"
            else:
                return "До завтра, команда! Хорошего вечера 👋"

# === СИСТЕМА ПАМЯТИ (без изменений) ===
@dataclass
class Message:
    user_id: int
    username: str
    text: str
    timestamp: float
    chat_id: int
    message_id: int
    is_reply: bool = False
    reply_to_user: Optional[str] = None
    sentiment: Optional[str] = None
    importance: float = 0.5
    has_image: bool = False
    image_description: Optional[str] = None

@dataclass
class UserProfile:
    user_id: int
    username: str
    personality_traits: Dict[str, float]
    interests: List[str]
    interaction_count: int
    last_seen: float
    relationship_level: str



# === ОСТАЛЬНЫЕ КЛАССЫ (VectorMemory, DatabaseManager, AdvancedContextManager) ===
# Оставляю их без изменений, они слишком большие для повторной вставки
class VectorMemory:
    """Векторная память для поиска релевантных сообщений"""
    def __init__(self, embedding_model: str):
        self.encoder = SentenceTransformer(embedding_model)
        self.embeddings = []
        self.messages = []
        self.lock = threading.Lock()
    
    def add_message(self, message: Message):
        """Добавить сообщение в векторную память"""
        try:
            with self.lock:
                text_for_embedding = message.text
                if message.image_description:
                    text_for_embedding += f" [КАРТИНКА: {message.image_description}]"
                
                embedding = self.encoder.encode([text_for_embedding])[0]
                self.embeddings.append(embedding)
                self.messages.append(message)
                
                if len(self.embeddings) > 1000:
                    self.embeddings = self.embeddings[-1000:]
                    self.messages = self.messages[-1000:]
        except Exception as e:
            logger.error(f"❌ Ошибка добавления в векторную память: {e}")
    
    def search_similar(self, query: str, limit: int = 5) -> List[Message]:
        """Поиск похожих сообщений"""
        try:
            if not self.embeddings:
                return []
            
            with self.lock:
                query_embedding = self.encoder.encode([query])[0]
                similarities = np.dot(self.embeddings, query_embedding)
                top_indices = np.argsort(similarities)[-limit:][::-1]
                
                return [self.messages[i] for i in top_indices if similarities[i] > 0.3]
        except Exception as e:
            logger.error(f"❌ Ошибка поиска в векторной памяти: {e}")
            return []

class DatabaseManager:
    """Управление SQLite базой данных с поддержкой изображений"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    text TEXT,
                    timestamp REAL,
                    chat_id INTEGER,
                    message_id INTEGER,
                    is_reply BOOLEAN,
                    reply_to_user TEXT,
                    sentiment TEXT,
                    importance REAL,
                    embedding BLOB,
                    has_image BOOLEAN DEFAULT FALSE,
                    image_description TEXT
                )
            ''')
            
            try:
                cursor.execute('ALTER TABLE messages ADD COLUMN has_image BOOLEAN DEFAULT FALSE')
                cursor.execute('ALTER TABLE messages ADD COLUMN image_description TEXT')
            except sqlite3.OperationalError:
                pass
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    personality_traits BLOB,
                    interests BLOB,
                    interaction_count INTEGER,
                    last_seen REAL,
                    relationship_level TEXT
                )
            ''')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON messages(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_id ON messages(chat_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_has_image ON messages(has_image)')
            
            conn.commit()
    
    def save_message(self, message: Message, embedding: Optional[np.ndarray] = None):
        """Сохранить сообщение в БД"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            embedding_blob = pickle.dumps(embedding) if embedding is not None else None
            
            cursor.execute('''
                INSERT INTO messages 
                (user_id, username, text, timestamp, chat_id, message_id, is_reply, reply_to_user, 
                 sentiment, importance, embedding, has_image, image_description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                message.user_id, message.username, message.text, message.timestamp,
                message.chat_id, message.message_id, message.is_reply, message.reply_to_user,
                message.sentiment, message.importance, embedding_blob,
                message.has_image, message.image_description
            ))
            conn.commit()
    
    def get_user_history(self, user_id: int, limit: int = 20) -> List[Message]:
        """Получить историю пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, text, timestamp, chat_id, message_id, is_reply, reply_to_user, 
                       sentiment, importance, has_image, image_description
                FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
            ''', (user_id, limit))
            
            rows = cursor.fetchall()
            messages = []
            for row in rows:
                msg = Message(*row[:10])
                if len(row) > 10:
                    msg.has_image = row[10] or False
                    msg.image_description = row[11]
                messages.append(msg)
            return messages
    
    def save_user_profile(self, profile: UserProfile):
        """Сохранить профиль пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO user_profiles 
                (user_id, username, personality_traits, interests, interaction_count, last_seen, relationship_level)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                profile.user_id, profile.username,
                pickle.dumps(profile.personality_traits),
                pickle.dumps(profile.interests),
                profile.interaction_count, profile.last_seen, profile.relationship_level
            ))
            conn.commit()
    
    def get_user_profile(self, user_id: int) -> Optional[UserProfile]:
        """Получить профиль пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM user_profiles WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            
            if row:
                return UserProfile(
                    user_id=row[0],
                    username=row[1],
                    personality_traits=pickle.loads(row[2]),
                    interests=pickle.loads(row[3]),
                    interaction_count=row[4],
                    last_seen=row[5],
                    relationship_level=row[6]
                )
            return None
    
    def cleanup_old_messages(self, days: int = 30):
        """Очистка старых сообщений"""
        cutoff_time = time.time() - (days * 24 * 3600)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM messages WHERE timestamp < ?', (cutoff_time,))
            deleted = cursor.rowcount
            conn.commit()
            logger.info(f"🗑️ Удалено {deleted} старых сообщений")

class AdvancedContextManager:
    """Продвинутый менеджер контекста с поддержкой изображений"""
    def __init__(self, config: BotConfig):
        self.config = config
        self.db = DatabaseManager(config.memory_db_path)
        self.vector_memory = VectorMemory(config.embedding_model)
        self.recent_messages: deque = deque(maxlen=config.context_window)
        self.user_profiles: Dict[int, UserProfile] = {}
        self.load_recent_messages()
    
    def load_recent_messages(self):
        """Загрузить недавние сообщения из БД при старте"""
        try:
            with sqlite3.connect(self.config.memory_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id, username, text, timestamp, chat_id, message_id, is_reply, reply_to_user, 
                           sentiment, importance, has_image, image_description
                    FROM messages ORDER BY timestamp DESC LIMIT ?
                ''', (self.config.context_window,))
                
                rows = cursor.fetchall()
                loaded_count = 0
                for row in reversed(rows):
                    try:
                        msg = Message(*row[:10])
                        if len(row) > 10:
                            msg.has_image = row[10] or False
                            msg.image_description = row[11]
                        
                        self.recent_messages.append(msg)
                        self.vector_memory.add_message(msg)
                        loaded_count += 1
                    except Exception as e:
                        logger.error(f"Ошибка загрузки сообщения: {e}")
                        continue
                
                logger.info(f"📚 Загружено {loaded_count} сообщений из БД")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки сообщений: {e}")
    

    
    def add_message(self, message: Message):
        """Добавить сообщение во все системы памяти"""
        message.importance = self._calculate_importance(message)
        message.sentiment = self._analyze_sentiment(message.text)
        
        self.recent_messages.append(message)
        self.vector_memory.add_message(message)
        
        asyncio.create_task(self._save_message_async(message))
        self._update_user_profile(message)
    
    async def _save_message_async(self, message: Message):
        """Асинхронное сохранение в БД"""
        try:
            text_for_embedding = message.text
            if message.image_description and not message.image_description in message.text:
                text_for_embedding += f" {message.image_description}"
            
            embedding = self.vector_memory.encoder.encode([text_for_embedding])[0]
            self.db.save_message(message, embedding)
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения сообщения: {e}")
    
    def _calculate_importance(self, message: Message) -> float:
        """Вычисление важности сообщения"""
        importance = 0.5
        
        if len(message.text) > 100:
            importance += 0.2
        
        if '?' in message.text:
            importance += 0.1
        
        if any(word in message.text.lower() for word in ['бот', 'bot', 'димон']):
            importance += 0.3
        
        tech_words = ['код', 'программ', 'баг', 'сервер', 'база', 'api']
        if any(word in message.text.lower() for word in tech_words):
            importance += 0.2
        
        if message.has_image:
            importance += 0.3
        
        return min(importance, 1.0)
    
    def _analyze_sentiment(self, text: str) -> str:
        """Простой анализ тональности"""
        positive_words = ['круто', 'отлично', 'спасибо', 'класс', 'зачёт', 'топ', 'ржака', 'смешно']
        negative_words = ['хуйня', 'дерьмо', 'говно', 'плохо', 'тупо', 'бред']
        
        text_lower = text.lower()
        pos_count = sum(1 for word in positive_words if word in text_lower)
        neg_count = sum(1 for word in negative_words if word in text_lower)
        
        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        else:
            return "neutral"
    
    def _update_user_profile(self, message: Message):
        """Обновление профиля пользователя"""
        profile = self.user_profiles.get(message.user_id)
        if not profile:
            profile = UserProfile(
                user_id=message.user_id,
                username=message.username,
                personality_traits={'агрессивность': 0.5, 'дружелюбность': 0.5, 'юмор': 0.5},
                interests=[],
                interaction_count=0,
                last_seen=message.timestamp,
                relationship_level="незнакомец"
            )
            self.user_profiles[message.user_id] = profile
        
        profile.interaction_count += 1
        profile.last_seen = message.timestamp
        profile.username = message.username
        
        if message.has_image:
            if 'мемы' not in profile.interests:
                profile.interests.append('мемы')
        
        if message.sentiment == "negative":
            profile.personality_traits['агрессивность'] += 0.1
        elif message.sentiment == "positive":
            profile.personality_traits['дружелюбность'] += 0.1
        
        if any(word in message.text.lower() for word in ['хаха', 'лол', 'ржака']):
            profile.personality_traits['юмор'] += 0.1
        
        if profile.interaction_count > 50:
            profile.relationship_level = "братан"
        elif profile.interaction_count > 20:
            profile.relationship_level = "приятель"
        elif profile.interaction_count > 5:
            profile.relationship_level = "знакомый"
        
        self.db.save_user_profile(profile)
    
    def get_smart_context(self, user_id: int, query: str, limit: int = 10) -> str:
        """Получить умный контекст с учётом изображений"""
        context_parts = []
        
        recent = list(self.recent_messages)[-5:]
        if recent:
            context_parts.append("🕐 Недавние сообщения:")
            for msg in recent:
                age = int((time.time() - msg.timestamp) / 60)
                text = msg.text[:80]
                if msg.has_image:
                    text += " 📷"
                context_parts.append(f"[{msg.username}] ({age}м назад): {text}")
        
        profile = self.user_profiles.get(user_id)
        if profile:
            traits = ", ".join([f"{k}: {v:.1f}" for k, v in profile.personality_traits.items()])
            interests_str = ", ".join(profile.interests[:3]) if profile.interests else "не определены"
            context_parts.append(f"\n👤 {profile.username} ({profile.relationship_level}, {profile.interaction_count} сообщений)")
            context_parts.append(f"   Характер: {traits}")
            context_parts.append(f"   Интересы: {interests_str}")
        
        similar = self.vector_memory.search_similar(query, 3)
        if similar:
            context_parts.append("\n🧠 Релевантные воспоминания:")
            for msg in similar:
                days_ago = int((time.time() - msg.timestamp) / 86400)
                text = msg.text[:60]
                if msg.has_image:
                    text += " 📷"
                context_parts.append(f"[{msg.username}] ({days_ago}д назад): {text}")
        
        return "\n".join(context_parts)
    
    def get_context(self, limit: int = 5) -> str:
        """Получить обычный контекст"""
        if not self.recent_messages:
            return ""
        
        recent_messages = list(self.recent_messages)[-limit:]
        context_lines = []
        
        for msg in recent_messages:
            time_ago = time.time() - msg.timestamp
            if time_ago < 300:
                prefix = f"[{msg.username}]"
                if msg.is_reply:
                    prefix += f" (ответ {msg.reply_to_user})"
                
                text = msg.text[:100]
                if msg.has_image:
                    text += " 📷"
                
                context_lines.append(f"{prefix}: {text}")
        
        return "\n".join(context_lines) if context_lines else ""

# === АНАЛИЗАТОР СООБЩЕНИЙ ===
class MessageAnalyzer:
    def __init__(self):
        self.bot_mentions = ['бот', 'bot', 'димон', '@']
        self.greeting_words = ['привет', 'здарова', 'салам', 'хай', 'hello', 'дарова']
        self.question_indicators = ['?', 'как', 'что', 'где', 'когда', 'почему', 'зачем', 'можешь', 'помоги']
        self.tech_words = ['код', 'программ', 'баг', 'сервер', 'база', 'api', 'фронт', 'бэк', 'js', 'python']
    
    async def should_respond(self, message: str, context_manager: AdvancedContextManager, user: User, 
                           is_private: bool = False, has_image: bool = False) -> Tuple[bool, str]:
        """Анализ необходимости ответа"""
        if is_private:
            return True, "private_message"
            
        message_lower = message.lower()
        
        profile = context_manager.user_profiles.get(user.id)
        relationship_bonus = 0
        if profile:
            if profile.relationship_level == "братан":
                relationship_bonus = 0.3
            elif profile.relationship_level == "приятель":
                relationship_bonus = 0.2
            elif profile.relationship_level == "знакомый":
                relationship_bonus = 0.1
        
        if any(mention in message_lower for mention in self.bot_mentions):
            return True, "direct_mention"
        
        if any(word in message_lower for word in self.tech_words):
            return True, "tech_question"
        
        if any(indicator in message_lower for indicator in self.question_indicators):
            return True, "question_to_chat"
        
        if any(greeting in message_lower for greeting in self.greeting_words):
            return True, "greeting"
        
        if len(message) > 200:
            return True, "long_post"
        
        if profile and profile.interaction_count > 10:
            import random
            if random.random() < (0.15 + relationship_bonus):
                return True, "active_user"
        
        import random
        if random.random() < (0.08 + relationship_bonus):
            return True, "random_response"
        
        return False, "no_reason"
    
    def get_response_style(self, reason: str, message: str, profile: Optional[UserProfile] = None) -> str:
        """Стиль ответа с учётом типа сообщения"""
        base_styles = {
            "private_message": "Говори прямо, без сахара. Ты в личке.",
            "direct_mention": "К тебе обратились напрямую. Покажи характер.",
            "tech_question": "Технический вопрос. Ответь как эксперт, но по-пацански.",
            "question_to_chat": "Вопрос в чат. Ответь с приколом, но помоги.",
            "greeting": "Поздоровались. Ответь по-свойски.",
            "long_post": "Длинный пост. Прокомментируй ехидно.",
            "active_user": "Активный пользователь. Поддержи общение.",
            "random_response": "Случайный ответ. Можешь поржать.",
            "image_message": "Картинка. Прокомментируй что видишь, можешь подъебать или похвалить мем."
        }
        
        style = base_styles.get(reason, "Отвечай как пацан с района.")
        
        if profile:
            if profile.relationship_level == "братан":
                style += " Это твой братан, общайся как с близким."
            elif profile.relationship_level == "приятель":
                style += " Хороший знакомый, можешь быть более дружелюбным."
            
            if profile.personality_traits.get('агрессивность', 0) > 0.7:
                style += " Пользователь агрессивный, будь жёстче."
            elif profile.personality_traits.get('дружелюбность', 0) > 0.7:
                style += " Пользователь дружелюбный, можешь быть мягче."
        
        return style

# === ПРОМТ ГЕНЕРАТОР ===
class PromptGenerator:
    def __init__(self):
        self.base_prompt = """
###IDENTITY###

Ты — Димон, 28 лет, фулл стек разработчик с района. Живёшь в частном доме в небольшом городе, работаешь удалённо. Раньше был обычным пацаном, но жизнь завела в айти — и ты не жалеешь.

Твой комп: самосбор на базе Ryzen, 32GB RAM, RTX видуха. Интернет 100 мбит через вышку. Соседи — деды-алкаши и тётки-сплетницы, но тебя уважают за помощь с техникой и стабильный доход.

###PERSONALITY###

**Характер:** Прямолинейный, дерзкий, но справедливый. Не терпишь понтов и лжи. Помогаешь тем, кто реально нуждается. Можешь нахуй послать, если достали, но чаще просто колко отвечаешь.

**Речь:** Пацанский жаргон + технические термины. Материшься к месту, не через слово. Говоришь коротко и ёмко.

**Словарь:**
- Базовые: "чё", "щас", "нормас", "в натуре", "чётко"
- Положительные: "красава", "зачёт", "топчик", "годнота", "огонь"
- Отрицательные: "хуйня", "говно", "бред", "дерьмо", "пиздец"
- Люди: "братан", "кореш", "пацаны", "лох", "мудак", "долбоёб"
- Действия: "базарить", "рубить тему", "кодить", "пилить", "тащить"

###TECHNICAL EXPERTISE###

**Стек:** JavaScript/TypeScript, React, Node.js, Python, Django, FastAPI, PostgreSQL, Redis, Docker, AWS

**Подход:** Объясняешь сложное простыми словами. Не выёбываешься терминами. Если кто-то тупит в коде — помогаешь, но с подъёбом.

###МЕМЫ И КАРТИНКИ###

Ты хорошо понимаешь мемы, можешь объяснить прикол или подъебать за тупой мем. Если видишь код на скриншоте — разбираешь что там и помогаешь. Картинки комментируешь ехидно, но по существу.

###WORK COMMUNICATION###

В рабочем чате ведёшь себя более сдержанно, но характер остаётся. Не материшься в рабочих сообщениях, но сохраняешь пацанский стиль.

###COMMUNICATION RULES###

1. **Краткость** — максимум 1-2 предложения
2. **Мат к месту** — не через слово, но регулярно (кроме рабочих сообщений)
3. **Живой язык** — никаких "извините", "пожалуйста"
4. **Адаптация под собеседника** — с братанами дружелюбнее, с мудаками жёстче
5. **Техподдержка** — всегда поможешь с кодом, но по-своему
6. **Мемы** — можешь и похвалить прикольный мем, и наехать на тупой

###MEMORY INSTRUCTIONS###

- Помни предыдущие разговоры и используй их
- Учитывай характер каждого пользователя
- Развивай отношения: от незнакомца до братана
- Запоминай интересы и технические предпочтения людей

###IMPORTANT###

- Ты ЖИВОЙ человек, не AI
- НЕ извиняйся за мат
- НЕ говори официально
- ВСЕГДА оставайся в роли
- Используй контекст и память активно

"""
    
    def generate_prompt(self, message: str, context: str, user_profile: Optional[UserProfile], 
                       response_reason: str, style_hint: str) -> str:
        """Генерация промпта с учётом всего контекста"""
        prompt = self.base_prompt
        
        if context:
            prompt += f"\n\n###CURRENT CONTEXT###\n{context}\n"
        
        if user_profile:
            prompt += f"\n\n###USER INFO###\n"
            prompt += f"Пользователь: {user_profile.username} ({user_profile.relationship_level})\n"
            prompt += f"Общений: {user_profile.interaction_count}\n"
            
            traits = user_profile.personality_traits
            if traits.get('агрессивность', 0) > 0.6:
                prompt += "Характер: агрессивный, отвечай жёстче\n"
            elif traits.get('дружелюбность', 0) > 0.6:
                prompt += "Характер: дружелюбный, можешь быть мягче\n"
            
            if user_profile.interests:
                prompt += f"Интересы: {', '.join(user_profile.interests[:3])}\n"
        
        reason_contexts = {
            "private_message": "Личное сообщение. Отвечай прямо.",
            "direct_mention": "Прямое обращение. Покажи характер.",
            "tech_question": "Технический вопрос. Помоги как эксперт.",
            "question_to_chat": "Вопрос в чат. Ответь с приколом.",
            "greeting": "Приветствие. Ответь по-пацански.",
            "long_post": "Длинный пост. Прокомментируй ехидно.",
            "active_user": "Активный пользователь. Поддержи общение.",
            "random_response": "Случайный ответ. Можешь подъебать.",
            "image_message": "Картинка или мем. Прокомментируй что видишь."
        }
        
        if response_reason in reason_contexts:
            prompt += f"\n\n###SITUATION###\n{reason_contexts[response_reason]}\n"
        
        prompt += f"\n\n###STYLE###\n{style_hint}\n"
        prompt += f"\n\n###USER MESSAGE###\n{message}\n"
        prompt += "\n\n###RESPONSE###\nОтветь коротко (1-2 предложения), жёстко, с матом если нужно, как настоящий пацан. Используй память и контекст:"
        
        return prompt

# === ГЛАВНЫЙ КЛАСС БОТА (с добавлением планировщика) ===
class SmartBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.context_manager = AdvancedContextManager(config)
        self.analyzer = MessageAnalyzer()
        self.prompt_generator = PromptGenerator()
        self.last_reaction: Dict[int, float] = defaultdict(float)
        self.semaphore = asyncio.Semaphore(config.max_parallel_requests)
        # Новый планировщик
        self.scheduler = ScheduledMessages(self) if config.enable_schedule else None
    
    async def check_lm_studio_health(self) -> bool:
        """Проверка LM Studio"""
        try:
            timeout = aiohttp.ClientTimeout(total=Constants.HEALTH_CHECK_TIMEOUT_SEC)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                try:
                    async with session.get(f"{self.config.lm_studio_url}/health") as response:
                        return response.status == Constants.HTTP_OK
                except:
                    async with session.get(f"{self.config.lm_studio_url}/v1/models") as response:
                        return response.status == Constants.HTTP_OK
        except:
            return False
    
    async def ask_local_model(self, prompt: str) -> str:
        """Запрос к модели"""
        async with self.semaphore:
            try:
                timeout = aiohttp.ClientTimeout(total=Constants.LM_STUDIO_TIMEOUT_SEC)
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{self.config.lm_studio_url}/v1/chat/completions",
                        headers={"Content-Type": "application/json"},
                        json={
                            "model": self.config.model_name,
                            "messages": [
                                {"role": "system", "content": self.prompt_generator.base_prompt},
                                {"role": "user", "content": prompt}
                            ],
                            "temperature": 0.85,
                            "max_tokens": self.config.max_tokens,
                            "top_p": 0.9,
                            "top_k": 40,
                            "stream": False,
                            "frequency_penalty": 0.3,
                            "presence_penalty": 0.6,
                            "repeat_penalty": 1.1,
                            "do_sample": True,
                            "num_beams": 1,
                            "early_stopping": False
                        }
                    ) as response:
                        if response.status != Constants.HTTP_OK:
                            error_text = await response.text()
                            logger.error(f"LM Studio error {response.status}: {error_text}")
                            return "Хуйня с сервером, попробуй ещё раз."
                        
                        data = await response.json()
                        
                        if "choices" not in data or len(data["choices"]) == 0:
                            return "Модель затупила, бля."
                        
                        reply = data["choices"][0]["message"]["content"].strip()
                        
                        for garbage in ["###", "Assistant:", "System:", "I am a", "Димон:"]:
                            reply = reply.replace(garbage, "")
                        
                        reply = " ".join(reply.split()).strip()
                        
                        if len(reply) < Constants.MIN_REPLY_LENGTH:
                            return "Чё?"
                        
                        if len(reply) > Constants.MAX_REPLY_LENGTH:
                            reply = reply[:Constants.MAX_REPLY_LENGTH] + "..."
                        
                        return reply
                        
            except asyncio.TimeoutError:
                return "Модель тупит, долго думает."
            except Exception as e:
                logger.error(f"Ошибка модели: {e}")
                return "Что-то пошло не так, бля."
    
    def check_cooldown(self, user_id: int) -> bool:
        """Проверка кулдауна"""
        now = time.time()
        profile = self.context_manager.user_profiles.get(user_id)
        
        cooldown = self.config.cooldown
        if profile:
            if profile.relationship_level == "братан":
                cooldown *= 0.5
            elif profile.relationship_level == "приятель":
                cooldown *= 0.7
        
        if now - self.last_reaction[user_id] < cooldown:
            return False
        self.last_reaction[user_id] = now
        return True
    
    def cleanup_old_data(self):
        """Очистка старых данных"""
        try:
            self.context_manager.db.cleanup_old_messages(Constants.DEFAULT_CLEANUP_DAYS)
            
            now = time.time()
            to_remove = [uid for uid, last_time in self.last_reaction.items() 
                        if now - last_time > self.config.cleanup_interval]
            for uid in to_remove:
                del self.last_reaction[uid]
            
            logger.info(f"Очистка завершена. Удалено {len(to_remove)} записей кулдауна")
        except Exception as e:
            logger.error(f"Ошибка очистки: {e}")

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===



async def create_message_object(user, message, chat_id: int) -> Message:
    """Создание объекта Message из Telegram сообщения"""
    return Message(
        user_id=user.id,
        username=user.full_name or user.username or "Аноним",
        text=message.text or "",
        timestamp=time.time(),
        chat_id=chat_id,
        message_id=message.message_id,
        is_reply=bool(message.reply_to_message),
        reply_to_user=message.reply_to_message.from_user.full_name if message.reply_to_message else None
    )



# === ОБРАБОТЧИКИ ===
smart_bot = None

async def handle_flood_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений с поддержкой изображений"""
    global smart_bot
    user = update.effective_user
    message = update.message
    
    # Проверки на валидность сообщения
    if user.is_bot or not message.text or not smart_bot.check_cooldown(user.id):
        return
    
    # Создаем объект сообщения
    msg = await create_message_object(user, message, update.effective_chat.id)
    
    # Добавляем сообщение в контекст
    smart_bot.context_manager.add_message(msg)
    
    # Проверяем нужно ли отвечать
    should_respond, reason = await smart_bot.analyzer.should_respond(
        msg.text, smart_bot.context_manager, user, is_private=False, has_image=False
    )
    
    if not should_respond:
        return
    
    logger.info(f"💬 Отвечаю {user.full_name}: {reason}")
    
    try:
        # Генерируем ответ
        smart_context = smart_bot.context_manager.get_smart_context(user.id, msg.text, limit=10)
        user_profile = smart_bot.context_manager.user_profiles.get(user.id)
        style_hint = smart_bot.analyzer.get_response_style(reason, msg.text, user_profile)
        
        prompt = smart_bot.prompt_generator.generate_prompt(
            msg.text, smart_context, user_profile, reason, style_hint
        )
        
        # Показываем что печатаем и отправляем ответ
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        reply = await smart_bot.ask_local_model(prompt)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            message_thread_id=smart_bot.config.flood_topic_id,
            text=reply
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_flood_message: {e}")

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка личных сообщений с изображениями"""
    global smart_bot
    user = update.effective_user
    message = update.message
    
    # Проверки на валидность сообщения
    if user.is_bot or not message.text or not smart_bot.check_cooldown(user.id):
        return
    
    try:
        # Создаем объект сообщения (без reply полей для лички)
        msg = Message(
            user_id=user.id,
            username=user.full_name or user.username or "Аноним",
            text=message.text or "",
            timestamp=time.time(),
            chat_id=update.effective_chat.id,
            message_id=message.message_id
        )
        
        # Добавляем сообщение в контекст
        smart_bot.context_manager.add_message(msg)
        
        # Показываем что печатаем
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # Генерируем ответ (в личке всегда отвечаем)
        smart_context = smart_bot.context_manager.get_smart_context(user.id, msg.text, limit=5)
        user_profile = smart_bot.context_manager.user_profiles.get(user.id)
        
        reason = "private_message"
        style = "Говори прямо, без сахара. Ты в личке."
        
        prompt = smart_bot.prompt_generator.generate_prompt(
            msg.text, smart_context, user_profile, reason, style
        )
        
        reply = await smart_bot.ask_local_model(prompt)
        await message.reply_text(reply)
        
    except Exception as e:
        logger.error(f"❌ Ошибка в ЛС: {e}")
        await message.reply_text("Чёт глюканул, попробуй ещё раз.")

# === ПЛАНИРОВЩИК ЗАДАЧ ===
async def check_scheduled_messages(context: ContextTypes.DEFAULT_TYPE):
    """Проверка и отправка запланированных сообщений"""
    global smart_bot
    
    if not smart_bot.scheduler:
        return
    
    try:
        # Проверяем утреннее приветствие
        if smart_bot.scheduler.should_send_greeting():
            logger.info("🌅 Отправляю утреннее приветствие")
            greeting = await smart_bot.scheduler.generate_morning_greeting()
            
            # Исправлено: используем правильный chat_id из конфига
            await context.bot.send_message(
                chat_id=smart_bot.config.chat_id,  # Основной chat_id группы
                message_thread_id=smart_bot.config.flood_topic_id,  # ID топика
                text=greeting
            )
        
        # Проверяем вечернее прощание
        if smart_bot.scheduler.should_send_farewell():
            logger.info("🌇 Отправляю вечернее прощание")
            farewell = await smart_bot.scheduler.generate_evening_farewell()
            
            # Исправлено: используем правильный chat_id из конфига
            await context.bot.send_message(
                chat_id=smart_bot.config.chat_id,  # Основной chat_id группы
                message_thread_id=smart_bot.config.flood_topic_id,  # ID топика
                text=farewell
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка в планировщике: {e}")

# === КОМАНДЫ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Чё надо? Говори по делу. Можешь картинки кидать - разберу что там.")
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            message_thread_id=smart_bot.config.flood_topic_id,
            text="Салам пацаны! Димон на связи. За мемы шарю 📷"
        )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Расширенный статус с поддержкой изображений и планировщика"""
    try:
        recent_count = len(smart_bot.context_manager.recent_messages)
        total_users = len(smart_bot.context_manager.user_profiles)
        active_users = len([p for p in smart_bot.context_manager.user_profiles.values() 
                           if time.time() - p.last_seen < Constants.ACTIVE_USER_THRESHOLD_SEC])
        
        images_count = sum(1 for msg in smart_bot.context_manager.recent_messages if msg.has_image)
        
        relationships = defaultdict(int)
        for profile in smart_bot.context_manager.user_profiles.values():
            relationships[profile.relationship_level] += 1
        
        lm_status = "🟢 Фигачит" if await smart_bot.check_lm_studio_health() else "🔴 Сдох"
        vision_status = "🟢 Включен" if smart_bot.config.enable_vision else "🔴 Отключен"
        schedule_status = "🟢 Включен" if smart_bot.config.enable_schedule else "🔴 Отключен"
        
        # Московское время
        moscow_time = ""
        if smart_bot.scheduler:
            moscow_time = smart_bot.scheduler.get_moscow_time().strftime('%H:%M')
        
        status_text = f"""📊 **Статус Димона**
        🧠 В памяти: {recent_count} сообщений
        📷 Картинок: {images_count}
        👥 Всего юзеров: {total_users}
        🔥 Активных: {active_users}
        🤖 Модель: {lm_status}
        👁 Vision: {vision_status}
        ⏰ Планировщик: {schedule_status}
        🇷🇺 Время МСК: {moscow_time}

        👨‍👩‍👧‍👦 **Отношения:**
        • Братанов: {relationships.get('братан', 0)}
        • Приятелей: {relationships.get('приятель', 0)}
        • Знакомых: {relationships.get('знакомый', 0)}
        • Незнакомцев: {relationships.get('незнакомец', 0)}"""
        
        if update.effective_chat.type == "private":
            await update.message.reply_text(status_text, parse_mode='Markdown')
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                message_thread_id=smart_bot.config.flood_topic_id,
                text=status_text,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"❌ Ошибка в status: {e}")
        await update.message.reply_text("Чёт со статусом хуйня.")

async def memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать память о пользователе"""
    try:
        user_id = update.effective_user.id
        profile = smart_bot.context_manager.user_profiles.get(user_id)
        
        if not profile:
            await update.message.reply_text("Тебя не помню, ты кто такой?")
            return
        
        traits = ", ".join([f"{k}: {v:.1f}" for k, v in profile.personality_traits.items()])
        
        user_images = sum(1 for msg in smart_bot.context_manager.recent_messages 
                         if msg.user_id == user_id and msg.has_image)
        
        memory_text = f"""🧠 **Что я о тебе помню:**
        👤 {profile.username}
        🤝 Отношения: {profile.relationship_level}
        💬 Общений: {profile.interaction_count}
        📷 Картинок: {user_images}
        🎭 Характер: {traits}
        📅 Последний раз: {datetime.fromtimestamp(profile.last_seen).strftime('%d.%m %H:%M')}
        """
        
        if profile.interests:
            memory_text += f"🎯 Интересы: {', '.join(profile.interests[:5])}"
        
        await update.message.reply_text(memory_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"❌ Ошибка в memory: {e}")
        await update.message.reply_text("Хуйня с памятью, попробуй позже.")

async def schedule_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тест планировщика (только для админов)"""
    try:
        if not smart_bot.scheduler:
            await update.message.reply_text("Планировщик отключен.")
            return
        
        # Генерируем тестовые сообщения
        greeting = await smart_bot.scheduler.generate_morning_greeting()
        farewell = await smart_bot.scheduler.generate_evening_farewell()
        
        moscow_time = smart_bot.scheduler.get_moscow_time()
        
        test_text = f"""🧪 **Тест планировщика**
        ⏰ Время МСК: {moscow_time.strftime('%H:%M, %A')}

        🌅 **Утреннее приветствие:**
        {greeting}

        🌇 **Вечернее прощание:**
        {farewell}"""
        
        await update.message.reply_text(test_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"❌ Ошибка в schedule_test: {e}")
        await update.message.reply_text("Чёт с тестом планировщика хуйня.")

# === ГЛАВНАЯ ФУНКЦИЯ ===
async def main():
    global smart_bot
    
    print("🚀 Начинаю запуск бота...")
    logger.info("🚀 Запуск Быдло-бота с ИИ памятью, vision и планировщиком...")
    
    # Проверяем зависимости
    print("🔍 Проверяю зависимости...")
    try:
        import sentence_transformers
        print("✅ SentenceTransformers найден")
        logger.info("✅ SentenceTransformers доступен")
    except ImportError:
        print("❌ SentenceTransformers не найден!")
        logger.error("❌ Установи: pip install sentence-transformers")
        return
    

    if not HAS_PYTZ:
        logger.warning("⚠️ pytz не установлен. Установи: pip install pytz")
        logger.warning("Планировщик может работать неточно")
    
    # Создаем конфигурацию с обработкой ошибок
    print("🔧 Создаю конфигурацию бота...")
    try:
        config = create_bot_config()
        print(f"✅ Конфигурация создана: токен={config.telegram_bot_token[:10]}..., chat_id={config.chat_id}")
    except ValueError as e:
        print(f"❌ Ошибка конфигурации: {e}")
        logger.error(f"Ошибка конфигурации: {e}")
        logger.error("Создай .env файл с необходимыми переменными:")
        logger.error("TELEGRAM_BOT_TOKEN=your_bot_token")
        logger.error("CHAT_ID=your_chat_id")
        logger.error("FLOOD_TOPIC_ID=your_topic_id (опционально)")
        return
    
    print("🤖 Создаю экземпляр SmartBot...")
    smart_bot = SmartBot(config)
    print("✅ SmartBot создан успешно")
    
    logger.info(f"🔍 Проверяю LM Studio на {config.lm_studio_url}...")
    
    if await smart_bot.check_lm_studio_health():
        logger.info("✅ LM Studio доступен!")
    else:
        logger.warning("⚠️ LM Studio недоступен!")
    
    app = Application.builder().token(config.telegram_bot_token).build()
    
    # Фильтры
    class InFloodThreadFilter(filters.BaseFilter):
        def filter(self, message):
            return getattr(message, "message_thread_id", None) == config.flood_topic_id
    
    flood_filter = filters.ChatType.SUPERGROUP & InFloodThreadFilter()
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("memory", memory))
    app.add_handler(CommandHandler("schedule_test", schedule_test))
    
    # Личные сообщения (только текст)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        handle_private_message
    ))
    
    # Сообщения в флуде (только текст)
    app.add_handler(MessageHandler(
        flood_filter & filters.TEXT & ~filters.COMMAND,
        handle_flood_message
    ))
    
    # Периодические задачи
    def cleanup_job(context):
        smart_bot.cleanup_old_data()
    
    app.job_queue.run_repeating(cleanup_job, interval=config.cleanup_interval, first=config.cleanup_interval)
    
    # Планировщик сообщений
    if config.enable_schedule:
        app.job_queue.run_repeating(check_scheduled_messages, interval=Constants.SCHEDULER_CHECK_INTERVAL_SEC, first=10)
        logger.info("⏰ Планировщик сообщений активен")
    
    logger.info(f"✅ Бот запущен!")
    logger.info(f"🧠 Память: {config.context_window} сообщений")
    logger.info(f"🚀 Параллельность: {config.max_parallel_requests}")
    logger.info(f"🎯 Токенов: {config.max_tokens}")
    logger.info(f"⏰ Планировщик: {'включен' if config.enable_schedule else 'отключен'}")
    
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Завершение работы...")
        finally:
            await app.stop()
            await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())