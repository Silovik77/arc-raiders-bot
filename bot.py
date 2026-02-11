import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import json
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web, ClientSession

# --- Загрузка переменных окружения ---
load_dotenv()

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

WEB_APP_URL = "https://silovik77.github.io/bot_web/"
STREAMERS_FILE = "/data/streamers.json"

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Инициализация бота ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Twitch API настройки ---
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

async def get_twitch_access_token(session: ClientSession):
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
        async with session.post(url, data=payload) as response:
            if response.status == 200:
                data = await response.json()
                return data['access_token']
            else:
                logger.error(f"Ошибка получения токена Twitch: {response.status}")
                return None
    except Exception as e:
        logger.error(f"Исключение при получении токена Twitch: {e}")
        return None

async def is_stream_live(session: ClientSession, twitch_username):
    """Проверяет, идёт ли стрим у пользователя на Twitch."""
    token = await get_twitch_access_token(session)
    if not token:
        return False

    url = f"https://api.twitch.tv/helix/streams?user_login={twitch_username}"
    headers = {
        'Client-ID': TWITCH_CLIENT_ID,
        'Authorization': f'Bearer {token}'
    }
    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return len(data['data']) > 0
            else:
                logger.error(f"Ошибка запроса к Twitch API: {response.status}")
                return False
    except Exception as e:
        logger.error(f"Исключение при запросе к Twitch API: {e}")
        return False

# --- URL API для ARC Raiders ---
EVENT_SCHEDULE_API_URL = 'https://metaforge.app/api/arc-raiders/events-schedule'

# --- Обработчики команд и кнопок ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎮 Открыть ARC Raiders Dashboard", web_app=types.WebAppInfo(url=WEB_APP_URL))]
    ])
    await message.answer("Добро пожаловать!", reply_markup=keyboard)

# --- API эндпоинты ---

async def get_user_events(request):
    """
    Возвращает активные и предстоящие события ARC Raiders.
    Использует актуальный формат API с startTime/endTime.
    """
    try:
        # Текущее время в миллисекундах (UTC)
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(f"🔍 Текущее время (UTC): {current_time_ms}")

        async with ClientSession() as session:
            headers = {"User-Agent": "Mozilla/5.0"}
            async with session.get(EVENT_SCHEDULE_API_URL, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"❌ HTTP {response.status} от MetaForge")
                    return web.json_response({"active": [], "upcoming": []})

                data = await response.json()
                raw_events = data.get('data', [])
                logger.info(f"📥 Получено событий из API: {len(raw_events)}")

                if not raw_events:
                    return web.json_response({"active": [], "upcoming": []})

                active_events = []
                upcoming_events = []

                # --- Внутренние функции для парсинга ---
                def _get_events_exact(raw_events):
                    active = []
                    upcoming = []
                    for event_obj in raw_events:
                        name = event_obj.get('name', 'Unknown Event')
                        location = event_obj.get('map', 'Unknown Location')
                        start_timestamp_ms = event_obj.get('startTime')
                        end_timestamp_ms = event_obj.get('endTime')

                        if not start_timestamp_ms or not end_timestamp_ms:
                            continue

                        try:
                            # Активное событие: startTime <= current < endTime
                            if start_timestamp_ms <= current_time_ms < end_timestamp_ms:
                                time_left_ms = end_timestamp_ms - current_time_ms
                                time_left_str = _format_time_ms(time_left_ms)
                                active.append({
                                    'name': name,
                                    'location': location,
                                    'time_left': time_left_str,
                                })
                                logger.debug(f"✅ Активное: {name} | Осталось: {time_left_str}")
                            # Предстоящее событие: startTime > current
                            elif current_time_ms < start_timestamp_ms:
                                time_to_start_ms = start_timestamp_ms - current_time_ms
                                time_to_start_str = _format_time_ms(time_to_start_ms)
                                upcoming.append({
                                    'name': name,
                                    'location': location,
                                    'time_left': time_to_start_str,
                                })
                                logger.debug(f"⏳ Предстоящее: {name} | Начнётся через: {time_to_start_str}")

                        except Exception as e:
                            logger.error(f"Error processing time for event {name}: {e}")
                            continue
                    return active, upcoming

                def _format_time_ms(milliseconds):
                    """Форматирует миллисекунды в строку Чч Мм Сс."""
                    total_seconds = milliseconds // 1000
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    parts = []
                    if hours > 0: parts.append(f"{hours}ч")
                    if minutes > 0: parts.append(f"{minutes}м")
                    if seconds > 0 or not parts: parts.append(f"{seconds}с")
                    return " ".join(parts)

                # --- Конец внутренних функций ---

                # Обрабатываем события
                active_events, upcoming_events = _get_events_exact(raw_events)

                # Сортируем предстоящие по времени начала (лимит 10)
                upcoming_events.sort(key=lambda x: x['time_left'])
                upcoming_events = upcoming_events[:10]

                logger.info(f"📊 Итог: активных={len(active_events)}, предстоящих={len(upcoming_events)}")
                return web.json_response({
                    "active": active_events,
                    "upcoming": upcoming_events
                })

    except Exception as e:
        logger.error(f"💥 Ошибка в /api/user_events: {e}", exc_info=True)
        return web.json_response({"active": [], "upcoming": []})

