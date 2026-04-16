import os
import logging
import random
import threading
import re
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
import psycopg2
from psycopg2.extras import RealDictCursor
from deep_translator import GoogleTranslator

# --- Настройка логирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Состояния ---
AWAIT_COMMENT = 1

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
            comment TEXT,
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
    try:
        cur.execute("ALTER TABLE words ADD COLUMN IF NOT EXISTS comment TEXT")
        conn.commit()
    except:
        pass
    conn.commit()
    cur.close()
    conn.close()

# --- Парсинг комментария ---
def parse_word_with_comment(text):
    match = re.match(r'^(.+?)\s*\((.+)\)$', text.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text.strip(), None

# --- Клавиатуры ---
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить слово", callback_data="add_word")],
        [InlineKeyboardButton("📘 Добавить фразовый глагол", callback_data="add_phrasal")],
        [InlineKeyboardButton("📝 Тест", callback_data="test_menu")],
        [InlineKeyboardButton("📋 Список слов", callback_data="list")],
        [InlineKeyboardButton("❌ Удалить слова", callback_data="delete_words")],
        [InlineKeyboardButton("❌ Удалить фразовые глаголы", callback_data="delete_phrasal")],
        [InlineKeyboardButton("🗑 Очистить словарь", callback_data="delete_all")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🎮 Открыть приложение", web_app=WebAppInfo(url="https://revabyola.github.io/eng-bot-app/"))]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_test_direction_keyboard():
    keyboard = [
        [InlineKeyboardButton("🇬🇧 Английский → Русский", callback_data="test_en_ru")],
        [InlineKeyboardButton("🇷🇺 Русский → Английский", callback_data="test_ru_en")],
        [InlineKeyboardButton("📖 Фразовые глаголы", callback_data="test_phrasal")],
        [InlineKeyboardButton("🔀 Общий тест", callback_data="test_mixed")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]])

def get_delete_confirmation_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить всё", callback_data="confirm_delete")],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_test_active_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Завершить тест", callback_data="end_test")]])

