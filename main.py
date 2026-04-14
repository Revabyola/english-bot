import os
import logging
import random
from flask import Flask, request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler, CallbackQueryHandler
)
import psycopg2
from psycopg2.extras import RealDictCursor

# --- Настройка логирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Состояния ---
(ADD_WORD_WAIT_RUS, ADD_PHRASAL_WAIT_PREP_RUS, WAIT_DELETE_CONFIRMATION) = range(3)

# --- Flask приложение для вебхука и health-check ---
app = Flask(__name__)

# --- Подключение к БД (Render Postgres) ---
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id SERIAL PRIMARY KEY,
            english TEXT NOT NULL,
            russian TEXT NOT NULL,
            user_id BIGINT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS phrasal_verbs (
            id SERIAL PRIMARY KEY,
            verb TEXT NOT NULL,
            prepositions TEXT NOT NULL,
            russian TEXT NOT NULL,
            user_id BIGINT NOT NULL
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

# --- Клавиатуры ---
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить слово", callback_data="add_word")],
        [InlineKeyboardButton("📘 Добавить фразовый глагол", callback_data="add_phrasal")],
        [InlineKeyboardButton("📝 Тест", callback_data="test_menu")],
        [InlineKeyboardButton("📋 Список слов", callback_data="list")],
        [InlineKeyboardButton("🗑 Очистить словарь", callback_data="delete_all")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_test_direction_keyboard():
    keyboard = [
        [InlineKeyboardButton("🇬🇧 Английский → Русский", callback_data="test_en_ru")],
        [InlineKeyboardButton("🇷🇺 Русский → Английский", callback_data="test_ru_en")],
        [InlineKeyboardButton("📖 Фразовые глаголы", callback_data="test_phrasal")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
    return InlineKeyboardMarkup(keyboard)

def get_delete_confirmation_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить всё", callback_data="confirm_delete")],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Основные функции ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я словарный бот.\n\nВыбери действие на клавиатуре:",
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "add_word":
        await query.edit_message_text("✏️ Введи слово на английском:")
        context.user_data['awaiting'] = 'add_word_eng'
    elif data == "add_phrasal":
        await query.edit_message_text("📘 Введи глагол (например, 'look'):")
        context.user_data['awaiting'] = 'add_phrasal_verb'
    elif data == "test_menu":
        await query.edit_message_text(
            "📝 *Выбери тип теста:*",
            reply_markup=get_test_direction_keyboard(),
            parse_mode='Markdown'
        )
    elif data == "test_en_ru":
        await start_test(query, context, "en_ru")
    elif data == "test_ru_en":
        await start_test(query, context, "ru_en")
    elif data == "test_phrasal":
        await start_phrasal_test(query, context)
    elif data == "list":
        await show_word_list(query, context)
    elif data == "delete_all":
        await query.edit_message_text(
            "⚠️ *Внимание!*\n\nТы уверен, что хочешь удалить ВСЕ слова и фразовые глаголы?\nЭто действие нельзя отменить!",
            reply_markup=get_delete_confirmation_keyboard(),
            parse_mode='Markdown'
        )
    elif data == "confirm_delete":
        user_id = update.effective_user.id
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM words WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM phrasal_verbs WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        await query.edit_message_text("✅ Словарь полностью очищен!", reply_markup=get_back_keyboard())
    elif data == "back_to_main":
        await query.edit_message_text("👋 Главное меню:", reply_markup=get_main_keyboard())
        context.user_data.clear()
    elif data == "help":
        await query.edit_message_text(
            "📚 *Справка по командам:*\n\n"
            "➕ *Добавить слово* — добавить пару англ-рус\n"
            "📘 *Добавить фразовый глагол* — глагол с предлогами\n"
            "📝 *Тест* — проверка знаний\n"
            "📋 *Список слов* — показать все слова\n"
            "🗑 *Очистить словарь* — удалить все данные\n\n"
            "В тесте можно выбрать направление перевода.",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )

async def start_test(query, context, direction):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM words WHERE user_id = %s ORDER BY RANDOM() LIMIT 1", (user_id,))
    word = cur.fetchone()
    cur.close()
    conn.close()
    
    if not word:
        await query.edit_message_text("❌ Словарь пуст. Сначала добавь слова!", reply_markup=get_back_keyboard())
        return
    
    context.user_data['current_word'] = word
    context.user_data['test_direction'] = direction
    context.user_data['awaiting'] = 'test_answer'
    
    if direction == "en_ru":
        question = f"🇬🇧 Как переводится: *{word['english']}*?"
        context.user_data['correct_answer'] = word['russian'].lower()
    else:
        question = f"🇷🇺 Как будет по-английски: *{word['russian']}*?"
        context.user_data['correct_answer'] = word['english'].lower()
    
    await query.edit_message_text(
        f"{question}\n\n_Введи ответ текстом:_",
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )

async def start_phrasal_test(query, context):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s ORDER BY RANDOM() LIMIT 1", (user_id,))
    verb = cur.fetchone()
    cur.close()
    conn.close()
    
    if not verb:
        await query.edit_message_text("❌ Нет фразовых глаголов. Добавь их через меню!", reply_markup=get_back_keyboard())
        return
    
    context.user_data['current_phrasal'] = verb
    preps_list = [p.strip() for p in verb['prepositions'].split(',')]
    chosen_prep = random.choice(preps_list)
    context.user_data['chosen_prep'] = chosen_prep
    context.user_data['awaiting'] = 'phrasal_answer'
    
    translations = verb['russian'].split(';')
    correct_rus = ""
    for t in translations:
        if chosen_prep in t:
            correct_rus = t.split('—')[1].strip()
            break
    
    await query.edit_message_text(
        f"📖 *{verb['verb']}* ______\n\nЗначение: _{correct_rus}_\n\nВведи правильный предлог:",
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )

async def show_word_list(query, context):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT english, russian FROM words WHERE user_id = %s ORDER BY id", (user_id,))
    words = cur.fetchall()
    cur.execute("SELECT verb, prepositions, russian FROM phrasal_verbs WHERE user_id = %s ORDER BY id", (user_id,))
    phrasals = cur.fetchall()
    cur.close()
    conn.close()
    
    text = "📋 *Твой словарь:*\n\n"
    if words:
        text += "📝 *Слова:*\n"
        for w in words[:15]: text += f"• {w[0]} — {w[1]}\n"
    else:
        text += "📝 *Слова:* пока нет\n"
    text += "\n"
    if phrasals:
        text += "📘 *Фразовые глаголы:*\n"
        for p in phrasals[:10]: text += f"• {p[0]} ({p[1]}) — {p[2]}\n"
    else:
        text += "📘 *Фразовые глаголы:* пока нет\n"
    
    await query.edit_message_text(text, reply_markup=get_back_keyboard(), parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text.strip()
    awaiting = context.user_data.get('awaiting')
    
    if awaiting == 'add_word_eng':
        context.user_data['temp_eng'] = user_text
        context.user_data['awaiting'] = 'add_word_rus'
        await update.message.reply_text("🇷🇺 Теперь введи перевод на русском:", reply_markup=get_back_keyboard())
    elif awaiting == 'add_word_rus':
        eng = context.user_data.get('temp_eng')
        rus = user_text
        user_id = update.effective_user.id
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO words (english, russian, user_id) VALUES (%s, %s, %s)", (eng, rus, user_id))
        conn.commit()
        cur.close()
        conn.close()
        context.user_data.clear()
        await update.message.reply_text(f"✅ Пара *{eng} — {rus}* сохранена!", reply_markup=get_main_keyboard(), parse_mode='Markdown')
    elif awaiting == 'add_phrasal_verb':
        context.user_data['temp_verb'] = user_text.lower()
        context.user_data['awaiting'] = 'add_phrasal_data'
        await update.message.reply_text("✏️ Введи предлог(и) и перевод в формате:\n\n`after = присматривать, down = презирать`", reply_markup=get_back_keyboard(), parse_mode='Markdown')
    elif awaiting == 'add_phrasal_data':
        verb = context.user_data.get('temp_verb')
        raw_text = user_text
        user_id = update.effective_user.id
        preps, trans = [], []
        for p in raw_text.split(','):
            if '=' in p:
                prep, tran = p.split('=')
                preps.append(prep.strip())
                trans.append(f"{prep.strip()} — {tran.strip()}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO phrasal_verbs (verb, prepositions, russian, user_id) VALUES (%s, %s, %s, %s)", (verb, ", ".join(preps), "; ".join(trans), user_id))
        conn.commit()
        cur.close()
        conn.close()
        context.user_data.clear()
        await update.message.reply_text(f"✅ Глагол *{verb}* сохранён!", reply_markup=get_main_keyboard(), parse_mode='Markdown')
    elif awaiting == 'test_answer':
        user_answer = user_text.lower()
        correct = context.user_data.get('correct_answer')
        word_data = context.user_data.get('current_word')
        if user_answer == correct: response = "✅ *Верно!*"
        else: response = f"❌ *Неверно!*\nПравильно: *{word_data['english']} — {word_data['russian']}*"
        context.user_data.clear()
        await update.message.reply_text(response, reply_markup=get_main_keyboard(), parse_mode='Markdown')
    elif awaiting == 'phrasal_answer':
        user_answer = user_text.lower()
        correct_prep = context.user_data.get('chosen_prep')
        if user_answer == correct_prep: response = "✅ *Точно!*"
        else: response = f"❌ *Не угадал!*\nНужен предлог: *{correct_prep}*"
        context.user_data.clear()
        await update.message.reply_text(response, reply_markup=get_main_keyboard(), parse_mode='Markdown')
    else:
        await update.message.reply_text("Используй кнопки меню для навигации.", reply_markup=get_main_keyboard())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text("❌ Действие отменено.", reply_markup=get_main_keyboard())

# --- Инициализация бота (ДО Flask эндпоинтов) ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Не найден TELEGRAM_BOT_TOKEN")

init_db()

# Создаём приложение Telegram ГЛОБАЛЬНО
application = Application.builder().token(TOKEN).build()

# Регистрируем все handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("cancel", cancel))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# --- Фоновый обработчик очереди ---
import asyncio
from threading import Thread

async def process_updates():
    while True:
        try:
            update = await application.update_queue.get()
            await application.process_update(update)
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")

def start_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(process_updates())

Thread(target=start_loop, daemon=True).start()
logger.info("Фоновый обработчик очереди запущен")

# --- Flask эндпоинты ---
@app.route('/health')
def health():
    return Response("OK", status=200)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Принимает обновления от Telegram."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return Response("OK", status=200)

# --- Запуск ---
if __name__ == "__main__":
    # Установка вебхука
    app_url = os.environ.get("RENDER_EXTERNAL_URL")
    if app_url:
        application.bot.set_webhook(f"{app_url}/webhook")
        logger.info(f"Webhook установлен: {app_url}/webhook")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)