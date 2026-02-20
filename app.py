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
from collections import defaultdict

# ================= SETUP =================
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")

logger = logging.getLogger(__name__)

# ================= ADMIN CREDENTIALS (env-based) =================
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD_RAW = os.getenv("ADMIN_PASSWORD", "admin123")
# FIX #1: Hash the admin password at startup so comparison is never plaintext
ADMIN_PASSWORD_HASH = generate_password_hash(_ADMIN_PASSWORD_RAW)
del _ADMIN_PASSWORD_RAW  # Remove raw password from memory

# ================= FIX #2: IN-MEMORY RATE LIMITER =================
_rate_limit_store = defaultdict(list)
_rate_limit_lock  = threading.Lock()

RATE_LIMIT_MAX_ATTEMPTS = 10   # max failed attempts
RATE_LIMIT_WINDOW_SECS  = 60   # within this rolling window (seconds)
RATE_LIMIT_LOCKOUT_SECS = 300  # lockout duration after exceeding limit (5 min)

def _get_client_ip():
    """Return the real client IP, respecting Railway / proxy headers."""
    return (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "")
        or request.remote_addr
        or "unknown"
    )

def is_rate_limited(ip: str) -> tuple:
    """Returns (is_limited, seconds_remaining). Cleans up old timestamps on every call."""
    now = time.time()
    with _rate_limit_lock:
        timestamps = _rate_limit_store[ip]
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW_SECS]
        _rate_limit_store[ip] = timestamps
        if len(timestamps) >= RATE_LIMIT_MAX_ATTEMPTS:
            oldest    = timestamps[0]
            remaining = int(RATE_LIMIT_LOCKOUT_SECS - (now - oldest))
            if remaining > 0:
                return True, remaining
            _rate_limit_store[ip] = []
    return False, 0

def record_failed_attempt(ip: str):
    with _rate_limit_lock:
        _rate_limit_store[ip].append(time.time())

def clear_attempts(ip: str):
    """Call on successful login to reset the counter."""
    with _rate_limit_lock:
        _rate_limit_store[ip] = []

def rate_limit_response(seconds_remaining: int):
    minutes  = seconds_remaining // 60
    seconds  = seconds_remaining % 60
    time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
    return jsonify({
        "success": False,
        "message": f"Too many failed attempts. Please wait {time_str} before trying again."
    }), 429

# ================= DIFFICULTY-BASED XP =================
DIFFICULTY_XP = {
    "easy": 1,
    "medium": 2,
    "hard": 5
}

# ================= VALID CLASS / DIVISION OPTIONS =================
VALID_CLASSES    = [str(i) for i in range(1, 11)]
VALID_DIVISIONS  = ["A", "B", "C", "D", "E"]

# ================= USER ID GENERATION =================
def generate_user_id():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    suffix = ''.join(random.choices(chars, k=5))
    return f"GSS-{suffix}"

def generate_unique_user_id(conn):
    for _ in range(20):
        uid = generate_user_id()
        existing = conn.execute(
            'SELECT id FROM users WHERE user_id_code = ?', (uid,)
        ).fetchone()
        if not existing:
            return uid
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
    # Word Puzzle Badges
    {"id": "puzzle_easy",    "name": "Puzzle Starter",    "icon": "🧩", "description": "Solve an Easy Word Puzzle",      "category": "wordpuzzle"},
    {"id": "puzzle_medium",  "name": "Puzzle Pro",        "icon": "🔤", "description": "Solve a Medium Word Puzzle",     "category": "wordpuzzle"},
    {"id": "puzzle_hard",    "name": "Puzzle Master",     "icon": "🏆", "description": "Solve a Hard Word Puzzle",       "category": "wordpuzzle"},
    {"id": "puzzle_50",      "name": "Word Detective",    "icon": "🔍", "description": "Earn 50 XP in Word Puzzle",      "category": "wordpuzzle"},
    # Grammar Badges
    {"id": "grammar_easy",   "name": "Grammar Rookie",    "icon": "📝", "description": "Answer an Easy Grammar question","category": "grammar"},
    {"id": "grammar_medium", "name": "Grammar Wizard",    "icon": "🧙", "description": "Answer a Medium Grammar question","category": "grammar"},
    {"id": "grammar_hard",   "name": "Grammar Champion",  "icon": "🎓", "description": "Answer a Hard Grammar question", "category": "grammar"},
    {"id": "grammar_50",     "name": "Grammar Legend",    "icon": "👑", "description": "Earn 50 XP in Grammar",          "category": "grammar"},
    # Star Badges
    {"id": "stars_5",        "name": "Star Collector",    "icon": "⭐", "description": "Earn 5 stars",                   "category": "stars"},
    {"id": "stars_15",       "name": "Star Gazer",        "icon": "🌠", "description": "Earn 15 stars",                  "category": "stars"},
    {"id": "stars_30",       "name": "Superstar",         "icon": "💫", "description": "Earn 30 stars",                  "category": "stars"},
    # Perfect Score Badges
    {"id": "perfect_repeat", "name": "Flawless Speaker",  "icon": "🎯", "description": "Score 100% in a Repeat session", "category": "perfect"},
    {"id": "perfect_spell",  "name": "Perfect Speller",   "icon": "✨", "description": "Spell all words correctly",      "category": "perfect"},
    {"id": "perfect_puzzle", "name": "Puzzle Ace",        "icon": "🃏", "description": "Solve a puzzle on first try",    "category": "perfect"},
    # Streak Badges
    {"id": "streak_3",       "name": "3-Day Streak",      "icon": "🔥", "description": "Practice 3 days in a row",       "category": "streak"},
    {"id": "streak_7",       "name": "Week Warrior",      "icon": "📅", "description": "Practice 7 days in a row",       "category": "streak"},
    # All-Rounder Badge (requires all 7 modes)
    {"id": "all_modes",      "name": "All-Rounder",       "icon": "🌈", "description": "Earn XP in all 7 modes",         "category": "special"},
]

BADGE_MAP = {b["id"]: b for b in ALL_BADGES}

def check_earned_badges(user_id_code, progress_data, mode=None, difficulty=None, score=None, stars_earned=None, attempt=None):
    conn = get_db_connection()
    existing = conn.execute(
        'SELECT badge_id FROM student_badges WHERE user_id_code = ?', (user_id_code,)
    ).fetchall()
    conn.close()
    already_earned = {row['badge_id'] for row in existing}
    newly_earned = []

    def award(badge_id):
        if badge_id not in already_earned:
            newly_earned.append(badge_id)
            already_earned.add(badge_id)

    total_xp    = progress_data.get('xp', 0)
    conv_xp     = progress_data.get('conversation_xp', 0)
    role_xp     = progress_data.get('roleplay_xp', 0)
    repeat_xp   = progress_data.get('repeat_xp', 0)
    spell_xp    = progress_data.get('spellbee_xp', 0)
    mean_xp     = progress_data.get('meanings_xp', 0)
    puzzle_xp   = progress_data.get('wordpuzzle_xp', 0)
    grammar_xp  = progress_data.get('grammar_xp', 0)
    total_stars = progress_data.get('total_stars', 0)
    streak      = progress_data.get('streak', 0)

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

    if mode == 'wordpuzzle':
        if difficulty == 'easy':   award("puzzle_easy")
        if difficulty == 'medium': award("puzzle_medium")
        if difficulty == 'hard':   award("puzzle_hard")
        if score == 100 and (attempt is None or attempt == 1):
            award("perfect_puzzle")
    if puzzle_xp >= 50: award("puzzle_50")

    if mode == 'grammar':
        if difficulty == 'easy':   award("grammar_easy")
        if difficulty == 'medium': award("grammar_medium")
        if difficulty == 'hard':   award("grammar_hard")
    if grammar_xp >= 50: award("grammar_50")

    if total_stars >= 5:  award("stars_5")
    if total_stars >= 15: award("stars_15")
    if total_stars >= 30: award("stars_30")

    if streak >= 3: award("streak_3")
    if streak >= 7: award("streak_7")

    if conv_xp > 0 and role_xp > 0 and repeat_xp > 0 and spell_xp > 0 and mean_xp > 0 and puzzle_xp > 0 and grammar_xp > 0:
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

def get_session_recent_puzzle_words():
    return session.get('recent_puzzle_words', [])

def set_session_recent_puzzle_words(lst):
    session['recent_puzzle_words'] = lst

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
FEATURE_SEQUENCE = ["conversation", "roleplay", "repeat", "spellbee", "wordpuzzle", "grammar", "meanings"]
XP_PER_UNLOCK = 50

def get_unlocked_features(progress_data):
    unlocked = ["conversation"]
    if progress_data.get('conversation_xp', 0) >= XP_PER_UNLOCK:
        unlocked.append("roleplay")
    if progress_data.get('roleplay_xp', 0) >= XP_PER_UNLOCK:
        unlocked.append("repeat")
    if progress_data.get('repeat_xp', 0) >= XP_PER_UNLOCK:
        unlocked.append("spellbee")
    if progress_data.get('spellbee_xp', 0) >= XP_PER_UNLOCK:
        unlocked.append("wordpuzzle")
    if progress_data.get('wordpuzzle_xp', 0) >= XP_PER_UNLOCK:
        unlocked.append("grammar")
    if progress_data.get('grammar_xp', 0) >= XP_PER_UNLOCK:
        unlocked.append("meanings")
    return unlocked

def get_next_unlock(progress_data):
    chain = [
        ("roleplay",   "conversation"),
        ("repeat",     "roleplay"),
        ("spellbee",   "repeat"),
        ("wordpuzzle", "spellbee"),
        ("grammar",    "wordpuzzle"),
        ("meanings",   "grammar"),
    ]
    for feature, current_mode in chain:
        current_xp = progress_data.get(f"{current_mode}_xp", 0)
        if current_xp < XP_PER_UNLOCK:
            return {
                'feature':      feature,
                'current_mode': current_mode,
                'xp_needed':    XP_PER_UNLOCK - current_xp,
                'current_xp':   current_xp,
            }
    return None

