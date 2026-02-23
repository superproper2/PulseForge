# bot.py — PulseForge (обновление: фикс лиг + эмодзи + fallback)

import os
import json
import requests
import io
import matplotlib.pyplot as plt
import sqlite3
from datetime import datetime
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====================== CONFIG ======================
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
API_KEY = os.getenv('API_SPORTS_KEY')

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан!")
if not API_KEY:
    raise ValueError("API_SPORTS_KEY не указан!")

bot = telebot.TeleBot(TOKEN)

# Путь к БД в volume
DB_PATH = '/data/pulseforge.db'

# ====================== БАЗА ДАННЫХ ======================
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                sport TEXT,
                region TEXT,
                country TEXT,
                league_id TEXT,
                updated_at TEXT
            )
        ''')
        conn.commit()
        logger.info(f"База данных успешно инициализирована: {DB_PATH}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка инициализации БД: {e}")
    finally:
        conn.close()

init_db()

def save_user_state(chat_id, data):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO users (chat_id, sport, region, country, league_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            chat_id,
            data.get('sport'),
            data.get('region'),
            data.get('country'),
            data.get('league_id'),
            datetime.utcnow().isoformat()
        ))
        conn.commit()
        logger.info(f"Состояние сохранено для chat_id={chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка сохранения состояния: {e}")
    finally:
        conn.close()

def get_user_state(chat_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT sport, region, country, league_id FROM users WHERE chat_id = ?', (chat_id,))
        row = c.fetchone()
        if row:
            return {
                'sport': row[0],
                'region': row[1],
                'country': row[2],
                'league_id': row[3]
            }
        return {}
    except sqlite3.Error as e:
        logger.error(f"Ошибка чтения состояния: {e}")
        return {}
    finally:
        conn.close()

# ====================== HELPERS ======================
def create_inline_markup(items, callback_prefix, per_row=2):
    markup = InlineKeyboardMarkup(row_width=per_row)
    for item in items:
        if isinstance(item, dict):
            text = item.get('name', item.get('text', ''))
            cb = item.get('id', item.get('code', ''))
        else:
            text = str(item)
            cb = str(item)
        markup.add(InlineKeyboardButton(text, callback_data=f"{callback_prefix}_{cb}"))
    return markup

def add_back_button(markup, back_callback):
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data=back_callback))
    return markup

def api_request(sport, endpoint, params=None):
    base_urls = {
        'football': 'https://v3.football.api-sports.io/',
        'basketball': 'https://v1.basketball.api-sports.io/',
        'ice-hockey': 'https://v1.hockey.api-sports.io/',
        'tennis': 'https://v1.tennis.api-sports.io/',
    }
    base = base_urls.get(sport)
    if not base:
        logger.warning(f"Нет базы для спорта: {sport}")
        return None
    url = f"{base}{endpoint}"
    if params:
        url += '?' + '&'.join([f"{k}={v}" for k, v in params.items()])
    try:
        r = requests.get(url, headers={'x-apisports-key': API_KEY}, timeout=10)
        if r.status_code == 200:
            response = r.json().get('response', [])
            logger.info(f"API вернул {len(response)} элементов для {endpoint}")
            return response
        logger.warning(f"API ошибка {r.status_code}: {r.text}")
        return []
    except Exception as e:
        logger.error(f"Ошибка запроса API: {e}")
        return []

# ====================== HANDLERS ======================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    
    welcome = (
        "PulseForge активирован\n\n"
        "Результаты матчей, аналитика, прогнозы и графики формы команд\n"
        "Здесь нет ставок — только чистая информация о спорте\n\n"
        "Выбери вид спорта:"
    )
    
    markup = InlineKeyboardMarkup(row_width=2)
    sports = [
        ("⚽ Футбол", "sport_football"),
        ("🏀 Баскетбол", "sport_basketball"),
        ("🏒 Хоккей", "sport_ice-hockey"),
        ("🎾 Теннис", "sport_tennis"),
    ]
    for txt, cb in sports:
        markup.add(InlineKeyboardButton(txt, callback_data=cb))
    
    markup.add(InlineKeyboardButton("О PulseForge", callback_data="about_bot"))
    
    bot.send_message(chat_id, welcome, reply_markup=markup)
    logger.info(f"/start от chat_id={chat_id}")

@bot.callback_query_handler(func=lambda call: call.data == "about_bot")
def about_bot(call):
    text = (
        "PulseForge — бот для спортивных результатов и аналитики\n\n"
        "Живые результаты\n"
        "Прогнозы на основе формы\n"
        "Графики команд\n"
        "Без рекламы и ставок\n\n"
        "Куём дальше?"
    )
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)

@bot.callback_query_handler(func=lambda call: call.data.startswith('sport_'))
def choose_sport(call):
    chat_id = call.message.chat.id
    sport = call.data.split('_')[1]
    
    state = get_user_state(chat_id)
    state['sport'] = sport
    save_user_state(chat_id, state)
    
    markup = InlineKeyboardMarkup(row_width=2)
    regions = ['europe', 'america', 'asia', 'africa', 'international']
    for r in regions:
        markup.add(InlineKeyboardButton(r.capitalize(), callback_data=f"region_{r}"))
    add_back_button(markup, "back_to_start")
    
    bot.edit_message_text(
        f"Выбери регион для {sport.capitalize()}:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )
    logger.info(f"Выбран спорт: {sport} от chat_id={chat_id}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('region_'))
def choose_region(call):
    chat_id = call.message.chat.id
    region = call.data.split('_')[1]
    
    state = get_user_state(chat_id)
    state['region'] = region
    save_user_state(chat_id, state)
    
    # Пример стран (расширь)
    regions_countries = {
        'europe': ['england', 'spain', 'germany', 'italy', 'france'],
        'america': ['usa', 'brazil', 'argentina'],
        'asia': ['japan', 'south korea', 'china'],
        'africa': ['egypt', 'south africa'],
        'international': ['world'],
    }
    
    countries = regions_countries.get(region, [])
    if not countries:
        bot.edit_message_text(
            "Страны не найдены для этого региона.",
            chat_id,
            call.message.message_id
        )
        return
    
    items = [{'name': c.capitalize(), 'code': c} for c in countries]
    markup = create_inline_markup(items, "country", per_row=2)
    add_back_button(markup, "back_to_region")
    
    bot.edit_message_text(
        f"Выбери страну в {region.capitalize()}:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )
    logger.info(f"Выбран регион: {region} от chat_id={chat_id}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def choose_country(call):
    chat_id = call.message.chat.id
    country = call.data.split('_')[1]
    
    state = get_user_state(chat_id)
    state['country'] = country
    save_user_state(chat_id, state)
    
    sport = state.get('sport')
    if not sport:
        bot.answer_callback_query(call.id, "Сначала выбери спорт")
        return
    
    logger.info(f"Выбрана страна: {country} для спорта {sport} от chat_id={chat_id}")
    
    # Запрос лиг с сезоном 2024 (актуальные данные)
    leagues = api_request(sport, 'leagues', {'country': country, 'season': 2024})
    
    if not leagues:
        bot.edit_message_text(
            "Лиги не найдены для этой страны или сезона. Попробуй другой регион или спорт.",
            chat_id,
            call.message.message_id
        )
        logger.info(f"Лиги не найдены для {country} / {sport}")
        return
    
    # Первые 10 лиг
    items = [{'name': l['league']['name'], 'id': l['league']['id']} for l in leagues[:10]]
    markup = create_inline_markup(items, "league", per_row=1)
    add_back_button(markup, "back_to_country")
    
    bot.edit_message_text(
        f"Выбери лигу в {country.capitalize()}:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )
    logger.info(f"Показаны лиги для страны {country}")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_start")
def back_to_start(call):
    start(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_sport")
def back_to_sport(call):
    chat_id = call.message.chat.id
    markup = InlineKeyboardMarkup(row_width=2)
    sports = [
        ("⚽ Футбол", "sport_football"),
        ("🏀 Баскетбол", "sport_basketball"),
        ("🏒 Хоккей", "sport_ice-hockey"),
        ("🎾 Теннис", "sport_tennis"),
    ]
    for txt, cb in sports:
        markup.add(InlineKeyboardButton(txt, callback_data=cb))
    
    bot.edit_message_text(
        "Выбери спорт заново:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "back_to_region")
def back_to_region(call):
    chat_id = call.message.chat.id
    state = get_user_state(chat_id)
    sport = state.get('sport')
    
    if not sport:
        bot.answer_callback_query(call.id, "Сначала выбери спорт")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    regions = ['europe', 'america', 'asia', 'africa', 'international']
    for r in regions:
        markup.add(InlineKeyboardButton(r.capitalize(), callback_data=f"region_{r}"))
    add_back_button(markup, "back_to_sport")
    
    bot.edit_message_text(
        f"Выбери регион для {sport.capitalize()}:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )

# ====================== POLLING ======================
if __name__ == '__main__':
    try:
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён, запускаем polling")
    except Exception as e:
        logger.warning(f"Ошибка удаления webhook: {e}")
    
    logger.info("Polling запущен — бот должен отвечать мгновенно")
    bot.polling(none_stop=True, interval=0, timeout=20)
