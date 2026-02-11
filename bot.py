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
STREAMERS_FILE = "/data/streamers.json"  # Сохраняем в persistent storage

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
                logger.error(f"Ошибка получения токена Twitch: {response.status} {await response.text()}")
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
                logger.error(f"Ошибка запроса к Twitch API: {response.status} {await response.text()}")
                return False
    except Exception as e:
        logger.error(f"Исключение при запросе к Twitch API: {e}")
        return False

# --- URL API для ARC Raiders ---
EVENT_SCHEDULE_API_URL = 'https://metaforge.app/api/arc-raiders/events-schedule'

EVENT_TRANSLATIONS = {
    "Electromagnetic Storm": "⚡ Электромагнитная буря",
    "Harvester": "🪴 Сборщик",
    "Lush Blooms": "🌿 Повышенная растительность",
    "Matriarch": "👑 Матриарх",
    "Night Raid": "🌙 Ночной рейд",
    "Uncovered Caches": "宝藏 Обнаруженные тайники",
    "Launch Tower Loot": "🚀 Добыча с пусковой башни",
    "Hidden Bunker": " bunker Скрытый бункер",
    "Husk Graveyard": "💀 Кладбище ARC",
    "Prospecting Probes": "📡 Геологические зонды",
    "Cold Snap": "❄️ Холодная вспышка",
    "Locked Gate": "🔒 Закрытые врата",
}

MAP_TRANSLATIONS = {
    "Dam": "Плотина",
    "Buried City": "Погребённый город",
    "Spaceport": "Космопорт",
    "Blue Gate": "Синие врата",
    "Stella Montis": "Стелла Монти",
}

async def get_arc_raiders_events_from_api_schedule(session: ClientSession):
    try:
        async with session.get(EVENT_SCHEDULE_API_URL) as response:
            if response.status != 200:
                logger.error(f"Ошибка API MetaForge: {response.status}")
                return [], []
            data = await response.json()
            raw_events = data.get('data', [])

            if raw_events and 'startTime' in raw_events[0] and 'endTime' in raw_events[0]:
                return _get_events_exact(raw_events)
            elif raw_events and 'times' in raw_events[0]:
                return _get_events_schedule(raw_events)
            else:
                return [], []
    except Exception as e:
        logger.error(f"Ошибка при получении данных из API (events-schedule): {e}")
        return [], []

def _get_events_exact(raw_events):
    active_events = []
    upcoming_events = []
    current_time_utc = datetime.now(timezone.utc)

    for event_obj in raw_events:
        name = event_obj.get('name', 'Unknown Event')
        location = event_obj.get('map', 'Unknown Location')
        start_timestamp_ms = event_obj.get('startTime')
        end_timestamp_ms = event_obj.get('endTime')

        if not start_timestamp_ms or not end_timestamp_ms:
            continue

        try:
            start_dt = datetime.fromtimestamp(start_timestamp_ms / 1000, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_timestamp_ms / 1000, tz=timezone.utc)

            if start_dt <= current_time_utc < end_dt:
                time_left = end_dt - current_time_utc
                total_seconds = int(time_left.total_seconds())
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                time_parts = []
                if hours > 0: time_parts.append(f"{hours}ч")
                if minutes > 0: time_parts.append(f"{minutes}м")
                if seconds > 0 or not time_parts: time_parts.append(f"{seconds}с")
                time_left_str = " ".join(time_parts)

                active_events.append({
                    'name': name,
                    'location': location,
                    'time_left': time_left_str,
                })
                continue

            if start_dt > current_time_utc:
                time_to_start = start_dt - current_time_utc
                total_seconds = int(time_to_start.total_seconds())
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                time_parts = []
                if hours > 0: time_parts.append(f"{hours}ч")
                if minutes > 0: time_parts.append(f"{minutes}м")
                if seconds > 0 or not time_parts: time_parts.append(f"{seconds}с")
                time_to_start_str = " ".join(time_parts)

                upcoming_events.append({
                    'name': name,
                    'location': location,
                    'time_left': time_to_start_str,
                })

        except Exception as e:
            logger.error(f"Error processing time for event {name}: {e}")
            continue

    return active_events, upcoming_events

