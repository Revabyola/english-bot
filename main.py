import os
import logging
import random
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
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
(ADD_WORD_WAIT_RUS, ADD_PHRASAL_WAIT_PREP_RUS) = range(2)

# --- Подключение к БД ---
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

# --- Функции бота (без изменений) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я словарный бот.\n"
        "/add - Добавить слово\n"
        "/add_phrasal - Добавить фразовый глагол\n"
        "/test - Тест (перевод)\n"
        "/test_phrasal - Тест по предлогам\n"
        "/list - Список слов"
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
        "✏️ Введи предлог(и) и перевод:\n"
        "<code>after = присматривать, down = презирать</code>",
        parse_mode='HTML'
    )
    return 1

async def add_phrasal_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    verb = context.user_data['temp_verb']
    raw_text = update.message.text
    
    preps = []
    trans = []
    for p in raw_text.split(','):
        if '=' in p:
            prep, tran = p.split('=')
            preps.append(prep.strip())
            trans.append(f"{prep.strip()} — {tran.strip()}")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO phrasal_verbs (verb, prepositions, russian, user_id) VALUES (%s, %s, %s, %s)",
        (verb, ", ".join(preps), "; ".join(trans), user_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    await update.message.reply_text(f"✅ Глагол '{verb}' сохранен!")
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
        await update.message.reply_text("Словарь пуст. Добавь слова через /add")
        return
    
    context.user_data['current_word'] = word
    direction = random.randint(0, 1)
    
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
        await update.message.reply_text("Список пуст. Добавь через /add_phrasal")
        return
    
    context.user_data['current_phrasal'] = verb
    preps_list = [p.strip() for p in verb['prepositions'].split(',')]
    chosen_prep = random.choice(preps_list)
    context.user_data['chosen_prep'] = chosen_prep
    
    translations = verb['russian'].split(';')
    correct_rus = ""
    for t in translations:
        if chosen_prep in t:
            correct_rus = t.split('—')[1].strip()
            break
    
    await update.message.reply_text(f"📖 {verb['verb']} ______ (значение: {correct_rus})\n\nВведи предлог:")

async def handle_test_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_answer = update.message.text.strip().lower()
    
    if 'current_word' in context.user_data:
        correct = context.user_data['correct_answer']
        word_data = context.user_data['current_word']
        
        if user_answer == correct:
            await update.message.reply_text("✅ Верно!")
        else:
            await update.message.reply_text(f"❌ Правильно: {word_data['english']} — {word_data['russian']}")
        
        del context.user_data['current_word']
        del context.user_data['correct_answer']
        
    elif 'current_phrasal' in context.user_data:
        correct_prep = context.user_data['chosen_prep']
        if user_answer == correct_prep:
            await update.message.reply_text("✅ Точно!")
        else:
            await update.message.reply_text(f"❌ Нужен предлог: *{correct_prep}*", parse_mode='Markdown')
        
        del context.user_data['current_phrasal']
        del context.user_data['chosen_prep']

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

# --- Запуск ---
def main():
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise ValueError("Не найден TELEGRAM_BOT_TOKEN")
    
    init_db()
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('add', add_word_start)],
        states={0: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_word_rus)],
                1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_word_save)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('add_phrasal', add_phrasal_start)],
        states={0: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_phrasal_prep_rus)],
                1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_phrasal_save)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    ))
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_start))
    app.add_handler(CommandHandler("test_phrasal", test_phrasal_start))
    app.add_handler(CommandHandler("list", list_words))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_test_answer))
    
    logger.info("Бот запущен в режиме POLLING")
    app.run_polling()

if __name__ == "__main__":
    main()