def get_translation_variants_keyboard(variants, english_word):
    keyboard = []
    for variant in variants[:6]:
        safe_data = variant.replace(' ', '_').replace(',', '').replace('.', '')[:50]
        keyboard.append([InlineKeyboardButton(variant, callback_data=f"tr_{safe_data}")])
    keyboard.append([InlineKeyboardButton("✏️ Ввести свой вариант", callback_data="custom_translation")])
    keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_comment_keyboard(word_id, word_type="word"):
    keyboard = [
        [InlineKeyboardButton("📝 Добавить комментарий", callback_data=f"add_comment_{word_type}_{word_id}")],
        [InlineKeyboardButton("✅ Пропустить", callback_data="skip_comment")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_delete_items_keyboard(items, page=0, per_page=5, item_type="word"):
    keyboard = []
    start = page * per_page
    end = start + per_page
    current_items = items[start:end]
    
    for item in current_items:
        if item_type == "word":
            display = f"🗑 {item['english']} — {item['russian']}"
        else:
            display = f"🗑 {item['verb']} ({item['prepositions']}) — {item['russian']}"
        keyboard.append([InlineKeyboardButton(display, callback_data=f"delete_{item_type}_{item['id']}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"delete_{item_type}_page_{page-1}"))
    if end < len(items):
        nav_buttons.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"delete_{item_type}_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")])
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

# --- ИИ-переводчик ---
def translate_word(word):
    translations = []
    translator = GoogleTranslator(source='en', target='ru')
    try:
        result = translator.translate(word)
        if result:
            translations.append(result.lower())
    except Exception as e:
        logger.warning(f"Google Translate error: {e}")
    try:
        url = f"https://api.datamuse.com/words?rel_syn={word}&max=5"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            for syn in response.json()[:5]:
                syn_word = syn.get('word', '')
                if syn_word and syn_word != word:
                    try:
                        syn_trans = translator.translate(syn_word)
                        if syn_trans and syn_trans.lower() not in translations:
                            translations.append(syn_trans.lower())
                    except:
                        pass
    except Exception as e:
        logger.warning(f"Datamuse API error: {e}")
    try:
        for prefix in ['to ', 'a ']:
            res = translator.translate(prefix + word)
            if res and res.lower() not in translations:
                translations.append(res.lower())
    except:
        pass
    return list(dict.fromkeys(translations))[:6]

# --- Основные функции ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я словарный бот с ИИ-переводчиком.\n\n"
        "✨ *Возможности:*\n"
        "• Комментарии к словам\n"
        "• Общий тест (слова + фразовые глаголы)\n"
        "• Mini App с красивым интерфейсом\n\n"
        "Выбери действие:",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Callback answer error: {e}")
    
    data = query.data
    
    if data == "add_word":
        await query.edit_message_text("✏️ Введи слово на английском:", parse_mode='Markdown')
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
    elif data == "test_mixed":
        await start_mixed_test(query, context)
        
    elif data == "list":
        await show_word_list(query, context)
    
    elif data == "delete_words":
        await show_delete_menu(query, context, "word", page=0)
    elif data.startswith("delete_word_page_"):
        page = int(data.replace("delete_word_page_", ""))
        await show_delete_menu(query, context, "word", page=page)
    elif data.startswith("delete_word_"):
        item_id = int(data.replace("delete_word_", ""))
        await delete_single_item(query, context, "word", item_id)
    
    elif data == "delete_phrasal":
        await show_delete_menu(query, context, "phrasal", page=0)
    elif data.startswith("delete_phrasal_page_"):
        page = int(data.replace("delete_phrasal_page_", ""))
        await show_delete_menu(query, context, "phrasal", page=page)
    elif data.startswith("delete_phrasal_"):
        item_id = int(data.replace("delete_phrasal_", ""))
        await delete_single_item(query, context, "phrasal", item_id)
        
    elif data == "delete_all":
        await query.edit_message_text(
            "⚠️ *Внимание!*\n\nУдалить ВСЕ слова и фразовые глаголы?\nЭто необратимо!",
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
        text = f"🏁 *Тест прерван!*\n\n✅ Правильно: {correct}\n❌ Ошибок: {total - correct}\n📊 Точность: {int(correct/total*100) if total > 0 else 0}%"
        await query.edit_message_text(text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
        
    elif data == "custom_translation":
        await query.edit_message_text("✏️ Введи свой перевод:", reply_markup=get_back_keyboard())
        context.user_data['awaiting'] = 'add_word_rus_manual'
        
    elif data.startswith("tr_"):
        translation = data[3:].replace('_', ' ')
        english = context.user_data.get('temp_eng', '')
        user_id = update.effective_user.id
        
        if not translation:
            await query.answer("❌ Ошибка выбора.")
            return
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO words (english, russian, comment, user_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (english, translation, None, user_id)
        )
        word_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        context.user_data['last_word_id'] = word_id
        context.user_data['last_english'] = english
        context.user_data['last_russian'] = translation
        
        await query.edit_message_text(
            f"✅ Пара *{english} — {translation}* сохранена!\n\nХочешь добавить комментарий?",
            reply_markup=get_comment_keyboard(word_id, "word"),
            parse_mode='Markdown'
        )
    
    elif data.startswith("add_comment_word_"):
        word_id = int(data.replace("add_comment_word_", ""))
        context.user_data['comment_word_id'] = word_id
        await query.edit_message_text(
            "📝 Введи комментарий к слову:",
            reply_markup=get_back_keyboard()
        )
        context.user_data['awaiting'] = 'add_comment'
        
    elif data == "skip_comment":
        word_id = context.user_data.get('last_word_id')
        english = context.user_data.get('last_english', '')
        russian = context.user_data.get('last_russian', '')
        context.user_data.clear()
        await query.edit_message_text(
            f"✅ Слово *{english} — {russian}* сохранено без комментария.",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        
    elif data == "help":
        await query.edit_message_text(
            "📚 *Справка:*\n\n"
            "➕ *Добавить слово* — ИИ предложит варианты\n"
            "📝 *Комментарий* — можно добавить после сохранения\n"
            "📘 *Фразовый глагол* — глагол с предлогами\n"
            "🔀 *Общий тест* — слова + фразовые глаголы\n"
            "🎮 *Mini App* — красивый интерфейс\n"
            "📋 *Список* — все слова\n"
            "❌ *Удалить* — выборочное удаление\n"
            "🗑 *Очистить* — удалить всё",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )

async def show_delete_menu(query, context, item_type, page=0):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    if item_type == "word":
        cur.execute("SELECT id, english, russian FROM words WHERE user_id = %s ORDER BY id", (user_id,))
        title = "🗑 *Выбери слово для удаления:*"
        empty = "📭 Словарь пуст."
    else:
        cur.execute("SELECT id, verb, prepositions, russian FROM phrasal_verbs WHERE user_id = %s ORDER BY id", (user_id,))
        title = "🗑 *Выбери фразовый глагол для удаления:*"
        empty = "📭 Нет фразовых глаголов."
    
    items = cur.fetchall()
    cur.close()
    conn.close()
    
    if not items:
        await query.edit_message_text(empty, reply_markup=get_back_keyboard())
        return
    
    total_pages = (len(items) - 1) // 5 + 1
    await query.edit_message_text(
        f"{title}\n\nСтраница {page + 1} из {total_pages}",
        reply_markup=get_delete_items_keyboard(items, page, item_type=item_type),
        parse_mode='Markdown'
    )

async def delete_single_item(query, context, item_type, item_id):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    if item_type == "word":
        cur.execute("DELETE FROM words WHERE id = %s AND user_id = %s", (item_id, user_id))
        msg = "✅ Слово удалено!"
    else:
        cur.execute("DELETE FROM phrasal_verbs WHERE id = %s AND user_id = %s", (item_id, user_id))
        msg = "✅ Фразовый глагол удалён!"
    conn.commit()
    cur.close()
    conn.close()
    await query.answer(msg)
    await show_delete_menu(query, context, item_type, page=0)

async def start_word_test(query, context, direction):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM words WHERE user_id = %s", (user_id,))
    words = cur.fetchall()
    cur.close()
    conn.close()
    
    if not words:
        await query.edit_message_text("❌ Словарь пуст.", reply_markup=get_back_keyboard())
        return
    
    random.shuffle(words)
    
    context.user_data['test_items'] = [{'type': 'word', 'data': w} for w in words]
    context.user_data['test_index'] = 0
    context.user_data['test_direction'] = direction
    context.user_data['test_correct'] = 0
    context.user_data['test_total'] = 0
    context.user_data['awaiting'] = 'mixed_test_answer'
    await ask_next_mixed_question(query, context)

async def start_phrasal_test(query, context):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s", (user_id,))
    verbs = cur.fetchall()
    cur.close()
    conn.close()
    
    if not verbs:
        await query.edit_message_text("❌ Нет фразовых глаголов.", reply_markup=get_back_keyboard())
        return
    
    test_items = []
    for verb in verbs:
        preps = [p.strip() for p in verb['prepositions'].split(',') if p.strip()]
        if not preps:
            continue
        chosen = random.choice(preps)
        translations = verb['russian'].split(';')
        meaning = ""
        for t in translations:
            if chosen in t:
                for sep in ['—', '-', ':']:
                    if sep in t:
                        parts = t.split(sep, 1)
                        if len(parts) > 1:
                            meaning = parts[1].strip()
                            break
                if meaning:
                    break
        if not meaning:
            meaning = f"({chosen})"
        test_items.append({'type': 'phrasal', 'data': {**verb, 'chosen_prep': chosen, 'meaning': meaning}})
    
    if not test_items:
        await query.edit_message_text("❌ Нет данных для теста.", reply_markup=get_back_keyboard())
        return
    
    random.shuffle(test_items)
    
    context.user_data['test_items'] = test_items
    context.user_data['test_index'] = 0
    context.user_data['test_correct'] = 0
    context.user_data['test_total'] = 0
    context.user_data['awaiting'] = 'mixed_test_answer'
    await ask_next_mixed_question(query, context)

async def start_mixed_test(query, context):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT * FROM words WHERE user_id = %s", (user_id,))
    words = cur.fetchall()
    
    cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s", (user_id,))
    verbs = cur.fetchall()
    cur.close()
    conn.close()
    
    if not words and not verbs:
        await query.edit_message_text("❌ Словарь пуст.", reply_markup=get_back_keyboard())
        return
    
    test_items = []
    
    for w in words:
        test_items.append({'type': 'word', 'data': w})
    
    for verb in verbs:
        preps = [p.strip() for p in verb['prepositions'].split(',') if p.strip()]
        if not preps:
            continue
        chosen = random.choice(preps)
        translations = verb['russian'].split(';')
        meaning = ""
        for t in translations:
            if chosen in t:
                for sep in ['—', '-', ':']:
                    if sep in t:
                        parts = t.split(sep, 1)
                        if len(parts) > 1:
                            meaning = parts[1].strip()
                            break
                if meaning:
                    break
        if not meaning:
            meaning = f"({chosen})"
        test_items.append({'type': 'phrasal', 'data': {**verb, 'chosen_prep': chosen, 'meaning': meaning}})
    
    random.shuffle(test_items)
    
    context.user_data['test_items'] = test_items
    context.user_data['test_index'] = 0
    context.user_data['test_correct'] = 0
    context.user_data['test_total'] = 0
    context.user_data['awaiting'] = 'mixed_test_answer'
    context.user_data['is_mixed'] = True
    await ask_next_mixed_question(query, context)

async def ask_next_mixed_question(query, context):
    items = context.user_data.get('test_items', [])
    index = context.user_data.get('test_index', 0)
    
    if index >= len(items):
        correct = context.user_data.get('test_correct', 0)
        total = context.user_data.get('test_total', 0)
        context.user_data.clear()
        text = f"🏁 *Тест завершён!*\n\n✅ Правильно: {correct}\n❌ Ошибок: {total - correct}\n📊 Точность: {int(correct/total*100) if total > 0 else 0}%"
        await query.edit_message_text(text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
        return
    
    item = items[index]
    context.user_data['current_item'] = item
    progress = f"📌 Вопрос {index + 1} из {len(items)}"
    
    if item['type'] == 'word':
        word = item['data']
        direction = context.user_data.get('test_direction', 'en_ru')
        
        if direction == "en_ru":
            question = f"🇬🇧 *{word['english']}*"
            context.user_data['correct_answer'] = word['russian'].lower().strip()
        else:
            question = f"🇷🇺 *{word['russian']}*"
            context.user_data['correct_answer'] = word['english'].lower().strip()
        
        await query.edit_message_text(
            f"{progress}\n\n{question}\n\n_Введи перевод:_",
            reply_markup=get_test_active_keyboard(),
            parse_mode='Markdown'
        )
    else:
        item_data = item['data']
        await query.edit_message_text(
            f"{progress}\n\n📖 *{item_data['verb']}* ______\n\nЗначение: _{item_data['meaning']}_\n\n_Введи предлог:_",
            reply_markup=get_test_active_keyboard(),
            parse_mode='Markdown'
        )

async def show_word_list(query, context):
    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT english, russian, comment FROM words WHERE user_id = %s ORDER BY id", (user_id,))
    words = cur.fetchall()
    cur.execute("SELECT verb, prepositions, russian FROM phrasal_verbs WHERE user_id = %s ORDER BY id", (user_id,))
    phrasals = cur.fetchall()
    cur.close()
    conn.close()
    
    text = "📋 *Твой словарь:*\n\n"
    if words:
        text += "📝 *Слова:*\n"
        for w in words[:15]:
            text += f"• {w[0]} — {w[1]}"
            if w[2]:
                text += f" _({w[2]})_"
            text += "\n"
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
    
    if awaiting == 'add_word_eng':
        word_only, comment = parse_word_with_comment(user_text)
        await update.message.reply_text("🔄 Перевожу через ИИ...")
        translations = translate_word(word_only)
        context.user_data['temp_eng'] = word_only
        context.user_data['temp_comment'] = comment
        context.user_data['translation_variants'] = translations
        
        if translations:
            comment_text = f"\n\n📝 Комментарий: _{comment}_" if comment else ""
            await update.message.reply_text(
                f"📖 Переводы для *{word_only}*:{comment_text}\n\nВыбери вариант:",
                reply_markup=get_translation_variants_keyboard(translations, word_only),
                parse_mode='Markdown'
            )
            context.user_data['awaiting'] = None
        else:
            await update.message.reply_text(
                "❌ Не удалось найти перевод.\n\n🇷🇺 Введи перевод вручную:",
                reply_markup=get_back_keyboard()
            )
            context.user_data['awaiting'] = 'add_word_rus_manual'
    
    elif awaiting == 'add_word_rus_manual':
        english = context.user_data.get('temp_eng', '')
        russian = user_text
        comment = context.user_data.get('temp_comment')
        user_id = update.effective_user.id
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO words (english, russian, comment, user_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (english, russian, comment, user_id)
        )
        word_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        context.user_data['last_word_id'] = word_id
        context.user_data['last_english'] = english
        context.user_data['last_russian'] = russian
        context.user_data['awaiting'] = None
        
        comment_text = f"\n📝 _({comment})_" if comment else ""
        await update.message.reply_text(
            f"✅ Пара *{english} — {russian}* сохранена!{comment_text}\n\nХочешь добавить комментарий?",
            reply_markup=get_comment_keyboard(word_id, "word"),
            parse_mode='Markdown'
        )
    
    elif awaiting == 'add_comment':
        word_id = context.user_data.get('comment_word_id')
        comment = user_text
        user_id = update.effective_user.id
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE words SET comment = %s WHERE id = %s AND user_id = %s", (comment, word_id, user_id))
        conn.commit()
        cur.close()
        conn.close()
        
        english = context.user_data.get('last_english', '')
        russian = context.user_data.get('last_russian', '')
        context.user_data.clear()
        
        await update.message.reply_text(
            f"✅ Комментарий к *{english} — {russian}* сохранён!\n📝 _({comment})_",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    
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
            await update.message.reply_text("❌ Неверный формат.", reply_markup=get_main_keyboard())
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
    
    elif awaiting == 'mixed_test_answer':
        user_answer = user_text.lower().strip()
        item = context.user_data.get('current_item', {})
        
        context.user_data['test_total'] = context.user_data.get('test_total', 0) + 1
        
        if item['type'] == 'word':
            correct = context.user_data.get('correct_answer', '').lower().strip()
            word = item['data']
            is_correct = user_answer in [v.strip().lower() for v in correct.split(',')] if ',' in correct else user_answer == correct
            
            if is_correct:
                context.user_data['test_correct'] = context.user_data.get('test_correct', 0) + 1
                response = f"✅ *Верно!* ({word['english']} — {word['russian']})"
            else:
                response = f"❌ *Неверно!*\nПравильно: *{word['english']} — {word['russian']}*"
        else:
            item_data = item['data']
            correct_prep = item_data.get('chosen_prep', '').lower()
            is_correct = user_answer == correct_prep
            
            if is_correct:
                context.user_data['test_correct'] = context.user_data.get('test_correct', 0) + 1
                response = f"✅ *Верно!* ({item_data['verb']} {correct_prep} — {item_data['meaning']})"
            else:
                response = f"❌ *Неверно!*\nПравильно: *{item_data['verb']} {correct_prep}* — {item_data['meaning']}"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        context.user_data['test_index'] = context.user_data.get('test_index', 0) + 1
        
        class FakeQuery:
            def __init__(self, msg): self.message = msg
            async def edit_message_text(self, text, **kw): await self.message.reply_text(text, **kw)
        await ask_next_mixed_question(FakeQuery(update.message), context)
    
    else:
        await update.message.reply_text("Используй кнопки меню.", reply_markup=get_main_keyboard())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text("❌ Действие отменено.", reply_markup=get_main_keyboard())

# --- ЗАПУСК ---
def main():
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise ValueError("Не найден TELEGRAM_BOT_TOKEN")
    
    init_db()
    
    threading.Thread(target=start_http_server, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("Бот с Mini App кнопкой запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()