
# from flask import Flask, render_template, request, jsonify
# import os
# from dotenv import load_dotenv
# from gtts import gTTS
# from difflib import SequenceMatcher
# from groq import Groq
# import uuid

# # ---------------- SETUP ----------------
# load_dotenv()
# client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# app = Flask(__name__)

# # ---------------- TTS ----------------
# def speak_to_file(text):
#     os.makedirs("static/audio", exist_ok=True)
#     filename = f"{uuid.uuid4()}.mp3"
#     path = f"static/audio/{filename}"
#     gTTS(text=text, lang="en").save(path)
#     return "/" + path

# # ---------------- AI: GENERATE SENTENCE ----------------
# def generate_repeat_sentence():
#     prompt = """
# You are an English teacher for children aged 6 to 15.

# Rules:
# - Give ONLY ONE sentence
# - Very simple English
# - Suitable for speaking practice
# - No punctuation explanation
# - No emojis
# - Max 10 words

# Examples (do NOT repeat these):
# Good morning teacher
# I like playing with my friends

# Now give ONE new sentence.
# """

#     response = client.chat.completions.create(
#         model="llama-3.1-8b-instant",
#         messages=[{"role": "user", "content": prompt}],
#         temperature=0.8
#     )

#     sentence = response.choices[0].message.content.strip()
#     return sentence

# # ---------------- ROUTES ----------------
# @app.route("/")
# def index():
#     return render_template("test.html")

# @app.route("/repeat_sentence")
# def repeat_sentence():
#     sentence = generate_repeat_sentence()
#     audio = speak_to_file(sentence)

#     return jsonify({
#         "sentence": sentence,
#         "audio": audio
#     })

# @app.route("/check_repeat", methods=["POST"])
# def check_repeat():
#     data = request.json
#     student = data["student"]
#     correct = data["correct"]

#     score = SequenceMatcher(None, student.lower(), correct.lower()).ratio()

#     if score >= 0.85:
#         feedback = "Excellent 👍 Very clear pronunciation!"
#     elif score >= 0.6:
#         feedback = "Good 🙂 Try to speak a little more clearly."
#     else:
#         feedback = "Try again 😄 Speak slowly and clearly."

#     return jsonify({
#         "feedback": feedback,
#         "score": round(score * 100)
#     })

# # ---------------- RUN ----------------
# if __name__ == "__main__":
#     app.run(debug=True)



from flask import Flask, render_template, request, jsonify
import os
from dotenv import load_dotenv
from gtts import gTTS
from difflib import SequenceMatcher
from groq import Groq
import uuid

# ================= SETUP =================
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)

conversation_context = ""

# ================= TTS =================
def speak_to_file(text):
    os.makedirs("static/audio", exist_ok=True)
    filename = f"{uuid.uuid4()}.mp3"
    path = f"static/audio/{filename}"
    gTTS(text=text, lang="en").save(path)
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

# ================= REPEAT MODE AI =================
def generate_repeat_sentence():
    prompt = """
You are an English teacher for children aged 6 to 15.

Rules:
- Give ONLY ONE sentence
- Very simple English
- For speaking practice
- Max 10 words
- No emojis
- No explanation

Give a NEW sentence every time.
"""

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8
    )

    return response.choices[0].message.content.strip()

# ================= ROUTES =================
@app.route("/")
def index():
    return render_template("new.html")

# ---------- NORMAL SPEAKING ----------
@app.route("/process", methods=["POST"])
def process():
    data = request.json
    user_text = data["text"]

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
@app.route("/repeat_sentence")
def repeat_sentence():
    sentence = generate_repeat_sentence()
    audio = speak_to_file(sentence)

    return jsonify({
        "sentence": sentence,
        "audio": audio
    })

@app.route("/check_repeat", methods=["POST"])
def check_repeat():
    data = request.json
    student = data["student"]
    correct = data["correct"]

    score = SequenceMatcher(None, student.lower(), correct.lower()).ratio()

    if score >= 0.85:
        feedback = "Excellent 👍 Very clear pronunciation!"
    elif score >= 0.6:
        feedback = "Good 🙂 Try speaking more clearly."
    else:
        feedback = "Try again 😄 Speak slowly."

    return jsonify({
        "feedback": feedback,
        "score": round(score * 100)
    })

# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True)
