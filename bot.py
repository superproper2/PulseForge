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
        "Результаты матчей, аналитика, прогнозы и графики формы команд.\n"
        "Здесь нет ставок — только чистая информация о спорте\n\n"
        "Выбери вид спорта или сразу ищи матч:"
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
    
    # НОВАЯ КНОПКА — Поиск матча
    markup.add(InlineKeyboardButton("🔍 Поиск матча", callback_data="search_match"))
    
    markup.add(InlineKeyboardButton("О PulseForge", callback_data="about_bot"))
    
    bot.send_message(chat_id, welcome, reply_markup=markup)
    logger.info(f"/start от chat_id={chat_id}")

@bot.callback_query_handler(func=lambda call: call.data == "search_match")
def search_match(call):
    chat_id = call.message.chat.id
    bot.edit_message_text(
        "Напиши название команды, лиги или матча (например, Барселона, Премьер-лига, NBA сегодня):",
        chat_id,
        call.message.message_id
    )
    logger.info(f"Пользователь зашёл в поиск от chat_id={chat_id}")

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
    
    # Запрос лиг (сезон 2024 — актуальные данные)
    leagues = api_request(sport, 'leagues', {'country': country, 'season': 2024})
    
    if not leagues:
        bot.send_message(
            chat_id,
            "Лиги не найдены для этой страны или сезона. Попробуй другой регион или спорт."
        )
        logger.info(f"Лиги не найдены для {country} / {sport}")
        return
    
    # Первые 10 лиг (можно больше)
    items = [{'name': l['league']['name'], 'id': l['league']['id']} for l in leagues[:10]]
    markup = create_inline_markup(items, "league", per_row=1)
    add_back_button(markup, "back_to_country")
    
    bot.send_message(
        chat_id,
        f"Выбери лигу в {country.capitalize()}:",
        reply_markup=markup
    )
    logger.info(f"Отправлены лиги для страны {country} (новое сообщение)")

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

# Поиск по любому тексту
@bot.message_handler(content_types=['text'])
def text_search(message):
    query = message.text.strip()
    if len(query) < 3:
        bot.reply_to(message, "Напиши минимум 3 символа для поиска")
        return
    
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    sport = state.get('sport') or 'football'  # по умолчанию футбол
    
    logger.info(f"Поиск по '{query}' для спорта {sport} от chat_id={chat_id}")
    
    # 1. Поиск по командам
    teams = api_request(sport, 'teams', {'search': query})
    if teams:
        items = [{'name': t['team']['name'], 'id': t['team']['id']} for t in teams[:5]]
        markup = create_inline_markup(items, "team_search", per_row=1)
        bot.reply_to(message, f"Найдено команд по '{query}':", reply_markup=markup)
        return
    
    # 2. Поиск по лигам
    leagues = api_request(sport, 'leagues', {'search': query, 'season': 2024})
    if leagues:
        items = [{'name': l['league']['name'], 'id': l['league']['id']} for l in leagues[:5]]
        markup = create_inline_markup(items, "league_search", per_row=1)
        bot.reply_to(message, f"Найдено лиг по '{query}':", reply_markup=markup)
        return
    
    # 3. Поиск матчей (если ничего не нашли выше)
    fixtures = api_request(sport, 'fixtures', {'search': query})
    if fixtures:
        text = "Найдено матчей:\n"
        for fx in fixtures[:5]:
            text += f"{fx['teams']['home']['name']} vs {fx['teams']['away']['name']} ({fx['league']['name']})\n"
        bot.reply_to(message, text)
        return
    
    bot.reply_to(message, "Ничего не найдено. Попробуй название команды, лиги или матча.")

# ====================== POLLING ======================
@bot.message_handler(content_types=['text'])
def text_search(message):
    query = message.text.strip()
    if len(query) < 3:
        bot.reply_to(message, "Напиши минимум 3 символа для поиска")
        return
    
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    sport = state.get('sport') or 'football'
    
    logger.info(f"AI-поиск по '{query}' для спорта {sport} от chat_id={chat_id}")
    
    bot.reply_to(message, f"Ищу по '{query}'... ⏳")
    
    # Промпт для Grok
    grok_prompt = f"""
    Пользователь ищет спортивную информацию.
    Запрос: "{query}"

    Верни ТОЛЬКО валидный JSON, без лишнего текста:
    {{
      "teams": ["команда1", "команда2"] или [],
      "leagues": ["лига1"] или [],
      "match_query": "матч Барселона vs Реал" или null,
      "date_filter": "today", "tomorrow", "yesterday", "live" или null,
      "sport": "football", "basketball" или null
    }}

    Если ничего не понял — верни пустые массивы и null.
    """

    # Реальный вызов Grok API
    grok_url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {os.getenv('GROK_API_KEY')}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "grok-beta",
        "messages": [{"role": "user", "content": grok_prompt}],
        "temperature": 0.3,
        "max_tokens": 200
    }
    
    try:
        r = requests.post(grok_url, json=payload, headers=headers)
        r.raise_for_status()
        response_text = r.json()['choices'][0]['message']['content'].strip()
        logger.info(f"Grok ответил: {response_text}")
        
        # Парсим JSON (Grok может вернуть с лишним текстом, чистим)
        if response_text.startswith("```json"):
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        grok_response = json.loads(response_text)
    except Exception as e:
        logger.error(f"Ошибка Grok API: {e}")
        grok_response = {"teams": [], "leagues": [], "match_query": None, "date_filter": None, "sport": None}
        bot.reply_to(message, "Ошибка поиска. Попробуй позже или напиши по-английски (Barcelona, Premier League).")

    found = False

    # 1. Поиск по командам
    if grok_response.get('teams'):
        for team_name in grok_response['teams']:
            teams = api_request(sport, 'teams', {'search': team_name})
            if teams:
                items = [{'name': t['team']['name'], 'id': t['team']['id']} for t in teams[:5]]
                markup = create_inline_markup(items, "team_search", per_row=1)
                bot.reply_to(message, f"Найдено команд:", reply_markup=markup)
                found = True
                break

    # 2. Поиск по лигам
    if not found and grok_response.get('leagues'):
        for league_name in grok_response['leagues']:
            leagues = api_request(sport, 'leagues', {'search': league_name, 'season': 2024})
            if leagues:
                items = [{'name': l['league']['name'], 'id': l['league']['id']} for l in leagues[:5]]
                markup = create_inline_markup(items, "league_search", per_row=1)
                bot.reply_to(message, f"Найдено лиг:", reply_markup=markup)
                found = True
                break

    # 3. Поиск матча (если есть match_query)
    if not found and grok_response.get('match_query'):
        fixtures = api_request(sport, 'fixtures', {'search': grok_response['match_query']})
        if fixtures:
            text = "Найдено матчей:\n"
            for fx in fixtures[:5]:
                text += f"{fx['teams']['home']['name']} vs {fx['teams']['away']['name']} ({fx['league']['name']})\n"
            bot.reply_to(message, text)
            found = True

    if not found:
        bot.reply_to(message, "Ничего не нашёл. Попробуй английское название (Barcelona, Premier League) или уточни запрос.")