# ================= STREAK CALCULATION =================
def calculate_streak(user_id_code):
    conn = get_db_connection()
    rows = conn.execute(
        '''SELECT DISTINCT date(date) as day FROM activity_log
           WHERE user_id_code = ? ORDER BY day DESC''',
        (user_id_code,)
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

def has_completed_daily(user_id_code):
    conn = get_db_connection()
    row = conn.execute(
        '''SELECT id FROM activity_log WHERE user_id_code = ? AND mode = 'daily'
           AND date(date) = date('now')''',
        (user_id_code,)
    ).fetchone()
    conn.close()
    return row is not None

# ================= PERSONAL SUGGESTIONS ENGINE =================

MODE_META = {
    "conversation": {
        "icon": "💬", "label": "Conversation",
        "tips": [
            "Try speaking in longer sentences during Conversation practice.",
            "When chatting, describe your feelings in detail — use words like 'excited', 'nervous', or 'curious'.",
            "Practice asking follow-up questions in Conversation mode to boost your score.",
            "Read a short English passage aloud before each Conversation session to warm up.",
        ],
        "praise": "Your Conversation skills are really strong! Keep the streak going.",
    },
    "roleplay": {
        "icon": "🎭", "label": "Roleplay",
        "tips": [
            "In Roleplay, stay in character! Pretend the scenario is real and respond naturally.",
            "Before Roleplay, think about how the character (teacher/friend/interviewer) would speak.",
            "Use polite phrases like 'Could you please...', 'I believe...' in Interviewer roleplay.",
            "For the Teacher roleplay, try answering in complete sentences with reasons.",
        ],
        "praise": "You're doing great in Roleplay! Your character responses are natural.",
    },
    "repeat": {
        "icon": "🔁", "label": "Repeat After Me",
        "tips": [
            "In Repeat mode, listen to the full sentence first, then speak clearly and at a steady pace.",
            "Try the Slow Audio button to catch every word before repeating.",
            "Focus on getting the ending of each sentence right — that's where most mistakes happen.",
            "Record yourself and compare with the original to spot pronunciation differences.",
        ],
        "praise": "Excellent pronunciation work in Repeat mode! You're a natural speaker.",
    },
    "spellbee": {
        "icon": "🐝", "label": "Spell Bee",
        "tips": [
            "For Spell Bee, break words into syllables — e.g., 'beau-ti-ful' is easier to spell that way.",
            "Listen to the word two or three times before typing your spelling.",
            "Learn common word patterns like '-tion', '-ough', '-ight' to spell faster.",
            "Practice with Easy words daily to build spelling confidence before tackling Hard words.",
        ],
        "praise": "Your spelling is fantastic! You have a sharp eye for letters.",
    },
    "wordpuzzle": {
        "icon": "🧩", "label": "Word Puzzle",
        "tips": [
            "In Word Puzzle, read the hint carefully — it always points to the right answer.",
            "Try sorting the scrambled letters alphabetically in your head first.",
            "Look for vowels (A, E, I, O, U) first — they're the skeleton of every word.",
            "If stuck, use the category clue alongside the hint for extra guidance.",
        ],
        "praise": "You're a Word Puzzle champion! Your lateral thinking is impressive.",
    },
    "grammar": {
        "icon": "📝", "label": "Grammar",
        "tips": [
            "For Grammar, read the full sentence aloud — the correct option usually sounds natural.",
            "Remember: 'He/She/It' → always use verbs ending in -s (goes, runs, eats).",
            "Study subject-verb agreement: singular subjects take singular verbs.",
            "Practice Medium Grammar questions after mastering Easy ones — the jump is big!",
        ],
        "praise": "Your grammar instincts are sharp! Keep challenging yourself.",
    },
    "meanings": {
        "icon": "📖", "label": "Word Meanings",
        "tips": [
            "In Meanings mode, make a small notebook of new words and review them daily.",
            "Try using each new word in a sentence of your own right after looking it up.",
            "Group words by theme (feelings, nature, actions) to remember them better.",
            "Challenge yourself to learn 3 new words per day in Meanings mode.",
        ],
        "praise": "You love learning new words! Your vocabulary is growing beautifully.",
    },
}

def generate_personal_suggestions(mode_stats, weak_sessions, progress_data, streak):
    """
    Generates a list of personal suggestion objects based on the student's
    performance data. Each suggestion has: type, icon, title, message, priority.
    """
    suggestions = []

    total_xp     = progress_data.get('xp', 0)
    total_stars  = progress_data.get('total_stars', 0)
    total_sessions = progress_data.get('total_sessions', 0)

    # ── 1. Streak suggestions ──────────────────────────────────────────────────
    if streak == 0:
        suggestions.append({
            "type": "streak",
            "icon": "🔥",
            "priority": 10,
            "title": "Start Your Streak Today!",
            "message": "You haven't practiced recently. Log in and complete any activity today to start a streak. Even 5 minutes counts!",
            "action": "Practice Now",
            "action_link": "/main",
        })
    elif streak < 3:
        suggestions.append({
            "type": "streak",
            "icon": "🔥",
            "priority": 8,
            "title": f"Keep Going — {streak}-Day Streak!",
            "message": f"You're on a {streak}-day streak. Practice today to reach 3 days and earn the 🔥 3-Day Streak badge!",
            "action": "Continue Streak",
            "action_link": "/main",
        })
    elif streak >= 7:
        suggestions.append({
            "type": "praise",
            "icon": "🏆",
            "priority": 2,
            "title": f"Amazing {streak}-Day Streak!",
            "message": "You're incredibly consistent! Your daily practice habit is the secret to speaking great English.",
            "action": None,
            "action_link": None,
        })

    # ── 2. New learner — no data yet ──────────────────────────────────────────
    if total_sessions == 0:
        suggestions.append({
            "type": "info",
            "icon": "👋",
            "priority": 9,
            "title": "Welcome! Let's Get Started",
            "message": "You haven't completed any sessions yet. Start with Conversation mode — it's the most fun way to begin!",
            "action": "Start Conversation",
            "action_link": "/main",
        })
        return suggestions  # nothing else to analyse yet

    # ── 3. Analyse each mode's average score ──────────────────────────────────
    struggling_modes  = []  # avg < 60
    improving_modes   = []  # avg 60–74
    strong_modes      = []  # avg >= 75
    untried_modes     = []  # no attempts

    all_modes = ["conversation", "roleplay", "repeat", "spellbee", "wordpuzzle", "grammar", "meanings"]
    for mode in all_modes:
        stats = mode_stats.get(mode)
        if not stats or stats.get('totalAttempts', 0) == 0:
            untried_modes.append(mode)
        else:
            avg = stats.get('avgScore', 0)
            if avg < 60:
                struggling_modes.append((mode, avg))
            elif avg < 75:
                improving_modes.append((mode, avg))
            else:
                strong_modes.append((mode, avg))

    # Suggestions for struggling modes (priority: highest)
    for mode, avg in sorted(struggling_modes, key=lambda x: x[1]):
        meta = MODE_META.get(mode, {})
        tip  = random.choice(meta.get('tips', ["Practice this mode a little every day."]))
        suggestions.append({
            "type": "struggle",
            "icon": meta.get('icon', '❗'),
            "priority": 9,
            "title": f"Needs Work: {meta.get('label', mode.title())} ({int(avg)}% avg)",
            "message": tip,
            "action": f"Practice {meta.get('label', mode.title())}",
            "action_link": "/main",
        })

    # Suggestions for improving modes
    for mode, avg in sorted(improving_modes, key=lambda x: x[1]):
        meta = MODE_META.get(mode, {})
        tip  = random.choice(meta.get('tips', ["You're getting better — keep it up!"]))
        suggestions.append({
            "type": "improve",
            "icon": meta.get('icon', '📈'),
            "priority": 5,
            "title": f"Almost There: {meta.get('label', mode.title())} ({int(avg)}% avg)",
            "message": tip,
            "action": f"Improve {meta.get('label', mode.title())}",
            "action_link": "/main",
        })

    # Praise for strong modes
    for mode, avg in sorted(strong_modes, key=lambda x: -x[1])[:2]:  # top 2 only
        meta = MODE_META.get(mode, {})
        suggestions.append({
            "type": "praise",
            "icon": "⭐",
            "priority": 1,
            "title": f"Excellent: {meta.get('label', mode.title())} ({int(avg)}% avg)",
            "message": meta.get('praise', "You're doing really well here! Keep it up."),
            "action": None,
            "action_link": None,
        })

    # ── 4. Untried unlocked modes ──────────────────────────────────────────────
    unlocked = get_unlocked_features(progress_data)
    for mode in untried_modes:
        if mode in unlocked:
            meta = MODE_META.get(mode, {})
            suggestions.append({
                "type": "explore",
                "icon": meta.get('icon', '🆕'),
                "priority": 4,
                "title": f"Try It: {meta.get('label', mode.title())} is Unlocked!",
                "message": f"You've unlocked {meta.get('label', mode.title())} but haven't tried it yet. Give it a go — you might love it!",
                "action": f"Try {meta.get('label', mode.title())}",
                "action_link": "/main",
            })

    # ── 5. Recent failures (last 3 weak sessions) ─────────────────────────────
    recent_weak = weak_sessions[:3]
    for ws in recent_weak:
        mode = ws.get('mode', '')
        score = ws.get('score', 0)
        meta = MODE_META.get(mode, {})
        if meta:
            tip = random.choice(meta.get('tips', ["Review this mode and try again!"]))
            suggestions.append({
                "type": "retry",
                "icon": "🔄",
                "priority": 6,
                "title": f"Retry Recommended: {meta.get('label', mode.title())} ({int(score)}%)",
                "message": tip,
                "action": f"Retry {meta.get('label', mode.title())}",
                "action_link": "/main",
            })

    # ── 6. XP milestone encouragement ─────────────────────────────────────────
    xp_milestones = [(25, 25), (50, 50), (100, 100), (250, 250), (500, 500)]
    next_milestone = None
    for threshold, _ in xp_milestones:
        if total_xp < threshold:
            next_milestone = threshold
            break
    if next_milestone:
        gap = next_milestone - total_xp
        suggestions.append({
            "type": "milestone",
            "icon": "⚡",
            "priority": 3,
            "title": f"Only {gap} XP to {next_milestone} XP Milestone!",
            "message": f"You have {total_xp} XP. Earn {gap} more to hit {next_milestone} XP and unlock a new badge. You're so close!",
            "action": "Earn XP Now",
            "action_link": "/main",
        })

    # ── 7. Low-star suggestion ─────────────────────────────────────────────────
    if total_stars < 5 and total_sessions > 3:
        suggestions.append({
            "type": "stars",
            "icon": "⭐",
            "priority": 4,
            "title": "Collect More Stars!",
            "message": "You have fewer than 5 stars. Aim for 90%+ scores in Repeat and Spell Bee — those modes give the most stars!",
            "action": "Practice for Stars",
            "action_link": "/main",
        })

    # Sort by priority descending, limit to 6
    suggestions.sort(key=lambda x: -x['priority'])
    return suggestions[:6]


# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect('students.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK(role IN ('student', 'teacher', 'admin')),
            name TEXT NOT NULL,
            user_id_code TEXT UNIQUE,
            roll_no TEXT,
            class_name TEXT,
            division TEXT,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            is_approved INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            approval_note TEXT,
            approved_by TEXT,
            approved_at TIMESTAMP,
            reset_token TEXT,
            reset_token_expiry TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(roll_no, class_name, division)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_username TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            target_name TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            user_id_code TEXT NOT NULL UNIQUE,
            roll_no TEXT,
            class_name TEXT,
            division TEXT,
            xp INTEGER DEFAULT 0,
            conversation_xp INTEGER DEFAULT 0,
            roleplay_xp INTEGER DEFAULT 0,
            repeat_xp INTEGER DEFAULT 0,
            spellbee_xp INTEGER DEFAULT 0,
            meanings_xp INTEGER DEFAULT 0,
            wordpuzzle_xp INTEGER DEFAULT 0,
            grammar_xp INTEGER DEFAULT 0,
            total_stars INTEGER DEFAULT 0,
            total_sessions INTEGER DEFAULT 0,
            average_accuracy REAL DEFAULT 0,
            streak INTEGER DEFAULT 0,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id_code TEXT NOT NULL,
            roll_no TEXT,
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
            user_id_code TEXT NOT NULL,
            roll_no TEXT,
            class_name TEXT,
            division TEXT,
            badge_id TEXT NOT NULL,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id_code, badge_id)
        )
    ''')

    def col_exists(table, col):
        rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in rows)

    for tbl in ['student_progress', 'activity_log', 'student_badges']:
        if not col_exists(tbl, 'user_id_code'):
            c.execute(f'ALTER TABLE {tbl} ADD COLUMN user_id_code TEXT')

    for tbl in ['users', 'student_progress', 'activity_log', 'student_badges']:
        if not col_exists(tbl, 'class_name'):
            c.execute(f'ALTER TABLE {tbl} ADD COLUMN class_name TEXT')
        if not col_exists(tbl, 'division'):
            c.execute(f'ALTER TABLE {tbl} ADD COLUMN division TEXT')

    if not col_exists('users', 'user_id_code'):
        c.execute('ALTER TABLE users ADD COLUMN user_id_code TEXT')
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

    for col, definition in [
        ('is_approved', 'INTEGER DEFAULT 0'),
        ('is_active', 'INTEGER DEFAULT 1'),
        ('approval_note', 'TEXT'),
        ('approved_by', 'TEXT'),
        ('approved_at', 'TIMESTAMP'),
    ]:
        if not col_exists('users', col):
            c.execute(f'ALTER TABLE users ADD COLUMN {col} {definition}')

    c.execute("UPDATE users SET is_approved=1 WHERE role='student' AND (is_approved IS NULL OR is_approved=0)")
    c.execute("UPDATE users SET is_approved=1 WHERE role='teacher' AND (is_approved IS NULL OR is_approved=0)")

    if not col_exists('student_progress', 'conversation_xp'):
        c.execute('ALTER TABLE student_progress ADD COLUMN conversation_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN roleplay_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN repeat_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN spellbee_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN meanings_xp INTEGER DEFAULT 0')

    if not col_exists('student_progress', 'wordpuzzle_xp'):
        c.execute('ALTER TABLE student_progress ADD COLUMN wordpuzzle_xp INTEGER DEFAULT 0')
    if not col_exists('student_progress', 'grammar_xp'):
        c.execute('ALTER TABLE student_progress ADD COLUMN grammar_xp INTEGER DEFAULT 0')
    if not col_exists('student_progress', 'streak'):
        c.execute('ALTER TABLE student_progress ADD COLUMN streak INTEGER DEFAULT 0')
    if not col_exists('users', 'reset_token'):
        c.execute('ALTER TABLE users ADD COLUMN reset_token TEXT')
        c.execute('ALTER TABLE users ADD COLUMN reset_token_expiry TIMESTAMP')

    c.execute('''
        UPDATE student_progress SET user_id_code = (
            SELECT u.user_id_code FROM users u
            WHERE u.roll_no = student_progress.roll_no
              AND u.class_name = student_progress.class_name
              AND u.division = student_progress.division
              AND u.role = 'student'
            LIMIT 1
        )
        WHERE (user_id_code IS NULL OR user_id_code = '')
          AND roll_no IS NOT NULL
    ''')

    c.execute('''
        UPDATE activity_log SET user_id_code = (
            SELECT u.user_id_code FROM users u
            WHERE u.roll_no = activity_log.roll_no
              AND u.class_name = activity_log.class_name
              AND u.division = activity_log.division
              AND u.role = 'student'
            LIMIT 1
        )
        WHERE (user_id_code IS NULL OR user_id_code = '')
          AND roll_no IS NOT NULL
    ''')

    c.execute('''
        UPDATE student_badges SET user_id_code = (
            SELECT u.user_id_code FROM users u
            WHERE u.roll_no = student_badges.roll_no
              AND u.class_name = student_badges.class_name
              AND u.division = student_badges.division
              AND u.role = 'student'
            LIMIT 1
        )
        WHERE (user_id_code IS NULL OR user_id_code = '')
          AND roll_no IS NOT NULL
    ''')

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
                c.execute('''
                    CREATE TABLE IF NOT EXISTS users_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        role TEXT NOT NULL CHECK(role IN ('student', 'teacher', 'admin')),
                        name TEXT NOT NULL,
                        user_id_code TEXT UNIQUE,
                        roll_no TEXT,
                        class_name TEXT,
                        division TEXT,
                        email TEXT UNIQUE,
                        password_hash TEXT NOT NULL,
                        is_approved INTEGER DEFAULT 0,
                        is_active INTEGER DEFAULT 1,
                        approval_note TEXT,
                        approved_by TEXT,
                        approved_at TIMESTAMP,
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

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login_page'))
        return f(*args, **kwargs)
    return decorated_function

