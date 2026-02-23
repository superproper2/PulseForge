# bot.py — PulseForge (обновление: фикс лиг + эмодзи + fallback + Groq API + volume perms + deprecation fix + матчи last/next + рекомендации + вопрос о спорте)
import os
import json
import requests
import io
import matplotlib.pyplot as plt
import sqlite3
from datetime import datetime, timezone
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====================== CONFIG ======================
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
API_KEY = os.getenv('API_SPORTS_KEY')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан!")
if not API_KEY:
    raise ValueValue("API_SPORTS_KEY не указан!")

bot = telebot.TeleBot(TOKEN)

# Путь к БД в volume
DB_PATH = '/data/pulseforge.db'
DB_DIR = os.path.dirname(DB_PATH)

# Глобальный словарь для последнего меню
last_menu_msgs = {}

# Функция отложенного удаления
def delayed_delete(chat_id, message_id, delay=45):
    def delete_func():
        try:
            bot.delete_message(chat_id, message_id)
            logger.info(f"Автоудалено сообщение {message_id} в {chat_id}")
        except Exception as e:
            logger.debug(f"Удаление не удалось: {e}")
    threading.Timer(delay, delete_func).start()

# Фикс прав доступа
def fix_volume_permissions():
    try:
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR, exist_ok=True)
        os.chmod(DB_DIR, 0o777)
        if os.path.exists(DB_PATH):
            os.chmod(DB_PATH, 0o666)
        logger.info("Права на /data исправлены")
    except Exception as e:
        logger.warning(f"Не удалось исправить права: {e}")

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
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()
        logger.info(f"Состояние сохранено для chat_id={chat_id}")
        
        # Диагностика
        if os.path.exists(DB_PATH):
            size = os.path.getsize(DB_PATH)
            perms = oct(os.stat(DB_PATH).st_mode)[-3:]
            logger.info(f"База сохранена | Размер: {size} байт | Права: {perms}")
        else:
            logger.error(f"Файл БД НЕ существует после сохранения! Путь: {DB_PATH}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка сохранения состояния: {e}")
    except Exception as perm_e:
        logger.error(f"Ошибка проверки файла БД: {perm_e}")
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
        'formula-1': 'https://v1.formula-1.api-sports.io/',
        'mma': 'https://v1.mma.api-sports.io/',
        'handball': 'https://v1.handball.api-sports.io/',
        'volleyball': 'https://v1.volleyball.api-sports.io/',
        'rugby': 'https://v1.rugby.api-sports.io/',
        'american-football': 'https://v1.american-football.api-sports.io/',
        'baseball': 'https://v1.baseball.api-sports.io/',
        'cricket': 'https://v1.cricket.api-sports.io/',
        'golf': 'https://v1.golf.api-sports.io/',
        'darts': 'https://v1.darts.api-sports.io/',
        'snooker': 'https://v1.snooker.api-sports.io/',
        'table-tennis': 'https://v1.table-tennis.api-sports.io/',
        'cycling': 'https://v1.cycling.api-sports.io/',
        'boxing': 'https://v1.boxing.api-sports.io/',
        # Для biathlon и winter sports — нет прямой поддержки, добавим fallback
        'biathlon': 'https://v1.winter-sports.api-sports.io/'  # если есть, иначе ошибка
    }
    base = base_urls.get(sport.lower(), None)
    if not base:
        logger.warning(f"Нет API для спорта: {sport}")
        return []
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
   
    markup.add(InlineKeyboardButton("🔍 Поиск матча", callback_data="search_match"))
    markup.add(InlineKeyboardButton("📈 Популярные матчи", callback_data="popular_fixtures"))
    markup.add(InlineKeyboardButton("О PulseForge", callback_data="about_bot"))
   
    bot.send_message(chat_id, welcome, reply_markup=markup)
    logger.info(f"/start от chat_id={chat_id}")
   
    # Если нет спорта в состоянии, спрашиваем
    if not state.get('sport'):
        bot.send_message(chat_id, "Какой вид спорта вас интересует? Напишите название (футбол, биатлон и т.д.)")

@bot.callback_query_handler(func=lambda call: call.data == "popular_fixtures")
def popular_fixtures(call):
    chat_id = call.message.chat.id
    text = "📈 Популярные матчи сегодня:\n\n"
    
    # Пример популярных лиг: EPL 39, NBA 12
    popular_leagues = {'football': 39, 'basketball': 12}  # расширь
    
    for sport, league_id in popular_leagues.items():
        fixtures = api_request(sport, 'fixtures', {'league': league_id, 'date': datetime.now().strftime('%Y-%m-%d')})
        if fixtures:
            text += f"* {sport.capitalize()} *\n"
            for fx in fixtures[:5]:
                home = fx['teams']['home']['name']
                away = fx['teams']['away']['name']
                text += f"{home} vs {away}\n"
    
    bot.edit_message_text(text, chat_id, call.message.message_id)
    logger.info(f"Показаны популярные матчи для {chat_id}")

