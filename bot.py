import logging
import telebot
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан!")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start', 'help'])
def start(message):
    bot.reply_to(message, "Бот работает! 😎\n\nТокен найден, polling запущен.")

@bot.message_handler(func=lambda m: True)
def echo(message):
    bot.reply_to(message, f"Ты написал: {message.text}")

if __name__ == '__main__':
    logger.info("Polling запущен — минимальный тест")
    bot.delete_webhook(drop_pending_updates=True)
    bot.polling(none_stop=True, interval=1, timeout=30)
