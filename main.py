import os
import logging
import random
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
import psycopg2
from psycopg2.extras import RealDictCursor
from googletrans import Translator

# --- Настройка логирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
    keyboard = [[InlineKeyboardButton("❌ Завершить тест", callback_data="end_test")]]
    return InlineKeyboardMarkup(keyboard)

def get_translation_variants_keyboard(variants, english_word):
    keyboard = []
    for variant in variants[:4]:
        keyboard.append([InlineKeyboardButton(variant, callback_data=f"choose_{variant}")])
    keyboard.append([InlineKeyboardButton("✏️ Ввести свой вариант", callback_data="custom_translation")])
    keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

# --- HTTP сервер для Render ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def start_http_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"HTTP сервер запущен на порту {port}")
    server.serve_forever()

# --- ИИ-переводчик (MyMemory + Google Translate) ---
def translate_word(word):
    translations = []
    
    # Способ 1: MyMemory API
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": word, "langpair": "en|ru"}
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        
        if data.get("responseStatus") == 200:
            main_translation = data.get("responseData", {}).get("translatedText", "").lower()
            if main_translation:
                translations.append(main_translation)
            
            for match in data.get("matches", [])[:3]:
                translation = match.get("translation", "").lower()
                if translation and translation not in translations:
                    translations.append(translation)
    except Exception as e:
        logger.warning(f"MyMemory API ошибка: {e}")
    
    # Способ 2: Google Translate (резервный)
    if not translations:
        try:
            translator = Translator()
            result = translator.translate(word, src='en', dest='ru')
            if result and result.text:
                translations.append(result.text.lower())
        except Exception as e:
            logger.warning(f"Google Translate ошибка: {e}")
    
    unique_translations = list(dict.fromkeys(translations))
    return unique_translations[:4]

