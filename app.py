from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
from dotenv import load_dotenv
from gtts import gTTS
from difflib import SequenceMatcher
from groq import Groq
import uuid
import re
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, date, timedelta
import random
import time
import hashlib
import glob
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional

# ================= SETUP =================
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")

logger = logging.getLogger(__name__)

# ================= DIFFICULTY-BASED XP =================
DIFFICULTY_XP = {
    "easy": 1,
    "medium": 2,
    "hard": 5
}

# ================= VALID CLASS / DIVISION OPTIONS =================
VALID_CLASSES    = [str(i) for i in range(1, 11)]          # "1" … "10"
VALID_DIVISIONS  = ["A", "B", "C", "D", "E"]

# ================= USER ID GENERATION =================
def generate_user_id():
    """Generate a unique User ID in the format GSS-XXXXX (e.g. GSS-A3K7P)."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No I, O, 0, 1 to avoid confusion
    suffix = ''.join(random.choices(chars, k=5))
    return f"GSS-{suffix}"

def generate_unique_user_id(conn):
    """Generate a User ID guaranteed to be unique in the DB."""
    for _ in range(20):  # Max 20 attempts
        uid = generate_user_id()
        existing = conn.execute(
            'SELECT id FROM users WHERE user_id_code = ?', (uid,)
        ).fetchone()
        if not existing:
            return uid
    # Fallback: use timestamp-based ID
    return f"GSS-{int(time.time() * 1000) % 100000:05d}"

# ================= BADGE DEFINITIONS =================
ALL_BADGES = [
    # XP Milestone Badges
    {"id": "first_xp",       "name": "First Step",        "icon": "🌱", "description": "Earn your first XP",              "category": "milestone"},
    {"id": "xp_25",          "name": "Getting Started",   "icon": "🔥", "description": "Earn 25 XP total",                "category": "milestone"},
    {"id": "xp_50",          "name": "On a Roll",         "icon": "🚀", "description": "Earn 50 XP total",                "category": "milestone"},
    {"id": "xp_100",         "name": "Century",           "icon": "💯", "description": "Earn 100 XP total",               "category": "milestone"},
    {"id": "xp_250",         "name": "XP Warrior",        "icon": "⚔️",  "description": "Earn 250 XP total",               "category": "milestone"},
    {"id": "xp_500",         "name": "XP Legend",         "icon": "👑",  "description": "Earn 500 XP total",               "category": "milestone"},

    # Conversation Badges
    {"id": "conv_10",        "name": "Chatterbox",        "icon": "💬", "description": "Earn 10 XP in Conversation",      "category": "conversation"},
    {"id": "conv_50",        "name": "Conversationalist", "icon": "🗣️",  "description": "Earn 50 XP in Conversation",      "category": "conversation"},

    # Roleplay Badges
    {"id": "role_10",        "name": "Actor",             "icon": "🎭", "description": "Earn 10 XP in Roleplay",          "category": "roleplay"},
    {"id": "role_50",        "name": "Stage Star",        "icon": "🌟", "description": "Earn 50 XP in Roleplay",          "category": "roleplay"},

    # Repeat Badges
    {"id": "repeat_easy",    "name": "Echo",              "icon": "🔁", "description": "Complete an Easy Repeat stage",   "category": "repeat"},
    {"id": "repeat_medium",  "name": "Parrot",            "icon": "🦜", "description": "Complete a Medium Repeat stage",  "category": "repeat"},
    {"id": "repeat_hard",    "name": "Mimic Master",      "icon": "🎙️",  "description": "Complete a Hard Repeat stage",    "category": "repeat"},
    {"id": "repeat_50",      "name": "Repeat Champion",   "icon": "🏅", "description": "Earn 50 XP in Repeat",           "category": "repeat"},

    # Spell Bee Badges
    {"id": "spell_easy",     "name": "Speller",           "icon": "🐝", "description": "Complete an Easy Spell stage",   "category": "spellbee"},
    {"id": "spell_medium",   "name": "Word Wizard",       "icon": "🧙", "description": "Complete a Medium Spell stage",  "category": "spellbee"},
    {"id": "spell_hard",     "name": "Spelling Champion", "icon": "🏆", "description": "Complete a Hard Spell stage",    "category": "spellbee"},
    {"id": "spell_50",       "name": "Spell Bee King",    "icon": "👑", "description": "Earn 50 XP in Spell Bee",        "category": "spellbee"},

    # Meanings Badges
    {"id": "meanings_1",     "name": "Curious Mind",      "icon": "🤔", "description": "Look up your first word",        "category": "meanings"},
    {"id": "meanings_50",    "name": "Wordsmith",         "icon": "📖", "description": "Earn 50 XP in Word Meanings",    "category": "meanings"},

    # Star Badges
    {"id": "stars_5",        "name": "Star Collector",    "icon": "⭐", "description": "Earn 5 stars",                   "category": "stars"},
    {"id": "stars_15",       "name": "Star Gazer",        "icon": "🌠", "description": "Earn 15 stars",                  "category": "stars"},
    {"id": "stars_30",       "name": "Superstar",         "icon": "💫", "description": "Earn 30 stars",                  "category": "stars"},

    # Perfect Score Badges
    {"id": "perfect_repeat", "name": "Flawless Speaker",  "icon": "🎯", "description": "Score 100% in a Repeat session", "category": "perfect"},
    {"id": "perfect_spell",  "name": "Perfect Speller",   "icon": "✨", "description": "Spell all words correctly",      "category": "perfect"},

    # Streak Badges
    {"id": "streak_3",       "name": "3-Day Streak",      "icon": "🔥", "description": "Practice 3 days in a row",       "category": "streak"},
    {"id": "streak_7",       "name": "Week Warrior",      "icon": "📅", "description": "Practice 7 days in a row",       "category": "streak"},

    # All-Rounder Badge
    {"id": "all_modes",      "name": "All-Rounder",       "icon": "🌈", "description": "Earn XP in all 5 modes",         "category": "special"},
]

BADGE_MAP = {b["id"]: b for b in ALL_BADGES}

def check_earned_badges(roll_no, progress_data, mode=None, difficulty=None, score=None, stars_earned=None):
    conn = get_db_connection()
    existing = conn.execute(
        'SELECT badge_id FROM student_badges WHERE roll_no = ?', (roll_no,)
    ).fetchall()
    conn.close()
    already_earned = {row['badge_id'] for row in existing}

    newly_earned = []

    def award(badge_id):
        if badge_id not in already_earned:
            newly_earned.append(badge_id)
            already_earned.add(badge_id)

    total_xp   = progress_data.get('xp', 0)
    conv_xp    = progress_data.get('conversation_xp', 0)
    role_xp    = progress_data.get('roleplay_xp', 0)
    repeat_xp  = progress_data.get('repeat_xp', 0)
    spell_xp   = progress_data.get('spellbee_xp', 0)
    mean_xp    = progress_data.get('meanings_xp', 0)
    total_stars= progress_data.get('total_stars', 0)
    streak     = progress_data.get('streak', 0)

    if total_xp >= 1:   award("first_xp")
    if total_xp >= 25:  award("xp_25")
    if total_xp >= 50:  award("xp_50")
    if total_xp >= 100: award("xp_100")
    if total_xp >= 250: award("xp_250")
    if total_xp >= 500: award("xp_500")

    if conv_xp >= 10: award("conv_10")
    if conv_xp >= 50: award("conv_50")

    if role_xp >= 10: award("role_10")
    if role_xp >= 50: award("role_50")

    if mode == 'repeat':
        if difficulty == 'easy':   award("repeat_easy")
        if difficulty == 'medium': award("repeat_medium")
        if difficulty == 'hard':   award("repeat_hard")
        if score == 100:           award("perfect_repeat")
    if repeat_xp >= 50: award("repeat_50")

    if mode == 'spellbee':
        if difficulty == 'easy':   award("spell_easy")
        if difficulty == 'medium': award("spell_medium")
        if difficulty == 'hard':   award("spell_hard")
        if score == 100:           award("perfect_spell")
    if spell_xp >= 50: award("spell_50")

    if mean_xp >= 10: award("meanings_1")
    if mean_xp >= 50: award("meanings_50")

    if total_stars >= 5:  award("stars_5")
    if total_stars >= 15: award("stars_15")
    if total_stars >= 30: award("stars_30")

    if streak >= 3: award("streak_3")
    if streak >= 7: award("streak_7")

    if conv_xp > 0 and role_xp > 0 and repeat_xp > 0 and spell_xp > 0 and mean_xp > 0:
        award("all_modes")

    return newly_earned

_ROLEPLAY_ROLE_PROMPTS = {
    "teacher": (
        "You are a warm, enthusiastic school teacher conducting a lesson or check-in with a student aged 6-15.\n"
        "QUESTION STYLE: Ask academic, subject-based, or learning-focused questions.\n"
        "  ✓ Quiz them on subjects: 'What is the capital of France?'\n"
        "  ✓ Check homework: 'Did you finish your math problems?'\n"
        "  ✓ Build curiosity: 'What do you think causes rain?'\n"
        "  ✗ NEVER ask casual friend-style questions like 'what game do you play?'\n"
        "TONE: Patient, encouraging, educational. Praise effort. Gently correct mistakes.\n"
        "If they ask you something, answer clearly like a real teacher explaining to a child."
    ),
    "friend": (
        "You are a cheerful, playful classmate the same age as the student (6-15 years old).\n"
        "QUESTION STYLE: Ask casual, fun, relatable questions a child would ask a friend.\n"
        "  ✓ Hangout talk: 'Want to play cricket after school?'\n"
        "  ✓ Opinions: 'What's your favourite cartoon?'\n"
        "  ✓ Shared experiences: 'Did you see that match yesterday?'\n"
        "  ✗ NEVER ask formal, academic, or interview-style questions.\n"
        "TONE: Energetic, fun, casual. Use friendly language. React with excitement.\n"
        "If they ask you something, answer like a kid friend would — naturally and personally."
    ),
    "interviewer": (
        "You are a polite, professional HR interviewer conducting a formal job interview.\n"
        "QUESTION STYLE: Ask structured, professional interview questions.\n"
        "  ✓ Opening: 'Please introduce yourself.'\n"
        "  ✓ Competency: 'Describe a time you handled pressure well.'\n"
        "  ✓ Motivation: 'Why do you want this role?'\n"
        "  ✗ NEVER ask casual or school-style questions.\n"
        "TONE: Formal, professional, encouraging but evaluative. Use 'please', 'could you', 'thank you'.\n"
        "Acknowledge good answers with brief professional praise: 'That's a strong answer.'\n"
        "If they ask you something, respond professionally and briefly."
    ),
    "viva": (
        "You are a fair, thorough academic examiner conducting an oral viva examination.\n"
        "QUESTION STYLE: Ask probing, analytical, project-focused questions.\n"
        "  ✓ Understanding: 'Can you explain your methodology?'\n"
        "  ✓ Critical thinking: 'What are the limitations of your approach?'\n"
        "  ✓ Application: 'How would this work in a real-world scenario?'\n"
        "  ✗ NEVER ask casual or off-topic questions.\n"
        "TONE: Academic, precise, encouraging. Probe deeper when answers are vague.\n"
        "Use phrases like: 'Interesting point — can you elaborate?', 'Good. Now tell me...'\n"
        "If they ask you something, answer it helpfully as an examiner guiding them."
    ),
}

def get_session_recent_sentences():
    return session.get('recent_sentences', [])

def set_session_recent_sentences(lst):
    session['recent_sentences'] = lst

def get_session_recent_words():
    return session.get('recent_words', [])

def set_session_recent_words(lst):
    session['recent_words'] = lst

def get_session_recent_roleplay(rtype):
    return session.get(f'recent_roleplay_{rtype}', [])

def set_session_recent_roleplay(rtype, lst):
    session[f'recent_roleplay_{rtype}'] = lst

MAX_HISTORY = 20

ROLEPLAY_QUESTIONS = {
    "teacher": [
        "What did you learn in school today?",
        "Can you tell me what photosynthesis means?",
        "What is the capital city of India?",
        "Can you solve: what is 12 multiplied by 8?",
        "What is the largest planet in our solar system?",
        "Can you name three types of triangles?",
        "What causes day and night on Earth?",
        "Who wrote the national anthem of India?",
    ],
    "friend": [
        "Want to play cricket after school?",
        "What's your favorite cartoon?",
        "Did you bring lunch today?",
        "Which subject do you like most?",
        "What game do you play on weekends?",
        "Have you watched any good movies lately?",
        "What's your favorite ice cream flavor?",
        "Do you have any pets at home?",
    ],
    "interviewer": [
        "Please introduce yourself.",
        "What are your strengths and weaknesses?",
        "Where do you see yourself in five years?",
        "Describe a challenge you overcame.",
        "Why should we select you for this role?",
        "Tell me about a time you worked in a team.",
        "What motivates you to do your best work?",
        "Do you have any questions for us?",
    ],
    "viva": [
        "Can you explain your project methodology?",
        "What are the limitations of your approach?",
        "How does your solution work in practice?",
        "What references did you use for your research?",
        "Can you walk me through your key findings?",
        "How would you improve your project if given more time?",
        "What alternative approaches did you consider?",
        "How does your work contribute to the field?",
    ],
}

def get_roleplay_question(roleplay_type):
    questions = ROLEPLAY_QUESTIONS.get(roleplay_type, ROLEPLAY_QUESTIONS["friend"])
    recent = get_session_recent_roleplay(roleplay_type)
    available_questions = [q for q in questions if q not in recent]
    if not available_questions:
        recent = recent[-5:] if len(recent) > 5 else []
        set_session_recent_roleplay(roleplay_type, recent)
        available_questions = [q for q in questions if q not in recent]
    if not available_questions:
        available_questions = questions
    selected_question = random.choice(available_questions)
    recent.append(selected_question)
    if len(recent) > 10:
        recent = recent[-10:]
    set_session_recent_roleplay(roleplay_type, recent)
    return selected_question

# ================= TTS CACHE =================
CACHE_DIR = "static/audio_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def get_cache_filename(text, slow=False):
    text_hash = hashlib.md5(text.encode()).hexdigest()
    speed = "slow" if slow else "normal"
    return f"{text_hash}_{speed}.mp3"

def get_cached_audio(text, slow=False):
    filename = get_cache_filename(text, slow)
    filepath = os.path.join(CACHE_DIR, filename)
    if os.path.exists(filepath):
        return "/" + filepath
    return None

def save_to_cache(text, filepath, slow=False):
    filename = get_cache_filename(text, slow)
    cache_path = os.path.join(CACHE_DIR, filename)
    try:
        import shutil
        shutil.copy(filepath, cache_path)
    except:
        pass

def cleanup_old_audio():
    audio_dir = "static/audio"
    if not os.path.exists(audio_dir):
        return
    now = time.time()
    cutoff = 3600
    for f in glob.glob(os.path.join(audio_dir, "*.mp3")):
        try:
            if now - os.path.getmtime(f) > cutoff:
                os.remove(f)
        except:
            pass

def schedule_audio_cleanup():
    cleanup_old_audio()
    t = threading.Timer(1800, schedule_audio_cleanup)
    t.daemon = True
    t.start()

schedule_audio_cleanup()

# ================= FEATURE UNLOCK SYSTEM =================
FEATURE_SEQUENCE = ["conversation", "roleplay", "repeat", "spellbee", "meanings"]
XP_PER_UNLOCK = 50

def get_unlocked_features(progress_data):
    unlocked = ["conversation"]
    if progress_data['conversation_xp'] >= XP_PER_UNLOCK:
        unlocked.append("roleplay")
    if progress_data['roleplay_xp'] >= XP_PER_UNLOCK:
        unlocked.append("repeat")
    if progress_data['repeat_xp'] >= XP_PER_UNLOCK:
        unlocked.append("spellbee")
    if progress_data['spellbee_xp'] >= XP_PER_UNLOCK:
        unlocked.append("meanings")
    return unlocked

def get_next_unlock(progress_data):
    if progress_data['conversation_xp'] < XP_PER_UNLOCK:
        return {'feature': 'roleplay', 'current_mode': 'conversation',
                'xp_needed': XP_PER_UNLOCK - progress_data['conversation_xp'],
                'current_xp': progress_data['conversation_xp']}
    elif progress_data['roleplay_xp'] < XP_PER_UNLOCK:
        return {'feature': 'repeat', 'current_mode': 'roleplay',
                'xp_needed': XP_PER_UNLOCK - progress_data['roleplay_xp'],
                'current_xp': progress_data['roleplay_xp']}
    elif progress_data['repeat_xp'] < XP_PER_UNLOCK:
        return {'feature': 'spellbee', 'current_mode': 'repeat',
                'xp_needed': XP_PER_UNLOCK - progress_data['repeat_xp'],
                'current_xp': progress_data['repeat_xp']}
    elif progress_data['spellbee_xp'] < XP_PER_UNLOCK:
        return {'feature': 'meanings', 'current_mode': 'spellbee',
                'xp_needed': XP_PER_UNLOCK - progress_data['spellbee_xp'],
                'current_xp': progress_data['spellbee_xp']}
    return None

# ================= STREAK CALCULATION =================
def calculate_streak(roll_no):
    conn = get_db_connection()
    rows = conn.execute(
        '''SELECT DISTINCT date(date) as day FROM activity_log
           WHERE roll_no = ? ORDER BY day DESC''',
        (roll_no,)
    ).fetchall()
    conn.close()

    if not rows:
        return 0

    streak = 0
    today = date.today()
    check_date = today

    for row in rows:
        day = date.fromisoformat(row['day'])
        if day == check_date or day == check_date - timedelta(days=1):
            streak += 1
            check_date = day - timedelta(days=1)
        elif day < check_date - timedelta(days=1):
            break

    most_recent = date.fromisoformat(rows[0]['day'])
    if most_recent < today - timedelta(days=1):
        return 0

    return streak

# ================= DAILY WORD/SENTENCE =================
DAILY_WORDS = [
    "brave", "honest", "grateful", "patient", "creative", "curious", "generous",
    "diligent", "humble", "cheerful", "determined", "confident", "respectful",
    "responsible", "thoughtful", "ambitious", "compassionate", "enthusiastic"
]

DAILY_SENTENCES = [
    "Kindness is the language everyone understands.",
    "Every day is a new chance to learn something.",
    "Small steps every day lead to big achievements.",
    "Reading opens the door to a world of knowledge.",
    "Practice makes progress, not just perfect.",
    "Helping others makes you feel happy inside.",
    "Believe in yourself and your abilities.",
    "Hard work and patience bring great results.",
    "A smile can brighten someone's whole day.",
    "Always be curious and ask questions."
]

def get_daily_challenge():
    today_num = date.today().toordinal()
    word = DAILY_WORDS[today_num % len(DAILY_WORDS)]
    sentence = DAILY_SENTENCES[today_num % len(DAILY_SENTENCES)]
    return {"word": word, "sentence": sentence}

def has_completed_daily(roll_no):
    conn = get_db_connection()
    row = conn.execute(
        '''SELECT id FROM activity_log WHERE roll_no = ? AND mode = 'daily'
           AND date(date) = date('now')''',
        (roll_no,)
    ).fetchone()
    conn.close()
    return row is not None

# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect('students.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK(role IN ('student', 'teacher')),
            name TEXT NOT NULL,
            user_id_code TEXT UNIQUE,
            roll_no TEXT,
            class_name TEXT,
            division TEXT,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            reset_token TEXT,
            reset_token_expiry TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(roll_no, class_name, division)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS student_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS student_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_no TEXT NOT NULL,
            class_name TEXT,
            division TEXT,
            xp INTEGER DEFAULT 0,
            conversation_xp INTEGER DEFAULT 0,
            roleplay_xp INTEGER DEFAULT 0,
            repeat_xp INTEGER DEFAULT 0,
            spellbee_xp INTEGER DEFAULT 0,
            meanings_xp INTEGER DEFAULT 0,
            total_stars INTEGER DEFAULT 0,
            total_sessions INTEGER DEFAULT 0,
            average_accuracy REAL DEFAULT 0,
            streak INTEGER DEFAULT 0,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(roll_no, class_name, division)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_no TEXT NOT NULL,
            class_name TEXT,
            division TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            mode TEXT NOT NULL,
            score REAL,
            xp_earned INTEGER,
            stars_earned INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS student_badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_no TEXT NOT NULL,
            class_name TEXT,
            division TEXT,
            badge_id TEXT NOT NULL,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(roll_no, class_name, division, badge_id)
        )
    ''')

    # ---- migrate existing DB columns ----
    def col_exists(table, col):
        rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in rows)

    for tbl in ['users', 'student_progress', 'activity_log', 'student_badges']:
        if not col_exists(tbl, 'class_name'):
            c.execute(f'ALTER TABLE {tbl} ADD COLUMN class_name TEXT')
        if not col_exists(tbl, 'division'):
            c.execute(f'ALTER TABLE {tbl} ADD COLUMN division TEXT')

    # Add user_id_code column if missing (migration for existing DBs)
    if not col_exists('users', 'user_id_code'):
        c.execute('ALTER TABLE users ADD COLUMN user_id_code TEXT')
        # Generate user_id_codes for existing students who don't have one
        existing_students = c.execute(
            "SELECT id, roll_no FROM users WHERE role='student' AND (user_id_code IS NULL OR user_id_code='')"
        ).fetchall()
        used_ids = set()
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for student in existing_students:
            for _ in range(50):
                suffix = ''.join(random.choices(chars, k=5))
                uid = f"GSS-{suffix}"
                if uid not in used_ids:
                    used_ids.add(uid)
                    c.execute('UPDATE users SET user_id_code=? WHERE id=?', (uid, student['id']))
                    break
        print(f"Generated User IDs for {len(existing_students)} existing students.")

    if not col_exists('student_progress', 'conversation_xp'):
        c.execute('ALTER TABLE student_progress ADD COLUMN conversation_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN roleplay_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN repeat_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN spellbee_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN meanings_xp INTEGER DEFAULT 0')
    if not col_exists('student_progress', 'streak'):
        c.execute('ALTER TABLE student_progress ADD COLUMN streak INTEGER DEFAULT 0')
    if not col_exists('users', 'reset_token'):
        c.execute('ALTER TABLE users ADD COLUMN reset_token TEXT')
        c.execute('ALTER TABLE users ADD COLUMN reset_token_expiry TIMESTAMP')

    # ---- Fix stale UNIQUE(roll_no) constraint ----
    try:
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if row:
            table_sql = row[0]
            has_old_unique = (
                'UNIQUE(roll_no)' in table_sql or
                'unique(roll_no)' in table_sql.lower()
            ) and 'roll_no, class_name, division' not in table_sql
            if has_old_unique:
                print("Detected stale UNIQUE(roll_no) constraint — rebuilding users table...")
                c.execute('''
                    CREATE TABLE IF NOT EXISTS users_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        role TEXT NOT NULL CHECK(role IN ('student', 'teacher')),
                        name TEXT NOT NULL,
                        user_id_code TEXT UNIQUE,
                        roll_no TEXT,
                        class_name TEXT,
                        division TEXT,
                        email TEXT UNIQUE,
                        password_hash TEXT NOT NULL,
                        reset_token TEXT,
                        reset_token_expiry TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(roll_no, class_name, division)
                    )
                ''')
                c.execute('''
                    INSERT INTO users_new
                        (id, role, name, user_id_code, roll_no, class_name, division, email,
                         password_hash, reset_token, reset_token_expiry, created_at)
                    SELECT
                        id, role, name, user_id_code, roll_no, class_name, division, email,
                        password_hash, reset_token, reset_token_expiry, created_at
                    FROM users
                ''')
                c.execute('DROP TABLE users')
                c.execute('ALTER TABLE users_new RENAME TO users')
                print("users table rebuilt successfully.")
    except Exception as e:
        print(f"Migration note: {e}")

    conn.commit()
    conn.close()

init_db()

# ================= AUTHENTICATION HELPERS =================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def teacher_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'teacher':
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def student_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'student':
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def get_db_connection():
    conn = sqlite3.connect('students.db')
    conn.row_factory = sqlite3.Row
    return conn

def student_key(roll_no, class_name, division):
    return (roll_no, class_name, division)

# ================= TTS =================
def speak_to_file(text, slow=False, max_retries=3):
    if len(text) > 300:
        text = text[:300]
    cached_audio = get_cached_audio(text, slow)
    if cached_audio:
        return cached_audio
    os.makedirs("static/audio", exist_ok=True)
    filename = f"{uuid.uuid4()}.mp3"
    path = f"static/audio/{filename}"
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                delay = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
            else:
                time.sleep(random.uniform(0.3, 0.8))
            gTTS(text=text, lang="en", slow=slow).save(path)
            save_to_cache(text, path, slow)
            return "/" + path
        except Exception as e:
            print(f"TTS attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_retries - 1:
                return None
    return None

# ================= SESSION CONTEXT HELPERS =================
def get_conversation_context() -> str:
    return session.get('conversation_context', '')

def set_conversation_context(ctx: str) -> None:
    session['conversation_context'] = ctx[-2000:]

def get_conversation_turn_count() -> int:
    return session.get('conversation_turn_count', 0)

def increment_conversation_turn() -> int:
    count = get_conversation_turn_count() + 1
    session['conversation_turn_count'] = count
    return count

def get_conversation_topic() -> str:
    return session.get('conversation_topic', '')

def set_conversation_topic(topic: str) -> None:
    session['conversation_topic'] = topic

# ================= INPUT INTENT DETECTION =================
_QUESTION_STARTERS = (
    "what", "who", "where", "when", "why", "how", "which", "can", "could",
    "do", "does", "did", "is", "are", "was", "were", "will", "would", "should",
    "have", "has", "am", "tell me", "explain", "define", "describe"
)

_GREETING_WORDS = {
    "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
    "good night", "bye", "goodbye", "see you", "namaste", "howdy", "sup",
    "greetings", "hiya", "how are you", "how r you", "how r u", "how are u",
    "hows it going", "what's up", "whats up", "wassup"
}

_FEELING_WORDS = {
    "happy", "sad", "angry", "tired", "excited", "scared", "bored",
    "hungry", "thirsty", "sick", "fine", "good", "bad", "okay", "great",
    "awesome", "terrible", "wonderful", "fantastic", "nervous", "worried"
}

_SHORT_ANSWER_PATTERNS = (
    r"^\w+$",
    r"^(yes|no|okay|ok|sure|maybe|nope|yep|yeah|nah)[\.\!]?$",
)

def detect_intent(text: str) -> str:
    stripped = text.strip().rstrip("?.!")
    lower = text.lower().strip()
    how_are_you_patterns = ["how are you", "how r you", "how r u", "how are u",
                            "how do you do", "how's it going", "hows it going"]
    for pattern in how_are_you_patterns:
        if pattern in lower:
            return "greeting"
    for word in _GREETING_WORDS:
        if lower.startswith(word) or lower == word:
            return "greeting"
    if text.strip().endswith("?"):
        return "question"
    first_word = lower.split()[0] if lower.split() else ""
    if first_word in _QUESTION_STARTERS or lower.startswith("tell me") or lower.startswith("explain"):
        return "question"
    for word in _FEELING_WORDS:
        if f" {word}" in f" {lower}" or lower.startswith(word):
            return "feeling"
    for pattern in _SHORT_ANSWER_PATTERNS:
        if re.match(pattern, lower, re.IGNORECASE):
            return "short_answer"
    return "statement"


# ================= COACH RESPONSE DATACLASS =================
@dataclass
class CoachResponse:
    corrected: str
    answer: str
    praise: str
    question: str
    raw: str
    intent: str = "statement"

    def format(self) -> str:
        return (
            f"CORRECT: {self.corrected}\n"
            f"ANSWER: {self.answer}\n"
            f"PRAISE: {self.praise}\n"
            f"QUESTION: {self.question}"
        )

    def to_speech_text(self) -> str:
        parts = []
        if self.intent == "greeting":
            if self.answer:
                parts.append(self.answer)
            if self.question:
                parts.append(self.question)
            return " ".join(parts)
        if self.intent == "question":
            if self.corrected and self.corrected.lower() != self.raw.lower().strip("?.! "):
                parts.append(self.corrected + ".")
            if self.answer:
                parts.append(self.answer)
            if self.praise:
                parts.append(self.praise)
            if self.question:
                parts.append(self.question)
            return " ".join(parts)
        if self.corrected:
            parts.append(self.corrected + ".")
        if self.answer:
            parts.append(self.answer)
        if self.praise:
            parts.append(self.praise)
        if self.question:
            parts.append(self.question)
        return " ".join(parts)

    def to_display_dict(self) -> dict:
        return {
            "correct":   self.corrected,
            "answer":    self.answer,
            "praise":    self.praise,
            "question":  self.question,
            "intent":    self.intent,
        }


# ================= ENGLISH COACH =================
COACH_SYSTEM_PROMPT = """You are a warm, friendly English coach having a natural conversation with children aged 6–15.

