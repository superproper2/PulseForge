# bot.py — PulseForge (polling + экранированный MarkdownV2)

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

def escape_md(text):
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for c in chars:
        text = text.replace(c, f'\\{c}')
    return text

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
            return r.json().get('response', [])
        logger.warning(f"API ошибка {r.status_code}: {r.text}")
        return []
    except Exception as e:
        logger.error(f"Ошибка запроса API: {e}")
        return []

# ====================== MATCH & GRAPH ======================
def generate_form_graph(form):
    if not form:
        return None
    labels = list(range(1, len(form) + 1))
    values = [1 if f == 'W' else 0 if f == 'L' else 0.5 for f in form]
    
    fig, ax = plt.subplots(figsize=(6, 3))
    colors = ['#4CAF50' if v == 1 else '#F44336' if v == 0 else '#FFEB3B' for v in values]
    ax.bar(labels, values, color=colors)
    ax.set_title('Пульс формы команды 🔥')
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

def simple_prognosis(fixture, sport):
    if not fixture:
        return "Прогноз недоступен"
    
    home = fixture['teams']['home']['name']
    home_id = fixture['teams']['home']['id']
    away_id = fixture['teams']['away']['id']
    
    stats_params = {'team': home_id, 'league': fixture['league']['id'], 'season': 2025}
    stats = api_request(sport, 'teams/statistics', stats_params)
    
    prog = f"• {home} — фаворит 🔥"
    if stats and 'form' in stats:
        form = stats['form']
        wins = form.count('W')
        rate = (wins / len(form)) * 100 if form else 50
        prog = f"• {home} имеет ≈{rate:.0f}% шансов на победу (по пульсу формы)"
    
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
        return "Матч не найден 😕"

    home = escape_md(fixture['teams']['home']['name'])
    away = escape_md(fixture['teams']['away']['name'])
    league_name = escape_md(fixture['league']['name'])
    
    score = "?"
    if fixture['goals']['home'] is not None and fixture['goals']['away'] is not None:
        score = f"{fixture['goals']['home']}–{fixture['goals']['away']}"
    
    status = fixture['fixture']['status']['short']
    date_str = fixture['fixture']['date'][:10]
    time_str = fixture['fixture']['date'][11:16]
    
    emoji = {'football': '⚽️', 'basketball': '🏀', 'ice-hockey': '🏒', 'tennis': '🎾'}.get(sport, '🏆')
    
    text = f"🔥 *{home} vs {away}* 🔥\n\n"
    text += f"🏟️ Лига: {league_name} | Пульс: {status}\n"
    text += f"📅 {date_str} • {time_str}\n"
    text += f"⚡ Счёт: *{score}*\n\n"
    text += f"🔥 *Пульс прогноза:*\n{simple_prognosis(fixture, sport)}"
    
    return text

# ====================== HANDLERS ======================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    
    welcome = (
        "🔥 *PulseForge активирован\\!* 🔥\n\n"
        "Мы куём настоящий *пульс спорта* — результаты, аналитика, прогнозы и графики формы\\.\n"
        "⚡ Здесь нет ставок — только чистый огонь инсайтов и ритм матчей\\! 🏆\n\n"
        "Выбери спорт и почувствуй удар пульса:\n\n"
        "Готов кузнечить победу? 💪"
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
    
    markup.add(InlineKeyboardButton("🔥 О PulseForge", callback_data="about_bot"))
    
bot.send_message(
    chat_id,
    welcome,
   # parse_mode='MarkdownV2',  # ← закомментируй или удали эту строку
    reply_markup=markup
)
    logger.info(f"Команда /start от chat_id={chat_id}")

@bot.callback_query_handler(func=lambda call: call.data == "about_bot")
def about_bot(call):
    text = (
        "PulseForge — твоя кузница спортивных инсайтов 🔥\n\n"
        "• Живые результаты и live-пульс\n"
        "• Прогнозы + H2H\n"
        "• Графики формы команд 📈\n"
        "• Без рекламы и ставок — чистый спорт\\!\n\n"
        "Куём дальше вместе? 💥"
    )
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)  # без parse_mode

@bot.callback_query_handler(func=lambda call: call.data.startswith('sport_'))
def choose_sport(call):
    chat_id = call.message.chat.id
    sport = call.data.split('_')[1]
    
    state = get_user_state(chat_id)
    state['sport'] = sport
    save_user_state(chat_id, state)
    
    markup = InlineKeyboardMarkup(row_width=2)
    for r in ['europe', 'america', 'asia', 'africa', 'international']:
        markup.add(InlineKeyboardButton(f"🌐 {r.capitalize()}", callback_data=f"region_{r}"))
    add_back_button(markup, "back_to_start")
    
    bot.edit_message_text(
        f"🔥 *Выбери регион* для {sport.capitalize()}:\n\n",
        chat_id, call.message.message_id,
        #parse_mode='MarkdownV2', reply_markup=markup
    )
    logger.info(f"Выбран спорт: {sport} для chat_id={chat_id}")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_start")
def back_to_start(call):
    start(call.message)

# ====================== POLLING ======================
if __name__ == '__main__':
    try:
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён, запускаем polling")
    except Exception as e:
        logger.warning(f"Ошибка удаления webhook: {e}")
    
    logger.info("Polling запущен — бот должен отвечать мгновенно")
    bot.polling(none_stop=True, interval=0, timeout=20)
