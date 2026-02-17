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
from datetime import datetime
import random
import time
import hashlib

# ================= SETUP =================
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")

conversation_context = ""

# ================= ANTI-REPETITION TRACKING =================
recent_sentences = []
recent_words = []
MAX_HISTORY = 20

# ================= ROLEPLAY QUESTION POOLS =================
ROLEPLAY_QUESTIONS = {
    "teacher": [
        "What did you learn in school today?",
        "Can you tell me about your favorite subject?",
        "Have you completed your homework?",
        "What is 5 plus 3?",
        "Can you name three colors?",
        "What are you reading these days?",
        "Do you have any questions about your lessons?",
        "What would you like to learn next?",
        "Can you tell me one thing you learned this week?",
        "How can I help you with your studies today?",
        "What is your favorite book?",
        "Can you spell the word 'happy'?",
        "What do you want to be when you grow up?",
        "Do you enjoy science or math more?",
        "Can you name the days of the week?"
    ],
    "friend": [
        "What games do you like to play?",
        "What's your favorite cartoon or movie?",
        "Do you have any pets?",
        "What did you do yesterday?",
        "What's your favorite food?",
        "Do you want to play together after school?",
        "What do you like to do on weekends?",
        "Who is your best friend?",
        "What's your favorite sport?",
        "Do you like ice cream or cake more?",
        "What makes you happy?",
        "What's your favorite color?",
        "Do you have any siblings?",
        "What's the funniest thing that happened to you?",
        "What do you want to do during vacation?"
    ],
    "interviewer": [
        "Tell me about yourself.",
        "Why do you want this job?",
        "What are your strengths?",
        "Where do you see yourself in 5 years?",
        "Can you describe a challenging situation you faced?",
        "What is your biggest achievement?",
        "Why should we hire you?",
        "What are your hobbies and interests?",
        "How do you handle stress?",
        "What motivates you to work hard?",
        "Tell me about your education background.",
        "What skills do you bring to this role?",
        "How do you work in a team?",
        "What is your greatest weakness?",
        "Do you have any questions for us?"
    ],
    "viva": [
        "Can you explain what your project is about?",
        "Why did you choose this topic?",
        "What challenges did you face during your project?",
        "What is the main objective of your work?",
        "Can you explain this concept in simple terms?",
        "What did you learn from this project?",
        "How is your project useful in real life?",
        "What would you do differently next time?",
        "Can you explain your methodology?",
        "What are the future applications of your work?",
        "How did you solve the main problem in your project?",
        "What resources did you use for your research?",
        "Can you explain your results?",
        "What conclusions did you draw?",
        "How does your project compare to existing solutions?"
    ]
}

# Track recently asked questions per role to avoid repetition
recent_roleplay_questions = {
    "teacher": [],
    "friend": [],
    "interviewer": [],
    "viva": []
}

def get_roleplay_question(roleplay_type):
    """Get a contextually relevant question for the roleplay type without repetition"""
    questions = ROLEPLAY_QUESTIONS.get(roleplay_type, ROLEPLAY_QUESTIONS["friend"])
    recent = recent_roleplay_questions.get(roleplay_type, [])
    available_questions = [q for q in questions if q not in recent]
    if not available_questions:
        recent_roleplay_questions[roleplay_type] = recent[-5:] if len(recent) > 5 else []
        available_questions = [q for q in questions if q not in recent_roleplay_questions[roleplay_type]]
    if not available_questions:
        available_questions = questions
    selected_question = random.choice(available_questions)
    recent_roleplay_questions[roleplay_type].append(selected_question)
    if len(recent_roleplay_questions[roleplay_type]) > 10:
        recent_roleplay_questions[roleplay_type] = recent_roleplay_questions[roleplay_type][-10:]
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

# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect('students.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK(role IN ('student', 'teacher')),
            name TEXT NOT NULL,
            roll_no TEXT UNIQUE,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            roll_no TEXT UNIQUE NOT NULL,
            xp INTEGER DEFAULT 0,
            conversation_xp INTEGER DEFAULT 0,
            roleplay_xp INTEGER DEFAULT 0,
            repeat_xp INTEGER DEFAULT 0,
            spellbee_xp INTEGER DEFAULT 0,
            meanings_xp INTEGER DEFAULT 0,
            total_stars INTEGER DEFAULT 0,
            total_sessions INTEGER DEFAULT 0,
            average_accuracy REAL DEFAULT 0,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (roll_no) REFERENCES users (roll_no)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_no TEXT NOT NULL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            mode TEXT NOT NULL,
            score REAL,
            xp_earned INTEGER,
            stars_earned INTEGER,
            FOREIGN KEY (roll_no) REFERENCES users (roll_no)
        )
    ''')
    c.execute("PRAGMA table_info(student_progress)")
    columns = [column[1] for column in c.fetchall()]
    if 'conversation_xp' not in columns:
        print("Migrating database to add mode-specific XP columns...")
        c.execute('ALTER TABLE student_progress ADD COLUMN conversation_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN roleplay_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN repeat_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN spellbee_xp INTEGER DEFAULT 0')
        c.execute('ALTER TABLE student_progress ADD COLUMN meanings_xp INTEGER DEFAULT 0')
        print("Migration complete!")
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

# ================= IMPROVED TTS WITH CACHING AND RETRY =================
def speak_to_file(text, slow=False, max_retries=3):
    if len(text) > 300:
        text = text[:300]
    cached_audio = get_cached_audio(text, slow)
    if cached_audio:
        print(f"Using cached audio for: {text[:50]}...")
        return cached_audio
    os.makedirs("static/audio", exist_ok=True)
    filename = f"{uuid.uuid4()}.mp3"
    path = f"static/audio/{filename}"
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                delay = (2 ** attempt) + random.uniform(0, 1)
                print(f"Retry attempt {attempt + 1} after {delay:.2f}s delay...")
                time.sleep(delay)
            else:
                time.sleep(random.uniform(0.3, 0.8))
            gTTS(text=text, lang="en", slow=slow).save(path)
            save_to_cache(text, path, slow)
            print(f"Successfully generated audio for: {text[:50]}...")
            return "/" + path
        except Exception as e:
            print(f"TTS attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_retries - 1:
                print(f"All {max_retries} attempts failed for TTS")
                return None
    return None

# ================= AI FUNCTIONS =================
def english_coach(child_text):
    global conversation_context
    prompt = f"""English coach for children aged 6-15.
Correct the sentence, encourage, ask ONE question.
Reply ONLY:
CORRECT: <corrected sentence>
PRAISE: <short praise>
QUESTION: <one question>
Context: {conversation_context[-400:]}
Child: "{child_text}" """
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=120
    )
    reply = response.choices[0].message.content.strip()
    conversation_context += f"\nChild: {child_text}\nAssistant: {reply}"
    conversation_context = conversation_context[-1200:]
    return reply

def roleplay_coach(child_text, roleplay_type):
    global conversation_context
    roles = {
        "teacher": "You are a kind school teacher.\nHelp the student learn English.\nAsk study-related questions.\nBe encouraging and patient.",
        "friend": "You are a friendly classmate.\nTalk casually and happily.\nAsk daily-life questions.\nBe cheerful and supportive.",
        "interviewer": "You are a job interviewer.\nBe polite and professional.\nAsk short interview questions.\nBe encouraging but professional.",
        "viva": "You are a viva examiner.\nBe polite and professional.\nAsk academic project questions.\nFocus on understanding.\nBe fair and encouraging."
    }
    role_instruction = roles.get(roleplay_type, "You are a friendly English speaking partner.")
    suggested_question = get_roleplay_question(roleplay_type)
    prompt = f"""
{role_instruction}

You are doing roleplay with a student aged 6 to 15.

STRICT RULES:
- Always correct the student's sentence
- Very simple English
- Stay strictly in your role as {roleplay_type}
- Encourage the student
- Ask ONE role-appropriate question
- No grammar explanation
- Make your question relevant to your role

Here's a suggested question for your role (you can use it or create a similar one):
"{suggested_question}"

Respond ONLY in this format:

CORRECT: <correct sentence>
PRAISE: <short encouragement>
QUESTION: <one role-appropriate question>

Conversation so far:
{conversation_context}

Student says:
"{child_text}"
"""
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=150
    )
    reply = response.choices[0].message.content.strip()
    conversation_context += f"\nStudent: {child_text}\nAssistant: {reply}"
    conversation_context = conversation_context[-1200:]
    return reply

# ================= REPEAT AFTER ME - CIVIC SENSE ADDED =================
def generate_repeat_sentence(category="civic_sense", difficulty="easy"):
    global recent_sentences
    category_details = {
        # ===== CIVIC SENSE (replaces general) =====
        "civic_sense": {
            "easy": [
                "Keep your city clean",
                "Do not litter on roads",
                "Help old people cross",
                "Wait for your turn please",
                "Say thank you always",
                "Be kind to others",
                "Do not waste water",
                "Turn off lights please",
                "Respect your neighbours always",
                "Use dustbin for waste"
            ],
            "medium": [
                "We should not throw waste on the road",
                "Always stand in a queue patiently",
                "Help keep our neighbourhood clean and tidy",
                "Switch off fans when leaving the room",
                "We must respect traffic rules always",
                "Plant trees to keep our earth green",
                "Save water for the future generations",
                "Be polite and greet everyone around you",
                "Do not make noise in public places",
                "Always use the zebra crossing safely"
            ],
            "hard": [
                "We should always keep our surroundings clean and free from litter",
                "Respecting public property is the duty of every good citizen",
                "Saving electricity and water helps protect our environment for the future",
                "Every citizen must follow traffic rules to keep roads safe for all",
                "Being kind and helpful to others makes our community a better place",
                "We must never waste food because many people go hungry every day",
                "Planting trees and caring for nature is our responsibility as citizens",
                "Good citizens always stand in a queue and wait for their turn patiently"
            ]
        },
        "animals": {
            "easy": ["Dogs bark loudly", "Cats drink milk", "Birds sing songs", "Fish swim fast", "Cows eat grass",
                     "Horses run quick", "Ducks say quack", "Lions roar loud", "Bears sleep long", "Monkeys climb trees"],
            "medium": ["The brown dog plays with a ball", "My pet cat sleeps on the sofa",
                      "Colorful birds fly in the sky", "Little fish swim in the pond",
                      "The white rabbit hops around happily", "Elephants have very long trunks",
                      "Tigers are big striped cats", "Dolphins jump in the ocean"],
            "hard": ["The big elephant uses its trunk to drink water every day",
                    "My pet dog loves to chase butterflies in the garden",
                    "The clever monkey climbs trees very quickly and easily",
                    "Beautiful peacocks spread their colorful feathers when dancing",
                    "Tiny hummingbirds can fly backwards and hover in the air"]
        },
        "food": {
            "easy": ["I eat apples", "Pizza tastes good", "Milk is white", "Bread is soft", "Ice cream melts",
                     "Cookies are sweet", "Juice is cold", "Cake is yummy", "Soup is hot", "Eggs are round"],
            "medium": ["I enjoy eating chocolate ice cream", "Fresh vegetables are good for health",
                      "Mom makes delicious pasta for lunch", "Orange juice is my favorite drink",
                      "Hot soup warms me up quickly", "Strawberries taste sweet and juicy",
                      "I love eating crunchy potato chips", "Sandwiches are perfect for picnics"],
            "hard": ["My grandmother makes the most delicious cookies in the whole world",
                    "We should eat healthy fruits and vegetables every single day",
                    "The restaurant serves fresh and tasty food to all customers",
                    "Drinking water keeps our body healthy and strong always",
                    "Breakfast is the most important meal of the entire day"]
        },
        "sports": {
            "easy": ["I play football", "Run very fast", "Jump rope daily", "Swim in pool", "Kick the ball",
                     "Throw the ball", "Catch it quick", "Hit the target", "Race with friends", "Climb the rope"],
            "medium": ["I practice basketball every single day", "Running in the park is fun",
                      "My friends play cricket together happily", "Swimming keeps us healthy and fit",
                      "The team won the match yesterday", "Soccer is played with feet",
                      "Tennis players use special rackets always", "Cycling helps build strong muscles"],
            "hard": ["Playing outdoor games helps us stay healthy and active always",
                    "My favorite sport is basketball because it's exciting and fun",
                    "The athletes train very hard to win the championship trophy",
                    "Regular exercise makes our bodies stronger and more energetic daily",
                    "Teamwork is very important when playing any sport together"]
        },
        "feelings": {
            "easy": ["I feel happy", "Mom is sad", "Brother is angry", "Sister feels tired", "I am excited",
                     "Dad is proud", "I feel scared", "She is brave", "He seems worried", "We are cheerful"],
            "medium": ["I feel very happy when playing", "My friend is feeling sad today",
                      "The movie made everyone laugh loudly", "I get excited about birthday parties",
                      "Helping others makes me feel good", "Sometimes I feel nervous before tests",
                      "My sister feels proud of her artwork", "The surprise made him very happy"],
            "hard": ["When I help my friends I feel very proud and happy",
                    "My little sister gets scared during thunderstorms at night",
                    "Winning the competition made the entire team feel wonderful",
                    "Sharing toys with others shows that we care about them",
                    "Being kind to everyone makes the world a better place"]
        },
        "colors": {
            "easy": ["Sky is blue", "Grass is green", "Sun is yellow", "Roses are red", "Clouds are white",
                     "Night is black", "Orange is bright", "Purple flowers bloom", "Pink is pretty", "Brown dirt falls"],
            "medium": ["The beautiful rainbow has many colors", "My favorite color is bright blue",
                      "Red roses bloom in the garden", "The green leaves look very fresh",
                      "Yellow butterflies fly near flowers happily", "White snow covers the ground",
                      "Orange pumpkins grow in the field", "Purple grapes taste very sweet"],
            "hard": ["The colorful painting has red blue yellow and green colors",
                    "My room walls are painted in light blue color",
                    "The sunset sky shows beautiful orange and pink shades",
                    "Rainbows appear when sunlight passes through water droplets magically",
                    "Artists mix different colors together to create new beautiful shades"]
        },
        "family": {
            "easy": ["I love mom", "Dad helps me", "Sister is kind", "Brother plays games", "Grandma tells stories",
                     "Grandpa is funny", "Baby cries loud", "Uncle visits us", "Aunt bakes cake", "Cousin is fun"],
            "medium": ["My mother cooks delicious food daily", "Dad takes me to school everyday",
                      "My sister helps with homework always", "Brother plays video games with me",
                      "Grandparents visit us every weekend regularly", "My aunt makes tasty cookies",
                      "Uncle tells us funny jokes", "Cousins play together at parties"],
            "hard": ["My entire family goes on vacation together every summer season",
                    "Mom and dad work very hard to give us everything",
                    "I love spending quality time with all my family members",
                    "Grandparents always share interesting stories from their childhood days",
                    "Family dinners are special times when everyone talks and laughs"]
        },
        "school": {
            "easy": ["I go school", "Teacher is nice", "Books are heavy", "Math is hard", "I study daily",
                     "Tests are scary", "Lunch is yummy", "Friends play together", "Pencils write words", "Classes start early"],
            "medium": ["My teacher explains lessons very clearly", "I carry my school bag everyday",
                      "Math homework is quite challenging today", "The library has many interesting books",
                      "Science class is really fun and exciting", "Friends help each other with studies",
                      "Reading improves our vocabulary and knowledge", "Art class lets us be creative"],
            "hard": ["My school has a big playground where we play games",
                    "Every morning I wake up early to catch the bus",
                    "The teacher gives us homework to practice at home daily",
                    "Learning new things at school makes us smarter every day",
                    "Good students always pay attention and complete their work on time"]
        }
    }
    cat_info = category_details.get(category, category_details["civic_sense"])
    examples = cat_info.get(difficulty, cat_info["easy"])
    available_examples = [ex for ex in examples if ex not in recent_sentences]
    if not available_examples:
        recent_sentences = recent_sentences[-5:] if len(recent_sentences) > 5 else []
        available_examples = [ex for ex in examples if ex not in recent_sentences]
    if not available_examples:
        available_examples = examples
    selected_sentence = random.choice(available_examples)
    recent_sentences.append(selected_sentence)
    if len(recent_sentences) > MAX_HISTORY:
        recent_sentences = recent_sentences[-MAX_HISTORY:]
    return selected_sentence

def generate_spell_word(difficulty="easy"):
    global recent_words
    word_pools = {
        "easy": ["cat", "dog", "sun", "run", "fun", "hat", "bat", "rat", "pen", "hen",
                 "cup", "bus", "bed", "red", "leg", "bag", "fan", "can", "ten", "net",
                 "wet", "jet", "pet", "set", "box", "fox", "six", "mix", "pig", "big",
                 "hot", "pot", "top", "hop", "mop", "zip", "tip", "dip", "cut", "nut"],
        "medium": ["apple", "table", "happy", "money", "water", "tiger", "banana", "flower",
                   "garden", "winter", "summer", "mother", "father", "sister", "better",
                   "letter", "number", "dinner", "butter", "purple", "yellow", "orange",
                   "Monday", "Friday", "Sunday", "pencil", "window", "rabbit", "market",
                   "simple", "castle", "people", "circle", "middle", "bottle", "little",
                   "bubble", "double", "jungle", "candle", "handle", "puzzle", "turtle"],
        "hard": ["beautiful", "wonderful", "elephant", "tomorrow", "yesterday", "chocolate",
                 "hamburger", "basketball", "butterfly", "strawberry", "restaurant",
                 "dictionary", "adventure", "delicious", "important", "different",
                 "incredible", "vegetables", "understand", "comfortable", "celebration",
                 "imagination", "encyclopedia", "refrigerator", "spectacular",
                 "communication", "responsibility", "extraordinary", "accomplishment"]
    }
    words = word_pools.get(difficulty, word_pools["easy"])
    available_words = [w for w in words if w not in recent_words]
    if not available_words:
        recent_words = recent_words[-10:] if len(recent_words) > 10 else []
        available_words = [w for w in words if w not in recent_words]
    if not available_words:
        available_words = words
    selected_word = random.choice(available_words)
    recent_words.append(selected_word)
    if len(recent_words) > MAX_HISTORY:
        recent_words = recent_words[-MAX_HISTORY:]
    return selected_word

def get_word_sentence_usage(word):
    prompt = f"""Create ONE simple sentence using the word "{word}" for children aged 6-15.
Requirements:
- Must use the word "{word}"
- Very simple, clear sentence
- 5 to 10 words total
- Easy to understand
Return ONLY the sentence, nothing else."""
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=50
        )
        sentence = response.choices[0].message.content.strip()
        sentence = sentence.replace('"', '').replace("'", '').strip()
        return sentence
    except:
        return f"The {word} is very nice."

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
            temperature=0.5,
            max_tokens=200
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

# ================= KEEP-ALIVE PING ROUTE =================
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
        roll_no = data.get("rollNo")
        if not roll_no or not password:
            return jsonify({"success": False, "message": "Please provide roll number and password"})
        user = conn.execute('SELECT * FROM users WHERE role=? AND roll_no=?', (role, roll_no)).fetchone()
    else:
        email = data.get("email")
        if not email or not password:
            return jsonify({"success": False, "message": "Please provide email and password"})
        user = conn.execute('SELECT * FROM users WHERE role=? AND email=?', (role, email)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        session['user_id'] = user['id']
        session['name'] = user['name']
        session['role'] = user['role']
        if role == "student":
            session['roll_no'] = user['roll_no']
            session['student_name'] = user['name']
            conn = get_db_connection()
            conn.execute('INSERT INTO student_sessions (student_id) VALUES (?)', (user['id'],))
            conn.commit()
            conn.close()
        else:
            session['email'] = user['email']
        return jsonify({"success": True, "message": "Login successful", "name": user['name']})
    else:
        return jsonify({"success": False, "message": "Invalid credentials"})

@app.route("/signup", methods=["POST"])
def signup():
    data = request.json
    name = data.get("name")
    password = data.get("password")
    role = data.get("role", "student")
    if not name or not password:
        return jsonify({"success": False, "message": "All fields are required"})
    conn = get_db_connection()
    password_hash = generate_password_hash(password)
    try:
        if role == "student":
            roll_no = data.get("rollNo")
            if not roll_no:
                return jsonify({"success": False, "message": "Roll number is required"})
            existing = conn.execute('SELECT * FROM users WHERE roll_no = ?', (roll_no,)).fetchone()
            if existing:
                conn.close()
                return jsonify({"success": False, "message": "Roll number already registered"})
            conn.execute('INSERT INTO users (role, name, roll_no, password_hash) VALUES (?, ?, ?, ?)',
                        (role, name, roll_no, password_hash))
            conn.execute('''INSERT INTO student_progress
                           (roll_no, xp, conversation_xp, roleplay_xp, repeat_xp, spellbee_xp, meanings_xp, total_stars)
                           VALUES (?, 0, 0, 0, 0, 0, 0, 0)''', (roll_no,))
        else:
            email = data.get("email")
            if not email:
                return jsonify({"success": False, "message": "Email is required"})
            existing = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            if existing:
                conn.close()
                return jsonify({"success": False, "message": "Email already registered"})
            conn.execute('INSERT INTO users (role, name, email, password_hash) VALUES (?, ?, ?, ?)',
                        (role, name, email, password_hash))
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

@app.route("/delete_account", methods=["POST"])
@student_required
def delete_account():
    if 'roll_no' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    roll_no = session['roll_no']
    user_id = session['user_id']
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM activity_log WHERE roll_no = ?', (roll_no,))
        conn.execute('DELETE FROM student_progress WHERE roll_no = ?', (roll_no,))
        conn.execute('DELETE FROM student_sessions WHERE student_id = ?', (user_id,))
        conn.execute('DELETE FROM users WHERE roll_no = ? AND role = ?', (roll_no, 'student'))
        conn.commit()
        conn.close()
        session.clear()
        return jsonify({'success': True, 'message': 'Account deleted successfully'})
    except Exception as e:
        conn.close()
        print(f"Delete account error: {e}")
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
        if roleplay:
            ai_reply = roleplay_coach(user_text, roleplay)
        else:
            ai_reply = english_coach(user_text)
        correct = praise = question = ""
        for line in ai_reply.split("\n"):
            if line.startswith("CORRECT:"):
                correct = line.replace("CORRECT:", "").strip()
            elif line.startswith("PRAISE:"):
                praise = line.replace("PRAISE:", "").strip()
            elif line.startswith("QUESTION:"):
                question = line.replace("QUESTION:", "").strip()
        final_text = f"{correct}. {praise} {question}"
        audio = speak_to_file(final_text)
        if audio is None:
            return jsonify({"reply": final_text, "audio": None,
                           "audio_error": "Audio temporarily unavailable. Please try again."})
        return jsonify({"reply": final_text, "audio": audio})
    except Exception as e:
        print(f"Error in process: {e}")
        return jsonify({"reply": "Sorry, something went wrong. Please try again.",
                       "audio": None, "error": str(e)}), 500

@app.route("/repeat_sentence", methods=["POST"])
@student_required
def repeat_sentence():
    data = request.json
    category = data.get("category", "civic_sense")  # Default changed to civic_sense
    difficulty = data.get("difficulty", "easy")
    sentence = generate_repeat_sentence(category, difficulty)
    audio_normal = speak_to_file(sentence, slow=False)
    audio_slow = speak_to_file(sentence, slow=True)
    if audio_normal is None or audio_slow is None:
        return jsonify({"sentence": sentence, "audio": None, "audio_slow": None,
                       "audio_error": "Audio temporarily unavailable. Please read the sentence."})
    return jsonify({"sentence": sentence, "audio": audio_normal, "audio_slow": audio_slow})

@app.route("/check_repeat", methods=["POST"])
@student_required
def check_repeat():
    data = request.json
    student = data["student"]
    correct = data["correct"]
    score = SequenceMatcher(None, student.lower(), correct.lower()).ratio()
    word_comparison = compare_words(student, correct)
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
    return jsonify({"feedback": feedback, "score": round(score * 100),
                   "stars": stars, "word_comparison": word_comparison})

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
        return jsonify({"word": word, "usage": usage, "audio_word": None, "audio_sentence": None,
                       "audio_error": "Audio temporarily unavailable. Please read the word."})
    return jsonify({"word": word, "usage": usage, "audio_word": audio_word, "audio_sentence": audio_sentence})

@app.route("/check_spelling", methods=["POST"])
@student_required
def check_spelling():
    data = request.json
    student_spelling = data["spelling"]
    correct_word = data["correct"]
    student = student_spelling.lower().strip()
    correct = correct_word.lower().strip()
    is_correct = (student == correct)
    letter_comparison = compare_spelling(student, correct)
    if is_correct:
        feedback = "🎉 Perfect! You spelled it correctly!"
        stars = 3
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
    return jsonify({"correct": is_correct, "feedback": feedback, "stars": stars,
                   "letter_comparison": letter_comparison, "correct_spelling": correct})

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
        return jsonify({"word": word, "meaning": meaning, "usage": usage, "type": word_type,
                       "tip": tip, "audio": None, "audio_error": "Audio temporarily unavailable."})
    return jsonify({"word": word, "meaning": meaning, "usage": usage, "type": word_type, "tip": tip, "audio": audio})

# ================= XP SYSTEM ROUTES =================
@app.route("/get_student_info")
@student_required
def get_student_info():
    if 'roll_no' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    roll_no = session['roll_no']
    conn = get_db_connection()
    student = conn.execute('SELECT name, roll_no FROM users WHERE roll_no = ?', (roll_no,)).fetchone()
    progress = conn.execute('SELECT * FROM student_progress WHERE roll_no = ?', (roll_no,)).fetchone()
    conn.close()
    if student and progress:
        progress_data = {
            'conversation_xp': progress['conversation_xp'] or 0,
            'roleplay_xp': progress['roleplay_xp'] or 0,
            'repeat_xp': progress['repeat_xp'] or 0,
            'spellbee_xp': progress['spellbee_xp'] or 0,
            'meanings_xp': progress['meanings_xp'] or 0
        }
        unlocked_features = get_unlocked_features(progress_data)
        next_unlock = get_next_unlock(progress_data)
        return jsonify({
            'success': True,
            'student': {
                'name': student['name'],
                'rollNo': student['roll_no'],
                'xp': progress['xp'],
                'conversationXp': progress_data['conversation_xp'],
                'roleplayXp': progress_data['roleplay_xp'],
                'repeatXp': progress_data['repeat_xp'],
                'spellbeeXp': progress_data['spellbee_xp'],
                'meaningsXp': progress_data['meanings_xp'],
                'totalStars': progress['total_stars'],
                'totalSessions': progress['total_sessions'],
                'averageAccuracy': round(progress['average_accuracy'], 1),
                'unlockedFeatures': unlocked_features,
                'nextUnlock': next_unlock
            }
        })
    else:
        return jsonify({'success': False, 'message': 'Student not found'})

@app.route("/update_xp", methods=["POST"])
@student_required
def update_xp():
    if 'roll_no' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = request.json
    roll_no = session['roll_no']
    xp_earned = data.get('xpEarned', 0)
    mode = data.get('mode', '').lower()
    score = data.get('score', 0)
    stars_earned = data.get('starsEarned', 0)
    conn = get_db_connection()
    progress = conn.execute('SELECT * FROM student_progress WHERE roll_no = ?', (roll_no,)).fetchone()
    if progress:
        old_progress = {
            'conversation_xp': progress['conversation_xp'] or 0,
            'roleplay_xp': progress['roleplay_xp'] or 0,
            'repeat_xp': progress['repeat_xp'] or 0,
            'spellbee_xp': progress['spellbee_xp'] or 0,
            'meanings_xp': progress['meanings_xp'] or 0
        }
        old_unlocked = get_unlocked_features(old_progress)
        new_total_xp = progress['xp'] + xp_earned
        mode_xp_column = f"{mode}_xp"
        if mode_xp_column in old_progress:
            new_mode_xp = old_progress[mode_xp_column] + xp_earned
            old_progress[mode_xp_column] = new_mode_xp
        else:
            new_mode_xp = 0
        new_unlocked = get_unlocked_features(old_progress)
        newly_unlocked_features = [f for f in new_unlocked if f not in old_unlocked]
        old_avg = progress['average_accuracy']
        total_sessions = progress['total_sessions']
        if total_sessions == 0:
            new_avg = score
        else:
            new_avg = ((old_avg * total_sessions) + score) / (total_sessions + 1)
        update_query = f'''
            UPDATE student_progress
            SET xp = ?,
                {mode_xp_column} = ?,
                total_stars = total_stars + ?,
                total_sessions = total_sessions + 1,
                average_accuracy = ?,
                last_active = ?
            WHERE roll_no = ?
        '''
        conn.execute(update_query, (new_total_xp, new_mode_xp, stars_earned, new_avg, datetime.now(), roll_no))
        conn.execute('''INSERT INTO activity_log (roll_no, mode, score, xp_earned, stars_earned)
            VALUES (?, ?, ?, ?, ?)''', (roll_no, mode, score, xp_earned, stars_earned))
        conn.commit()
        conn.close()
        next_unlock = get_next_unlock(old_progress)
        return jsonify({
            'success': True,
            'newXP': new_total_xp,
            'newModeXP': new_mode_xp,
            'mode': mode,
            'newlyUnlockedFeatures': newly_unlocked_features,
            'unlockedFeatures': new_unlocked,
            'nextUnlock': next_unlock,
            'averageAccuracy': round(new_avg, 1)
        })
    else:
        conn.close()
        return jsonify({'success': False, 'message': 'Progress not found'})

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
        SELECT u.name, u.roll_no, sp.xp, sp.conversation_xp, sp.roleplay_xp,
               sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
               sp.total_stars, sp.total_sessions, sp.average_accuracy, sp.last_active
        FROM users u
        LEFT JOIN student_progress sp ON u.roll_no = sp.roll_no
        WHERE u.role = 'student'
        ORDER BY sp.xp DESC
    ''').fetchall()
    conn.close()
    students_list = []
    for student in students:
        progress_data = {
            'conversation_xp': student['conversation_xp'] or 0,
            'roleplay_xp': student['roleplay_xp'] or 0,
            'repeat_xp': student['repeat_xp'] or 0,
            'spellbee_xp': student['spellbee_xp'] or 0,
            'meanings_xp': student['meanings_xp'] or 0
        }
        unlocked_features = get_unlocked_features(progress_data)
        students_list.append({
            'name': student['name'],
            'rollNo': student['roll_no'],
            'xp': student['xp'] or 0,
            'conversationXp': progress_data['conversation_xp'],
            'roleplayXp': progress_data['roleplay_xp'],
            'repeatXp': progress_data['repeat_xp'],
            'spellbeeXp': progress_data['spellbee_xp'],
            'meaningsXp': progress_data['meanings_xp'],
            'totalStars': student['total_stars'] or 0,
            'totalSessions': student['total_sessions'] or 0,
            'averageAccuracy': round(student['average_accuracy'] or 0, 1),
            'lastActive': student['last_active'],
            'unlockedFeatures': unlocked_features
        })
    return jsonify({'success': True, 'students': students_list})

