import os
import logging
import random
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

def get_test_active_keyboard():
    """Клавиатура во время активного теста."""
    keyboard = [[InlineKeyboardButton("❌ Завершить тест", callback_data="end_test")]]
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
        await start_word_test(query, context, "en_ru")
        
    elif data == "test_ru_en":
        await start_word_test(query, context, "ru_en")
        
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
        context.user_data.clear()
        await query.edit_message_text("👋 Главное меню:", reply_markup=get_main_keyboard())
        
    elif data == "end_test":
        # Завершение теста досрочно
        correct = context.user_data.get('test_correct', 0)
        total = context.user_data.get('test_total', 0)
        context.user_data.clear()
        
        if total > 0:
            text = f"🏁 *Тест прерван!*\n\n✅ Правильно: {correct}\n❌ Ошибок: {total - correct}\n📊 Точность: {int(correct/total*100)}%"
        else:
            text = "🏁 *Тест прерван!*"
        
        await query.edit_message_text(text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
        
    elif data == "help":
        await query.edit_message_text(
            "📚 *Справка:*\n\n"
            "➕ *Добавить слово* — пара англ-рус\n"
            "📘 *Фразовый глагол* — глагол с предлогами\n"
            "📝 *Тест* — непрерывная проверка знаний\n"
            "📋 *Список* — все слова\n"
            "🗑 *Очистить* — удалить всё\n\n"
            "В тесте слова идут друг за другом. Нажми «Завершить тест» для выхода.",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )

async def start_word_test(query, context, direction):
    """Запуск непрерывного теста по словам."""
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM words WHERE user_id = %s ORDER BY RANDOM()", (user_id,))
    words = cur.fetchall()
    cur.close()
    conn.close()
    
    if not words:
        await query.edit_message_text("❌ Словарь пуст. Сначала добавь слова!", reply_markup=get_back_keyboard())
        return
    
    context.user_data['test_words'] = words
    context.user_data['test_index'] = 0
    context.user_data['test_direction'] = direction
    context.user_data['test_correct'] = 0
    context.user_data['test_total'] = 0
    context.user_data['in_word_test'] = True
    context.user_data['awaiting'] = 'word_test_answer'
    
    await ask_next_word_question(query, context)

async def ask_next_word_question(query, context):
    """Задаёт следующий вопрос в тесте по словам."""
    words = context.user_data.get('test_words', [])
    index = context.user_data.get('test_index', 0)
    direction = context.user_data.get('test_direction', 'en_ru')
    
    if index >= len(words):
        # Тест закончен
        correct = context.user_data.get('test_correct', 0)
        total = context.user_data.get('test_total', 0)
        context.user_data.clear()
        
        text = f"🏁 *Тест завершён!*\n\n✅ Правильно: {correct}\n❌ Ошибок: {total - correct}\n📊 Точность: {int(correct/total*100) if total > 0 else 0}%"
        await query.edit_message_text(text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
        return
    
    word = words[index]
    context.user_data['current_word'] = word
    
    if direction == "en_ru":
        question = f"🇬🇧 *{word['english']}*"
        context.user_data['correct_answer'] = word['russian'].lower()
    else:
        question = f"🇷🇺 *{word['russian']}*"
        context.user_data['correct_answer'] = word['english'].lower()
    
    progress = f"📌 Вопрос {index + 1} из {len(words)}"
    
    await query.edit_message_text(
        f"{progress}\n\n{question}\n\n_Введи перевод:_",
        reply_markup=get_test_active_keyboard(),
        parse_mode='Markdown'
    )

async def start_phrasal_test(query, context):
    """Запуск непрерывного теста по фразовым глаголам."""
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s ORDER BY RANDOM()", (user_id,))
    verbs = cur.fetchall()
    cur.close()
    conn.close()
    
    if not verbs:
        await query.edit_message_text("❌ Нет фразовых глаголов. Добавь их через меню!", reply_markup=get_back_keyboard())
        return
    
    # Для каждого глагола выбираем случайный предлог
    test_items = []
    for verb in verbs:
        preps_list = [p.strip() for p in verb['prepositions'].split(',')]
        chosen_prep = random.choice(preps_list)
        
        translations = verb['russian'].split(';')
        correct_rus = ""
        for t in translations:
            if chosen_prep in t:
                correct_rus = t.split('—')[1].strip()
                break
        
        test_items.append({
            'verb': verb['verb'],
            'prep': chosen_prep,
            'meaning': correct_rus
        })
    
    random.shuffle(test_items)
    
    context.user_data['test_phrasal_items'] = test_items
    context.user_data['test_index'] = 0
    context.user_data['test_correct'] = 0
    context.user_data['test_total'] = 0
    context.user_data['in_phrasal_test'] = True
    context.user_data['awaiting'] = 'phrasal_test_answer'
    
    await ask_next_phrasal_question(query, context)

async def ask_next_phrasal_question(query, context):
    """Задаёт следующий вопрос в тесте по фразовым глаголам."""
    items = context.user_data.get('test_phrasal_items', [])
    index = context.user_data.get('test_index', 0)
    
    if index >= len(items):
        # Тест закончен
        correct = context.user_data.get('test_correct', 0)
        total = context.user_data.get('test_total', 0)
        context.user_data.clear()
        
        text = f"🏁 *Тест завершён!*\n\n✅ Правильно: {correct}\n❌ Ошибок: {total - correct}\n📊 Точность: {int(correct/total*100) if total > 0 else 0}%"
        await query.edit_message_text(text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
        return
    
    item = items[index]
    context.user_data['current_phrasal_item'] = item
    
    progress = f"📌 Вопрос {index + 1} из {len(items)}"
    
    await query.edit_message_text(
        f"{progress}\n\n📖 *{item['verb']}* ______\n\nЗначение: _{item['meaning']}_\n\n_Введи предлог:_",
        reply_markup=get_test_active_keyboard(),
        parse_mode='Markdown'
    )

async def show_word_list(query, context):
    """Показывает список всех слов пользователя."""
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
        for w in words[:15]:
            text += f"• {w[0]} — {w[1]}\n"
        if len(words) > 15:
            text += f"_...и ещё {len(words)-15}_\n"
    else:
        text += "📝 *Слова:* пока нет\n"
    
    text += "\n"
    
    if phrasals:
        text += "📘 *Фразовые глаголы:*\n"
        for p in phrasals[:10]:
            text += f"• {p[0]} ({p[1]}) — {p[2]}\n"
        if len(phrasals) > 10:
            text += f"_...и ещё {len(phrasals)-10}_\n"
    else:
        text += "📘 *Фразовые глаголы:* пока нет\n"
    
    await query.edit_message_text(text, reply_markup=get_back_keyboard(), parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений."""
    user_text = update.message.text.strip()
    awaiting = context.user_data.get('awaiting')
    
    # --- Добавление обычного слова ---
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
    
    # --- Добавление фразового глагола ---
    elif awaiting == 'add_phrasal_verb':
        context.user_data['temp_verb'] = user_text.lower()
        context.user_data['awaiting'] = 'add_phrasal_data'
        await update.message.reply_text(
            "✏️ Введи предлог(и) и перевод:\n`after = присматривать, down = презирать`",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        
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
        cur.execute(
            "INSERT INTO phrasal_verbs (verb, prepositions, russian, user_id) VALUES (%s, %s, %s, %s)",
            (verb, ", ".join(preps), "; ".join(trans), user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        context.user_data.clear()
        await update.message.reply_text(f"✅ Глагол *{verb}* сохранён!", reply_markup=get_main_keyboard(), parse_mode='Markdown')
    
    # --- Тест по словам ---
    elif awaiting == 'word_test_answer':
        user_answer = user_text.lower()
        correct = context.user_data.get('correct_answer', '')
        word = context.user_data.get('current_word', {})
        
        context.user_data['test_total'] = context.user_data.get('test_total', 0) + 1
        
        if user_answer == correct:
            context.user_data['test_correct'] = context.user_data.get('test_correct', 0) + 1
            response = f"✅ *Верно!* ({word.get('english', '')} — {word.get('russian', '')})"
        else:
            response = f"❌ *Неверно!*\nПравильно: *{word.get('english', '')} — {word.get('russian', '')}*"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
        # Переходим к следующему вопросу
        context.user_data['test_index'] = context.user_data.get('test_index', 0) + 1
        
        # Создаём фейковый query для функции ask_next
        class FakeQuery:
            def __init__(self, message):
                self.message = message
            async def edit_message_text(self, text, **kwargs):
                await self.message.reply_text(text, **kwargs)
        
        fake_query = FakeQuery(update.message)
        await ask_next_word_question(fake_query, context)
    
    # --- Тест по фразовым глаголам ---
    elif awaiting == 'phrasal_test_answer':
        user_answer = user_text.lower()
        item = context.user_data.get('current_phrasal_item', {})
        correct_prep = item.get('prep', '')
        
        context.user_data['test_total'] = context.user_data.get('test_total', 0) + 1
        
        if user_answer == correct_prep:
            context.user_data['test_correct'] = context.user_data.get('test_correct', 0) + 1
            response = f"✅ *Верно!* ({item.get('verb', '')} {correct_prep} — {item.get('meaning', '')})"
        else:
            response = f"❌ *Неверно!*\nПравильно: *{item.get('verb', '')} {correct_prep}* — {item.get('meaning', '')}"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
        # Переходим к следующему вопросу
        context.user_data['test_index'] = context.user_data.get('test_index', 0) + 1
        
        class FakeQuery:
            def __init__(self, message):
                self.message = message
            async def edit_message_text(self, text, **kwargs):
                await self.message.reply_text(text, **kwargs)
        
        fake_query = FakeQuery(update.message)
        await ask_next_phrasal_question(fake_query, context)
    
    # --- Нет активного действия ---
    else:
        await update.message.reply_text("Используй кнопки меню для навигации.", reply_markup=get_main_keyboard())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text("❌ Действие отменено.", reply_markup=get_main_keyboard())

# --- ЗАПУСК POLLING ---
def main():
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise ValueError("Не найден TELEGRAM_BOT_TOKEN")
    
    init_db()
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("Бот запущен в режиме POLLING")
    app.run_polling()

if __name__ == "__main__":
    main()