def _get_events_schedule(raw_events):
    active_events = []
    upcoming_events = []
    current_time_utc = datetime.now(timezone.utc)
    current_date_utc = current_time_utc.date()
    current_time_only = current_time_utc.time()

    for event_obj in raw_events:
        name = event_obj.get('name', 'Unknown Event')
        location = event_obj.get('map', 'Unknown Location')
        times_list = event_obj.get('times', [])

        for time_window in times_list:
            start_str = time_window.get('start')
            end_str = time_window.get('end')

            if not start_str or not end_str:
                continue

            try:
                start_time = datetime.strptime(start_str, '%H:%M').time()
                is_end_midnight_next_day = end_str == "24:00"

                if is_end_midnight_next_day:
                    is_active = start_time <= current_time_only
                else:
                    end_time_for_comparison = datetime.strptime(end_str, '%H:%M').time()
                    is_active = start_time <= current_time_only < end_time_for_comparison

                if is_active:
                    if is_end_midnight_next_day:
                        end_datetime_naive = datetime.combine(current_date_utc + timedelta(days=1), datetime.min.time())
                    else:
                        end_time_for_comparison = datetime.strptime(end_str, '%H:%M').time()
                        end_datetime_naive = datetime.combine(current_date_utc, end_time_for_comparison)
                    end_datetime = end_datetime_naive.replace(tzinfo=timezone.utc)

                    time_left = end_datetime - current_time_utc
                    total_seconds = int(time_left.total_seconds())
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    time_parts = []
                    if hours > 0: time_parts.append(f"{hours}ч")
                    if minutes > 0: time_parts.append(f"{minutes}м")
                    if seconds > 0 or not time_parts: time_parts.append(f"{seconds}с")
                    time_left_str = " ".join(time_parts)

                    active_events.append({
                        'name': name,
                        'location': location,
                        'time_left': time_left_str,
                    })
                    continue

                # Вычисление предстоящего
                if is_end_midnight_next_day:
                    if current_time_only < start_time:
                        start_datetime_naive = datetime.combine(current_date_utc, start_time)
                    else:
                        start_datetime_naive = datetime.combine(current_date_utc + timedelta(days=1), start_time)
                else:
                    end_time_for_comparison = datetime.strptime(end_str, '%H:%M').time()
                    if start_time > current_time_only:
                        start_datetime_naive = datetime.combine(current_date_utc, start_time)
                    else:
                        start_datetime_naive = datetime.combine(current_date_utc + timedelta(days=1), start_time)

                start_datetime = start_datetime_naive.replace(tzinfo=timezone.utc)
                time_to_start = start_datetime - current_time_utc
                total_seconds = int(time_to_start.total_seconds())
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                time_parts = []
                if hours > 0: time_parts.append(f"{hours}ч")
                if minutes > 0: time_parts.append(f"{minutes}м")
                if seconds > 0 or not time_parts: time_parts.append(f"{seconds}с")
                time_to_start_str = " ".join(time_parts)

                upcoming_events.append({
                    'name': name,
                    'location': location,
                    'time_left': time_to_start_str,
                })

            except Exception as e:
                logger.error(f"Error parsing time for event {name}: {e}")
                continue

    return active_events, upcoming_events

# --- Обработчики команд и кнопок ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎮 Открыть ARC Raiders Dashboard", web_app=types.WebAppInfo(url=WEB_APP_URL))]
    ])
    await message.answer("Добро пожаловать!", reply_markup=keyboard)

# --- API эндпоинты ---

async def get_user_events(request):
    try:
        async with ClientSession() as session:
            active, upcoming = await get_arc_raiders_events_from_api_schedule(session)
        return web.json_response({"active": active, "upcoming": upcoming})
    except Exception as e:
        logger.error(f"Ошибка в /api/user_events: {e}")
        return web.json_response({"error": "Internal Server Error"}, status=500)

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
        
        return web.json_response({"status": "success", "message": "Стример зарегистрирован!"})
    except Exception as e:
        logger.error(f"Ошибка в /api/register_streamer: {e}")
        return web.json_response({"error": "Internal Server Error"}, status=500)

# --- Загрузка/сохранение данных стримеров ---
def load_streamers():
    if os.path.exists(STREAMERS_FILE):
        with open(STREAMERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_streamers(streamers):
    # Создаём директорию, если её нет
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
                                logger.error(f"❌ Ошибка отправки уведомления: {e}")
            
            await asyncio.sleep(60) # Проверяем каждые 60 секунд
            
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
    app.router.add_get('/health', lambda r: web.json_response({"status": "ok"}))

    runner = web.AppRunner(app)
    await runner.setup()
    
    # Получаем порт из переменной окружения (Amvera использует PORT)
    port = int(os.getenv("PORT", 80))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ Веб-сервер запущен на http://0.0.0.0:{port}")

    # 🔑 ЗАПУСКАЕМ ФОННУЮ ЗАДАЧУ проверки стримов
    logger.info("✅ Запускаем фоновую задачу проверки стримов (каждую минуту)...")
    asyncio.create_task(check_streams_task())

    # Запускаем бота
    logger.info("✅ Запускаем Telegram-бота...")
    await dp.start_polling(bot)
    await runner.cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем.")