async def register_streamer(request):
    try:
        data = await request.json()
        channel_id = data.get('channel_id')
        twitch_url = data.get('twitch_url')

        if not channel_id or not twitch_url:
            return web.json_response({"error": "Missing channel_id or twitch_url"}, status=400)

        streamers = load_streamers()
        streamers["temp_user"] = {
            "channel_id": channel_id,
            "twitch_url": twitch_url
        }
        save_streamers(streamers)

        logger.info(f"✅ Стример зарегистрирован: {channel_id}, {twitch_url}")
        return web.json_response({"status": "success", "message": "Стример зарегистрирован!"})
    except Exception as e:
        logger.error(f"❌ Ошибка в /api/register_streamer: {e}")
        return web.json_response({"error": "Internal Server Error"}, status=500)

# --- Загрузка/сохранение данных стримеров ---
def load_streamers():
    if os.path.exists(STREAMERS_FILE):
        with open(STREAMERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_streamers(streamers):
    os.makedirs(os.path.dirname(STREAMERS_FILE), exist_ok=True)
    with open(STREAMERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(streamers, f, ensure_ascii=False, indent=2)

# --- Middleware для CORS ---
@web.middleware
async def cors_middleware(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as ex:
        response = web.Response(status=ex.status, text=str(ex))

    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# --- Эндпоинт для проверки состояния ---
async def health(request):
    return web.json_response({"status": "ok"})

# --- Фоновая задача: проверка стримов ---
async def check_streams_task():
    """Проверяет статус стримов каждую минуту."""
    while True:
        try:
            streamers = load_streamers()
            if not streamers:
                logger.info("Нет зарегистрированных стримеров.")
                await asyncio.sleep(60)
                continue

            async with ClientSession() as session:
                for user_id, data in streamers.items():
                    channel_id = data.get('channel_id')
                    twitch_url = data.get('twitch_url', '')

                    if 'twitch.tv/' in twitch_url:
                        username = twitch_url.split('/')[-1]
                        if await is_stream_live(session, username):
                            try:
                                await bot.send_message(
                                    chat_id=channel_id,
                                    text=f"🔴 <b>Стрим начался!</b>\n\nПрисоединяйтесь: {twitch_url}",
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Уведомление отправлено в канал {channel_id}")
                            except Exception as e:
                                logger.error(f"❌ Ошибка при отправке уведомления: {e}")

            await asyncio.sleep(60)  # Проверяем каждые 60 секунд

        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(60)

# --- Основная функция запуска ---
async def main():
    logger.info("🚀 Запуск Telegram-бота и веб-сервера...")

    # Создаём aiohttp приложение
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get('/api/user_events', get_user_events)
    app.router.add_post('/api/register_streamer', register_streamer)
    app.router.add_get('/health', health)  # Теперь 'health' определена

    runner = web.AppRunner(app)
    await runner.setup()

    # Amvera: слушаем порт 80
    port = 80
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ Веб-сервер запущен на http://0.0.0.0:{port}")

    # Запускаем фоновую задачу
    asyncio.create_task(check_streams_task())

    # Запускаем бота
    await dp.start_polling(bot)
    await runner.cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем.")