def get_db_connection():
    conn = sqlite3.connect('students.db')
    conn.row_factory = sqlite3.Row
    return conn

def log_admin_action(action, target_type=None, target_id=None, target_name=None, details=None):
    admin_username = session.get('admin_username', 'admin')
    conn = get_db_connection()
    conn.execute(
        '''INSERT INTO admin_audit_log
           (admin_username, action, target_type, target_id, target_name, details)
           VALUES (?,?,?,?,?,?)''',
        (admin_username, action, target_type, str(target_id) if target_id else None,
         target_name, details)
    )
    conn.commit()
    conn.close()

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
def get_conversation_context():
    return session.get('conversation_context', '')

def set_conversation_context(ctx):
    session['conversation_context'] = ctx[-2000:]

def get_conversation_turn_count():
    return session.get('conversation_turn_count', 0)

def increment_conversation_turn():
    count = get_conversation_turn_count() + 1
    session['conversation_turn_count'] = count
    return count

def get_conversation_topic():
    return session.get('conversation_topic', '')

def set_conversation_topic(topic):
    session['conversation_topic'] = topic

def reset_conversation_context():
    session.pop('conversation_context', None)
    session.pop('conversation_topic', None)
    session.pop('conversation_turn_count', None)

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

def detect_intent(text):
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

    def format(self):
        return (
            f"CORRECT: {self.corrected}\n"
            f"ANSWER: {self.answer}\n"
            f"PRAISE: {self.praise}\n"
            f"QUESTION: {self.question}"
        )

    def to_speech_text(self):
        parts = []
        if self.intent == "greeting":
            if self.answer: parts.append(self.answer)
            if self.question: parts.append(self.question)
            return " ".join(parts)
        if self.intent == "question":
            if self.corrected and self.corrected.lower() != self.raw.lower().strip("?.! "):
                parts.append(self.corrected + ".")
            if self.answer: parts.append(self.answer)
            if self.praise: parts.append(self.praise)
            if self.question: parts.append(self.question)
            return " ".join(parts)
        if self.corrected: parts.append(self.corrected + ".")
        if self.answer: parts.append(self.answer)
        if self.praise: parts.append(self.praise)
        if self.question: parts.append(self.question)
        return " ".join(parts)

    def to_display_dict(self):
        return {
            "correct":  self.corrected,
            "answer":   self.answer,
            "praise":   self.praise,
            "question": self.question,
            "intent":   self.intent,
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

def _parse_coach_response(raw):
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

def _build_coach_messages(child_text, context, intent, topic):
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

def _extract_topic_from_text(text, intent):
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
def _build_roleplay_messages(child_text, context, roleplay_type, suggested_question, intent, topic):
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

def roleplay_coach(child_text, roleplay_type, *, model="llama-3.1-8b-instant",
                   temperature=0.5, max_tokens=200, fallback_on_error=True):
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
        child_text.strip(), context, roleplay_type, suggested_question, intent, topic
    )
    try:
        completion = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
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

# ================= WORD PUZZLE DATA & LOGIC =================
WORD_PUZZLE_WORDS = {
    "easy": [
        {"word": "CAT",   "hint": "A furry pet that meows",           "category": "Animals"},
        {"word": "DOG",   "hint": "A loyal pet that barks",           "category": "Animals"},
        {"word": "SUN",   "hint": "It shines in the sky during day",  "category": "Nature"},
        {"word": "BUS",   "hint": "A big vehicle for many people",    "category": "Transport"},
        {"word": "CUP",   "hint": "You drink tea or coffee from this","category": "Objects"},
        {"word": "MAP",   "hint": "Shows you directions and places",  "category": "Objects"},
        {"word": "HEN",   "hint": "A female chicken that lays eggs",  "category": "Animals"},
        {"word": "PEN",   "hint": "You write with this",             "category": "Objects"},
        {"word": "NET",   "hint": "Used to catch fish or play tennis","category": "Objects"},
        {"word": "FAN",   "hint": "Spins to keep you cool",          "category": "Objects"},
        {"word": "JAM",   "hint": "Sweet spread on bread",           "category": "Food"},
        {"word": "MUD",   "hint": "Wet dirty earth",                 "category": "Nature"},
        {"word": "OWL",   "hint": "A wise bird that hoots at night", "category": "Animals"},
        {"word": "ANT",   "hint": "A tiny insect that lives in colonies", "category": "Animals"},
        {"word": "EGG",   "hint": "Oval food laid by a bird",        "category": "Food"},
        {"word": "BAG",   "hint": "You carry your books in this",    "category": "Objects"},
        {"word": "COW",   "hint": "Farm animal that gives milk",     "category": "Animals"},
        {"word": "FOX",   "hint": "A clever wild animal with a bushy tail", "category": "Animals"},
        {"word": "BEE",   "hint": "Insect that makes honey",        "category": "Animals"},
        {"word": "ICE",   "hint": "Frozen water",                   "category": "Nature"},
    ],
    "medium": [
        {"word": "TIGER",  "hint": "A big striped wild cat",              "category": "Animals"},
        {"word": "APPLE",  "hint": "A red or green fruit",               "category": "Food"},
        {"word": "TABLE",  "hint": "Furniture you eat or work on",       "category": "Objects"},
        {"word": "WATER",  "hint": "Liquid we drink every day",          "category": "Nature"},
        {"word": "CLOUD",  "hint": "White fluffy thing in the sky",      "category": "Nature"},
        {"word": "BREAD",  "hint": "Baked food made from flour",         "category": "Food"},
        {"word": "FLOWER", "hint": "Pretty plant with colourful petals", "category": "Nature"},
        {"word": "BRIDGE", "hint": "Structure built over a river",       "category": "Places"},
        {"word": "CANDLE", "hint": "Gives light when it burns",          "category": "Objects"},
        {"word": "JUNGLE", "hint": "Dense tropical forest",              "category": "Nature"},
        {"word": "MARKET", "hint": "Place where things are bought/sold", "category": "Places"},
        {"word": "PENCIL", "hint": "Used to write and can be erased",    "category": "Objects"},
        {"word": "RABBIT", "hint": "Fluffy animal with long ears",       "category": "Animals"},
        {"word": "CASTLE", "hint": "A large old fortress of kings",      "category": "Places"},
        {"word": "BUTTER", "hint": "Yellow spread made from milk",       "category": "Food"},
        {"word": "GARDEN", "hint": "Outdoor space to grow plants",       "category": "Nature"},
        {"word": "MIRROR", "hint": "You see your reflection in this",    "category": "Objects"},
        {"word": "FINGER", "hint": "Part of your hand",                  "category": "Body"},
        {"word": "PLANET", "hint": "A large object orbiting a star",     "category": "Space"},
        {"word": "ROCKET", "hint": "Vehicle that travels to space",      "category": "Space"},
    ],
    "hard": [
        {"word": "ELEPHANT",    "hint": "Largest land animal with a trunk",          "category": "Animals"},
        {"word": "BEAUTIFUL",   "hint": "Very pleasing to look at",                  "category": "Adjectives"},
        {"word": "CHOCOLATE",   "hint": "Sweet brown treat loved by children",       "category": "Food"},
        {"word": "BUTTERFLY",   "hint": "Insect with colourful wings",               "category": "Animals"},
        {"word": "ADVENTURE",   "hint": "An exciting journey or experience",         "category": "Concepts"},
        {"word": "DICTIONARY",  "hint": "A book that explains word meanings",        "category": "Objects"},
        {"word": "STRAWBERRY",  "hint": "Small red fruit with seeds on outside",     "category": "Food"},
        {"word": "RESTAURANT",  "hint": "A place where you eat and pay for food",    "category": "Places"},
        {"word": "IMAGINATION", "hint": "Ability to form pictures in your mind",     "category": "Concepts"},
        {"word": "CELEBRATION", "hint": "A happy event to mark a special occasion",  "category": "Concepts"},
        {"word": "BASKETBALL",  "hint": "Sport played by throwing a ball into a hoop","category": "Sports"},
        {"word": "COMFORTABLE", "hint": "Feeling relaxed and at ease",               "category": "Adjectives"},
        {"word": "SPECTACULAR", "hint": "Extremely impressive or dramatic",          "category": "Adjectives"},
        {"word": "INCREDIBLE",  "hint": "Impossible to believe; amazing",            "category": "Adjectives"},
        {"word": "UNDERSTAND",  "hint": "To know the meaning of something",          "category": "Concepts"},
    ],
}

