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
    conn.commit()
    cur.close()
    conn.close()

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

# --- API ---
@app.route('/api/translate', methods=['GET'])
def api_translate():
    word = request.args.get('word', '')
    if not word:
        return jsonify({'error': 'No word'}), 400
    return jsonify({'translations': translate_word(word)})

@app.route('/api/save_word', methods=['POST'])
def api_save_word():
    data = request.get_json()
    english = data.get('english', '')
    russian = data.get('russian', '')
    comment = data.get('comment', '')
    user_id = data.get('user_id', 0)
    
    if not english or not russian or not user_id:
        return jsonify({'success': False}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO words (english, russian, comment, user_id) VALUES (%s, %s, %s, %s)",
        (english, russian, comment, user_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/save_phrasal', methods=['POST'])
def api_save_phrasal():
    data = request.get_json()
    verb = data.get('verb', '')
    prepositions = data.get('prepositions', '')
    russian = data.get('russian', '')
    user_id = data.get('user_id', 0)
    
    if not verb or not prepositions or not russian or not user_id:
        return jsonify({'success': False}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO phrasal_verbs (verb, prepositions, russian, user_id) VALUES (%s, %s, %s, %s)",
        (verb, prepositions, russian, user_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/get_all_words', methods=['GET'])
def api_get_all_words():
    user_id = request.args.get('user_id', 0)
    if not user_id or user_id == '0':
        return jsonify({'words': [], 'phrasal_verbs': []})
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, english, russian, comment FROM words WHERE user_id = %s ORDER BY id DESC", (user_id,))
    words = cur.fetchall()
    cur.execute("SELECT id, verb, prepositions, russian FROM phrasal_verbs WHERE user_id = %s ORDER BY id DESC", (user_id,))
    phrasal = cur.fetchall()
    cur.close()
    conn.close()
    
    return jsonify({'words': words, 'phrasal_verbs': phrasal})

@app.route('/api/delete_word', methods=['POST'])
def api_delete_word():
    data = request.get_json()
    word_id = data.get('word_id', 0)
    user_id = data.get('user_id', 0)
    
    if not word_id or not user_id:
        return jsonify({'success': False}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM words WHERE id = %s AND user_id = %s", (word_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/delete_phrasal', methods=['POST'])
def api_delete_phrasal():
    data = request.get_json()
    verb_id = data.get('verb_id', 0)
    user_id = data.get('user_id', 0)
    
    if not verb_id or not user_id:
        return jsonify({'success': False}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM phrasal_verbs WHERE id = %s AND user_id = %s", (verb_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/update_comment', methods=['POST'])
def api_update_comment():
    data = request.get_json()
    word_id = data.get('word_id', 0)
    comment = data.get('comment', '')
    user_id = data.get('user_id', 0)
    
    if not word_id or not user_id:
        return jsonify({'success': False}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE words SET comment = %s WHERE id = %s AND user_id = %s", (comment, word_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/get_test_data', methods=['GET'])
def api_get_test_data():
    user_id = request.args.get('user_id', 0)
    test_type = request.args.get('type', 'mixed')
    
    if not user_id:
        return jsonify({'items': []})
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    items = []
    
    if test_type in ['en_ru', 'ru_en', 'mixed']:
        cur.execute("SELECT * FROM words WHERE user_id = %s", (user_id,))
        words = cur.fetchall()
        for w in words:
            items.append({
                'type': 'word',
                'id': w['id'],
                'english': w['english'],
                'russian': w['russian'],
                'comment': w['comment']
            })
    
    if test_type in ['phrasal', 'mixed']:
        cur.execute("SELECT * FROM phrasal_verbs WHERE user_id = %s", (user_id,))
        verbs = cur.fetchall()
        for v in verbs:
            items.append({
                'type': 'phrasal',
                'id': v['id'],
                'verb': v['verb'],
                'prepositions': v['prepositions'],
                'russian': v['russian']
            })
    
    cur.close()
    conn.close()
    
    random.shuffle(items)
    return jsonify({'items': items})

@app.route('/health')
def health():
    return "OK", 200

@app.route('/')
def index():
    return "English Bot API is running!", 200

# --- Telegram бот ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(
        "🎮 Открыть English Bot", 
        web_app=WebAppInfo(url="https://revabyola.github.io/eng-bot-app/")
    )]]
    await update.message.reply_text(
        "👋 Привет! Нажми кнопку ниже, чтобы открыть приложение:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Flask запущен на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("Токен не найден!")
        return
    
    init_db()
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Запускаем бота в главном потоке
    app_telegram = Application.builder().token(TOKEN).build()
    app_telegram.add_handler(CommandHandler("start", start))
    
    logger.info("Бот запущен!")
    app_telegram.run_polling()

if __name__ == "__main__":
    main()