YOUR CORE JOB: Have a REAL, flowing conversation - just like talking with a friend!

HOW TO RESPOND:
1. GREETINGS: Respond warmly and personally
2. QUESTIONS: Answer directly and naturally
3. STATEMENTS: React with genuine interest
4. FEELINGS: Acknowledge warmly
5. SHORT REPLIES: React naturally and keep conversation flowing

GRAMMAR CORRECTION: Fix ONE main error. If correct, copy as-is.

OUTPUT FORMAT (exactly these 4 lines):
CORRECT: <grammar-corrected sentence>
ANSWER: <natural, friendly response - 1-2 sentences>
PRAISE: <short encouragement>
QUESTION: <one follow-up question>"""


def _parse_coach_response(raw: str) -> dict:
    fields = {"CORRECT": "", "ANSWER": "", "PRAISE": "", "QUESTION": ""}
    current_key = None
    for line in raw.splitlines():
        line = line.strip()
        matched = False
        for key in fields:
            if line.startswith(f"{key}:"):
                fields[key] = line[len(key) + 1:].strip()
                current_key = key
                matched = True
                break
        if not matched and current_key and line:
            fields[current_key] += " " + line
    return fields


def _build_coach_messages(child_text: str, context: str, intent: str, topic: str) -> list[dict]:
    messages = [{"role": "system", "content": COACH_SYSTEM_PROMPT}]
    context_block = ""
    if context:
        context_block += f"[Conversation so far]\n{context[-1200:]}\n\n"
    if topic:
        context_block += f"[Current topic: {topic}]\n\n"
    if context_block:
        messages.append({"role": "user", "content": context_block.strip()})
    messages.append({"role": "user", "content": f"Child says: {child_text}"})
    return messages


def _extract_topic_from_text(text: str, intent: str) -> str:
    stopwords = {
        "what", "who", "where", "when", "why", "how", "which", "is", "are", "was",
        "were", "do", "does", "did", "can", "could", "would", "should", "will",
        "the", "a", "an", "i", "me", "my", "you", "your", "it", "this", "that",
        "tell", "explain", "define", "describe", "please", "me", "about"
    }
    words = [w.lower().strip("?.!,") for w in text.split()]
    topic_words = [w for w in words if w not in stopwords and len(w) > 2]
    return " ".join(topic_words[:3]) if topic_words else ""


def english_coach(child_text):
    conversation_context = get_conversation_context()
    prompt = f"""