def scramble_word(word):
    letters = list(word)
    for _ in range(20):
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:
            return scrambled
    return word[::-1]

# ================= GRAMMAR DATA & LOGIC =================
GRAMMAR_QUESTIONS = {
    "easy": [
        ("She ___ to school every day.",
         ["go", "goes", "going", "gone"], 1,
         "'She' is third-person singular, so we use 'goes'."),
        ("They ___ playing in the park.",
         ["is", "am", "are", "be"], 2,
         "'They' takes 'are' as the helping verb."),
        ("I ___ a student.",
         ["is", "am", "are", "be"], 1,
         "Use 'am' with 'I'."),
        ("He ___ not eat vegetables.",
         ["do", "does", "did", "doing"], 1,
         "Use 'does' with third-person singular (he/she/it)."),
        ("The cat ___ on the mat.",
         ["sit", "sits", "sitting", "sat"], 1,
         "Third-person singular present uses 'sits'."),
        ("We ___ happy today.",
         ["is", "am", "are", "be"], 2,
         "'We' takes 'are'."),
        ("___ you like ice cream?",
         ["Do", "Does", "Did", "Is"], 0,
         "Use 'Do' for questions with 'you'."),
        ("There ___ two apples on the table.",
         ["is", "am", "are", "were"], 2,
         "Two apples is plural, so use 'are'."),
        ("She has ___ brothers.",
         ["two", "a", "an", "much"], 0,
         "'Two' is correct for countable nouns like brothers."),
        ("The dog ___ loudly.",
         ["bark", "barks", "barking", "barked"], 1,
         "Third-person singular present: 'barks'."),
        ("My mother ___ tea every morning.",
         ["drink", "drinks", "drinking", "drank"], 1,
         "Third-person singular present: 'drinks'."),
        ("The children ___ in the garden.",
         ["play", "plays", "playing", "played"], 0,
         "'Children' is plural, so use 'play'."),
    ],
    "medium": [
        ("Neither John nor his friends ___ coming.",
         ["is", "are", "was", "were"], 1,
         "When 'neither…nor' pairs singular with plural, the verb agrees with the nearer subject ('friends' → 'are')."),
        ("She has been waiting ___ two hours.",
         ["since", "for", "from", "during"], 1,
         "Use 'for' with a period of time (two hours)."),
        ("If I ___ rich, I would travel the world.",
         ["am", "was", "were", "be"], 2,
         "In hypothetical conditionals, use 'were' for all persons."),
        ("The teacher, along with the students, ___ going on a trip.",
         ["are", "is", "were", "have"], 1,
         "The subject is 'teacher' (singular); 'along with' is a phrase, not a conjunction."),
        ("He asked me where I ___ going.",
         ["am", "was", "were", "have"], 1,
         "Reported speech shifts tense: present 'am' → past 'was'."),
        ("They have ___ their homework already.",
         ["finish", "finishing", "finished", "finishes"], 2,
         "'Have' + past participle forms present perfect: 'have finished'."),
        ("___ of the two books is yours?",
         ["Which", "What", "Who", "Whom"], 0,
         "Use 'Which' when choosing between a limited set."),
        ("She is ___ than her sister.",
         ["tall", "more tall", "taller", "tallest"], 2,
         "Comparative of a one-syllable adjective: add '-er'."),
        ("The news ___ surprising.",
         ["were", "are", "is", "have"], 2,
         "'News' is uncountable and takes a singular verb."),
        ("I wish I ___ a bird.",
         ["am", "was", "were", "be"], 2,
         "After 'wish', use subjunctive 'were' for all persons."),
        ("He is good ___ mathematics.",
         ["in", "at", "on", "for"], 1,
         "We say 'good at' a subject."),
        ("She ___ in this city since 2010.",
         ["lives", "is living", "has lived", "lived"], 2,
         "Use present perfect for an action that started in the past and continues now."),
    ],
    "hard": [
        ("___ he studied harder, he would have passed.",
         ["If", "Had", "Has", "Have"], 1,
         "Inverted third conditional: 'Had he studied…' = 'If he had studied…'"),
        ("The committee ___ divided in their opinions.",
         ["is", "are", "was", "were"], 1,
         "When a collective noun acts as individuals, use the plural verb."),
        ("No sooner ___ he left than it started raining.",
         ["had", "has", "have", "did"], 0,
         "'No sooner…than' takes the past perfect: 'had he left'."),
        ("She is one of those students who ___ always on time.",
         ["is", "are", "was", "were"], 1,
         "The antecedent of 'who' is 'students' (plural), so use 'are'."),
        ("It is high time we ___ the meeting.",
         ["start", "started", "have started", "starts"], 1,
         "'It is high time' is followed by past tense (subjunctive mood)."),
        ("The data ___ been analysed by the research team.",
         ["has", "have", "had", "is"], 1,
         "'Data' is treated as plural in formal usage → 'have'."),
        ("Hardly ___ she entered when the phone rang.",
         ["had", "has", "did", "does"], 0,
         "'Hardly…when' inverts subject and auxiliary: 'Hardly had she entered…'"),
        ("Each of the boys ___ done his homework.",
         ["have", "has", "had", "having"], 1,
         "'Each' is singular → 'has'."),
        ("The police ___ arrested the suspect.",
         ["has", "have", "is", "was"], 1,
         "'Police' is a plural noun → 'have'."),
        ("___ you arrive early, please wait outside.",
         ["Should", "Would", "Could", "Shall"], 0,
         "Conditional inversion: 'Should you arrive…' = 'If you should arrive…'"),
        ("The jury ___ unable to reach a verdict.",
         ["was", "were", "is", "are"], 1,
         "Here 'jury' refers to members acting individually → plural 'were'."),
        ("He suggested that she ___ a doctor.",
         ["see", "sees", "saw", "would see"], 0,
         "After 'suggest that', use bare infinitive (subjunctive): 'she see'."),
    ],
}

def get_grammar_question(difficulty="easy"):
    questions = GRAMMAR_QUESTIONS.get(difficulty, GRAMMAR_QUESTIONS["easy"])
    recent_key = f"recent_grammar_{difficulty}"
    recent = session.get(recent_key, [])
    available = [i for i in range(len(questions)) if i not in recent]
    if not available:
        recent = []
        available = list(range(len(questions)))
    idx = random.choice(available)
    recent.append(idx)
    if len(recent) > 6:
        recent = recent[-6:]
    session[recent_key] = recent
    q = questions[idx]
    return {
        "question":      q[0],
        "options":       q[1],
        "correct_index": q[2],
        "explanation":   q[3],
        "difficulty":    difficulty,
        "index":         idx,
    }

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
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login_page'))

@app.route("/login")
def login_page():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login():
    # FIX #2: Rate limit login attempts
    ip = _get_client_ip()
    limited, seconds_left = is_rate_limited(ip)
    if limited:
        return rate_limit_response(seconds_left)

    data = request.json
    role = data.get("role", "student")
    password = data.get("password")
    conn = get_db_connection()
    if role == "student":
        user_id_code = data.get("userIdCode", "").strip().upper()
        if not user_id_code or not password:
            conn.close()
            return jsonify({"success": False, "message": "Please provide your User ID and password."})
        user = conn.execute(
            'SELECT * FROM users WHERE role=? AND user_id_code=?', (role, user_id_code)
        ).fetchone()
    else:
        email = data.get("email")
        if not email or not password:
            conn.close()
            return jsonify({"success": False, "message": "Please provide email and password"})
        user = conn.execute('SELECT * FROM users WHERE role=? AND email=?', (role, email)).fetchone()
    conn.close()

    if user and check_password_hash(user['password_hash'], password):
        if role == "teacher":
            if not user['is_approved']:
                return jsonify({
                    "success": False,
                    "message": "Your teacher account is pending admin approval. Please contact the administrator."
                })
            if not user['is_active']:
                return jsonify({
                    "success": False,
                    "message": "Your account has been deactivated. Please contact the administrator."
                })
        if role == "student" and not user['is_active']:
            return jsonify({
                "success": False,
                "message": "Your account has been deactivated. Please contact your teacher or administrator."
            })

        # Successful login — clear rate limit counter
        clear_attempts(ip)
        session['user_id']  = user['id']
        session['name']     = user['name']
        session['role']     = user['role']
        session.pop('conversation_context', None)
        session.pop('recent_sentences', None)
        session.pop('recent_words', None)
        session.pop('recent_puzzle_words', None)
        session.pop('conversation_turn_count', None)
        session.pop('conversation_topic', None)
        session.pop('current_roleplay_type', None)
        if role == "student":
            session['roll_no']      = user['roll_no']
            session['class_name']   = user['class_name']
            session['division']     = user['division']
            session['student_name'] = user['name']
            session['user_id_code'] = user['user_id_code']
            conn2 = get_db_connection()
            conn2.execute('INSERT INTO student_sessions (student_id) VALUES (?)', (user['id'],))
            conn2.commit()
            conn2.close()
        else:
            session['email'] = user['email']
        return jsonify({"success": True, "message": "Login successful", "name": user['name']})
    else:
        record_failed_attempt(ip)
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
                return jsonify({"success": False, "message": f"Roll number {roll_no} is already registered in Class {class_name}-{division}"})
            new_user_id = generate_unique_user_id(conn)
            conn.execute(
                'INSERT INTO users (role, name, user_id_code, roll_no, class_name, division, password_hash, is_approved, is_active) VALUES (?,?,?,?,?,?,?,1,1)',
                (role, name, new_user_id, roll_no, class_name, division, password_hash)
            )
            conn.execute(
                '''INSERT INTO student_progress
                   (user_id_code, roll_no, class_name, division, xp, conversation_xp, roleplay_xp,
                    repeat_xp, spellbee_xp, meanings_xp, wordpuzzle_xp, grammar_xp,
                    total_stars, streak, total_sessions)
                   VALUES (?,?,?,?,0,0,0,0,0,0,0,0,0,0,0)''',
                (new_user_id, roll_no, class_name, division)
            )
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "Account created successfully", "userIdCode": new_user_id})
        else:
            email = data.get("email", "").strip()
            if not email:
                conn.close()
                return jsonify({"success": False, "message": "Email is required"})
            existing = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
            if existing:
                conn.close()
                return jsonify({"success": False, "message": "Email already registered"})
            conn.execute(
                'INSERT INTO users (role, name, email, password_hash, is_approved, is_active) VALUES (?,?,?,?,0,1)',
                (role, name, email, password_hash)
            )
            conn.commit()
            conn.close()
            return jsonify({
                "success": True,
                "message": "Teacher account created! Your account is pending admin approval. You will be able to login once approved."
            })
    except Exception as e:
        conn.close()
        print(f"Signup error: {e}")
        return jsonify({"success": False, "message": "Error creating account"})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route("/verify_identity", methods=["POST"])
