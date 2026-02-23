# bot.py — PulseForge (обновление: фикс кнопок + матчи last/next + надёжный Groq + автоудаление)
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

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан!")
if not API_KEY:
    raise ValueError("API_SPORTS_KEY не указан!")
if not GROQ_API_KEY:
    logger.warning("GROQ_API_KEY не найден — поиск по тексту отключён")

bot = telebot.TeleBot(TOKEN)

DB_PATH = '/data/pulseforge.db'
DB_DIR = os.path.dirname(DB_PATH)

last_menu_msgs = {}  # {chat_id: message_id последнего меню}

def delayed_delete(chat_id, message_id, delay=45):
    def delete_func():
        try:
            bot.delete_message(chat_id, message_id)
            logger.info(f"Автоудалено {message_id} в {chat_id}")
        except Exception as e:
            logger.debug(f"Удаление не удалось: {e}")
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
        ''', (
            chat_id,
            data.get('sport'),
            data.get('region'),
            data.get('country'),
            data.get('league_id'),
            datetime.now(timezone.utc).isoformat()
        ))
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
        logger.error(f"Ошибка чтения: {e}")
        return {}
    finally:
        conn.close()

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
    if not base:
        logger.warning(f"Нет API для {sport}")
        return []
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
        logger.error(f"API запрос ошибка: {e}")
        return []

# ====================== HANDLERS ======================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    
    welcome = (
        "✨ *PulseForge* активирован! ✨\n\n"
        "🔥 Живые результаты и статистика\n"
        "📊 Аналитика формы команд\n"
        "📈 Графики и прогнозы\n\n"
        "⚠️ Без ставок — только спорт\n\n"
        "Выбери вид спорта или ищи матч:"
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
    markup.add(InlineKeyboardButton("ℹ️ О боте", callback_data="about_bot"))
    
    if chat_id in last_menu_msgs:
        try:
            bot.delete_message(chat_id, last_menu_msgs[chat_id])
        except:
            pass
    
    sent = bot.send_message(chat_id, welcome, reply_markup=markup, parse_mode='Markdown')
    last_menu_msgs[chat_id] = sent.message_id
    delayed_delete(chat_id, sent.message_id, delay=180)
    logger.info(f"/start от {chat_id}")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    chat_id = call.message.chat.id
    data = call.data
   
    # Удаляем старое сообщение
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass
   
    if data == "search_match":
        text = (
            "🔍 *Введите запрос*\n\n"
            "Примеры:\n"
            "• Барселона последний матч\n"
            "• Лейкерс ближайший\n"
            "• Зенит сегодня\n"
            "• Биатлон крайний старт"
        )
        sent = bot.send_message(chat_id, text, parse_mode='Markdown')
        delayed_delete(chat_id, sent.message_id, delay=90)
   
    elif data == "about_bot":
        text = (
            "🌟 *PulseForge* — спортивный бот\n\n"
            "⚡ Живые результаты\n"
            "📊 Аналитика и форма\n"
            "📈 Графики команд\n"
            "🚫 Без рекламы и ставок\n\n"
            "Для фанатов спорта! 🔥"
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
            ("football", "La Liga", 140),
            ("basketball", "NBA", 12),
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
   
    elif data.startswith("region_") or data.startswith("country_") or data.startswith("league_") or data.startswith("back_"):
        # Добавь обработку остальных кнопок по аналогии
        sent = bot.send_message(chat_id, "Функция в разработке 😊")
        delayed_delete(chat_id, sent.message_id, delay=60)
   
    bot.answer_callback_query(call.id)

@bot.message_handler(content_types=['text'])
def text_search(message):
    query = message.text.strip()
    if len(query) < 3:
        sent = bot.reply_to(message, "❌ Минимум 3 символа")
        delayed_delete(message.chat.id, sent.message_id, delay=30)
        return
   
    chat_id = message.chat.id
    state = get_user_state(chat_id)
    sport = state.get('sport') or 'football'
   
    logger.info(f"Поиск: '{query}' ({sport}) от {chat_id}")
   
    loading = bot.reply_to(message, f"🔍 Ищу '{query}'... ⏳")
    delayed_delete(chat_id, loading.message_id, delay=15)
   
    groq_prompt = f"""
Пользователь ищет спортивную информацию. Запрос: {query}. Вид спорта: {sport}.

Верни ТОЛЬКО JSON без любого текста вне скобок. Без markdown. Без ```json. Без пояснений.

Структура:
{{
  "teams": ["команда1", "команда2"] или [],
  "leagues": ["лига1", "лига2"] или [],
  "match_query": "Барселона vs Реал" или null,
  "date_filter": "today" или "tomorrow" или "yesterday" или "live" или null,
  "fixture_type": "last" или "next" или "today" или "live" или null,
  "sport": "football" или "basketball" или null
}}

Правила:
- Если про последний/крайний матч — fixture_type: "last"
- Если про ближайший — "next"
- Если про сегодняшний — "today"
- ONLY JSON. Начинай с {{ и заканчивай }}. НИЧЕГО БОЛЬШЕ.
"""
   
    groq_url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
   
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Ты строгий парсер. ТОЛЬКО JSON. Без слов. Без markdown. Только объект от { до }."},
            {"role": "user", "content": groq_prompt}
        ],
        "temperature": 0.15,
        "max_tokens": 400,
        "stream": False
    }
   
    groq_response = {"teams": [], "leagues": [], "match_query": None, "date_filter": None, "fixture_type": None, "sport": None}
   
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = requests.post(groq_url, json=payload, headers=headers, timeout=12)
            r.raise_for_status()
            response_text = r.json()['choices'][0]['message']['content'].strip()
           
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx == -1 or end_idx == 0:
                raise ValueError("Нет JSON")
           
            clean_json = response_text[start_idx:end_idx]
            groq_response = json.loads(clean_json)
            break
        except Exception as e:
            logger.error(f"Groq ошибка (попытка {attempt+1}): {e}")
            if attempt == max_retries - 1:
                bot.reply_to(message, "ИИ вернул некорректный ответ. Попробуй перефразировать.")
                return
   
    found = False
   
    # Обработка команд и лиг
    if groq_response.get('teams'):
        for team_name in groq_response['teams'][:3]:
            teams_data = api_request(sport, 'teams', {'search': team_name})
            if teams_data:
                items = [{'name': t.get('team', {}).get('name', 'Unknown'), 'id': t.get('team', {}).get('id', '')} for t in teams_data[:5] if t.get('team')]
                if items:
                    markup = create_inline_markup(items, "team_search", per_row=1)
                    result = bot.reply_to(message, f"🏟️ Найденные команды по «{team_name}»:", reply_markup=markup, parse_mode='Markdown')
                    delayed_delete(chat_id, result.message_id, delay=120)
                    found = True
                    break
   
    # Обработка матчей
    if not found and (groq_response.get('fixture_type') or groq_response.get('match_query') or groq_response.get('teams')):
        team_name = groq_response.get('teams', [None])[0]
        if not team_name and groq_response.get('match_query'):
            team_name = groq_response.get('match_query', '').split(' vs ')[0].strip()
       
        fixture_type = groq_response.get('fixture_type') or 'today'
       
        if team_name:
            teams_data = api_request(sport, 'teams', {'search': team_name})
            if not teams_data or not teams_data[0].get('team'):
                bot.reply_to(message, f"Команда «{team_name}» не найдена. Попробуй английское название (Barcelona, Zenit).")
                return
           
            team_id = teams_data[0]['team'].get('id')
            if not team_id:
                bot.reply_to(message, "ID команды не найден.")
                return
           
            params = {'team': team_id}
            if fixture_type == 'last':
                params['last'] = 5
                params['status'] = 'FT'
            elif fixture_type == 'next':
                params['next'] = 5
            elif fixture_type == 'today':
                params['date'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
           
            fixtures = api_request(sport, 'fixtures', params)
           
            if fixtures:
                text = f"📅 *Матчи* **{team_name}** ({fixture_type.capitalize()}):\n\n"
                for fx in fixtures[:5]:
                    date = fx['fixture']['date'][:10]
                    time = fx['fixture']['date'][11:16]
                    home = fx['teams']['home']['name']
                    away = fx['teams']['away']['name']
                    score = f"{fx['goals']['home']}–{fx['goals']['away']}" if fx['goals']['home'] is not None else "?"
                    status = fx['fixture']['status']['short']
                    text += f"{date} {time} | {home} {score} {away} ({status})\n"
               
                result = bot.reply_to(message, text, parse_mode='Markdown')
                delayed_delete(chat_id, result.message_id, delay=180)
                found = True
            else:
                bot.reply_to(message, f"Матчи для «{team_name}» ({fixture_type}) не найдены.")
   
    if not found:
        bot.reply_to(message, "🔍 Ничего не нашёл...\n\nПопробуй:\n• По-английски\n• Уточни спорт\n• 'последний', 'ближайший', 'сегодня'")

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