# Добавь аналогично для других callback

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
   
    groq_prompt = f"""
Пользователь ищет спортивную информацию.
Запрос: "{query}"
Текущий выбранный вид спорта в боте: {sport}

Верни ТОЛЬКО валидный JSON, без лишнего текста, без markdown, без ```json:
{{
  "teams": ["команда1", "команда2"] или [],
  "leagues": ["лига1"] или [],
  "match_query": "матч Барселона vs Реал" или null,
  "date_filter": "today" или "tomorrow" или "yesterday" или "live" или null,
  "fixture_type": "last" или "next" или "live" или "today" или null,
  "sport": "football" или "basketball" или null
}}
Если не понятно — верни пустые массивы и null.
"""
    groq_url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {
                "role": "system",
                "content": "Ты точный парсер спортивных запросов. Отвечай исключительно JSON-объектом, без единого слова вне структуры."
            },
            {
                "role": "user",
                "content": groq_prompt
            }
        ],
        "temperature": 0.2,
        "max_tokens": 300,
        "stream": False
    }
   
    try:
        r = requests.post(groq_url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        response = r.json().get('response', {})
        logger.info(f"Grok вернул {len(response)} элементов для {endpoint}")
        return response
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
   
    markup.add(InlineKeyboardButton("🔍 Поиск матча", callback_data="search_match"))
    markup.add(InlineKeyboardButton("📈 Популярные матчи", callback_data="popular_fixtures"))
    markup.add(InlineKeyboardButton("О PulseForge", callback_data="about_bot"))
   
    bot.send_message(chat_id, welcome, reply_markup=markup)
    logger.info(f"/start от chat_id={chat_id}")
   
    # Если нет спорта в состоянии, спрашиваем
    if not state.get('sport'):
        bot.send_message(chat_id, "Какой вид спорта вас интересует? Напишите название (футбол, биатлон и т.д.)")

@bot.callback_query_handler(func=lambda call: True)  # Ловим ВСЕ callback'и
def callback_handler(call):
    chat_id = call.message.chat.id
    data = call.data
   
    # Удаляем старое сообщение (чтобы не было "message to edit not found")
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass  # Если уже удалено — ок
   
    if data == "search_match":
        text = "🔍 *Введите запрос*\n\nПримеры:\n• Барселона сегодня\n• NBA Лейкерс vs Голден Стэйт\n• Премьер-лига таблица\n• Биатлон последний матч"
        sent = bot.send_message(chat_id, text, parse_mode='Markdown')
        delayed_delete(chat_id, sent.message_id, delay=90)
   
    elif data == "about_bot":
        text = (
            "🌟 *PulseForge* — ваш спортивный помощник 🌟\n\n"
            "⚡ Живые результаты и статистика\n"
            "📈 Аналитика формы и прогнозы\n"
            "🎯 Графики команд и игроков\n"
            "🚫 Без рекламы и ставок — чистый спорт\n\n"
            "Создано для фанатов! 🔥"
        )
        sent = bot.send_message(chat_id, text, parse_mode='Markdown')
        delayed_delete(chat_id, sent.message_id, delay=90)
   
    elif data.startswith("sport_"):
        sport = data.split('_')[1]
        state = get_user_state(chat_id)
        state['sport'] = sport
        save_user_state(chat_id, state)
       
        markup = InlineKeyboardMarkup(row_width=2)
        regions = ['europe', 'america', 'asia', 'africa', 'international']
        for r in regions:
            markup.add(InlineKeyboardButton(r.capitalize(), callback_data=f"region_{r}"))
        add_back_button(markup, "back_to_start")
       
        text = f"🌍 *Выберите регион* для **{sport.capitalize()}**"
        sent = bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
        last_menu_msgs[chat_id] = sent.message_id
        delayed_delete(chat_id, sent.message_id, delay=180)
   
    elif data == "popular_fixtures":
        text = "📈 *Популярные матчи сегодня*\n\n"
        popular = [
            ("football", "Premier League", 39),
            ("basketball", "NBA", 12),
            ("football", "La Liga", 140),
            ("football", "Bundesliga", 78),
        ]
        for sport, league_name, league_id in popular:
            fixtures = api_request(sport, 'fixtures', {'league': league_id, 'date': datetime.now().strftime('%Y-%m-%d')})
            if fixtures:
                text += f"**{league_name}**\n"
                for fx in fixtures[:3]:
                    home = fx['teams']['home']['name']
                    away = fx['teams']['away']['name']
                    time = fx['fixture']['date'][11:16]
                    text += f"{time} | {home} vs {away}\n"
                text += "\n"
       
        sent = bot.send_message(chat_id, text or "Сегодня нет популярных матчей", parse_mode='Markdown')
        delayed_delete(chat_id, sent.message_id, delay=180)
   
    # Для остальных кнопок (region_, country_, league_, back_*) — добавь аналогично
    bot.answer_callback_query(call.id)  # обязательно отвечаем на callback, чтобы убрать "часики"

# ====================== POLLING ======================
if __name__ == '__main__':
    try:
        webhook_info = bot.get_webhook_info()
        if webhook_info.url:
            logger.info(f"Удаляем webhook: {webhook_info.url}")
            bot.delete_webhook(drop_pending_updates=True)
        else:
            logger.info("Webhook не установлен")
    except Exception as e:
        logger.warning(f"Ошибка проверки webhook: {e}")
    
    try:
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён")
    except Exception as e:
        logger.warning(f"Ошибка удаления webhook: {e}")
    
    logger.info("Polling запущен — бот должен отвечать мгновенно")
    bot.polling(none_stop=True, interval=0, timeout=20)