You are an English speaking coach for children aged 6 to 15.
STRICT RULES:
- Always correct the child's sentences.
- If the child says only ONE WORD or a short phrase, convert it into a full correct sentence.
- Use very simple English.
- Encourage the child.
- Ask ONE follow-up question.
- No grammar explanations and keep it short.
Respond ONLY in this format:
CORRECT: <correct sentence>
PRAISE: <short encouragement>
QUESTION: <one simple question>
Conversation so far:
{conversation_context}
Child says:
"{child_text}"
"""
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    reply = response.choices[0].message.content.strip()
    fields = _parse_coach_response(reply)
    conversation_context += f"\nChild: {child_text}\nAssistant: {reply}"
    conversation_context = conversation_context[-1000:]
    set_conversation_context(conversation_context)
    return CoachResponse(
        corrected=fields["CORRECT"] or child_text,
        answer="",
        praise=fields["PRAISE"],
        question=fields["QUESTION"],
        raw=reply,
        intent="statement"
    )


# ================= ROLEPLAY COACH =================
def _build_roleplay_messages(
    child_text: str, context: str, roleplay_type: str,
    suggested_question: str, intent: str, topic: str
) -> list[dict]:
    role_instruction = _ROLEPLAY_ROLE_PROMPTS.get(
        roleplay_type, "You are a friendly English speaking partner."
    )
    system_content = f"""{role_instruction}

