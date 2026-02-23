# bot.py — PulseForge
# Версия: стабильная, без MarkdownV2, с эмодзи, polling + база данных
# Запускается на Railway без проблем

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

# Логи для отладки (видно в Railway)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
API_KEY = os.getenv('API_SPORTS_KEY')

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан!")
if not API_KEY:
    raise ValueError("API_SPORTS_KEY не указан!")

bot = telebot.TeleBot(TOKEN)

# Путь к базе в Railway Volume
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
        logger.info(f"База данных готова: {DB_PATH}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка базы: {e}")
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
        logger.info(f"Состояние сохранено: chat_id={chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка сохранения: {e}")
    finally:
        conn.close()

def get_user_state(chat_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT sport, region, country, league_id FROM users WHERE chat_id = ?', (chat_id,))
        row = c.fetchone()
        if row:
            return {'sport': row[0], 'region': row[1], 'country': row[2], 'league_id': row[3]}
        return {}
    except sqlite3.Error as e:
        logger.error(f"Ошибка чтения: {e}")
        return {}
    finally:
        conn.close()

# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================
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

# ====================== API ЗАПРОСЫ ======================
def api_request(sport, endpoint, params=None):
    base_urls = {
        'football': 'https://v3.football.api-sports.io/',
        'basketball': 'https://v1.basketball.api-sports.io/',
        'ice-hockey': 'https://v1.hockey.api-sports.io/',
        'tennis': 'https://v1.tennis.api-sports.io/',
    }
    base = base_urls.get(sport)
    if not base:
        logger.warning(f"Нет API для спорта: {sport}")
        return None
    url = f"{base}{endpoint}"
    if params:
        url += '?' + '&'.join([f"{k}={v}" for k, v in params.items()])
    try:
        r = requests.get(url, headers={'x-apisports-key': API_KEY}, timeout=10)
        if r.status_code == 200:
            return r.json().get('response', [])
        logger.warning(f"API ошибка {r.status_code}: {r.text}")
        return []
    except Exception as e:
        logger.error(f"Ошибка API: {e}")
        return []

# ====================== ГРАФИК ФОРМЫ ======================
def generate_form_graph(form):
    if not form:
        return None
    labels = list(range(1, len(form) + 1))
    values = [1 if f == 'W' else 0 if f == 'L' else 0.5 for f in form]
    
    fig, ax = plt.subplots(figsize=(6, 3))
    colors = ['#4CAF50' if v == 1 else '#F44336' if v == 0 else '#FFEB3B' for v in values]
    ax.bar(labels, values, color=colors)
    ax.set_title('Форма команды')
    ax.set_ylim(0, 1.1)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(['Пор', 'Нич', 'Поб'])
    ax.grid(axis='y', linestyle='--', alpha=0.5)
    
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf

# ====================== ПРОГНОЗ ======================
def simple_prognosis(fixture, sport):
    if not fixture:
        return "Прогноз недоступен"
    
    home = fixture['teams']['home']['name']
    home_id = fixture['teams']['home']['id']
    away_id = fixture['teams']['away']['id']
    
    stats_params = {'team': home_id, 'league': fixture['league']['id'], 'season': 2025}
    stats = api_request(sport, 'teams/statistics', stats_params)
    
    prog = f"{home} — фаворит"
    if stats and 'form' in stats:
        form = stats['form']
        wins = form.count('W')
        rate = (wins / len(form)) * 100 if form else 50
        prog = f"{home} имеет примерно {rate:.0f}% шансов на победу (по форме)"
    
    h2h_text = "История встреч недоступна"
    try:
        h2h = api_request(sport, 'fixtures/headtohead', {'h2h': f"{home_id}-{away_id}", 'last': 5})
        if h2h:
            h2h_text = "Последние встречи:\n"
            for m in h2h[:3]:
                h = m['teams']['home']['name']
                a = m['teams']['away']['name']
                s = f"{m['goals']['home'] or '?'}–{m['goals']['away'] or '?'}"
                h2h_text += f"{h} {s} {a}\n"
    except Exception as e:
        logger.warning(f"H2H ошибка: {e}")
    
    return f"{prog}\n\n{h2h_text}"

def format_match(fixture, sport):
    if not fixture:
        return "Матч не найден"

    home = fixture['teams']['home']['name']
    away = fixture['teams']['away']['name']
    league_name = fixture['league']['name']
    
    score = "?"
    if fixture['goals']['home'] is not None and fixture['goals']['away'] is not None:
        score = f"{fixture['goals']['home']}–{fixture['goals']['away']}"
    
    status = fixture['fixture']['status']['short']
    date_str = fixture['fixture']['date'][:10]
    time_str = fixture['fixture']['date'][11:16]
    
    emoji = {'football': '⚽', 'basketball': '🏀', 'ice-hockey': '🏒', 'tennis': '🎾'}.get(sport, '🏆')
    
    text = f"{emoji} {home} vs {away}\n\n"
    text += f"Лига: {league_name} | Статус: {status}\n"
    text += f"Дата: {date_str} в {time_str}\n"
    text += f"Счёт: {score}\n\n"
    text += f"Прогноз:\n{simple_prognosis(fixture, sport)}"
    
    return text

# ====================== ОБРАБОТЧИКИ ======================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    
    welcome = (
        "PulseForge активирован\n\n"
        "Результаты матчей, аналитика, прогнозы и графики формы команд.\n"
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
    
    # Пример стран (расширь по необходимости)
    regions_countries = {
        'europe': ['england', 'spain', 'germany', 'italy', 'france'],
        'america': ['usa', 'brazil', 'argentina'],
        'asia': ['japan', 'south korea', 'china'],
        'africa': ['egypt', 'south africa'],
        'international': ['world'],
    }
    
    countries = regions_countries.get(region, [])
    items = [{'name': c.capitalize(), 'code': c} for c in countries]
    markup = create_inline_markup(items, "country", per_row=2)
    add_back_button(markup, "back_to_sport")
    
    bot.edit_message_text(
        f"Выбери страну в {region.capitalize()}:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )
    logger.info(f"Выбран регион: {region} от chat_id={chat_id}")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_start")
def back_to_start(call):
    start(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_sport")
def back_to_sport(call):
    chat_id = call.message.chat.id
    markup = InlineKeyboardMarkup(row_width=2)
    sports = [
        ("Футбол", "sport_football"),
        ("Баскетбол", "sport_basketball"),
        ("Хоккей", "sport_ice-hockey"),
        ("Теннис", "sport_tennis"),
    ]
    for txt, cb in sports:
        markup.add(InlineKeyboardButton(txt, callback_data=cb))
    
    bot.edit_message_text(
        "Выбери спорт заново:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )

# ====================== ЗАПУСК ======================
if __name__ == '__main__':
    try:
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён, запускаем polling")
    except Exception as e:
        logger.warning(f"Ошибка удаления webhook: {e}")
    
    logger.info("Polling запущен — бот должен отвечать мгновенно")
    bot.polling(none_stop=True, interval=0, timeout=20)