# --- Основные функции бота ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я словарный бот с ИИ-переводчиком.\n\n"
        "При добавлении слова я автоматически предлагаю варианты перевода!\n\n"
        "Выбери действие на клавиатуре:",
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Не удалось ответить на callback: {e}")
    
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
        correct = context.user_data.get('test_correct', 0)
        total = context.user_data.get('test_total', 0)
        context.user_data.clear()
        
        if total > 0:
            text = f"🏁 *Тест прерван!*\n\n✅ Правильно: {correct}\n❌ Ошибок: {total - correct}\n📊 Точность: {int(correct/total*100)}%"
        else:
            text = "🏁 *Тест прерван!*"
        
        await query.edit_message_text(text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
        
    elif data == "custom_translation":
        await query.edit_message_text(
            "✏️ Введи свой перевод:",
            reply_markup=get_back_keyboard()
        )
        context.user_data['awaiting'] = 'add_word_rus_manual'
        
    elif data.startswith("choose_"):
        translation = data.replace("choose_", "")
        english = context.user_data.get('temp_eng', '')
        user_id = update.effective_user.id
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO words (english, russian, user_id) VALUES (%s, %s, %s)", (english, translation, user_id))
        conn.commit()
        cur.close()
        conn.close()
        
        context.user_data.clear()
        await query.edit_message_text(
            f"✅ Пара *{english} — {translation}* сохранена!",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        
    elif data == "help":
        await query.edit_message_text(
            "📚 *Справка:*\n\n"
            "➕ *Добавить слово* — ИИ предложит варианты перевода\n"
            "📘 *Фразовый глагол* — глагол с предлогами\n"
            "📝 *Тест* — непрерывная проверка знаний\n"
            "📋 *Список* — все слова\n"
            "🗑 *Очистить* — удалить всё",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )

async def start_word_test(query, context, direction):
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
    words = context.user_data.get('test_words', [])
    index = context.user_data.get('test_index', 0)
    direction = context.user_data.get('test_direction', 'en_ru')
    
    if index >= len(words):
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
        context.user_data['correct_answer'] = word['russian'].lower().strip()
    else:
        question = f"🇷🇺 *{word['russian']}*"
        context.user_data['correct_answer'] = word['english'].lower().strip()
    
    progress = f"📌 Вопрос {index + 1} из {len(words)}"
    
    await query.edit_message_text(
        f"{progress}\n\n{question}\n\n_Введи перевод:_",
        reply_markup=get_test_active_keyboard(),
        parse_mode='Markdown'
    )

async def start_phrasal_test(query, context):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s", (user_id,))
    verbs = cur.fetchall()
    cur.close()
    conn.close()
    
    if not verbs:
        await query.edit_message_text("❌ Нет фразовых глаголов. Добавь их через меню!", reply_markup=get_back_keyboard())
        return
    
    test_items = []
    for verb in verbs:
        preps_list = [p.strip() for p in verb['prepositions'].split(',') if p.strip()]
        if not preps_list:
            continue
            
        chosen_prep = random.choice(preps_list)
        
        translations = verb['russian'].split(';')
        correct_rus = ""
        for t in translations:
            t = t.strip()
            if chosen_prep in t:
                for sep in ['—', '-', ':']:
                    if sep in t:
                        parts = t.split(sep, 1)
                        if len(parts) > 1:
                            correct_rus = parts[1].strip()
                            break
                if correct_rus:
                    break
        
        if not correct_rus:
            correct_rus = f"({chosen_prep})"
        
        test_items.append({
            'verb': verb['verb'],
            'prep': chosen_prep,
            'meaning': correct_rus
        })
    
    if not test_items:
        await query.edit_message_text(
            "❌ Нет данных для теста.",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    random.shuffle(test_items)
    
    context.user_data['test_phrasal_items'] = test_items
    context.user_data['test_index'] = 0
    context.user_data['test_correct'] = 0
    context.user_data['test_total'] = 0
    context.user_data['in_phrasal_test'] = True
    context.user_data['awaiting'] = 'phrasal_test_answer'
    
    await ask_next_phrasal_question(query, context)

async def ask_next_phrasal_question(query, context):
    items = context.user_data.get('test_phrasal_items', [])
    index = context.user_data.get('test_index', 0)
    
    if index >= len(items):
        correct = context.user_data.get('test_correct', 0)
        total = context.user_data.get('test_total', 0)
        context.user_data.clear()
        
        if total > 0:
            text = f"🏁 *Тест завершён!*\n\n✅ Правильно: {correct}\n❌ Ошибок: {total - correct}\n📊 Точность: {int(correct/total*100)}%"
        else:
            text = "🏁 *Тест завершён!*"
        
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
    user_text = update.message.text.strip()
    awaiting = context.user_data.get('awaiting')
    
    # --- Добавление слова: ввод английского ---
    if awaiting == 'add_word_eng':
        await update.message.reply_text("🔄 Перевожу через ИИ...")
        
        translations = translate_word(user_text)
        context.user_data['temp_eng'] = user_text
        
        if translations:
            context.user_data['translation_variants'] = translations
            await update.message.reply_text(
                f"📖 Переводы для *{user_text}*:\n\nВыбери подходящий вариант или введи свой:",
                reply_markup=get_translation_variants_keyboard(translations, user_text),
                parse_mode='Markdown'
            )
            context.user_data['awaiting'] = None
        else:
            await update.message.reply_text(
                "❌ Не удалось найти перевод.\n\n🇷🇺 Введи перевод на русском вручную:",
                reply_markup=get_back_keyboard()
            )
            context.user_data['awaiting'] = 'add_word_rus_manual'
    
    # --- Ручной ввод перевода ---
    elif awaiting == 'add_word_rus_manual':
        english = context.user_data.get('temp_eng', '')
        russian = user_text
        user_id = update.effective_user.id
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO words (english, russian, user_id) VALUES (%s, %s, %s)", (english, russian, user_id))
        conn.commit()
        cur.close()
        conn.close()
        
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Пара *{english} — {russian}* сохранена!",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    
    # --- Добавление фразового глагола ---
    elif awaiting == 'add_phrasal_verb':
        context.user_data['temp_verb'] = user_text.lower()
        context.user_data['awaiting'] = 'add_phrasal_data'
        await update.message.reply_text(
            "✏️ Введи предлог(и) и перевод:\n\n`after = присматривать, down = презирать`",
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
                prep, tran = p.split('=', 1)
                preps.append(prep.strip())
                trans.append(f"{prep.strip()} — {tran.strip()}")
        
        if not preps:
            await update.message.reply_text("❌ Неверный формат. Попробуй снова.", reply_markup=get_main_keyboard())
            context.user_data.clear()
            return
        
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
        user_answer = user_text.lower().strip()
        correct = context.user_data.get('correct_answer', '')
        word = context.user_data.get('current_word', {})
        
        context.user_data['test_total'] = context.user_data.get('test_total', 0) + 1
        
        is_correct = False
        if ',' in correct:
            correct_variants = [v.strip() for v in correct.split(',')]
            is_correct = user_answer in correct_variants
        else:
            is_correct = user_answer == correct
        
        if is_correct:
            context.user_data['test_correct'] = context.user_data.get('test_correct', 0) + 1
            response = f"✅ *Верно!* ({word.get('english', '')} — {word.get('russian', '')})"
        else:
            response = f"❌ *Неверно!*\nПравильно: *{word.get('english', '')} — {word.get('russian', '')}*"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
        context.user_data['test_index'] = context.user_data.get('test_index', 0) + 1
        
        class FakeQuery:
            def __init__(self, message):
                self.message = message
            async def edit_message_text(self, text, **kwargs):
                await self.message.reply_text(text, **kwargs)
        
        fake_query = FakeQuery(update.message)
        await ask_next_word_question(fake_query, context)
    
    # --- Тест по фразовым глаголам ---
    elif awaiting == 'phrasal_test_answer':
        user_answer = user_text.lower().strip()
        item = context.user_data.get('current_phrasal_item', {})
        correct_prep = item.get('prep', '').lower()
        
        context.user_data['test_total'] = context.user_data.get('test_total', 0) + 1
        
        if user_answer == correct_prep:
            context.user_data['test_correct'] = context.user_data.get('test_correct', 0) + 1
            response = f"✅ *Верно!* ({item.get('verb', '')} {correct_prep} — {item.get('meaning', '')})"
        else:
            response = f"❌ *Неверно!*\nПравильно: *{item.get('verb', '')} {correct_prep}* — {item.get('meaning', '')}"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
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

# --- ЗАПУСК ---
def main():
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise ValueError("Не найден TELEGRAM_BOT_TOKEN")
    
    init_db()
    
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("Бот с ИИ-переводчиком запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()