def verify_identity():
    # FIX #2: Rate limit identity verification
    ip = _get_client_ip()
    limited, seconds_left = is_rate_limited(ip)
    if limited:
        return rate_limit_response(seconds_left)

    data         = request.json
    user_id_code = data.get("userIdCode", "").strip().upper()
    name         = data.get("name", "").strip()
    roll_no      = data.get("rollNo", "").strip()
    class_name   = data.get("className", "").strip()
    division     = data.get("division", "").strip().upper()
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
           FROM users WHERE user_id_code = ? AND role = 'student'
           AND class_name = ? AND division = ?''',
        (user_id_code, class_name, division)
    ).fetchone()
    conn.close()
    if not user:
        record_failed_attempt(ip)
        return jsonify({"success": False, "message": "Details do not match our records. Please check your User ID, Class, and Division."})
    if user['roll_no'].strip() != roll_no:
        record_failed_attempt(ip)
        return jsonify({"success": False, "message": "Details do not match our records. Please check your roll number."})
    if user['name'].strip().lower() != name.lower():
        record_failed_attempt(ip)
        return jsonify({"success": False, "message": "The name you entered does not match our records."})
    clear_attempts(ip)
    return jsonify({"success": True, "studentName": user['name'], "message": "Identity verified successfully."})

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
        'SELECT * FROM users WHERE user_id_code=? AND role=?', (user_id_code, 'student')
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
    if 'user_id_code' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    user_id_code = session['user_id_code']
    user_id      = session['user_id']
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM activity_log WHERE user_id_code=?', (user_id_code,))
        conn.execute('DELETE FROM student_progress WHERE user_id_code=?', (user_id_code,))
        conn.execute('DELETE FROM student_badges WHERE user_id_code=?', (user_id_code,))
        conn.execute('DELETE FROM student_sessions WHERE student_id=?', (user_id,))
        conn.execute('DELETE FROM users WHERE user_id_code=? AND role=?', (user_id_code, 'student'))
        conn.commit()
        conn.close()
        session.clear()
        return jsonify({'success': True, 'message': 'Account deleted successfully'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'message': f'Error deleting account: {str(e)}'})

# ======================================================
# ================= ADMIN ROUTES =======================
# ======================================================

@app.route("/admin/login")
def admin_login_page():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    return render_template("admin_login.html")

@app.route("/admin/login", methods=["POST"])
def admin_login():
    # FIX #2: Rate limit admin login
    ip = _get_client_ip()
    limited, seconds_left = is_rate_limited(ip)
    if limited:
        return rate_limit_response(seconds_left)

    data     = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "")
    # FIX #1: Use check_password_hash instead of plaintext comparison
    if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
        clear_attempts(ip)
        session.clear()
        session['is_admin']       = True
        session['admin_username'] = username
        log_admin_action("ADMIN_LOGIN", details="Successful admin login")
        return jsonify({"success": True, "message": "Welcome, Administrator!"})
    record_failed_attempt(ip)
    return jsonify({"success": False, "message": "Invalid admin credentials."})

@app.route("/admin/logout")
def admin_logout():
    log_admin_action("ADMIN_LOGOUT", details="Admin logged out")
    session.clear()
    return redirect(url_for('admin_login_page'))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("admin_dashboard.html")

@app.route("/admin/stats")
@admin_required
def admin_stats():
    conn = get_db_connection()
    total_students    = conn.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
    total_teachers    = conn.execute("SELECT COUNT(*) FROM users WHERE role='teacher'").fetchone()[0]
    pending_teachers  = conn.execute("SELECT COUNT(*) FROM users WHERE role='teacher' AND is_approved=0").fetchone()[0]
    active_students   = conn.execute("SELECT COUNT(*) FROM users WHERE role='student' AND is_active=1").fetchone()[0]
    inactive_students = conn.execute("SELECT COUNT(*) FROM users WHERE role='student' AND is_active=0").fetchone()[0]
    total_xp          = conn.execute("SELECT SUM(xp) FROM student_progress").fetchone()[0] or 0
    total_sessions    = conn.execute("SELECT COUNT(*) FROM student_sessions").fetchone()[0]
    activity_today    = conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE date(date)=date('now')"
    ).fetchone()[0]
    top_students = conn.execute('''
        SELECT u.name, u.class_name, u.division, sp.xp, sp.total_stars, sp.streak
        FROM users u JOIN student_progress sp ON u.user_id_code=sp.user_id_code
        WHERE u.role='student'
        ORDER BY sp.xp DESC LIMIT 5
    ''').fetchall()
    conn.close()
    return jsonify({
        "success": True,
        "stats": {
            "totalStudents":   total_students,
            "totalTeachers":   total_teachers,
            "pendingTeachers": pending_teachers,
            "activeStudents":  active_students,
            "inactiveStudents": inactive_students,
            "totalXP":         total_xp,
            "totalSessions":   total_sessions,
            "activityToday":   activity_today,
            "topStudents": [
                {
                    "name":    row["name"],
                    "class":   f"Class {row['class_name']}-{row['division']}",
                    "xp":      row["xp"] or 0,
                    "stars":   row["total_stars"] or 0,
                    "streak":  row["streak"] or 0,
                } for row in top_students
            ]
        }
    })

@app.route("/admin/teachers")
@admin_required
def admin_get_teachers():
    conn = get_db_connection()
    teachers = conn.execute('''
        SELECT id, name, email, is_approved, is_active, approval_note,
               approved_by, approved_at, created_at
        FROM users WHERE role='teacher'
        ORDER BY is_approved ASC, created_at DESC
    ''').fetchall()
    conn.close()
    return jsonify({
        "success": True,
        "teachers": [
            {
                "id":           t["id"],
                "name":         t["name"],
                "email":        t["email"],
                "isApproved":   bool(t["is_approved"]),
                "isActive":     bool(t["is_active"]),
                "approvalNote": t["approval_note"],
                "approvedBy":   t["approved_by"],
                "approvedAt":   t["approved_at"],
                "createdAt":    t["created_at"],
            } for t in teachers
        ]
    })

@app.route("/admin/teachers/approve", methods=["POST"])
@admin_required
def admin_approve_teacher():
    data      = request.json
    teacher_id = data.get("teacherId")
    note      = data.get("note", "")
    if not teacher_id:
        return jsonify({"success": False, "message": "Teacher ID required"})
    conn = get_db_connection()
    teacher = conn.execute("SELECT * FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
    if not teacher:
        conn.close()
        return jsonify({"success": False, "message": "Teacher not found"})
    conn.execute('''
        UPDATE users SET is_approved=1, approval_note=?, approved_by=?, approved_at=?
        WHERE id=?
    ''', (note, session.get('admin_username'), datetime.now().isoformat(), teacher_id))
    conn.commit()
    conn.close()
    log_admin_action("APPROVE_TEACHER", "teacher", teacher_id, teacher["name"],
                     f"Note: {note}" if note else None)
    return jsonify({"success": True, "message": f"Teacher '{teacher['name']}' approved successfully."})

@app.route("/admin/teachers/reject", methods=["POST"])
@admin_required
def admin_reject_teacher():
    data       = request.json
    teacher_id = data.get("teacherId")
    note       = data.get("note", "")
    if not teacher_id:
        return jsonify({"success": False, "message": "Teacher ID required"})
    conn = get_db_connection()
    teacher = conn.execute("SELECT * FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
    if not teacher:
        conn.close()
        return jsonify({"success": False, "message": "Teacher not found"})
    conn.execute('''
        UPDATE users SET is_approved=0, is_active=0, approval_note=?, approved_by=?, approved_at=?
        WHERE id=?
    ''', (note or "Rejected by admin", session.get('admin_username'), datetime.now().isoformat(), teacher_id))
    conn.commit()
    conn.close()
    log_admin_action("REJECT_TEACHER", "teacher", teacher_id, teacher["name"],
                     f"Reason: {note}" if note else "No reason given")
    return jsonify({"success": True, "message": f"Teacher '{teacher['name']}' rejected."})

@app.route("/admin/teachers/toggle_active", methods=["POST"])
@admin_required
def admin_toggle_teacher_active():
    data       = request.json
    teacher_id = data.get("teacherId")
    if not teacher_id:
        return jsonify({"success": False, "message": "Teacher ID required"})
    conn = get_db_connection()
    teacher = conn.execute("SELECT * FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
    if not teacher:
        conn.close()
        return jsonify({"success": False, "message": "Teacher not found"})
    new_status = 0 if teacher["is_active"] else 1
    conn.execute("UPDATE users SET is_active=? WHERE id=?", (new_status, teacher_id))
    conn.commit()
    conn.close()
    action = "ACTIVATE_TEACHER" if new_status else "DEACTIVATE_TEACHER"
    log_admin_action(action, "teacher", teacher_id, teacher["name"])
    status_word = "activated" if new_status else "deactivated"
    return jsonify({"success": True, "message": f"Teacher '{teacher['name']}' {status_word}.", "newStatus": bool(new_status)})

@app.route("/admin/teachers/delete", methods=["POST"])
@admin_required
def admin_delete_teacher():
    data       = request.json
    teacher_id = data.get("teacherId")
    if not teacher_id:
        return jsonify({"success": False, "message": "Teacher ID required"})
    conn = get_db_connection()
    teacher = conn.execute("SELECT * FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
    if not teacher:
        conn.close()
        return jsonify({"success": False, "message": "Teacher not found"})
    conn.execute("DELETE FROM users WHERE id=?", (teacher_id,))
    conn.commit()
    conn.close()
    log_admin_action("DELETE_TEACHER", "teacher", teacher_id, teacher["name"])
    return jsonify({"success": True, "message": f"Teacher '{teacher['name']}' deleted permanently."})

@app.route("/admin/teachers/reset_password", methods=["POST"])
@admin_required
def admin_reset_teacher_password():
    data         = request.json
    teacher_id   = data.get("teacherId")
    new_password = data.get("newPassword", "").strip()
    if not teacher_id or not new_password:
        return jsonify({"success": False, "message": "Teacher ID and new password required"})
    if len(new_password) < 6:
        return jsonify({"success": False, "message": "Password must be at least 6 characters"})
    conn = get_db_connection()
    teacher = conn.execute("SELECT * FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
    if not teacher:
        conn.close()
        return jsonify({"success": False, "message": "Teacher not found"})
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_password), teacher_id))
    conn.commit()
    conn.close()
    log_admin_action("RESET_TEACHER_PASSWORD", "teacher", teacher_id, teacher["name"])
    return jsonify({"success": True, "message": f"Password for '{teacher['name']}' reset successfully."})

@app.route("/admin/students")
@admin_required
def admin_get_students():
    conn = get_db_connection()
    students = conn.execute('''
        SELECT u.id, u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
               u.is_active, u.created_at,
               sp.xp, sp.total_stars, sp.streak, sp.last_active
        FROM users u
        LEFT JOIN student_progress sp ON u.user_id_code=sp.user_id_code
        WHERE u.role='student'
        ORDER BY u.class_name*1 ASC, u.division ASC, u.name ASC
    ''').fetchall()
    conn.close()
    return jsonify({
        "success": True,
        "students": [
            {
                "id":         s["id"],
                "name":       s["name"],
                "rollNo":     s["roll_no"],
                "userIdCode": s["user_id_code"],
                "className":  s["class_name"],
                "division":   s["division"],
                "classLabel": f"Class {s['class_name']}-{s['division']}",
                "isActive":   bool(s["is_active"]),
                "createdAt":  s["created_at"],
                "xp":         s["xp"] or 0,
                "totalStars": s["total_stars"] or 0,
                "streak":     s["streak"] or 0,
                "lastActive": s["last_active"],
            } for s in students
        ]
    })

@app.route("/admin/students/toggle_active", methods=["POST"])
@admin_required
def admin_toggle_student_active():
    data       = request.json
    student_id = data.get("studentId")
    if not student_id:
        return jsonify({"success": False, "message": "Student ID required"})
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"success": False, "message": "Student not found"})
    new_status = 0 if student["is_active"] else 1
    conn.execute("UPDATE users SET is_active=? WHERE id=?", (new_status, student_id))
    conn.commit()
    conn.close()
    action = "ACTIVATE_STUDENT" if new_status else "DEACTIVATE_STUDENT"
    log_admin_action(action, "student", student_id, student["name"],
                     f"UserID: {student['user_id_code']}, Roll: {student['roll_no']}, Class: {student['class_name']}-{student['division']}")
    status_word = "activated" if new_status else "deactivated"
    return jsonify({"success": True, "message": f"Student '{student['name']}' {status_word}.", "newStatus": bool(new_status)})

@app.route("/admin/students/delete", methods=["POST"])
@admin_required
def admin_delete_student():
    data       = request.json
    student_id = data.get("studentId")
    if not student_id:
        return jsonify({"success": False, "message": "Student ID required"})
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"success": False, "message": "Student not found"})
    uid = student["user_id_code"]
    conn.execute("DELETE FROM activity_log WHERE user_id_code=?", (uid,))
    conn.execute("DELETE FROM student_progress WHERE user_id_code=?", (uid,))
    conn.execute("DELETE FROM student_badges WHERE user_id_code=?", (uid,))
    conn.execute("DELETE FROM student_sessions WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM users WHERE id=?", (student_id,))
    conn.commit()
    conn.close()
    log_admin_action("DELETE_STUDENT", "student", student_id, student["name"],
                     f"UserID: {uid}, Roll: {student['roll_no']}, Class: {student['class_name']}-{student['division']}")
    return jsonify({"success": True, "message": f"Student '{student['name']}' and all their data deleted."})

@app.route("/admin/students/reset_password", methods=["POST"])
@admin_required
def admin_reset_student_password():
    data         = request.json
    student_id   = data.get("studentId")
    new_password = data.get("newPassword", "").strip()
    if not student_id or not new_password:
        return jsonify({"success": False, "message": "Student ID and new password required"})
    if len(new_password) < 4:
        return jsonify({"success": False, "message": "Password must be at least 4 characters"})
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"success": False, "message": "Student not found"})
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_password), student_id))
    conn.commit()
    conn.close()
    log_admin_action("RESET_STUDENT_PASSWORD", "student", student_id, student["name"])
    return jsonify({"success": True, "message": f"Password for '{student['name']}' reset successfully."})

@app.route("/admin/students/reset_progress", methods=["POST"])
@admin_required
def admin_reset_student_progress():
    data       = request.json
    student_id = data.get("studentId")
    if not student_id:
        return jsonify({"success": False, "message": "Student ID required"})
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"success": False, "message": "Student not found"})
    uid = student["user_id_code"]
    conn.execute('''
        UPDATE student_progress SET
            xp=0, conversation_xp=0, roleplay_xp=0, repeat_xp=0,
            spellbee_xp=0, meanings_xp=0, wordpuzzle_xp=0, grammar_xp=0,
            total_stars=0, total_sessions=0, average_accuracy=0, streak=0
        WHERE user_id_code=?
    ''', (uid,))
    conn.execute("DELETE FROM activity_log WHERE user_id_code=?", (uid,))
    conn.execute("DELETE FROM student_badges WHERE user_id_code=?", (uid,))
    conn.commit()
    conn.close()
    log_admin_action("RESET_STUDENT_PROGRESS", "student", student_id, student["name"],
                     f"All XP, badges, and activity log cleared for {uid}")
    return jsonify({"success": True, "message": f"Progress for '{student['name']}' has been reset."})

@app.route("/admin/audit_log")
@admin_required
def admin_audit_log():
    limit = int(request.args.get("limit", 100))
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT admin_username, action, target_type, target_id, target_name, details, timestamp
        FROM admin_audit_log ORDER BY timestamp DESC LIMIT ?
    ''', (limit,)).fetchall()
    conn.close()
    return jsonify({
        "success": True,
        "log": [
            {
                "adminUsername": r["admin_username"],
                "action":        r["action"],
                "targetType":    r["target_type"],
                "targetId":      r["target_id"],
                "targetName":    r["target_name"],
                "details":       r["details"],
                "timestamp":     r["timestamp"],
            } for r in rows
        ]
    })

