import os
import logging
import random
from flask import Flask, request, Response
from telegram import Update
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

# --- Состояния для ConversationHandler ---
(ADD_WORD_WAIT_RUS, ADD_PHRASAL_WAIT_PREP_RUS) = range(2)

# --- Flask приложение для вебхука и health-check ---
app = Flask(__name__)

# --- Подключение к БД (Render Postgres) ---
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """Создаем таблицы, если их нет."""
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

# --- Основные функции бота (без изменений) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я словарный бот.\n"
        "Команды:\n"
        "/add - Добавить слово\n"
        "/add_phrasal - Добавить фразовый глагол\n"
        "/test - Тест (перевод)\n"
        "/test_phrasal - Тест по предлогам\n"
        "/list - Список твоих слов"
    )

async def add_word_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("✏️ Введи слово на английском:")
    return 0

async def add_word_rus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['temp_eng'] = update.message.text.strip()
    await update.message.reply_text("🇷🇺 Теперь введи перевод на русском:")
    return 1

async def add_word_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    eng = context.user_data['temp_eng']
    rus = update.message.text.strip()
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO words (english, russian, user_id) VALUES (%s, %s, %s)",
        (eng, rus, user_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    await update.message.reply_text(f"✅ Пара '{eng} — {rus}' сохранена!")
    return ConversationHandler.END

async def add_phrasal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📘 Введи глагол (например, 'look'):")
    return 0

async def add_phrasal_prep_rus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    verb = update.message.text.strip().lower()
    context.user_data['temp_verb'] = verb
    
    await update.message.reply_text(
        "✏️ Введи предлог(и) и перевод в формате:\n"
        "<code>after = присматривать, down = презирать</code>\n\n"
        "(Можно несколько через запятую)",
        parse_mode='HTML'
    )
    return 1

async def add_phrasal_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    verb = context.user_data['temp_verb']
    raw_text = update.message.text
    
    preps = []
    trans = []
    pairs = raw_text.split(',')
    for p in pairs:
        if '=' in p:
            prep, tran = p.split('=')
            preps.append(prep.strip())
            trans.append(f"{prep.strip()} — {tran.strip()}")
    
    prepositions_str = ", ".join(preps)
    translation_str = "; ".join(trans)
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO phrasal_verbs (verb, prepositions, russian, user_id) VALUES (%s, %s, %s, %s)",
        (verb, prepositions_str, translation_str, user_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    await update.message.reply_text(f"✅ Глагол '{verb}' с предлогами сохранен!")
    return ConversationHandler.END

async def test_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM words WHERE user_id = %s ORDER BY RANDOM() LIMIT 1", (user_id,))
    word = cur.fetchone()
    cur.close()
    conn.close()
    
    if not word:
        await update.message.reply_text("Словарь пуст. Добавь слова командой /add")
        return
    
    context.user_data['current_word'] = word
    direction = random.randint(0, 1)
    context.user_data['direction'] = direction
    
    if direction == 0:
        question = f"🇬🇧 Как переводится: *{word['english']}*?"
        context.user_data['correct_answer'] = word['russian'].lower()
    else:
        question = f"🇷🇺 Как будет по-английски: *{word['russian']}*?"
        context.user_data['correct_answer'] = word['english'].lower()
    
    await update.message.reply_text(question, parse_mode='Markdown')

async def test_phrasal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s ORDER BY RANDOM() LIMIT 1", (user_id,))
    verb = cur.fetchone()
    cur.close()
    conn.close()
    
    if not verb:
        await update.message.reply_text("Список фразовых глаголов пуст. Добавь командой /add_phrasal")
        return
    
    context.user_data['current_phrasal'] = verb
    
    preps_list = [p.strip() for p in verb['prepositions'].split(',')]
    chosen_prep = random.choice(preps_list)
    context.user_data['chosen_prep'] = chosen_prep
    
    translations = verb['russian'].split(';')
    correct_rus = None
    for t in translations:
        if chosen_prep in t:
            correct_rus = t.split('—')[1].strip()
            break
    
    context.user_data['correct_phrasal_answer'] = correct_rus.lower() if correct_rus else ""
    
    question = f"📖 {verb['verb']} ______ (значение: {correct_rus})"
    await update.message.reply_text(f"{question}\n\nВведи предлог:")

async def handle_test_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_answer = update.message.text.strip().lower()
    
    if 'current_word' in context.user_data:
        correct = context.user_data['correct_answer']
        word_data = context.user_data['current_word']
        
        if user_answer == correct:
            await update.message.reply_text("✅ Верно!")
        else:
            hint = f"❌ Неверно. Правильно: {word_data['english']} — {word_data['russian']}"
            await update.message.reply_text(hint)
        
        del context.user_data['current_word']
        del context.user_data['correct_answer']
        del context.user_data['direction']
        
    elif 'current_phrasal' in context.user_data:
        correct_prep = context.user_data['chosen_prep']
        if user_answer == correct_prep:
            await update.message.reply_text("✅ Точно!")
        else:
            await update.message.reply_text(f"❌ Не угадал. Нужен предлог: *{correct_prep}*", parse_mode='Markdown')
        
        del context.user_data['current_phrasal']
        del context.user_data['chosen_prep']
        del context.user_data['correct_phrasal_answer']

async def list_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT english, russian FROM words WHERE user_id = %s LIMIT 20", (user_id,))
    words = cur.fetchall()
    cur.close()
    conn.close()
    
    if not words:
        await update.message.reply_text("Список пуст.")
        return
    
    text = "Твои слова:\n" + "\n".join([f"{w[0]} — {w[1]}" for w in words])
    await update.message.reply_text(text)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

# --- Инициализация бота и вебхука ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Не найден TELEGRAM_BOT_TOKEN")

# Создаем приложение Telegram
application = Application.builder().token(TOKEN).build()

# Conversation для добавления слов
conv_handler_add = ConversationHandler(
    entry_points=[CommandHandler('add', add_word_start)],
    states={
        0: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_word_rus)],
        1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_word_save)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)

# Conversation для фразовых глаголов
conv_handler_phrasal = ConversationHandler(
    entry_points=[CommandHandler('add_phrasal', add_phrasal_start)],
    states={
        0: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_phrasal_prep_rus)],
        1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_phrasal_save)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)

application.add_handler(conv_handler_add)
application.add_handler(conv_handler_phrasal)
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("test", test_start))
application.add_handler(CommandHandler("test_phrasal", test_phrasal_start))
application.add_handler(CommandHandler("list", list_words))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_test_answer))

# Инициализация БД
init_db()

# --- Flask эндпоинты ---

@app.route('/health')
def health():
    """Эндпоинт для cron-job, чтобы сервер не засыпал."""
    return Response("OK", status=200)

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    """Принимает обновления от Telegram."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put(update)
    return Response("OK", status=200)

# --- Запуск (локально и на Render) ---
if __name__ == "__main__":
    # На Render порт берется из переменной окружения PORT
    port = int(os.environ.get("PORT", 5000))
    
    # Устанавливаем вебхук при старте
    app_url = os.environ.get("RENDER_EXTERNAL_URL")
    if app_url:
        webhook_url = f"{app_url}/{TOKEN}"
        application.bot.set_webhook(webhook_url)
        logger.info(f"Webhook установлен: {webhook_url}")
    
    # Запускаем Flask сервер
    app.run(host="0.0.0.0", port=port)