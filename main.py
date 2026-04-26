import os
import logging
import random
import requests
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes
import psycopg2
from psycopg2.extras import RealDictCursor
from deep_translator import GoogleTranslator

# --- Настройка ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get('DATABASE_URL')
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, user_id)
        );
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id SERIAL PRIMARY KEY,
            english TEXT NOT NULL,
            russian TEXT NOT NULL,
            comment TEXT,
            user_id BIGINT NOT NULL
        );
    """)
    
    try:
        cur.execute("ALTER TABLE words ADD COLUMN IF NOT EXISTS folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")
    except:
        pass
    
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
        cur.execute("ALTER TABLE phrasal_verbs ADD COLUMN IF NOT EXISTS folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")
    except:
        pass
    
    conn.commit()
    cur.close()
    conn.close()
    logger.info("База данных инициализирована")

def translate_word(word):
    translations = []
    translator = GoogleTranslator(source='en', target='ru')
    try:
        result = translator.translate(word)
        if result:
            translations.append(result.lower())
    except:
        pass
    return translations[:6] if translations else []

# --- API папки ---
@app.route('/api/folders', methods=['GET'])
def get_folders():
    user_id = request.args.get('user_id', 0)
    if not user_id:
        return jsonify({'folders': []})
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name FROM folders WHERE user_id = %s ORDER BY name", (user_id,))
    folders = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({'folders': folders})

@app.route('/api/folders', methods=['POST'])
def create_folder():
    data = request.get_json()
    name = data.get('name', '').strip()
    user_id = data.get('user_id', 0)
    if not name or not user_id:
        return jsonify({'success': False}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO folders (name, user_id) VALUES (%s, %s) ON CONFLICT (name, user_id) DO NOTHING RETURNING id", (name, user_id))
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'id': result[0] if result else None})

@app.route('/api/folders/<int:folder_id>', methods=['DELETE'])
def delete_folder(folder_id):
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM folders WHERE id = %s AND user_id = %s", (folder_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# --- API перевод ---
@app.route('/api/translate', methods=['GET'])
def api_translate():
    word = request.args.get('word', '')
    if not word:
        return jsonify({'error': 'No word'}), 400
    return jsonify({'translations': translate_word(word)})

# --- API слова ---
@app.route('/api/words', methods=['GET'])
def get_words():
    user_id = request.args.get('user_id', 0)
    folder_id = request.args.get('folder_id', '')
    if not user_id:
        return jsonify({'words': [], 'phrasal_verbs': []})
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if folder_id and folder_id != 'null' and folder_id != 'undefined':
        cur.execute("SELECT w.id, w.english, w.russian, w.comment, w.folder_id, f.name as folder_name FROM words w LEFT JOIN folders f ON w.folder_id = f.id WHERE w.user_id = %s AND w.folder_id = %s ORDER BY w.id DESC", (user_id, folder_id))
        words = cur.fetchall()
        cur.execute("SELECT p.id, p.verb, p.prepositions, p.russian, p.folder_id, f.name as folder_name FROM phrasal_verbs p LEFT JOIN folders f ON p.folder_id = f.id WHERE p.user_id = %s AND p.folder_id = %s ORDER BY p.id DESC", (user_id, folder_id))
        phrasal = cur.fetchall()
    else:
        cur.execute("SELECT w.id, w.english, w.russian, w.comment, w.folder_id, f.name as folder_name FROM words w LEFT JOIN folders f ON w.folder_id = f.id WHERE w.user_id = %s ORDER BY w.id DESC", (user_id,))
        words = cur.fetchall()
        cur.execute("SELECT p.id, p.verb, p.prepositions, p.russian, p.folder_id, f.name as folder_name FROM phrasal_verbs p LEFT JOIN folders f ON p.folder_id = f.id WHERE p.user_id = %s ORDER BY p.id DESC", (user_id,))
        phrasal = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({'words': words, 'phrasal_verbs': phrasal})

@app.route('/api/words', methods=['POST'])
def save_word():
    data = request.get_json()
    english = data.get('english', '')
    russian = data.get('russian', '')
    comment = data.get('comment', '')
    folder_id = data.get('folder_id')
    user_id = data.get('user_id', 0)
    if not english or not russian or not user_id:
        return jsonify({'success': False}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO words (english, russian, comment, folder_id, user_id) VALUES (%s, %s, %s, %s, %s) RETURNING id", (english, russian, comment, folder_id, user_id))
    word_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'word_id': word_id})

@app.route('/api/words/<int:word_id>', methods=['DELETE'])
def delete_word(word_id):
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM words WHERE id = %s AND user_id = %s", (word_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/words/<int:word_id>/move', methods=['PUT'])
def move_word(word_id):
    data = request.get_json()
    folder_id = data.get('folder_id')
    user_id = data.get('user_id', 0)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE words SET folder_id = %s WHERE id = %s AND user_id = %s", (folder_id, word_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/words/<int:word_id>/comment', methods=['PUT'])
def update_comment(word_id):
    data = request.get_json()
    comment = data.get('comment', '')
    user_id = data.get('user_id', 0)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE words SET comment = %s WHERE id = %s AND user_id = %s", (comment, word_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# --- API фразовые глаголы ---
@app.route('/api/phrasal', methods=['POST'])
def save_phrasal():
    data = request.get_json()
    verb = data.get('verb', '')
    prepositions = data.get('prepositions', '')
    russian = data.get('russian', '')
    folder_id = data.get('folder_id')
    user_id = data.get('user_id', 0)
    if not verb or not prepositions or not russian or not user_id:
        return jsonify({'success': False}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO phrasal_verbs (verb, prepositions, russian, folder_id, user_id) VALUES (%s, %s, %s, %s, %s) RETURNING id", (verb, prepositions, russian, folder_id, user_id))
    verb_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'verb_id': verb_id})

@app.route('/api/phrasal/<int:verb_id>', methods=['DELETE'])
def delete_phrasal(verb_id):
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM phrasal_verbs WHERE id = %s AND user_id = %s", (verb_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/phrasal/<int:verb_id>/move', methods=['PUT'])
def move_phrasal(verb_id):
    data = request.get_json()
    folder_id = data.get('folder_id')
    user_id = data.get('user_id', 0)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE phrasal_verbs SET folder_id = %s WHERE id = %s AND user_id = %s", (folder_id, verb_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# --- API тест ---
@app.route('/api/test', methods=['GET'])
def get_test_data():
    user_id = request.args.get('user_id', 0)
    test_type = request.args.get('type', 'mixed')
    folder_id = request.args.get('folder_id', '')
    if not user_id:
        return jsonify({'items': []})
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    items = []
    if test_type in ['en_ru', 'ru_en', 'mixed']:
        if folder_id and folder_id != 'null' and folder_id != 'undefined':
            cur.execute("SELECT * FROM words WHERE user_id = %s AND folder_id = %s", (user_id, folder_id))
        else:
            cur.execute("SELECT * FROM words WHERE user_id = %s", (user_id,))
        words = cur.fetchall()
        for w in words:
            items.append({'type': 'word', 'id': w['id'], 'english': w['english'], 'russian': w['russian'], 'comment': w['comment'], 'folder_id': w['folder_id']})
    if test_type in ['phrasal', 'mixed']:
        if folder_id and folder_id != 'null' and folder_id != 'undefined':
            cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s AND folder_id = %s", (user_id, folder_id))
        else:
            cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s", (user_id,))
        verbs = cur.fetchall()
        for v in verbs:
            items.append({'type': 'phrasal', 'id': v['id'], 'verb': v['verb'], 'prepositions': v['prepositions'], 'russian': v['russian'], 'folder_id': v['folder_id']})
    cur.close()
    conn.close()
    random.shuffle(items)
    return jsonify({'items': items})

@app.route('/health')
def health():
    return "OK", 200

# --- Telegram бот ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(
        "🎮 Открыть English Bot", 
        web_app=WebAppInfo(url="https://revabyola.github.io/eng-bot-app/")
    )]]
    await update.message.reply_text(
        "👋 Привет! Нажми кнопку ниже, чтобы открыть приложение для изучения слов:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def main():
    if not TOKEN:
        logger.error("Токен не найден!")
        return
    
    init_db()
    
    # Запускаем Flask в отдельном потоке
    def run_flask():
        port = int(os.environ.get('PORT', 10000))
        logger.info(f"Flask запущен на порту {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Запускаем бота в главном потоке
    app_telegram = Application.builder().token(TOKEN).build()
    app_telegram.add_handler(CommandHandler("start", start))
    
    logger.info("Бот запущен!")
    app_telegram.run_polling()

if __name__ == "__main__":
    main()