You are in a roleplay with a student aged 6 to 15.
STRICT RULES:
- Always correct their sentence gently (CORRECT field)
- Your ANSWER must genuinely respond to what they said — in character
- Use very simple English
- Stay strictly in your role as {roleplay_type}
- Encourage the student warmly
- Ask ONE role-appropriate follow-up question
- No grammar meta-explanation

Here is a suggested question: "{suggested_question}"

Reply in this EXACT format:
CORRECT: <corrected version of student's input>
ANSWER: <your in-character, direct response>
PRAISE: <short, warm encouragement>
QUESTION: <one role-appropriate follow-up question>"""

    messages = [{"role": "system", "content": system_content}]
    context_block = ""
    if context:
        context_block += f"[Conversation so far]\n{context[-1200:]}\n"
    if topic:
        context_block += f"[Current topic: {topic}]\n"
    if context_block:
        messages.append({"role": "user", "content": context_block.strip()})
    messages.append({"role": "user", "content": f"Student: {child_text}"})
    return messages


def roleplay_coach(
    child_text: str, roleplay_type: str, *,
    model: str = "llama-3.1-8b-instant",
    temperature: float = 0.5,
    max_tokens: int = 200,
    fallback_on_error: bool = True,
) -> CoachResponse:
    if not child_text or not child_text.strip():
        raise ValueError("child_text must be a non-empty string.")
    intent = detect_intent(child_text.strip())
    context = get_conversation_context()
    topic = get_conversation_topic()
    suggested_question = get_roleplay_question(roleplay_type)
    new_topic = _extract_topic_from_text(child_text, intent)
    if new_topic:
        set_conversation_topic(new_topic)
    messages = _build_roleplay_messages(
        child_text.strip(), context, roleplay_type,
        suggested_question, intent, topic
    )
    try:
        completion = client.chat.completions.create(
            model=model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        raw_reply = completion.choices[0].message.content.strip()
        fields = _parse_coach_response(raw_reply)
        if not fields["ANSWER"] and fields["PRAISE"]:
            fields["ANSWER"] = fields["PRAISE"]
            fields["PRAISE"] = "Well done for trying!"
        response = CoachResponse(
            corrected=fields["CORRECT"] or child_text,
            answer=fields["ANSWER"],
            praise=fields["PRAISE"],
            question=fields["QUESTION"] or suggested_question,
            raw=raw_reply, intent=intent,
        )
    except Exception as exc:
        logger.error("Roleplay coach error [%s]: %s", roleplay_type, exc)
        if not fallback_on_error:
            raise
        fallback_answers = {
            "greeting":     f"Hello! Great to see you. I'm your {roleplay_type} today!",
            "question":     "That's a great question! Let me answer that for you.",
            "feeling":      "I understand! Thank you for telling me how you feel.",
            "short_answer": "I see! That's a good point.",
            "statement":    "Very interesting! Tell me more about that.",
        }
        response = CoachResponse(
            corrected=child_text,
            answer=fallback_answers.get(intent, "Good effort! Keep going!"),
            praise="Well done!", question=suggested_question,
            raw="", intent=intent,
        )
    increment_conversation_turn()
    set_conversation_context(
        context + f"\nStudent [{intent}]: {child_text}\n{roleplay_type.title()}: {response.answer} {response.question}"
    )
    return response


# ================= REPEAT AFTER ME =================
def generate_repeat_sentence(category="civic_sense", difficulty="easy"):
    category_details = {
        "civic_sense": {
            "easy": ["Keep your city clean","Do not litter on roads","Help old people cross","Wait for your turn please","Say thank you always","Be kind to others","Do not waste water","Turn off lights please","Respect your neighbours always","Use dustbin for waste"],
            "medium": ["We should not throw waste on the road","Always stand in a queue patiently","Help keep our neighbourhood clean and tidy","Switch off fans when leaving the room","We must respect traffic rules always","Plant trees to keep our earth green","Save water for the future generations","Be polite and greet everyone around you","Do not make noise in public places","Always use the zebra crossing safely"],
            "hard": ["We should always keep our surroundings clean and free from litter","Respecting public property is the duty of every good citizen","Saving electricity and water helps protect our environment for the future","Every citizen must follow traffic rules to keep roads safe for all","Being kind and helpful to others makes our community a better place"]
        },
        "animals": {
            "easy": ["Dogs bark loudly","Cats drink milk","Birds sing songs","Fish swim fast","Cows eat grass","Horses run quick","Ducks say quack","Lions roar loud","Bears sleep long","Monkeys climb trees"],
            "medium": ["The brown dog plays with a ball","My pet cat sleeps on the sofa","Colorful birds fly in the sky","Little fish swim in the pond","The white rabbit hops around happily","Elephants have very long trunks","Tigers are big striped cats","Dolphins jump in the ocean"],
            "hard": ["The big elephant uses its trunk to drink water every day","My pet dog loves to chase butterflies in the garden","The clever monkey climbs trees very quickly and easily","Beautiful peacocks spread their colorful feathers when dancing","Tiny hummingbirds can fly backwards and hover in the air"]
        },
        "food": {
            "easy": ["I eat apples","Pizza tastes good","Milk is white","Bread is soft","Ice cream melts","Cookies are sweet","Juice is cold","Cake is yummy","Soup is hot","Eggs are round"],
            "medium": ["I enjoy eating chocolate ice cream","Fresh vegetables are good for health","Mom makes delicious pasta for lunch","Orange juice is my favorite drink","Hot soup warms me up quickly","Strawberries taste sweet and juicy","I love eating crunchy potato chips","Sandwiches are perfect for picnics"],
            "hard": ["My grandmother makes the most delicious cookies in the whole world","We should eat healthy fruits and vegetables every single day","The restaurant serves fresh and tasty food to all customers","Drinking water keeps our body healthy and strong always","Breakfast is the most important meal of the entire day"]
        },
        "sports": {
            "easy": ["I play football","Run very fast","Jump rope daily","Swim in pool","Kick the ball","Throw the ball","Catch it quick","Hit the target","Race with friends","Climb the rope"],
            "medium": ["I practice basketball every single day","Running in the park is fun","My friends play cricket together happily","Swimming keeps us healthy and fit","The team won the match yesterday","Soccer is played with feet","Tennis players use special rackets always","Cycling helps build strong muscles"],
            "hard": ["Playing outdoor games helps us stay healthy and active always","My favorite sport is basketball because it's exciting and fun","The athletes train very hard to win the championship trophy","Regular exercise makes our bodies stronger and more energetic daily","Teamwork is very important when playing any sport together"]
        },
        "feelings": {
            "easy": ["I feel happy","Mom is sad","Brother is angry","Sister feels tired","I am excited","Dad is proud","I feel scared","She is brave","He seems worried","We are cheerful"],
            "medium": ["I feel very happy when playing","My friend is feeling sad today","The movie made everyone laugh loudly","I get excited about birthday parties","Helping others makes me feel good","Sometimes I feel nervous before tests","My sister feels proud of her artwork","The surprise made him very happy"],
            "hard": ["When I help my friends I feel very proud and happy","My little sister gets scared during thunderstorms at night","Winning the competition made the entire team feel wonderful","Sharing toys with others shows that we care about them","Being kind to everyone makes the world a better place"]
        },
        "colors": {
            "easy": ["Sky is blue","Grass is green","Sun is yellow","Roses are red","Clouds are white","Night is black","Orange is bright","Purple flowers bloom","Pink is pretty","Brown dirt falls"],
            "medium": ["The beautiful rainbow has many colors","My favorite color is bright blue","Red roses bloom in the garden","The green leaves look very fresh","Yellow butterflies fly near flowers happily","White snow covers the ground","Orange pumpkins grow in the field","Purple grapes taste very sweet"],
            "hard": ["The colorful painting has red blue yellow and green colors","My room walls are painted in light blue color","The sunset sky shows beautiful orange and pink shades","Rainbows appear when sunlight passes through water droplets magically","Artists mix different colors together to create new beautiful shades"]
        },
        "family": {
            "easy": ["I love mom","Dad helps me","Sister is kind","Brother plays games","Grandma tells stories","Grandpa is funny","Baby cries loud","Uncle visits us","Aunt bakes cake","Cousin is fun"],
            "medium": ["My mother cooks delicious food daily","Dad takes me to school everyday","My sister helps with homework always","Brother plays video games with me","Grandparents visit us every weekend regularly","My aunt makes tasty cookies","Uncle tells us funny jokes","Cousins play together at parties"],
            "hard": ["My entire family goes on vacation together every summer season","Mom and dad work very hard to give us everything","I love spending quality time with all my family members","Grandparents always share interesting stories from their childhood days","Family dinners are special times when everyone talks and laughs"]
        },
        "school": {
            "easy": ["I go school","Teacher is nice","Books are heavy","Math is hard","I study daily","Tests are scary","Lunch is yummy","Friends play together","Pencils write words","Classes start early"],
            "medium": ["My teacher explains lessons very clearly","I carry my school bag everyday","Math homework is quite challenging today","The library has many interesting books","Science class is really fun and exciting","Friends help each other with studies","Reading improves our vocabulary and knowledge","Art class lets us be creative"],
            "hard": ["My school has a big playground where we play games","Every morning I wake up early to catch the bus","The teacher gives us homework to practice at home daily","Learning new things at school makes us smarter every day","Good students always pay attention and complete their work on time"]
        }
    }
    cat_info = category_details.get(category, category_details["civic_sense"])
    examples = cat_info.get(difficulty, cat_info["easy"])
    recent = get_session_recent_sentences()
    available = [ex for ex in examples if ex not in recent]
    if not available:
        recent = recent[-5:] if len(recent) > 5 else []
        available = [ex for ex in examples if ex not in recent]
    if not available:
        available = examples
    selected = random.choice(available)
    recent.append(selected)
    if len(recent) > MAX_HISTORY:
        recent = recent[-MAX_HISTORY:]
    set_session_recent_sentences(recent)
    return selected

def generate_spell_word(difficulty="easy"):
    word_pools = {
        "easy": ["cat","dog","sun","run","fun","hat","bat","rat","pen","hen","cup","bus","bed","red","leg","bag","fan","can","ten","net","wet","jet","pet","set","box","fox","six","mix","pig","big","hot","pot","top","hop","mop","zip","tip","dip","cut","nut"],
        "medium": ["apple","table","happy","money","water","tiger","banana","flower","garden","winter","summer","mother","father","sister","better","letter","number","dinner","butter","purple","yellow","orange","Monday","Friday","Sunday","pencil","window","rabbit","market","simple","castle","people","circle","middle","bottle","little","bubble","double","jungle","candle","handle","puzzle","turtle"],
        "hard": ["beautiful","wonderful","elephant","tomorrow","yesterday","chocolate","hamburger","basketball","butterfly","strawberry","restaurant","dictionary","adventure","delicious","important","different","incredible","vegetables","understand","comfortable","celebration","imagination","encyclopedia","refrigerator","spectacular","communication","responsibility","extraordinary","accomplishment"]
    }
    words = word_pools.get(difficulty, word_pools["easy"])
    recent = get_session_recent_words()
    available = [w for w in words if w not in recent]
    if not available:
        recent = recent[-10:] if len(recent) > 10 else []
        available = [w for w in words if w not in recent]
    if not available:
        available = words
    selected = random.choice(available)
    recent.append(selected)
    if len(recent) > MAX_HISTORY:
        recent = recent[-MAX_HISTORY:]
    set_session_recent_words(recent)
    return selected

def get_word_sentence_usage(word):
    prompt = f"""Write ONE simple sentence using the word "{word}" for children aged 6-15.