@app.route("/admin/change_password", methods=["POST"])
@admin_required
def admin_change_password():
    data         = request.json
    old_password = data.get("oldPassword", "")
    new_password = data.get("newPassword", "").strip()
    # FIX #1: Use hash comparison for old password verification
    if not check_password_hash(ADMIN_PASSWORD_HASH, old_password):
        return jsonify({"success": False, "message": "Current password is incorrect."})
    if len(new_password) < 8:
        return jsonify({"success": False, "message": "New password must be at least 8 characters."})
    log_admin_action("CHANGE_ADMIN_PASSWORD", details="Admin password change requested")
    return jsonify({
        "success": True,
        "message": "To apply the new password, set ADMIN_PASSWORD in your .env file and restart the server.",
        "newPassword": new_password
    })

@app.route("/admin/students/bulk_action", methods=["POST"])
@admin_required
def admin_bulk_student_action():
    data       = request.json
    action     = data.get("action")
    student_ids = data.get("studentIds", [])
    if not action or not student_ids:
        return jsonify({"success": False, "message": "Action and student IDs required"})
    if action not in ("activate", "deactivate", "delete"):
        return jsonify({"success": False, "message": "Invalid action"})
    conn = get_db_connection()
    affected = 0
    for sid in student_ids:
        student = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (sid,)).fetchone()
        if not student:
            continue
        uid = student["user_id_code"]
        if action == "activate":
            conn.execute("UPDATE users SET is_active=1 WHERE id=?", (sid,))
            log_admin_action("BULK_ACTIVATE_STUDENT", "student", sid, student["name"])
        elif action == "deactivate":
            conn.execute("UPDATE users SET is_active=0 WHERE id=?", (sid,))
            log_admin_action("BULK_DEACTIVATE_STUDENT", "student", sid, student["name"])
        elif action == "delete":
            conn.execute("DELETE FROM activity_log WHERE user_id_code=?", (uid,))
            conn.execute("DELETE FROM student_progress WHERE user_id_code=?", (uid,))
            conn.execute("DELETE FROM student_badges WHERE user_id_code=?", (uid,))
            conn.execute("DELETE FROM student_sessions WHERE student_id=?", (sid,))
            conn.execute("DELETE FROM users WHERE id=?", (sid,))
            log_admin_action("BULK_DELETE_STUDENT", "student", sid, student["name"])
        affected += 1
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Bulk {action} applied to {affected} students."})

# ================= STUDENT ROUTES =================
@app.route("/dashboard")
@student_required
def dashboard():
    return render_template("dashboard.html")

@app.route("/main")
@student_required
def main():
    return render_template("main.html")

@app.route("/reset_roleplay_context", methods=["POST"])
@student_required
def reset_roleplay_context():
    reset_conversation_context()
    new_roleplay = request.json.get("roleplay") if request.json else None
    session['current_roleplay_type'] = new_roleplay
    return jsonify({"success": True})

