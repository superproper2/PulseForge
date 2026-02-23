# bot.py — PulseForge (обновление: красивый текст + автоудаление + Groq + volume fix)
import os
import json
import requests
import sqlite3
from datetime import datetime, timezone
import logging
import telebot
import threading
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====================== CONFIG ======================
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
API_KEY = os.getenv('API_SPORTS_KEY')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

if not TOKEN: raise ValueError("TELEGRAM_BOT_TOKEN не указан!")
if not API_KEY: raise ValueError("API_SPORTS_KEY не указан!")
if not GROQ_API_KEY: logger.warning("GROQ_API_KEY не найден — поиск по тексту отключён")

bot = telebot.TeleBot(TOKEN)

DB_PATH = '/data/pulseforge.db'
DB_DIR = os.path.dirname(DB_PATH)

last_menu_msgs = {}  # {chat_id: message_id последнего меню}

def delayed_delete(chat_id, message_id, delay=45):
    def delete_func():
        try:
            bot.delete_message(chat_id, message_id)
            logger.info(f"Автоудалено сообщение {message_id} в {chat_id}")
        except Exception as e:
            logger.debug(f"Удаление {message_id} не удалось: {e}")
    threading.Timer(delay, delete_func).start()

def fix_volume_permissions():
    try:
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR, exist_ok=True)
        os.chmod(DB_DIR, 0o777)
        if os.path.exists(DB_PATH):
            os.chmod(DB_PATH, 0o666)
        logger.info("Права на /data исправлены")
    except Exception as e:
        logger.warning(f"Ошибка прав доступа: {e}")