Rules:
- The sentence must contain the word "{word}"
- Keep it between 5 to 10 words
- Do NOT write the word alone before the sentence
- Do NOT add labels, quotes, or explanations
- Output the sentence ONLY, nothing else"""
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=50
        )
        sentence = response.choices[0].message.content.strip()
        sentence = sentence.replace('"', '').replace("'", '').strip()
        sentence = re.sub(rf'^{re.escape(word.lower())}[\s:.\-–]+', '', sentence, flags=re.IGNORECASE).strip()
        sentence = re.sub(r'^(sentence|example|output|answer|usage)\s*[:\-–]\s*', '', sentence, flags=re.IGNORECASE).strip()
        if sentence:
            sentence = sentence[0].upper() + sentence[1:]
        if sentence and sentence[-1] not in '.!?':
            sentence += '.'
        if len(sentence.split()) < 3 or sentence.lower().strip('.!?') == word.lower() or word.lower() not in sentence.lower():
            return f"The word {word} is used every day."
        return sentence
    except Exception:
        return f"The word {word} is used every day."


def get_word_meaning(word):
    prompt = f"""Explain the word "{word}" to a child aged 6-15.
Respond in this EXACT format:
MEANING: <simple definition in one sentence>
EXAMPLE: <example sentence using the word>
TYPE: <noun/verb/adjective/etc>
TIP: <memory tip or helpful hint>
Keep everything very simple and child-friendly."""
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except:
        return f"MEANING: {word} is a word\nEXAMPLE: I know the word {word}\nTYPE: word\nTIP: Practice saying it"

def compare_words(student_text, correct_text):
    student_words = student_text.lower().split()
    correct_words = correct_text.lower().split()
    comparison = []
    for i, correct_word in enumerate(correct_words):
        if i < len(student_words):
            student_word = student_words[i]
            similarity = SequenceMatcher(None, student_word, correct_word).ratio()
            if similarity >= 0.8:
                comparison.append({"word": correct_word, "status": "correct"})
            else:
                comparison.append({"word": correct_word, "status": "incorrect", "spoken": student_word})
        else:
            comparison.append({"word": correct_word, "status": "missing"})
    return comparison

def compare_spelling(student_spelling, correct_word):
    student = student_spelling.lower().strip()
    correct = correct_word.lower().strip()
    comparison = []
    max_len = max(len(student), len(correct))
    for i in range(max_len):
        if i < len(correct):
            correct_letter = correct[i]
            if i < len(student):
                student_letter = student[i]
                if student_letter == correct_letter:
                    comparison.append({"letter": correct_letter, "status": "correct"})
                else:
                    comparison.append({"letter": correct_letter, "status": "incorrect", "typed": student_letter})
            else:
                comparison.append({"letter": correct_letter, "status": "missing"})
    return comparison

# ================= KEEP-ALIVE =================
@app.route("/ping")
def ping():
    return "pong", 200

# ================= AUTHENTICATION ROUTES =================
@app.route("/")
def home():
    if 'user_id' in session:
        if session.get('role') == 'teacher':
            return redirect(url_for('teacher_dashboard'))
        else:
            return redirect(url_for('dashboard'))
    return redirect(url_for('login_page'))

@app.route("/login")
def login_page():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    role = data.get("role", "student")
    password = data.get("password")
    conn = get_db_connection()

    if role == "student":
        # ── NEW: login by User ID ──
        user_id_code = data.get("userIdCode", "").strip().upper()

        if not user_id_code or not password:
            conn.close()
            return jsonify({"success": False, "message": "Please provide your User ID and password."})

        user = conn.execute(
            'SELECT * FROM users WHERE role=? AND user_id_code=?',
            (role, user_id_code)
        ).fetchone()
    else:
        email = data.get("email")
        if not email or not password:
            conn.close()
            return jsonify({"success": False, "message": "Please provide email and password"})
        user = conn.execute('SELECT * FROM users WHERE role=? AND email=?', (role, email)).fetchone()

    conn.close()

    if user and check_password_hash(user['password_hash'], password):
        session['user_id']  = user['id']
        session['name']     = user['name']
        session['role']     = user['role']
        session.pop('conversation_context', None)
        session.pop('recent_sentences', None)
        session.pop('recent_words', None)
        session.pop('conversation_turn_count', None)
        session.pop('conversation_topic', None)

        if role == "student":
            session['roll_no']        = user['roll_no']
            session['class_name']     = user['class_name']
            session['division']       = user['division']
            session['student_name']   = user['name']
            session['user_id_code']   = user['user_id_code']
            conn2 = get_db_connection()
            conn2.execute('INSERT INTO student_sessions (student_id) VALUES (?)', (user['id'],))
            conn2.commit()
            conn2.close()
        else:
            session['email'] = user['email']

        return jsonify({"success": True, "message": "Login successful", "name": user['name']})
    else:
        if role == "student":
            return jsonify({"success": False, "message": "Invalid User ID or password. Please check and try again."})
        return jsonify({"success": False, "message": "Invalid credentials."})

@app.route("/signup", methods=["POST"])
def signup():
    data       = request.json
    name       = data.get("name", "").strip()
    password   = data.get("password", "")
    role       = data.get("role", "student")

    if not name or not password:
        return jsonify({"success": False, "message": "All fields are required"})

    conn = get_db_connection()
    password_hash = generate_password_hash(password)

    try:
        if role == "student":
            roll_no    = data.get("rollNo", "").strip()
            class_name = data.get("className", "").strip()
            division   = data.get("division", "").strip().upper()

            if not roll_no:
                conn.close()
                return jsonify({"success": False, "message": "Roll number is required"})
            if class_name not in VALID_CLASSES:
                conn.close()
                return jsonify({"success": False, "message": "Please select a valid class (1–10)"})
            if division not in VALID_DIVISIONS:
                conn.close()
                return jsonify({"success": False, "message": "Please select a valid division (A–E)"})

            existing = conn.execute(
                'SELECT id FROM users WHERE roll_no=? AND class_name=? AND division=?',
                (roll_no, class_name, division)
            ).fetchone()
            if existing:
                conn.close()
                return jsonify({
                    "success": False,
                    "message": f"Roll number {roll_no} is already registered in Class {class_name}-{division}"
                })

            # Generate a unique User ID
            new_user_id = generate_unique_user_id(conn)

            conn.execute(
                'INSERT INTO users (role, name, user_id_code, roll_no, class_name, division, password_hash) VALUES (?,?,?,?,?,?,?)',
                (role, name, new_user_id, roll_no, class_name, division, password_hash)
            )
            conn.execute(
                '''INSERT INTO student_progress
                   (roll_no, class_name, division, xp, conversation_xp, roleplay_xp,
                    repeat_xp, spellbee_xp, meanings_xp, total_stars, streak)
                   VALUES (?,?,?,0,0,0,0,0,0,0,0)''',
                (roll_no, class_name, division)
            )
            conn.commit()
            conn.close()
            return jsonify({
                "success": True,
                "message": "Account created successfully",
                "userIdCode": new_user_id  # Return the generated ID to show to user
            })

        else:  # teacher
            email = data.get("email", "").strip()
            if not email:
                conn.close()
                return jsonify({"success": False, "message": "Email is required"})
            existing = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
            if existing:
                conn.close()
                return jsonify({"success": False, "message": "Email already registered"})
            conn.execute(
                'INSERT INTO users (role, name, email, password_hash) VALUES (?,?,?,?)',
                (role, name, email, password_hash)
            )
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "Account created successfully"})

    except Exception as e:
        conn.close()
        print(f"Signup error: {e}")
        return jsonify({"success": False, "message": "Error creating account"})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# ================= IDENTITY VERIFICATION (Forgot Password Step 1) =================
@app.route("/verify_identity", methods=["POST"])
def verify_identity():
    """
    Verify a student's identity using User ID + name + roll number + class + division.
    Called from the Forgot Password modal (Step 1) before allowing a password reset.
    Returns { success: true, studentName: "..." } on match, or an error message.
    """
    data         = request.json
    user_id_code = data.get("userIdCode", "").strip().upper()
    name         = data.get("name", "").strip()
    roll_no      = data.get("rollNo", "").strip()
    class_name   = data.get("className", "").strip()
    division     = data.get("division", "").strip().upper()

    # --- Basic field validation ---
    if not user_id_code:
        return jsonify({"success": False, "message": "Please enter your User ID."})
    if not name:
        return jsonify({"success": False, "message": "Please enter your full name."})
    if not roll_no:
        return jsonify({"success": False, "message": "Please enter your roll number."})
    if not class_name or not division:
        return jsonify({"success": False, "message": "Please select your class and division."})

    conn = get_db_connection()
    user = conn.execute(
        '''SELECT id, name, roll_no, class_name, division
           FROM users
           WHERE user_id_code = ? AND role = 'student'
           AND class_name = ? AND division = ?''',
        (user_id_code, class_name, division)
    ).fetchone()
    conn.close()

    if not user:
        # Don't reveal whether the User ID exists or not — generic message
        return jsonify({
            "success": False,
            "message": "Details do not match our records. Please check your User ID, Class, and Division."
        })

    # Check roll number
    if user['roll_no'].strip() != roll_no:
        return jsonify({
            "success": False,
            "message": "Details do not match our records. Please check your roll number."
        })

    # Check name — case-insensitive, strip extra whitespace
    if user['name'].strip().lower() != name.lower():
        return jsonify({
            "success": False,
            "message": "The name you entered does not match our records."
        })

    # All checks passed
    return jsonify({
        "success": True,
        "studentName": user['name'],
        "message": "Identity verified successfully."
    })


@app.route("/reset_password_request", methods=["POST"])
def reset_password_request():
    data         = request.json
    user_id_code = data.get("userIdCode", "").strip().upper()
    new_password = data.get("newPassword", "").strip()

    if not user_id_code or not new_password:
        return jsonify({"success": False, "message": "User ID and new password are required"})
    if len(new_password) < 4:
        return jsonify({"success": False, "message": "Password must be at least 4 characters"})

    conn = get_db_connection()
    user = conn.execute(
        'SELECT * FROM users WHERE user_id_code=? AND role=?',
        (user_id_code, 'student')
    ).fetchone()
    if not user:
        conn.close()
        return jsonify({"success": False, "message": "No student found with that User ID"})

    conn.execute(
        'UPDATE users SET password_hash=? WHERE user_id_code=?',
        (generate_password_hash(new_password), user_id_code)
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Password updated successfully! Please login with your new password."})

@app.route("/delete_account", methods=["POST"])
@student_required
def delete_account():
    if 'roll_no' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    roll_no    = session['roll_no']
    class_name = session['class_name']
    division   = session['division']
    user_id    = session['user_id']

    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM activity_log WHERE roll_no=? AND class_name=? AND division=?', (roll_no, class_name, division))
        conn.execute('DELETE FROM student_progress WHERE roll_no=? AND class_name=? AND division=?', (roll_no, class_name, division))
        conn.execute('DELETE FROM student_badges WHERE roll_no=? AND class_name=? AND division=?', (roll_no, class_name, division))
        conn.execute('DELETE FROM student_sessions WHERE student_id=?', (user_id,))
        conn.execute('DELETE FROM users WHERE roll_no=? AND class_name=? AND division=? AND role=?', (roll_no, class_name, division, 'student'))
        conn.commit()
        conn.close()
        session.clear()
        return jsonify({'success': True, 'message': 'Account deleted successfully'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'message': f'Error deleting account: {str(e)}'})

# ================= STUDENT ROUTES =================
@app.route("/dashboard")
@student_required
def dashboard():
    return render_template("dashboard.html")

@app.route("/main")
@student_required
def main():
    return render_template("main.html")

@app.route("/process", methods=["POST"])
@student_required
def process():
    data = request.json
    user_text = data["text"]
    roleplay = data.get("roleplay")
    try:
        coach_response: CoachResponse = (
            roleplay_coach(user_text, roleplay)
            if roleplay
            else english_coach(user_text)
        )
        final_text = coach_response.to_speech_text()
        audio = speak_to_file(final_text)
        response_data = {
            **coach_response.to_display_dict(),
            "reply": final_text,
            "audio": audio,
        }
        if audio is None:
            response_data["audio_error"] = "Audio temporarily unavailable. Please try again."
        return jsonify(response_data)
    except Exception as e:
        logger.error("Error in /process: %s", e)
        return jsonify({"reply": "Sorry, something went wrong. Please try again.", "audio": None, "error": str(e)}), 500

@app.route("/repeat_sentence", methods=["POST"])
@student_required
def repeat_sentence():
    data = request.json
    category = data.get("category", "civic_sense")
    difficulty = data.get("difficulty", "easy")
    sentence = generate_repeat_sentence(category, difficulty)
    audio_normal = speak_to_file(sentence, slow=False)
    audio_slow = speak_to_file(sentence, slow=True)
    if audio_normal is None or audio_slow is None:
        return jsonify({"sentence": sentence, "audio": None, "audio_slow": None, "audio_error": "Audio temporarily unavailable."})
    return jsonify({"sentence": sentence, "audio": audio_normal, "audio_slow": audio_slow})

@app.route("/check_repeat", methods=["POST"])
@student_required
def check_repeat():
    data = request.json
    student = data["student"]
    correct = data["correct"]
    score = SequenceMatcher(None, student.lower(), correct.lower()).ratio()
    word_comparison = compare_words(student, correct)
    needs_correction = score < 0.9
    if score >= 0.9:
        feedback = "Perfect! Amazing pronunciation!"
        stars = 3
    elif score >= 0.75:
        feedback = "Great job! Keep practicing!"
        stars = 2
    elif score >= 0.6:
        feedback = "Good try! Try speaking more clearly."
        stars = 1
    else:
        feedback = "Keep trying! Speak slowly and clearly."
        stars = 0
    return jsonify({
        "feedback": feedback, "score": round(score * 100),
        "stars": stars, "word_comparison": word_comparison,
        "you_said": student, "correct_version": correct if needs_correction else None
    })

@app.route("/spell_word", methods=["POST"])
@student_required
def spell_word():
    data = request.json
    difficulty = data.get("difficulty", "easy")
    word = generate_spell_word(difficulty)
    usage = get_word_sentence_usage(word)
    audio_word = speak_to_file(word, slow=True)
    audio_sentence = speak_to_file(usage, slow=False)
    if audio_word is None or audio_sentence is None:
        return jsonify({"word": word, "usage": usage, "audio_word": None, "audio_sentence": None, "audio_error": "Audio temporarily unavailable."})
    return jsonify({"word": word, "usage": usage, "audio_word": audio_word, "audio_sentence": audio_sentence})

@app.route("/check_spelling", methods=["POST"])
@student_required
def check_spelling():
    data = request.json
    student_spelling = data["spelling"]
    correct_word = data["correct"]
    attempt = data.get("attempt", 1)
    student = student_spelling.lower().strip()
    correct = correct_word.lower().strip()
    is_correct = (student == correct)
    letter_comparison = compare_spelling(student, correct)
    if is_correct:
        feedback = "🎉 Perfect! You spelled it correctly!"
        stars = 3
        hint = None
    else:
        similarity = SequenceMatcher(None, student, correct).ratio()
        if similarity >= 0.8:
            feedback = "Almost there! Check a few letters."
            stars = 2
        elif similarity >= 0.5:
            feedback = "Good try! Keep practicing!"
            stars = 1
        else:
            feedback = "Try again! Listen carefully to the word."
            stars = 0
        hint = None
        if attempt >= 2:
            hint = f"💡 Hint: The word starts with '{correct[0].upper()}' and has {len(correct)} letters."
    return jsonify({"correct": is_correct, "feedback": feedback, "stars": stars,
                   "letter_comparison": letter_comparison, "correct_spelling": correct, "hint": hint})

@app.route("/get_meaning", methods=["POST"])
@student_required
def get_meaning():
    data = request.json
    word = data["word"]
    meaning_response = get_word_meaning(word)
    meaning = usage = word_type = tip = ""
    for line in meaning_response.split("\n"):
        if line.startswith("MEANING:"):
            meaning = line.replace("MEANING:", "").strip()
        elif line.startswith("EXAMPLE:"):
            usage = line.replace("EXAMPLE:", "").strip()
        elif line.startswith("TYPE:"):
            word_type = line.replace("TYPE:", "").strip()
        elif line.startswith("TIP:"):
            tip = line.replace("TIP:", "").strip()
    audio_text = f"{word}. {meaning}. For example: {usage}. {tip}"
    audio = speak_to_file(audio_text, slow=False)
    if audio is None:
        return jsonify({"word": word, "meaning": meaning, "usage": usage, "type": word_type, "tip": tip, "audio": None, "audio_error": "Audio temporarily unavailable."})
    return jsonify({"word": word, "meaning": meaning, "usage": usage, "type": word_type, "tip": tip, "audio": audio})

# ================= DAILY CHALLENGE =================
@app.route("/get_daily_challenge")
@student_required
def get_daily_challenge_route():
    roll_no = session.get('roll_no')
    challenge = get_daily_challenge()
    completed = has_completed_daily(roll_no)
    return jsonify({"success": True, "challenge": challenge, "completed": completed, "bonus_xp": 3})

@app.route("/complete_daily", methods=["POST"])
@student_required
def complete_daily():
    roll_no    = session.get('roll_no')
    class_name = session.get('class_name')
    division   = session.get('division')
    if has_completed_daily(roll_no):
        return jsonify({"success": False, "message": "Already completed today's challenge!"})
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO activity_log (roll_no, class_name, division, mode, score, xp_earned, stars_earned) VALUES (?,?,?,?,?,?,?)",
        (roll_no, class_name, division, 'daily', 100, 3, 1)
    )
    conn.execute(
        "UPDATE student_progress SET xp=xp+3, last_active=? WHERE roll_no=? AND class_name=? AND division=?",
        (datetime.now(), roll_no, class_name, division)
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Daily challenge complete! +3 XP", "xp_earned": 3})

# ================= LEADERBOARD =================
@app.route("/get_leaderboard")
@student_required
def get_leaderboard():
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT u.name, u.class_name, u.division,
               sp.xp, sp.total_stars, sp.streak
        FROM users u
        JOIN student_progress sp
          ON u.roll_no=sp.roll_no AND u.class_name=sp.class_name AND u.division=sp.division
        WHERE u.role='student'
        ORDER BY sp.xp DESC LIMIT 10
    ''').fetchall()
    conn.close()
    leaderboard = []
    for i, row in enumerate(rows):
        leaderboard.append({
            "rank": i + 1, "name": row['name'],
            "class": f"Class {row['class_name']}-{row['division']}",
            "xp": row['xp'] or 0, "stars": row['total_stars'] or 0, "streak": row['streak'] or 0
        })
    my_roll  = session.get('roll_no')
    my_class = session.get('class_name')
    my_div   = session.get('division')
    conn = get_db_connection()
    my_rank_row = conn.execute('''
        SELECT COUNT(*)+1 as rank FROM student_progress
        WHERE xp > (SELECT xp FROM student_progress WHERE roll_no=? AND class_name=? AND division=?)
    ''', (my_roll, my_class, my_div)).fetchone()
    conn.close()
    my_rank = my_rank_row['rank'] if my_rank_row else '?'
    return jsonify({"success": True, "leaderboard": leaderboard, "my_rank": my_rank})

# ================= XP SYSTEM =================
@app.route("/get_student_info")
@student_required
def get_student_info():
    if 'roll_no' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    roll_no    = session['roll_no']
    class_name = session['class_name']
    division   = session['division']

    conn = get_db_connection()
    student = conn.execute(
        'SELECT name, roll_no, class_name, division, user_id_code FROM users WHERE roll_no=? AND class_name=? AND division=?',
        (roll_no, class_name, division)
    ).fetchone()
    progress = conn.execute(
        'SELECT * FROM student_progress WHERE roll_no=? AND class_name=? AND division=?',
        (roll_no, class_name, division)
    ).fetchone()
    badges_rows = conn.execute(
        'SELECT badge_id, earned_at FROM student_badges WHERE roll_no=? AND class_name=? AND division=? ORDER BY earned_at DESC',
        (roll_no, class_name, division)
    ).fetchall()
    conn.close()

    if student and progress:
        streak = calculate_streak(roll_no)
        conn = get_db_connection()
        conn.execute(
            'UPDATE student_progress SET streak=? WHERE roll_no=? AND class_name=? AND division=?',
            (streak, roll_no, class_name, division)
        )
        conn.commit()
        conn.close()

        progress_data = {
            'conversation_xp': progress['conversation_xp'] or 0,
            'roleplay_xp':     progress['roleplay_xp']     or 0,
            'repeat_xp':       progress['repeat_xp']       or 0,
            'spellbee_xp':     progress['spellbee_xp']     or 0,
            'meanings_xp':     progress['meanings_xp']     or 0
        }
        unlocked_features = get_unlocked_features(progress_data)
        next_unlock       = get_next_unlock(progress_data)

        earned_badge_ids = [row['badge_id'] for row in badges_rows]
        badges_detail = []
        for b in ALL_BADGES:
            badges_detail.append({
                **b,
                'earned':    b['id'] in earned_badge_ids,
                'earned_at': next((row['earned_at'] for row in badges_rows if row['badge_id'] == b['id']), None)
            })

        daily_challenge = get_daily_challenge()
        daily_completed = has_completed_daily(roll_no)

        return jsonify({
            'success': True,
            'student': {
                'name':             student['name'],
                'rollNo':           student['roll_no'],
                'className':        student['class_name'],
                'division':         student['division'],
                'classLabel':       f"Class {student['class_name']}-{student['division']}",
                'userIdCode':       student['user_id_code'],
                'xp':               progress['xp'],
                'conversationXp':   progress_data['conversation_xp'],
                'roleplayXp':       progress_data['roleplay_xp'],
                'repeatXp':         progress_data['repeat_xp'],
                'spellbeeXp':       progress_data['spellbee_xp'],
                'meaningsXp':       progress_data['meanings_xp'],
                'totalStars':       progress['total_stars'],
                'totalSessions':    progress['total_sessions'],
                'averageAccuracy':  round(progress['average_accuracy'], 1),
                'streak':           streak,
                'unlockedFeatures': unlocked_features,
                'nextUnlock':       next_unlock,
                'badges':           badges_detail,
                'earnedBadgeCount': len(earned_badge_ids),
                'totalBadgeCount':  len(ALL_BADGES),
                'dailyChallenge':   daily_challenge,
                'dailyCompleted':   daily_completed,
            }
        })
    else:
        return jsonify({'success': False, 'message': 'Student not found'})

@app.route("/update_xp", methods=["POST"])
@student_required
def update_xp():
    if 'roll_no' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data       = request.json
    roll_no    = session['roll_no']
    class_name = session['class_name']
    division   = session['division']
    xp_earned    = data.get('xpEarned', 0)
    mode         = data.get('mode', '').lower()
    score        = data.get('score', 0)
    stars_earned = data.get('starsEarned', 0)
    difficulty   = data.get('difficulty', 'easy')

    conn = get_db_connection()
    progress = conn.execute(
        'SELECT * FROM student_progress WHERE roll_no=? AND class_name=? AND division=?',
        (roll_no, class_name, division)
    ).fetchone()

    if progress:
        old_progress = {
            'xp':              progress['xp'] or 0,
            'conversation_xp': progress['conversation_xp'] or 0,
            'roleplay_xp':     progress['roleplay_xp']     or 0,
            'repeat_xp':       progress['repeat_xp']       or 0,
            'spellbee_xp':     progress['spellbee_xp']     or 0,
            'meanings_xp':     progress['meanings_xp']     or 0,
            'total_stars':     progress['total_stars']      or 0,
            'streak':          progress['streak']           or 0,
        }
        old_unlocked   = get_unlocked_features(old_progress)
        new_total_xp   = old_progress['xp'] + xp_earned
        mode_xp_column = f"{mode}_xp"
        if mode_xp_column in old_progress:
            new_mode_xp = old_progress[mode_xp_column] + xp_earned
            old_progress[mode_xp_column] = new_mode_xp
        else:
            new_mode_xp = 0
        old_progress['xp']          = new_total_xp
        old_progress['total_stars'] = old_progress['total_stars'] + stars_earned
        new_unlocked = get_unlocked_features(old_progress)
        newly_unlocked_features = [f for f in new_unlocked if f not in old_unlocked]

        streak = calculate_streak(roll_no)
        old_progress['streak'] = streak
        old_avg        = progress['average_accuracy']
        total_sessions = progress['total_sessions']
        new_avg = score if total_sessions == 0 else ((old_avg * total_sessions) + score) / (total_sessions + 1)

        update_query = f'''
            UPDATE student_progress
            SET xp=?, {mode_xp_column}=?,
                total_stars=total_stars+?,
                average_accuracy=?, streak=?, last_active=?
            WHERE roll_no=? AND class_name=? AND division=?
        '''
        conn.execute(update_query,
                     (new_total_xp, new_mode_xp, stars_earned, new_avg, streak,
                      datetime.now(), roll_no, class_name, division))
        conn.execute(
            'INSERT INTO activity_log (roll_no, class_name, division, mode, score, xp_earned, stars_earned) VALUES (?,?,?,?,?,?,?)',
            (roll_no, class_name, division, mode, score, xp_earned, stars_earned)
        )
        newly_earned_badge_ids = check_earned_badges(
            roll_no, old_progress, mode=mode, difficulty=difficulty, score=score, stars_earned=stars_earned
        )
        for badge_id in newly_earned_badge_ids:
            try:
                conn.execute(
                    'INSERT OR IGNORE INTO student_badges (roll_no, class_name, division, badge_id) VALUES (?,?,?,?)',
                    (roll_no, class_name, division, badge_id)
                )
            except Exception as e:
                print(f"Badge insert error: {e}")

        conn.commit()
        conn.close()
        newly_earned_badges_detail = [
            {**BADGE_MAP[bid], 'earned': True} for bid in newly_earned_badge_ids if bid in BADGE_MAP
        ]
        next_unlock = get_next_unlock(old_progress)
        return jsonify({
            'success':               True,
            'newXP':                 new_total_xp,
            'newModeXP':             new_mode_xp,
            'mode':                  mode,
            'streak':                streak,
            'newlyUnlockedFeatures': newly_unlocked_features,
            'unlockedFeatures':      new_unlocked,
            'nextUnlock':            next_unlock,
            'averageAccuracy':       round(new_avg, 1),
            'newlyEarnedBadges':     newly_earned_badges_detail
        })
    else:
        conn.close()
        return jsonify({'success': False, 'message': 'Progress not found'})

# ================= BADGE ROUTES =================
@app.route("/get_badges")
@student_required
def get_badges():
    if 'roll_no' not in session:
        return jsonify({'success': False})
    roll_no    = session['roll_no']
    class_name = session['class_name']
    division   = session['division']
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT badge_id, earned_at FROM student_badges WHERE roll_no=? AND class_name=? AND division=? ORDER BY earned_at DESC',
        (roll_no, class_name, division)
    ).fetchall()
    conn.close()
    earned_ids = {row['badge_id']: row['earned_at'] for row in rows}
    badges = [{**b, 'earned': b['id'] in earned_ids, 'earned_at': earned_ids.get(b['id'])} for b in ALL_BADGES]
    return jsonify({'success': True, 'badges': badges, 'earnedCount': len(earned_ids), 'totalCount': len(ALL_BADGES)})

# ================= TEACHER ROUTES =================
@app.route("/teacher-dashboard")
@teacher_required
def teacher_dashboard():
    return render_template("teacher_dashboard.html")

@app.route("/get_teacher_info")
@teacher_required
def get_teacher_info():
    if 'user_id' not in session:
        return jsonify({'success': False})
    return jsonify({'success': True, 'teacher': {'name': session.get('name'), 'email': session.get('email')}})

@app.route("/get_all_students")
@teacher_required
def get_all_students():
    conn = get_db_connection()
    students = conn.execute('''
        SELECT u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
               sp.xp, sp.conversation_xp, sp.roleplay_xp,
               sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
               sp.total_stars, sp.total_sessions, sp.average_accuracy,
               sp.last_active, sp.streak
        FROM users u
        LEFT JOIN student_progress sp
          ON u.roll_no=sp.roll_no AND u.class_name=sp.class_name AND u.division=sp.division
        WHERE u.role='student'
        ORDER BY u.class_name*1 ASC, u.division ASC, sp.xp DESC
    ''').fetchall()
    conn.close()

    students_list = []
    for s in students:
        roll_no    = s['roll_no']
        class_name = s['class_name']
        division   = s['division']
        progress_data = {
            'conversation_xp': s['conversation_xp'] or 0,
            'roleplay_xp':     s['roleplay_xp']     or 0,
            'repeat_xp':       s['repeat_xp']       or 0,
            'spellbee_xp':     s['spellbee_xp']     or 0,
            'meanings_xp':     s['meanings_xp']     or 0
        }
        unlocked_features = get_unlocked_features(progress_data)
        conn2 = get_db_connection()
        badge_count = conn2.execute(
            'SELECT COUNT(*) as cnt FROM student_badges WHERE roll_no=? AND class_name=? AND division=?',
            (roll_no, class_name, division)
        ).fetchone()['cnt']
        conn2.close()
        students_list.append({
            'name':             s['name'],
            'rollNo':           roll_no,
            'userIdCode':       s['user_id_code'],
            'className':        class_name,
            'division':         division,
            'classLabel':       f"Class {class_name}-{division}",
            'xp':               s['xp'] or 0,
            'conversationXp':   progress_data['conversation_xp'],
            'roleplayXp':       progress_data['roleplay_xp'],
            'repeatXp':         progress_data['repeat_xp'],
            'spellbeeXp':       progress_data['spellbee_xp'],
            'meaningsXp':       progress_data['meanings_xp'],
            'totalStars':       s['total_stars'] or 0,
            'totalSessions':    s['total_sessions'] or 0,
            'averageAccuracy':  round(s['average_accuracy'] or 0, 1),
            'lastActive':       s['last_active'],
            'streak':           s['streak'] or 0,
            'unlockedFeatures': unlocked_features,
            'earnedBadgeCount': badge_count,
            'totalBadgeCount':  len(ALL_BADGES),
        })
    return jsonify({'success': True, 'students': students_list})

@app.route("/get_student_details/<roll_no>")
@teacher_required
def get_student_details(roll_no):
    class_name = request.args.get('class_name', '')
    division   = request.args.get('division', '')
    conn = get_db_connection()
    if class_name and division:
        student = conn.execute('''
            SELECT u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
                   sp.xp, sp.conversation_xp, sp.roleplay_xp,
                   sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
                   sp.total_stars, sp.total_sessions, sp.average_accuracy, sp.last_active, sp.streak
            FROM users u
            LEFT JOIN student_progress sp
              ON u.roll_no=sp.roll_no AND u.class_name=sp.class_name AND u.division=sp.division
            WHERE u.roll_no=? AND u.class_name=? AND u.division=? AND u.role='student'
        ''', (roll_no, class_name, division)).fetchone()
    else:
        student = conn.execute('''
            SELECT u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
                   sp.xp, sp.conversation_xp, sp.roleplay_xp,
                   sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
                   sp.total_stars, sp.total_sessions, sp.average_accuracy, sp.last_active, sp.streak
            FROM users u
            LEFT JOIN student_progress sp
              ON u.roll_no=sp.roll_no AND u.class_name=sp.class_name AND u.division=sp.division
            WHERE u.roll_no=? AND u.role='student' LIMIT 1
        ''', (roll_no,)).fetchone()

    if not student:
        conn.close()
        return jsonify({'success': False, 'message': 'Student not found'})

    cn = student['class_name']
    dv = student['division']
    activities = conn.execute('''
        SELECT date, mode, score, xp_earned, stars_earned
        FROM activity_log WHERE roll_no=? AND class_name=? AND division=?
        ORDER BY date DESC LIMIT 50
    ''', (roll_no, cn, dv)).fetchall()
    badges_rows = conn.execute(
        'SELECT badge_id, earned_at FROM student_badges WHERE roll_no=? AND class_name=? AND division=? ORDER BY earned_at DESC',
        (roll_no, cn, dv)
    ).fetchall()
    conn.close()

    activity_list = [{'date': a['date'], 'mode': a['mode'], 'score': round(a['score'] or 0, 1),
                      'xpEarned': a['xp_earned'], 'starsEarned': a['stars_earned']} for a in activities]
    progress_data = {
        'conversation_xp': student['conversation_xp'] or 0,
        'roleplay_xp':     student['roleplay_xp']     or 0,
        'repeat_xp':       student['repeat_xp']       or 0,
        'spellbee_xp':     student['spellbee_xp']     or 0,
        'meanings_xp':     student['meanings_xp']     or 0
    }
    unlocked_features = get_unlocked_features(progress_data)
    next_unlock       = get_next_unlock(progress_data)
    earned_ids        = {row['badge_id']: row['earned_at'] for row in badges_rows}
    badges_detail     = [{**b, 'earned': b['id'] in earned_ids, 'earned_at': earned_ids.get(b['id'])} for b in ALL_BADGES]
    student_data = {
        'name':             student['name'],
        'rollNo':           student['roll_no'],
        'userIdCode':       student['user_id_code'],
        'className':        cn,
        'division':         dv,
        'classLabel':       f"Class {cn}-{dv}",
        'xp':               student['xp'] or 0,
        'conversationXp':   progress_data['conversation_xp'],
        'roleplayXp':       progress_data['roleplay_xp'],
        'repeatXp':         progress_data['repeat_xp'],
        'spellbeeXp':       progress_data['spellbee_xp'],
        'meaningsXp':       progress_data['meanings_xp'],
        'totalStars':       student['total_stars'] or 0,
        'totalSessions':    student['total_sessions'] or 0,
        'averageAccuracy':  round(student['average_accuracy'] or 0, 1),
        'lastActive':       student['last_active'],
        'streak':           student['streak'] or 0,
        'unlockedFeatures': unlocked_features,
        'nextUnlock':       next_unlock,
        'activityLog':      activity_list,
        'badges':           badges_detail,
        'earnedBadgeCount': len(earned_ids),
        'totalBadgeCount':  len(ALL_BADGES)
    }
    return jsonify({'success': True, 'student': student_data})

@app.route("/teacher/reset_student_password", methods=["POST"])
@teacher_required
def teacher_reset_student_password():
    data         = request.json
    user_id_code = data.get("userIdCode", "").strip().upper()
    new_password = data.get("newPassword", "").strip()

    if not user_id_code or not new_password:
        return jsonify({"success": False, "message": "User ID and new password are required"})

    conn = get_db_connection()
    user = conn.execute(
        'SELECT * FROM users WHERE user_id_code=? AND role=?',
        (user_id_code, 'student')
    ).fetchone()
    if not user:
        conn.close()
        return jsonify({"success": False, "message": "Student not found"})
    conn.execute(
        'UPDATE users SET password_hash=? WHERE user_id_code=?',
        (generate_password_hash(new_password), user_id_code)
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Password for {user['name']} (Class {user['class_name']}-{user['division']}) reset successfully"})

if __name__ == "__main__":
    app.run(debug=True)