@app.route("/get_student_details/<roll_no>")
@teacher_required
def get_student_details(roll_no):
    conn = get_db_connection()
    student = conn.execute('''
        SELECT u.name, u.roll_no, sp.xp, sp.conversation_xp, sp.roleplay_xp,
               sp.repeat_xp, sp.spellbee_xp, sp.meanings_xp,
               sp.total_stars, sp.total_sessions, sp.average_accuracy, sp.last_active
        FROM users u
        LEFT JOIN student_progress sp ON u.roll_no = sp.roll_no
        WHERE u.roll_no = ? AND u.role = 'student'
    ''', (roll_no,)).fetchone()
    if not student:
        conn.close()
        return jsonify({'success': False, 'message': 'Student not found'})
    activities = conn.execute('''
        SELECT date, mode, score, xp_earned, stars_earned
        FROM activity_log
        WHERE roll_no = ?
        ORDER BY date DESC
        LIMIT 50
    ''', (roll_no,)).fetchall()
    conn.close()
    activity_list = [{'date': a['date'], 'mode': a['mode'], 'score': round(a['score'] or 0, 1),
                      'xpEarned': a['xp_earned'], 'starsEarned': a['stars_earned']} for a in activities]
    progress_data = {
        'conversation_xp': student['conversation_xp'] or 0,
        'roleplay_xp': student['roleplay_xp'] or 0,
        'repeat_xp': student['repeat_xp'] or 0,
        'spellbee_xp': student['spellbee_xp'] or 0,
        'meanings_xp': student['meanings_xp'] or 0
    }
    unlocked_features = get_unlocked_features(progress_data)
    next_unlock = get_next_unlock(progress_data)
    student_data = {
        'name': student['name'], 'rollNo': student['roll_no'],
        'xp': student['xp'] or 0,
        'conversationXp': progress_data['conversation_xp'],
        'roleplayXp': progress_data['roleplay_xp'],
        'repeatXp': progress_data['repeat_xp'],
        'spellbeeXp': progress_data['spellbee_xp'],
        'meaningsXp': progress_data['meanings_xp'],
        'totalStars': student['total_stars'] or 0,
        'totalSessions': student['total_sessions'] or 0,
        'averageAccuracy': round(student['average_accuracy'] or 0, 1),
        'lastActive': student['last_active'],
        'unlockedFeatures': unlocked_features,
        'nextUnlock': next_unlock,
        'activityLog': activity_list
    }
    return jsonify({'success': True, 'student': student_data})

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render provides PORT
    app.run(host="0.0.0.0", port=port)