fix_volume_permissions()

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
        logger.info(f"База инициализирована: {DB_PATH}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка БД: {e}")
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
        ''', (chat_id, data.get('sport'), data.get('region'), data.get('country'),
              data.get('league_id'), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        logger.info(f"Сохранено состояние для {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
    finally:
        conn.close()

def get_user_state(chat_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT sport, region, country, league_id FROM users WHERE chat_id = ?', (chat_id,))
        row = c.fetchone()
        return {'sport': row[0], 'region': row[1], 'country': row[2], 'league_id': row[3]} if row else {}
    except Exception as e:
        logger.error(f"Ошибка чтения состояния: {e}")
        return {}
    finally:
        conn.close()

# ====================== HELPERS ======================
def create_inline_markup(items, callback_prefix, per_row=2):
    markup = InlineKeyboardMarkup(row_width=per_row)
    for item in items:
        text = item.get('name', item.get('text', ''))
        cb = item.get('id', item.get('code', ''))
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
    if not base: return []
    url = f"{base}{endpoint}"
    if params:
        url += '?' + '&'.join([f"{k}={v}" for k, v in params.items()])
    try:
        r = requests.get(url, headers={'x-apisports-key': API_KEY}, timeout=10)
        if r.status_code == 200:
            return r.json().get('response', [])
        return []
    except Exception as e:
        logger.error(f"API ошибка: {e}")
        return []

# ====================== HANDLERS ======================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    
    welcome = (
        "✨ *PulseForge* активирован! ✨\n\n"
        "🔥 Результаты матчей в реальном времени\n"
        "📊 Аналитика, форма команд, прогнозы\n"
        "📈 Красивые графики и статистика\n\n"
        "⚠️ Здесь нет ставок — только чистая спортивная информация\n\n"
        "Выберите вид спорта или сразу ищите матч:"
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
    
    markup.add(InlineKeyboardButton("🔍 Поиск матча", callback_data="search_match"))
    markup.add(InlineKeyboardButton("ℹ️ О PulseForge", callback_data="about_bot"))
    
    if chat_id in last_menu_msgs:
        try: bot.delete_message(chat_id, last_menu_msgs[chat_id])
        except: pass
    
    sent = bot.send_message(chat_id, welcome, reply_markup=markup, parse_mode='Markdown')
    last_menu_msgs[chat_id] = sent.message_id
    delayed_delete(chat_id, sent.message_id, delay=180)

@bot.callback_query_handler(func=lambda call: call.data == "search_match")
def search_match(call):
    chat_id = call.message.chat.id
    text = "🔍 *Введите запрос*\n\nПримеры:\n• Барселона сегодня\n• NBA Лейкерс vs Голден Стэйт\n• Премьер-лига таблица\n• Теннис Уимблдон 2025"
    sent = bot.edit_message_text(text, chat_id, call.message.message_id, parse_mode='Markdown')
    delayed_delete(chat_id, sent.message_id, delay=90)

@bot.callback_query_handler(func=lambda call: call.data == "about_bot")
def about_bot(call):
    text = (
        "🌟 *PulseForge* — ваш спортивный помощник 🌟\n\n"
        "⚡ Живые результаты и статистика\n"
        "📈 Аналитика формы и прогнозы\n"
        "🎯 Графики команд и игроков\n"
        "🚫 Без рекламы и ставок — чистый спорт\n\n"
        "Создано для настоящих фанатов! 🔥\n\nКуём дальше?"
    )
    bot.answer_callback_query(call.id)
    sent = bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
    delayed_delete(call.message.chat.id, sent.message_id, delay=90)

# Пример для choose_sport (добавь аналогично в другие callback-хендлеры)
@bot.callback_query_handler(func=lambda call: call.data.startswith('sport_'))
def choose_sport(call):
    chat_id = call.message.chat.id
    sport = call.data.split('_')[1]
    
    state = get_user_state(chat_id)
    state['sport'] = sport
    save_user_state(chat_id, state)
    
    if chat_id in last_menu_msgs:
        try: bot.delete_message(chat_id, last_menu_msgs[chat_id])
        except: pass
    
    markup = InlineKeyboardMarkup(row_width=2)
    regions = ['europe', 'america', 'asia', 'africa', 'international']
    for r in regions:
        markup.add(InlineKeyboardButton(r.capitalize(), callback_data=f"region_{r}"))
    add_back_button(markup, "back_to_start")
    
    text = f"🌍 Выберите регион для *{sport.capitalize()}*"
    sent = bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    last_menu_msgs[chat_id] = sent.message_id
    delayed_delete(chat_id, sent.message_id, delay=180)

# Аналогично обнови choose_region, choose_country, back_to_* и т.д. (добавь parse_mode='Markdown' и delayed_delete)

@bot.message_handler(content_types=['text'])
def text_search(message):
    query = message.text.strip()
    if len(query) < 3:
        sent = bot.reply_to(message, "❌ Минимум 3 символа для поиска")
        delayed_delete(message.chat.id, sent.message_id, delay=30)
        return
   
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    sport = state.get('sport') or 'football'
   
    logger.info(f"AI-поиск по '{query}' для {sport} от {chat_id}")
   
    loading = bot.reply_to(message, "🔎 Ищу информацию... ⏳")
    delayed_delete(chat_id, loading.message_id, delay=15)
   
    groq_prompt = f"""
Пользователь ищет спортивную информацию.
Запрос: "{query}"
Текущий вид спорта: {sport}

Верни ТОЛЬКО JSON без лишнего текста:
{{
  "teams": ["команда1", "команда2"] или [],
  "leagues": ["лига1", "лига2"] или [],
  "match_query": "Барселона vs Реал" или null,
  "date_filter": "today" | "tomorrow" | "yesterday" | "live" | null,
  "sport": "football" | "basketball" | "ice-hockey" | "tennis" | null
}}
Если непонятно — пустые массивы и null.
"""
   
    groq_url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}", "Content-Type": "application/json"}
   
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Ты точный парсер. Только JSON, без слов вне структуры."},
            {"role": "user", "content": groq_prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 300,
        "stream": False
    }
   
    groq_response = {"teams": [], "leagues": [], "match_query": None, "date_filter": None, "sport": None}
   
    try:
        r = requests.post(groq_url, json=payload, headers=headers, timeout=12)
        r.raise_for_status()
        response_text = r.json()['choices'][0]['message']['content'].strip()
        
        if response_text.startswith("```json"): response_text = response_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif response_text.startswith("```"): response_text = response_text.split("```", 2)[1].strip()
        
        groq_response = json.loads(response_text)
    
    except Exception as e:
        logger.error(f"Groq ошибка: {e}")
        sent = bot.reply_to(message, "😔 Ошибка поиска. Попробуйте позже или по-английски.")
        delayed_delete(chat_id, sent.message_id, delay=45)
        return
   
    found = False
   
    if groq_response.get('teams'):
        for team_name in groq_response['teams'][:3]:
            teams_data = api_request(sport, 'teams', {'search': team_name})
            if teams_data:
                items = [{'name': t['team']['name'], 'id': t['team']['id']} for t in teams_data[:5]]
                if items:
                    markup = create_inline_markup(items, "team_search", per_row=1)
                    result = bot.reply_to(message, f"🏟️ *Найденные команды* по запросу «{team_name}»:", reply_markup=markup, parse_mode='Markdown')
                    delayed_delete(chat_id, result.message_id, delay=180)
                    found = True
                    break
   
    if not found and groq_response.get('leagues'):
        for league_name in groq_response['leagues'][:3]:
            leagues_data = api_request(sport, 'leagues', {'search': league_name, 'season': 2024})
            if leagues_data:
                if sport == 'football':
                    items = [{'name': l['league']['name'], 'id': l['league']['id']} for l in leagues_data[:5] if 'league' in l]
                else:
                    items = [{'name': l.get('name', ''), 'id': l.get('id', '')} for l in leagues_data[:5] if l.get('name') and l.get('id')]
                if items:
                    markup = create_inline_markup(items, "league_search", per_row=1)
                    result = bot.reply_to(message, f"🏆 *Найденные лиги* по запросу «{league_name}»:", reply_markup=markup, parse_mode='Markdown')
                    delayed_delete(chat_id, result.message_id, delay=180)
                    found = True
                    break
   
    if not found and groq_response.get('match_query'):
        fixtures = api_request(sport, 'fixtures', {'search': groq_response['match_query']})
        if fixtures:
            text = "⚽ *Найденные матчи*:\n\n"
            for fx in fixtures[:5]:
                text += f"• {fx['teams']['home']['name']} 🆚 {fx['teams']['away']['name']} ({fx['league']['name']})\n"
            result = bot.reply_to(message, text, parse_mode='Markdown')
            delayed_delete(chat_id, result.message_id, delay=180)
            found = True
   
    if not found:
        sent = bot.reply_to(message, "🔍 Ничего не нашёл...\n\nПопробуйте:\n• По-английски (Barcelona vs Real)\n• Указать дату или лигу\n• Уточнить спорт")
        delayed_delete(chat_id, sent.message_id, delay=90)

# ====================== POLLING ======================
if __name__ == '__main__':
    try:
        webhook_info = bot.get_webhook_info()
        if webhook_info.url:
            logger.info(f"Удаляем webhook: {webhook_info.url}")
            bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"Webhook ошибка: {e}")
    
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except:
        pass
    
    logger.info("Polling запущен")
    
    bot.polling(none_stop=True, interval=1, timeout=35, long_polling_timeout=35)
