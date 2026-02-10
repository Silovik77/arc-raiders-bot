import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import requests
import json
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# --- Загрузка переменных окружения ---
load_dotenv()

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

WEB_APP_URL = "https://silovik77.github.io/bot_web/"

# Файл для хранения данных стримеров
STREAMERS_FILE = "streamers.json"

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Инициализация бота ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Twitch API настройки ---
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")


def get_twitch_access_token():
    """Получает временный access token от Twitch."""
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        logger.warning("Twitch API ключи не настроены.")
        return None

    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        'client_id': TWITCH_CLIENT_ID,
        'client_secret': TWITCH_CLIENT_SECRET,
        'grant_type': 'client_credentials'
    }
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            return response.json()['access_token']
        else:
            logger.error(f"Ошибка получения токена Twitch: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Исключение при получении токена Twitch: {e}")
        return None


def is_stream_live(twitch_username):
    """Проверяет, идёт ли стрим у пользователя на Twitch."""
    token = get_twitch_access_token()
    if not token:
        return False

    url = f"https://api.twitch.tv/helix/streams?user_login={twitch_username}"
    headers = {
        'Client-ID': TWITCH_CLIENT_ID,
        'Authorization': f'Bearer {token}'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return len(data['data']) > 0
        else:
            logger.error(f"Ошибка запроса к Twitch API: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Исключение при запросе к Twitch API: {e}")
        return False


# --- Загрузка/сохранение данных стримеров ---
def load_streamers():
    if os.path.exists(STREAMERS_FILE):
        with open(STREAMERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_streamers(streamers):
    with open(STREAMERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(streamers, f, ensure_ascii=False, indent=2)


# --- Обработчики команд и кнопок ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Отправляет сообщение с кнопкой, которая открывает Web App."""
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎮 Открыть ARC Raiders Dashboard", web_app=types.WebAppInfo(url=WEB_APP_URL))]
    ])
    await message.answer(
        text="Добро пожаловать! Нажмите кнопку ниже, чтобы открыть панель управления ARC Raiders.",
        reply_markup=keyboard
    )
    logger.info("Сообщение с кнопкой Web App отправлено.")


# --- Новый маршрут для API: регистрация стримера ---
async def register_streamer(request):
    """
    HTTP-эндпоинт для регистрации стримера.
    Ожидает JSON: {"channel_id": "@my_channel", "twitch_url": "https://twitch.tv/name"}
    """
    try:
        data = await request.json()
        channel_id = data.get('channel_id')
        twitch_url = data.get('twitch_url')

        if not channel_id or not twitch_url:
            return web.json_response({"error": "Missing channel_id or twitch_url"}, status=400)

        # Загружаем текущих стримеров
        streamers = load_streamers()
        # Сохраняем данные
        streamers["temp_user"] = {
            "channel_id": channel_id,
            "twitch_url": twitch_url
        }
        save_streamers(streamers)

        return web.json_response({"status": "success", "message": "Стример зарегистрирован!"})
    except Exception as e:
        logger.error(f"Ошибка в /api/register_streamer: {e}")
        return web.json_response({"error": "Internal Server Error"}, status=500)


# --- Фоновая задача: проверка стримов ---
async def check_streams_task():
    """Фоновая задача для проверки статуса стримов."""
    while True:
        try:
            streamers = load_streamers()
            for user_id, data in streamers.items():
                channel_id = data.get('channel_id')
                twitch_url = data.get('twitch_url', '')

                if 'twitch.tv/' in twitch_url:
                    username = twitch_url.split('/')[-1]
                    if is_stream_live(username):
                        try:
                            await bot.send_message(
                                chat_id=channel_id,
                                text=f"🔴 <b>Стрим начался!</b>\n\nПрисоединяйтесь: {twitch_url}",
                                parse_mode='HTML'
                            )
                            logger.info(f"Уведомление отправлено в канал {channel_id} для стримера {user_id}")
                        except Exception as e:
                            logger.error(f"Ошибка при отправке уведомления: {e}")

            await asyncio.sleep(300)  # Проверяем каждые 5 минут

        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(60)


# --- Основная функция запуска ---
async def main():
    logger.info("Запуск нового бота с Web App и интеграцией ARC Raiders...")

    # Создаём aiohttp приложение
    app = web.Application()
    app.router.add_post('/api/register_streamer', register_streamer)

    runner = web.AppRunner(app)
    await runner.setup()
    # КРИТИЧЕСКИ ВАЖНО: Слушаем на 0.0.0.0
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("Веб-сервер запущен на http://0.0.0.0:8080")

    # Запускаем фоновую задачу
    asyncio.create_task(check_streams_task())

    # Запускаем бота
    await dp.start_polling(bot)
    await runner.cleanup()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")