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

# ================= SETUP =================
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")  # Add this to .env file

conversation_context = ""

# ================= DATABASE SETUP =================
def init_db():
    """Initialize the SQLite database"""
    conn = sqlite3.connect('students.db')
    c = conn.cursor()
    
    # Create students table
    c.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll_no TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create sessions table for tracking student activity
    c.execute('''
        CREATE TABLE IF NOT EXISTS student_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# ================= AUTHENTICATION HELPERS =================
def login_required(f):
    """Decorator to require login for certain routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'student_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('students.db')
    conn.row_factory = sqlite3.Row
    return conn

# ================= TTS =================
def speak_to_file(text, slow=False):
    os.makedirs("static/audio", exist_ok=True)
    filename = f"{uuid.uuid4()}.mp3"
    path = f"static/audio/{filename}"
    gTTS(text=text, lang="en", slow=slow).save(path)
    return "/" + path

# ================= NORMAL CONVERSATION AI =================
def english_coach(child_text):
    global conversation_context

    prompt = f"""
You are an English speaking coach for children aged 6 to 15.

STRICT RULES:
- Always correct the child's sentence
- If only one word, make a full sentence
- Very simple English
- Encourage the child
- Ask ONE follow-up question
- No grammar explanation

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

    conversation_context += f"\nChild: {child_text}\nAssistant: {reply}"
    conversation_context = conversation_context[-1200:]

    return reply

# ================= ROLEPLAY COACH =================
def roleplay_coach(child_text, roleplay_type):
    global conversation_context

    roles = {
        "teacher": """
You are a kind school teacher.
Help the student learn English.
Ask study-related questions.
Be encouraging and patient.
""",
        "friend": """
You are a friendly classmate.
Talk casually and happily.
Ask daily-life questions.
Be cheerful and supportive.
""",
        "interviewer": """
You are a job interviewer.
Be polite and professional.
Ask short interview questions.
Be encouraging but professional.
""",
        "viva": """
You are a viva examiner.
Ask academic project questions.
Focus on understanding.
Be fair and encouraging.
"""
    }

    role_instruction = roles.get(
        roleplay_type,
        "You are a friendly English speaking partner."
    )

    prompt = f"""
{role_instruction}

You are doing roleplay with a student aged 6 to 15.

STRICT RULES:
- Always correct the student's sentence
- Very simple English
- Stay strictly in your role
- Encourage the student
- Ask ONE role-based question
- No grammar explanation

Respond ONLY in this format:

CORRECT: <correct sentence>
PRAISE: <short encouragement>
QUESTION: <one question>

Conversation so far:
{conversation_context}

Student says:
"{child_text}"
"""

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    reply = response.choices[0].message.content.strip()

    conversation_context += f"\nStudent: {child_text}\nAssistant: {reply}"
    conversation_context = conversation_context[-1200:]

    return reply

# ================= WORD/SENTENCE HISTORY TRACKING =================
# Store recently used sentences and words to avoid repetition
recent_sentences = []
recent_words = []
MAX_HISTORY = 20  # Remember last 20 sentences/words

# ================= IMPROVED REPEAT MODE AI WITH CATEGORIES AND DIFFICULTY =================
def generate_repeat_sentence(category="general", difficulty="easy"):
    global recent_sentences
    
    # Define word limits for each difficulty
    word_limits = {
        "easy": "3 to 5 words",
        "medium": "6 to 8 words",
        "hard": "9 to 12 words"
    }
   
    # Enhanced category-specific prompts with better context and examples
    category_details = {
        "general": {
            "description": "everyday activities, common objects, and simple actions",
            "easy": ["I love ice cream", "The sun is bright", "Mom reads books"],
            "medium": ["I brush my teeth every morning", "The blue sky looks very beautiful"],
            "hard": ["My favorite hobby is drawing colorful pictures in my notebook"]
        },
        "animals": {
            "description": "animals, pets, wildlife, and their behaviors",
            "easy": ["Dogs can bark loudly", "Cats like to sleep", "Birds fly very high"],
            "medium": ["My rabbit eats fresh carrots daily", "The elephant has a very long trunk"],
            "hard": ["The playful dolphin jumps high above the sparkling blue ocean waves"]
        },
        "food": {
            "description": "food items, meals, fruits, vegetables, and cooking",
            "easy": ["Pizza tastes really good", "I drink fresh milk", "Apples are so sweet"],
            "medium": ["I eat healthy vegetables every single day", "My mom makes delicious chocolate cookies"],
            "hard": ["For breakfast I enjoy eating scrambled eggs with crispy golden toast"]
        },
        "sports": {
            "description": "sports, games, physical activities, and exercise",
            "easy": ["I can run fast", "Soccer is so fun", "We play basketball well"],
            "medium": ["My sister swims in the pool today", "I practice tennis with my best friend"],
            "hard": ["Every morning I ride my bicycle to the park with my friends"]
        },
        "feelings": {
            "description": "emotions, feelings, moods, and personal expressions",
            "easy": ["I feel very happy", "She looks quite sad", "We are so excited"],
            "medium": ["My brother feels proud of his work", "I am really nervous about the test"],
            "hard": ["When my friends visit me I always feel extremely happy and joyful"]
        },
        "colors": {
            "description": "colors, shapes, sizes, and visual descriptions",
            "easy": ["The car is red", "I see yellow flowers", "Her dress looks blue"],
            "medium": ["The rainbow has many beautiful bright colors", "My new backpack is dark purple color"],
            "hard": ["The gigantic orange pumpkin sits in our garden looking absolutely magnificent"]
        },
        "family": {
            "description": "family members, relatives, friends, and relationships",
            "easy": ["Dad helps me learn", "I love my sister", "Grandma tells great stories"],
            "medium": ["My cousin visits us every summer vacation", "Uncle Tom teaches me how to swim"],
            "hard": ["On weekends my whole family enjoys eating dinner together at the table"]
        },
        "school": {
            "description": "school activities, learning, education, and classroom experiences",
            "easy": ["I like my teacher", "Math class is fun", "We learn new things"],
            "medium": ["My favorite subject in school is science", "I always do my homework after school"],
            "hard": ["During art class we create beautiful paintings using watercolors and special brushes"]
        }
    }
   
    category_info = category_details.get(category, category_details["general"])
    category_context = category_info["description"]
    word_limit = word_limits.get(difficulty, "3 to 5 words")
   
    # Get examples based on difficulty level
    examples = category_info.get(difficulty, category_info.get("easy", []))
   
    # Fallback to default examples if none found
    if not examples or len(examples) < 3:
        examples = ["I like to play", "The sun is bright", "We have fun together"]
    
    # Add recent sentences to avoid list
    avoid_list = ""
    if recent_sentences:
        avoid_list = f"\n\nIMPORTANT: DO NOT create sentences similar to these recent ones:\n" + "\n".join([f"- {s}" for s in recent_sentences[-10:]])

    # Generate unique random seed for more variety
    import random
    random_seed = random.randint(1, 10000)

    prompt = f"""You are an expert English teacher for children aged 6 to 15.

TASK: Create ONE completely UNIQUE and CREATIVE sentence for speaking practice.

CATEGORY: {category_context}
DIFFICULTY: {difficulty}
WORD COUNT: Must be {word_limit} exactly

RANDOMNESS SEED: {random_seed} (use this to create variety)

STRICT RULES:
1. Return ONLY the sentence - no quotation marks, no punctuation, no extra text
2. Use simple, natural vocabulary appropriate for children
3. Make it interesting and relatable to kids' daily lives
4. Use present tense or simple past tense
5. Avoid complex grammar structures
6. Make it sound like something a child would actually say
7. Keep it positive and encouraging
8. BE CREATIVE - think of unique situations and scenarios
9. Use different verbs, nouns, and subjects each time

GOOD EXAMPLES for {category} ({difficulty}):
- {examples[0]}
- {examples[1]}
- {examples[2]}

BAD EXAMPLES (avoid these):
- Too formal: "One should endeavor to maintain cleanliness"
- Too complex: "Notwithstanding the circumstances"
- Awkward phrasing: "The eating of vegetables is done by me"
- Negative: "I hate doing homework"
{avoid_list}

Now create ONE COMPLETELY NEW and DIFFERENT sentence following all rules above."""

    # Try up to 3 times to get a unique sentence
    max_attempts = 3
    for attempt in range(max_attempts):
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=1.2,  # INCREASED from 0.9 for more randomness
            max_tokens=50,
            top_p=0.95  # Add top_p for more diversity
        )

        sentence = response.choices[0].message.content.strip()
       
        # Clean up the sentence
        sentence = re.sub(r'^["\']+|["\']+$', '', sentence)
        sentence = re.sub(r'[.!?;:,]+$', '', sentence)
        sentence = sentence.strip()
       
        # Capitalize first letter
        if sentence:
            sentence = sentence[0].upper() + sentence[1:]
        
        # Check if sentence is unique enough
        is_unique = True
        for recent in recent_sentences:
            similarity = SequenceMatcher(None, sentence.lower(), recent.lower()).ratio()
            if similarity > 0.7:  # If more than 70% similar, try again
                is_unique = False
                break
        
        if is_unique or attempt == max_attempts - 1:
            # Add to history
            recent_sentences.append(sentence)
            if len(recent_sentences) > MAX_HISTORY:
                recent_sentences.pop(0)
            break
   
    return sentence

# ================= SPELL BEE AI =================
def generate_spell_word(difficulty="easy"):
    global recent_words
    
    difficulty_ranges = {
        "easy": "3 to 5 letter words that are common and simple for children aged 6-10",
        "medium": "6 to 8 letter words that are moderately challenging for children aged 10-13",
        "hard": "9 to 12 letter words that are challenging for children aged 13-15"
    }
    
    # Expanded word banks for more variety
    word_banks = {
        "easy": ["cat", "dog", "book", "sun", "moon", "tree", "fish", "bird", "star", "ball", 
                 "cake", "milk", "shoe", "hat", "bed", "pen", "cup", "door", "hand", "foot",
                 "red", "blue", "big", "happy", "home", "play", "jump", "run", "sit", "eat",
                 "hot", "cold", "new", "old", "day", "boy", "girl", "mom", "dad", "bike",
                 "game", "toy", "park", "farm", "pond", "hill", "rain", "snow", "wind", "corn"],
        "medium": ["elephant", "computer", "rainbow", "butterfly", "mountain", "hospital", "library",
                   "garden", "kitchen", "teacher", "doctor", "student", "picture", "monster", "dragon",
                   "princess", "castle", "rocket", "planet", "jungle", "desert", "ocean", "island",
                   "village", "city", "market", "station", "church", "temple", "bridge", "tunnel",
                   "adventure", "treasure", "mystery", "secret", "magic", "science", "history", "music",
                   "painting", "dancing", "singing", "swimming", "reading", "writing", "cooking", "building"],
        "hard": ["magnificent", "extraordinary", "temperature", "dictionary", "calculator", "independence",
                 "restaurant", "technology", "encyclopedia", "understanding", "celebration", "investigation",
                 "photography", "archaeology", "astronomy", "geography", "biography", "literature",
                 "mathematics", "multiplication", "environment", "government", "parliament", "democratic",
                 "responsibility", "appreciation", "imagination", "organization", "competition", "cooperation",
                 "communication", "transportation", "information", "education", "pollution", "conservation",
                 "civilization", "refrigerator", "entertainment", "electricity", "necessary", "opportunity"]
    }
    
    # Add recent words to avoid list
    avoid_list = ""
    if recent_words:
        avoid_list = f"\n\nIMPORTANT: DO NOT use these recent words:\n" + ", ".join(recent_words[-15:])
    
    # Generate unique random seed for more variety
    import random
    random_seed = random.randint(1, 10000)
   
    prompt = f"""You are a Spelling Bee coach for children.

CRITICAL INSTRUCTION: You must output EXACTLY ONE WORD and ABSOLUTELY NOTHING ELSE.

DIFFICULTY: {difficulty}
REQUIREMENT: {difficulty_ranges.get(difficulty, difficulty_ranges['easy'])}

RANDOMNESS SEED: {random_seed}

STRICT OUTPUT RULES - READ CAREFULLY:
1. Output ONLY a single word
2. NO quotes, NO punctuation, NO spaces, NO commas, NO explanation
3. Just the word itself - one word on one line
4. The word must contain ONLY letters (a-z)
5. Do NOT output multiple words separated by commas
6. Do NOT output a list of words
7. Do NOT add any text before or after the word

WORD SELECTION RULES:
- Use age-appropriate vocabulary
- Choose interesting words children encounter in daily life or school
- Avoid proper nouns, technical jargon, or rarely used words
- The word should be spell-able by listening to its pronunciation
- Choose words from different themes: nature, animals, food, school, sports, emotions, places, jobs, objects, actions

CORRECT OUTPUT EXAMPLES (output exactly like this):
elephant
computer
beautiful

INCORRECT OUTPUT EXAMPLES (NEVER do this):
"elephant"
elephant, computer, beautiful
The word is: elephant
elephant.
{avoid_list}

NOW OUTPUT EXACTLY ONE WORD:"""

    # Try up to 5 times to get a clean single word
    max_attempts = 5
    for attempt in range(max_attempts):
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,  # Reduced from 1.3 for more controlled output
            max_tokens=10,    # Reduced from 20 to limit output
            top_p=0.9
        )

        word = response.choices[0].message.content.strip()
        
        # Aggressive cleaning to ensure single word
        # Remove all quotes and punctuation
        word = re.sub(r'^["\'`]+|["\'`]+$', '', word)
        word = re.sub(r'[.!?;:,\s\n\r]+', '', word)
        word = word.lower()
        
        # Take only the first word if multiple words somehow got through
        if ' ' in word or '\n' in word or ',' in word:
            word = word.split()[0].split(',')[0].split('\n')[0]
        
        # Remove any non-alphabetic characters
        word = re.sub(r'[^a-z]', '', word)
        
        # Validate word length based on difficulty
        min_len = 3 if difficulty == "easy" else (6 if difficulty == "medium" else 9)
        max_len = 5 if difficulty == "easy" else (8 if difficulty == "medium" else 12)
        
        # Check if word is valid and unique
        if (word and 
            min_len <= len(word) <= max_len and 
            word.isalpha() and 
            (word not in recent_words or attempt == max_attempts - 1)):
            # Add to history
            recent_words.append(word)
            if len(recent_words) > MAX_HISTORY:
                recent_words.pop(0)
            break
        
        # If invalid, try fallback from word bank on last attempt
        if attempt == max_attempts - 1:
            available_words = [w for w in word_banks.get(difficulty, word_banks["easy"]) 
                             if w not in recent_words]
            if available_words:
                word = random.choice(available_words)
                recent_words.append(word)
                if len(recent_words) > MAX_HISTORY:
                    recent_words.pop(0)
   
    return word

def get_word_sentence_usage(word):
    prompt = f"""Create ONE simple example sentence using the word "{word}".

RULES:
1. The sentence must be simple and easy to understand for children aged 6-15
2. The sentence should clearly show the meaning of the word
3. Use simple vocabulary in the rest of the sentence
4. Make it relatable to children's daily life
5. Return ONLY the sentence - no quotes, no extra text

Example format: "The elephant is very big."

Now create a sentence using "{word}"."""

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=50
    )

    sentence = response.choices[0].message.content.strip()
    sentence = re.sub(r'^["\']+|["\']+$', '', sentence)
   
    return sentence

# ================= WORD MEANINGS AI =================
def get_word_meaning(word):
    prompt = f"""You are an English teacher explaining word meanings to children aged 6 to 15.

Word: "{word}"

Provide a clear, simple explanation of this word.

FORMAT YOUR RESPONSE EXACTLY AS:
MEANING: <simple definition in 1-2 sentences>
EXAMPLE: <one simple example sentence using the word>
TYPE: <noun/verb/adjective/adverb/etc>
TIP: <one helpful tip about using this word>

RULES:
1. Use very simple language that children can understand
2. Avoid complex terminology
3. Make examples relatable to children's daily life
4. Be encouraging and positive
5. If the word has multiple meanings, focus on the most common one
6. Keep explanations short and clear"""

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=200
    )

    return response.choices[0].message.content.strip()

# ================= WORD-BY-WORD COMPARISON =================
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

# ================= LETTER COMPARISON FOR SPELL BEE =================
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

# ================= AUTHENTICATION ROUTES =================
@app.route("/")
def home():
    """Home page - shows dashboard if logged in, otherwise redirects to login"""
    if 'student_id' in session:
        return redirect(url_for('dashboard'))  # CHANGED: redirect to dashboard
    return redirect(url_for('login_page'))

@app.route("/login")
def login_page():
    """Show login page"""
    if 'student_id' in session:
        return redirect(url_for('dashboard'))  # CHANGED: redirect to dashboard if already logged in
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login():
    """Handle login"""
    data = request.json
    roll_no = data.get("rollNo")
    password = data.get("password")
    
    if not roll_no or not password:
        return jsonify({"success": False, "message": "Please provide roll number and password"})
    
    conn = get_db_connection()
    student = conn.execute('SELECT * FROM students WHERE roll_no = ?', (roll_no,)).fetchone()
    conn.close()
    
    if student and check_password_hash(student['password_hash'], password):
        # Set session
        session['student_id'] = student['id']
        session['student_name'] = student['name']
        session['roll_no'] = student['roll_no']
        
        # Log session
        conn = get_db_connection()
        conn.execute('INSERT INTO student_sessions (student_id) VALUES (?)', (student['id'],))
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": "Login successful"})
    else:
        return jsonify({"success": False, "message": "Invalid roll number or password"})

@app.route("/signup", methods=["POST"])
def signup():
    """Handle signup"""
    data = request.json
    name = data.get("name")
    roll_no = data.get("rollNo")
    password = data.get("password")
    
    if not name or not roll_no or not password:
        return jsonify({"success": False, "message": "All fields are required"})
    
    # Check if roll number already exists
    conn = get_db_connection()
    existing = conn.execute('SELECT * FROM students WHERE roll_no = ?', (roll_no,)).fetchone()
    
    if existing:
        conn.close()
        return jsonify({"success": False, "message": "Roll number already registered"})
    
    # Create new student
    password_hash = generate_password_hash(password)
    try:
        conn.execute('INSERT INTO students (name, roll_no, password_hash) VALUES (?, ?, ?)',
                    (name, roll_no, password_hash))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Account created successfully"})
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "message": "Error creating account"})

@app.route("/logout")
def logout():
    """Handle logout"""
    session.clear()
    return redirect(url_for('login_page'))

# ================= DASHBOARD ROUTE (NEW) =================
@app.route("/dashboard")
@login_required
def dashboard():
    """Show student dashboard"""
    return render_template("dashboard.html")

# ================= MAIN APPLICATION ROUTES =================
@app.route("/main")
@login_required
def main():
    """Main application page"""
    return render_template("main.html")

# ---------- NORMAL SPEAKING + ROLEPLAY ----------
@app.route("/process", methods=["POST"])
@login_required
def process():
    data = request.json
    user_text = data["text"]
    roleplay = data.get("roleplay")

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

    return jsonify({
        "reply": final_text,
        "audio": audio
    })

# ---------- REPEAT AFTER ME ----------
@app.route("/repeat_sentence", methods=["POST"])
@login_required
def repeat_sentence():
    data = request.json
    category = data.get("category", "general")
    difficulty = data.get("difficulty", "easy")
   
    sentence = generate_repeat_sentence(category, difficulty)
    audio_normal = speak_to_file(sentence, slow=False)
    audio_slow = speak_to_file(sentence, slow=True)

    return jsonify({
        "sentence": sentence,
        "audio": audio_normal,
        "audio_slow": audio_slow
    })

@app.route("/check_repeat", methods=["POST"])
@login_required
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

    return jsonify({
        "feedback": feedback,
        "score": round(score * 100),
        "stars": stars,
        "word_comparison": word_comparison
    })

# ---------- SPELL BEE ----------
@app.route("/spell_word", methods=["POST"])
@login_required
def spell_word():
    data = request.json
    difficulty = data.get("difficulty", "easy")
   
    word = generate_spell_word(difficulty)
    usage = get_word_sentence_usage(word)
   
    # Create audio for the word (slow pronunciation)
    audio_word = speak_to_file(word, slow=True)
   
    # Create audio for the usage sentence
    audio_sentence = speak_to_file(usage, slow=False)
   
    return jsonify({
        "word": word,
        "usage": usage,
        "audio_word": audio_word,
        "audio_sentence": audio_sentence
    })

@app.route("/check_spelling", methods=["POST"])
@login_required
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
        # Calculate similarity for partial credit
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
   
    return jsonify({
        "correct": is_correct,
        "feedback": feedback,
        "stars": stars,
        "letter_comparison": letter_comparison,
        "correct_spelling": correct
    })

# ---------- WORD MEANINGS ----------
@app.route("/get_meaning", methods=["POST"])
@login_required
def get_meaning():
    data = request.json
    word = data["word"]
   
    meaning_response = get_word_meaning(word)
   
    # Parse the response
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
   
    # Generate audio for the complete explanation
    audio_text = f"{word}. {meaning}. For example: {usage}. {tip}"
    audio = speak_to_file(audio_text, slow=False)
   
    return jsonify({
        "word": word,
        "meaning": meaning,
        "usage": usage,
        "type": word_type,
        "tip": tip,
        "audio": audio
    })

# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True)