@app.route("/process", methods=["POST"])
@student_required
def process():
    data = request.json
    user_text = data["text"]
    roleplay = data.get("roleplay")
    prev_roleplay = session.get('current_roleplay_type')
    if roleplay != prev_roleplay:
        reset_conversation_context()
        session['current_roleplay_type'] = roleplay
    try:
        coach_response = (
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

# ================= WORD PUZZLE ROUTES =================
@app.route("/word_puzzle", methods=["POST"])
@student_required
def word_puzzle():
    data       = request.json or {}
    difficulty = data.get("difficulty", "easy")
    pool      = WORD_PUZZLE_WORDS.get(difficulty, WORD_PUZZLE_WORDS["easy"])
    recent    = get_session_recent_puzzle_words()
    available = [w for w in pool if w["word"] not in recent]
    if not available:
        recent    = recent[-5:] if len(recent) > 5 else []
        available = [w for w in pool if w["word"] not in recent]
    if not available:
        available = pool
    chosen    = random.choice(available)
    scrambled = scramble_word(chosen["word"])
    recent.append(chosen["word"])
    if len(recent) > MAX_HISTORY:
        recent = recent[-MAX_HISTORY:]
    set_session_recent_puzzle_words(recent)
    hint_audio = speak_to_file(chosen["hint"], slow=False)
    return jsonify({
        "scrambled":  scrambled,
        "hint":       chosen["hint"],
        "category":   chosen["category"],
        "length":     len(chosen["word"]),
        "difficulty": difficulty,
        "hint_audio": hint_audio,
    })

@app.route("/word_puzzle_start", methods=["POST"])
@student_required
def word_puzzle_start():
    data       = request.json or {}
    difficulty = data.get("difficulty", "easy")
    pool      = WORD_PUZZLE_WORDS.get(difficulty, WORD_PUZZLE_WORDS["easy"])
    recent    = get_session_recent_puzzle_words()
    available = [w for w in pool if w["word"] not in recent]
    if not available:
        recent    = recent[-5:] if len(recent) > 5 else []
        available = [w for w in pool if w["word"] not in recent]
    if not available:
        available = pool
    chosen    = random.choice(available)
    scrambled = scramble_word(chosen["word"])
    recent.append(chosen["word"])
    if len(recent) > MAX_HISTORY:
        recent = recent[-MAX_HISTORY:]
    set_session_recent_puzzle_words(recent)
    session['current_puzzle_word']       = chosen["word"]
    session['current_puzzle_difficulty'] = difficulty
    hint_audio = speak_to_file(chosen["hint"], slow=False)
    return jsonify({
        "scrambled":  scrambled,
        "hint":       chosen["hint"],
        "category":   chosen["category"],
        "length":     len(chosen["word"]),
        "difficulty": difficulty,
        "hint_audio": hint_audio,
    })

@app.route("/check_word_puzzle", methods=["POST"])
@student_required
def check_word_puzzle():
    data    = request.json or {}
    answer  = data.get("answer", "").strip().upper()
    attempt = data.get("attempt", 1)

    correct = session.get('current_puzzle_word', '').upper()
    if not correct:
        return jsonify({"correct": False, "feedback": "Session expired. Please start a new puzzle.", "stars": 0, "correct_word": "", "hint": None, "letter_comparison": []})

    is_correct = answer == correct
    if is_correct:
        feedback = "🎉 Brilliant! You solved the puzzle!"
        stars = 3
        hint  = None
        session.pop('current_puzzle_word', None)
        session.pop('current_puzzle_difficulty', None)
    else:
        similarity = SequenceMatcher(None, answer, correct).ratio()
        if similarity >= 0.75:
            feedback = "So close! A couple of letters are wrong."
            stars    = 1
        else:
            feedback = "Not quite — try rearranging the letters again!"
            stars    = 0
        hint = None
        if attempt >= 2:
            hint = f"💡 Hint: The word starts with '{correct[0]}' and has {len(correct)} letters."
    letter_comparison = []
    for i, letter in enumerate(correct):
        if i < len(answer):
            letter_comparison.append({
                "letter": letter,
                "status": "correct" if answer[i] == letter else "incorrect",
                "typed":  answer[i],
            })
        else:
            letter_comparison.append({"letter": letter, "status": "missing"})
    return jsonify({
        "correct":           is_correct,
        "feedback":          feedback,
        "stars":             stars,
        "correct_word":      correct,
        "hint":              hint,
        "letter_comparison": letter_comparison,
    })

# ================= GRAMMAR ROUTES =================
@app.route("/grammar_question", methods=["POST"])
@student_required
def grammar_question_route():
    data       = request.json or {}
    difficulty = data.get("difficulty", "easy")
    q          = get_grammar_question(difficulty)
    session['current_grammar_correct']     = q["correct_index"]
    session['current_grammar_explanation'] = q["explanation"]
    session['current_grammar_difficulty']  = difficulty
    spoken = q["question"].replace("___", "blank")
    audio  = speak_to_file(spoken, slow=False)
    return jsonify({
        "question":   q["question"],
        "options":    q["options"],
        "difficulty": difficulty,
        "audio":      audio,
    })

@app.route("/check_grammar", methods=["POST"])
@student_required
def check_grammar():
    data         = request.json or {}
    chosen_index = data.get("chosen_index", -1)
    difficulty   = data.get("difficulty", session.get('current_grammar_difficulty', 'easy'))
    correct_index = session.get('current_grammar_correct', -1)
    explanation   = session.get('current_grammar_explanation', '')
    if correct_index == -1:
        return jsonify({"correct": False, "feedback": "Session expired. Please reload the question.", "stars": 0, "correct_index": -1, "explanation": "", "explanation_audio": None})
    is_correct = (chosen_index == correct_index)
    if is_correct:
        feedback = "✅ Correct! Well done!"
        stars    = 3 if difficulty == "hard" else (2 if difficulty == "medium" else 1)
    else:
        feedback = "❌ Not quite. Read the explanation below."
        stars    = 0
    explanation_audio = speak_to_file(explanation, slow=False) if explanation else None
    return jsonify({
        "correct":           is_correct,
        "feedback":          feedback,
        "stars":             stars,
        "correct_index":     correct_index,
        "explanation":       explanation,
        "explanation_audio": explanation_audio,
    })

# ================= DAILY CHALLENGE =================
@app.route("/get_daily_challenge")
@student_required
def get_daily_challenge_route():
    user_id_code = session.get('user_id_code')
    challenge = get_daily_challenge()
    completed = has_completed_daily(user_id_code)
    return jsonify({"success": True, "challenge": challenge, "completed": completed, "bonus_xp": 3})

@app.route("/complete_daily", methods=["POST"])
@student_required
def complete_daily():
    user_id_code = session.get('user_id_code')
    roll_no      = session.get('roll_no')
    class_name   = session.get('class_name')
    division     = session.get('division')
    if has_completed_daily(user_id_code):
        return jsonify({"success": False, "message": "Already completed today's challenge!"})
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO activity_log (user_id_code, roll_no, class_name, division, mode, score, xp_earned, stars_earned) VALUES (?,?,?,?,?,?,?,?)",
        (user_id_code, roll_no, class_name, division, 'daily', 100, 3, 1)
    )
    conn.execute(
        "UPDATE student_progress SET xp=xp+3, total_sessions=total_sessions+1, last_active=? WHERE user_id_code=?",
        (datetime.now(), user_id_code)
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
        JOIN student_progress sp ON u.user_id_code=sp.user_id_code
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
    my_uid = session.get('user_id_code')
    conn = get_db_connection()
    my_rank_row = conn.execute('''
        SELECT COUNT(*)+1 as rank FROM student_progress
        WHERE xp > (SELECT xp FROM student_progress WHERE user_id_code=?)
    ''', (my_uid,)).fetchone()
    conn.close()
    my_rank = my_rank_row['rank'] if my_rank_row else '?'
    return jsonify({"success": True, "leaderboard": leaderboard, "my_rank": my_rank})

# ================= PROGRESS DETAILS (Mistake Tracker + Suggestions) =================
@app.route("/get_progress_details")
@student_required
def get_progress_details():
    """Returns per-mode score averages, weak sessions, recent sessions, and personal suggestions."""
    user_id_code = session.get('user_id_code')
    if not user_id_code:
        return jsonify({'success': False, 'message': 'Not logged in'})

    conn = get_db_connection()

    # Per-mode aggregated stats (exclude daily)
    mode_rows = conn.execute('''
        SELECT mode,
               COUNT(*) as total_attempts,
               AVG(score) as avg_score,
               SUM(CASE WHEN score >= 80 THEN 1 ELSE 0 END) as high_score_count,
               SUM(CASE WHEN score < 60 THEN 1 ELSE 0 END) as low_score_count,
               SUM(stars_earned) as total_stars
        FROM activity_log
        WHERE user_id_code = ? AND mode NOT IN ('daily')
        GROUP BY mode
    ''', (user_id_code,)).fetchall()

    # 10 most recent low-score sessions (score < 70)
    weak_rows = conn.execute('''
        SELECT mode, score, date
        FROM activity_log
        WHERE user_id_code = ? AND score < 70 AND mode NOT IN ('daily')
        ORDER BY date DESC
        LIMIT 10
    ''', (user_id_code,)).fetchall()

    # Most recent 5 sessions overall
    recent_rows = conn.execute('''
        SELECT mode, score, xp_earned, stars_earned, date
        FROM activity_log
        WHERE user_id_code = ? AND mode NOT IN ('daily')
        ORDER BY date DESC
        LIMIT 5
    ''', (user_id_code,)).fetchall()

    # Progress data for suggestions
    progress_row = conn.execute(
        'SELECT * FROM student_progress WHERE user_id_code=?', (user_id_code,)
    ).fetchone()

    conn.close()

    mode_stats = {}
    for row in mode_rows:
        mode_stats[row['mode']] = {
            'totalAttempts': row['total_attempts'],
            'avgScore':      round(row['avg_score'] or 0, 1),
            'highScoreCount': row['high_score_count'] or 0,
            'lowScoreCount':  row['low_score_count'] or 0,
            'totalStars':     row['total_stars'] or 0,
        }

    weak_sessions = [
        {'mode': r['mode'], 'score': round(r['score'] or 0, 1), 'date': r['date']}
        for r in weak_rows
    ]

    recent_sessions = [
        {
            'mode':       r['mode'],
            'score':      round(r['score'] or 0, 1),
            'xpEarned':   r['xp_earned'],
            'starsEarned': r['stars_earned'],
            'date':       r['date'],
        }
        for r in recent_rows
    ]

    # ── Generate personal suggestions ──────────────────────────────────────────
    progress_data = {}
    streak = 0
    if progress_row:
        progress_data = {
            'xp':              progress_row['xp'] or 0,
            'conversation_xp': progress_row['conversation_xp'] or 0,
            'roleplay_xp':     progress_row['roleplay_xp']     or 0,
            'repeat_xp':       progress_row['repeat_xp']       or 0,
            'spellbee_xp':     progress_row['spellbee_xp']     or 0,
            'meanings_xp':     progress_row['meanings_xp']     or 0,
            'wordpuzzle_xp':   progress_row['wordpuzzle_xp']   or 0,
            'grammar_xp':      progress_row['grammar_xp']      or 0,
            'total_stars':     progress_row['total_stars']      or 0,
            'total_sessions':  progress_row['total_sessions']   or 0,
        }
        streak = calculate_streak(user_id_code)

    suggestions = generate_personal_suggestions(mode_stats, weak_sessions, progress_data, streak)

    return jsonify({
        'success': True,
        'modeStats':      mode_stats,
        'weakSessions':   weak_sessions,
        'recentSessions': recent_sessions,
        'suggestions':    suggestions,
    })

# ================= XP SYSTEM =================
@app.route("/get_student_info")
@student_required
def get_student_info():
    if 'user_id_code' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    user_id_code = session['user_id_code']
    conn = get_db_connection()
    student = conn.execute(
        'SELECT name, roll_no, class_name, division, user_id_code FROM users WHERE user_id_code=?',
        (user_id_code,)
    ).fetchone()
    progress = conn.execute(
        'SELECT * FROM student_progress WHERE user_id_code=?',
        (user_id_code,)
    ).fetchone()
    badges_rows = conn.execute(
        'SELECT badge_id, earned_at FROM student_badges WHERE user_id_code=? ORDER BY earned_at DESC',
        (user_id_code,)
    ).fetchall()
    conn.close()

    if student and progress:
        streak = calculate_streak(user_id_code)
        conn = get_db_connection()
        conn.execute(
            'UPDATE student_progress SET streak=? WHERE user_id_code=?',
            (streak, user_id_code)
        )
        conn.commit()
        conn.close()

        progress_data = {
            'xp':              progress['xp'] or 0,
            'conversation_xp': progress['conversation_xp'] or 0,
            'roleplay_xp':     progress['roleplay_xp']     or 0,
            'repeat_xp':       progress['repeat_xp']       or 0,
            'spellbee_xp':     progress['spellbee_xp']     or 0,
            'meanings_xp':     progress['meanings_xp']     or 0,
            'wordpuzzle_xp':   progress['wordpuzzle_xp']   or 0,
            'grammar_xp':      progress['grammar_xp']      or 0,
            'total_stars':     progress['total_stars']      or 0,
            'streak':          streak,
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
        daily_completed = has_completed_daily(user_id_code)

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
                'wordpuzzleXp':     progress_data['wordpuzzle_xp'],
                'grammarXp':        progress_data['grammar_xp'],
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
    if 'user_id_code' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data         = request.json
    user_id_code = session['user_id_code']
    roll_no      = session.get('roll_no')
    class_name   = session.get('class_name')
    division     = session.get('division')
    xp_earned    = data.get('xpEarned', 0)
    mode         = data.get('mode', '').lower()
    score        = data.get('score', 0)
    stars_earned = data.get('starsEarned', 0)
    difficulty   = data.get('difficulty', 'easy')
    attempt      = data.get('attempt', None)

    conn = get_db_connection()
    progress = conn.execute(
        'SELECT * FROM student_progress WHERE user_id_code=?',
        (user_id_code,)
    ).fetchone()

    if progress:
        old_progress = {
            'xp':              progress['xp'] or 0,
            'conversation_xp': progress['conversation_xp'] or 0,
            'roleplay_xp':     progress['roleplay_xp']     or 0,
            'repeat_xp':       progress['repeat_xp']       or 0,
            'spellbee_xp':     progress['spellbee_xp']     or 0,
            'meanings_xp':     progress['meanings_xp']     or 0,
            'wordpuzzle_xp':   progress['wordpuzzle_xp']   or 0,
            'grammar_xp':      progress['grammar_xp']      or 0,
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

        streak = calculate_streak(user_id_code)
        old_progress['streak'] = streak

        old_avg        = progress['average_accuracy'] or 0
        total_sessions = progress['total_sessions'] or 0
        new_sessions   = total_sessions + 1
        new_avg        = ((old_avg * total_sessions) + score) / new_sessions

        update_query = f'''
            UPDATE student_progress
            SET xp=?, {mode_xp_column}=?,
                total_stars=total_stars+?,
                total_sessions=total_sessions+1,
                average_accuracy=?, streak=?, last_active=?
            WHERE user_id_code=?
        '''
        conn.execute(update_query,
                     (new_total_xp, new_mode_xp, stars_earned, new_avg, streak,
                      datetime.now(), user_id_code))
        conn.execute(
            'INSERT INTO activity_log (user_id_code, roll_no, class_name, division, mode, score, xp_earned, stars_earned) VALUES (?,?,?,?,?,?,?,?)',
            (user_id_code, roll_no, class_name, division, mode, score, xp_earned, stars_earned)
        )

        newly_earned_badge_ids = check_earned_badges(
            user_id_code, old_progress,
            mode=mode, difficulty=difficulty,
            score=score, stars_earned=stars_earned,
            attempt=attempt
        )
        for badge_id in newly_earned_badge_ids:
            try:
                conn.execute(
                    'INSERT OR IGNORE INTO student_badges (user_id_code, roll_no, class_name, division, badge_id) VALUES (?,?,?,?,?)',
                    (user_id_code, roll_no, class_name, division, badge_id)
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
    if 'user_id_code' not in session:
        return jsonify({'success': False})
    user_id_code = session['user_id_code']
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT badge_id, earned_at FROM student_badges WHERE user_id_code=? ORDER BY earned_at DESC',
        (user_id_code,)
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
    # FIX #5: Single query with badge counts via GROUP BY — eliminates N+1 queries
    students = conn.execute('''
        SELECT u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
               sp.xp, sp.conversation_xp, sp.roleplay_xp,
               sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
               sp.wordpuzzle_xp, sp.grammar_xp,
               sp.total_stars, sp.total_sessions, sp.average_accuracy,
               sp.last_active, sp.streak,
               COUNT(sb.id) AS badge_count
        FROM users u
        LEFT JOIN student_progress sp ON u.user_id_code=sp.user_id_code
        LEFT JOIN student_badges sb   ON u.user_id_code=sb.user_id_code
        WHERE u.role='student'
        GROUP BY u.id
        ORDER BY u.class_name*1 ASC, u.division ASC, sp.xp DESC
    ''').fetchall()
    conn.close()

    students_list = []
    for s in students:
        progress_data = {
            'conversation_xp': s['conversation_xp'] or 0,
            'roleplay_xp':     s['roleplay_xp']     or 0,
            'repeat_xp':       s['repeat_xp']       or 0,
            'spellbee_xp':     s['spellbee_xp']     or 0,
            'meanings_xp':     s['meanings_xp']     or 0,
            'wordpuzzle_xp':   s['wordpuzzle_xp']   or 0,
            'grammar_xp':      s['grammar_xp']       or 0,
        }
        unlocked_features = get_unlocked_features(progress_data)
        students_list.append({
            'name':             s['name'],
            'rollNo':           s['roll_no'],
            'userIdCode':       s['user_id_code'],
            'className':        s['class_name'],
            'division':         s['division'],
            'classLabel':       f"Class {s['class_name']}-{s['division']}",
            'xp':               s['xp'] or 0,
            'conversationXp':   progress_data['conversation_xp'],
            'roleplayXp':       progress_data['roleplay_xp'],
            'repeatXp':         progress_data['repeat_xp'],
            'spellbeeXp':       progress_data['spellbee_xp'],
            'meaningsXp':       progress_data['meanings_xp'],
            'wordpuzzleXp':     progress_data['wordpuzzle_xp'],
            'grammarXp':        progress_data['grammar_xp'],
            'totalStars':       s['total_stars'] or 0,
            'totalSessions':    s['total_sessions'] or 0,
            'averageAccuracy':  round(s['average_accuracy'] or 0, 1),
            'lastActive':       s['last_active'],
            'streak':           s['streak'] or 0,
            'unlockedFeatures': unlocked_features,
            'earnedBadgeCount': s['badge_count'] or 0,
            'totalBadgeCount':  len(ALL_BADGES),
        })
    return jsonify({'success': True, 'students': students_list})

@app.route("/get_student_details/<user_id_code>")
@teacher_required
def get_student_details(user_id_code):
    conn = get_db_connection()
    if user_id_code.startswith('GSS-'):
        student = conn.execute('''
            SELECT u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
                   sp.xp, sp.conversation_xp, sp.roleplay_xp,
                   sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
                   sp.wordpuzzle_xp, sp.grammar_xp,
                   sp.total_stars, sp.total_sessions, sp.average_accuracy, sp.last_active, sp.streak
            FROM users u
            LEFT JOIN student_progress sp ON u.user_id_code=sp.user_id_code
            WHERE u.user_id_code=? AND u.role='student'
        ''', (user_id_code,)).fetchone()
    else:
        class_name = request.args.get('class_name', '')
        division   = request.args.get('division', '')
        if class_name and division:
            student = conn.execute('''
                SELECT u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
                       sp.xp, sp.conversation_xp, sp.roleplay_xp,
                       sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
                       sp.wordpuzzle_xp, sp.grammar_xp,
                       sp.total_stars, sp.total_sessions, sp.average_accuracy, sp.last_active, sp.streak
                FROM users u
                LEFT JOIN student_progress sp ON u.user_id_code=sp.user_id_code
                WHERE u.roll_no=? AND u.class_name=? AND u.division=? AND u.role='student'
            ''', (user_id_code, class_name, division)).fetchone()
        else:
            student = conn.execute('''
                SELECT u.name, u.roll_no, u.user_id_code, u.class_name, u.division,
                       sp.xp, sp.conversation_xp, sp.roleplay_xp,
                       sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
                       sp.wordpuzzle_xp, sp.grammar_xp,
                       sp.total_stars, sp.total_sessions, sp.average_accuracy, sp.last_active, sp.streak
                FROM users u
                LEFT JOIN student_progress sp ON u.user_id_code=sp.user_id_code
                WHERE u.roll_no=? AND u.role='student' LIMIT 1
            ''', (user_id_code,)).fetchone()

    if not student:
        conn.close()
        return jsonify({'success': False, 'message': 'Student not found'})

    uid = student['user_id_code']
    activities = conn.execute('''
        SELECT date, mode, score, xp_earned, stars_earned
        FROM activity_log WHERE user_id_code=?
        ORDER BY date DESC LIMIT 50
    ''', (uid,)).fetchall()
    badges_rows = conn.execute(
        'SELECT badge_id, earned_at FROM student_badges WHERE user_id_code=? ORDER BY earned_at DESC',
        (uid,)
    ).fetchall()
    conn.close()

    activity_list = [{'date': a['date'], 'mode': a['mode'], 'score': round(a['score'] or 0, 1),
                      'xpEarned': a['xp_earned'], 'starsEarned': a['stars_earned']} for a in activities]
    progress_data = {
        'conversation_xp': student['conversation_xp'] or 0,
        'roleplay_xp':     student['roleplay_xp']     or 0,
        'repeat_xp':       student['repeat_xp']       or 0,
        'spellbee_xp':     student['spellbee_xp']     or 0,
        'meanings_xp':     student['meanings_xp']     or 0,
        'wordpuzzle_xp':   student['wordpuzzle_xp']   or 0,
        'grammar_xp':      student['grammar_xp']       or 0,
    }
    unlocked_features = get_unlocked_features(progress_data)
    next_unlock       = get_next_unlock(progress_data)
    earned_ids        = {row['badge_id']: row['earned_at'] for row in badges_rows}
    badges_detail     = [{**b, 'earned': b['id'] in earned_ids, 'earned_at': earned_ids.get(b['id'])} for b in ALL_BADGES]
    student_data = {
        'name':             student['name'],
        'rollNo':           student['roll_no'],
        'userIdCode':       uid,
        'className':        student['class_name'],
        'division':         student['division'],
        'classLabel':       f"Class {student['class_name']}-{student['division']}",
        'xp':               student['xp'] or 0,
        'conversationXp':   progress_data['conversation_xp'],
        'roleplayXp':       progress_data['roleplay_xp'],
        'repeatXp':         progress_data['repeat_xp'],
        'spellbeeXp':       progress_data['spellbee_xp'],
        'meaningsXp':       progress_data['meanings_xp'],
        'wordpuzzleXp':     progress_data['wordpuzzle_xp'],
        'grammarXp':        progress_data['grammar_xp'],
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
        'SELECT * FROM users WHERE user_id_code=? AND role=?', (user_id_code, 'student')
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

# ================= RUN =================
if __name__ == '__main__':
    app.run(debug=True)