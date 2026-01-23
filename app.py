# import os
# import pyttsx3
# from flask import Flask, render_template, request, jsonify
# from groq import Groq
# from dotenv import load_dotenv

# load_dotenv()

# app = Flask(__name__)
# client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# conversation_context = ""

# # ---------- AI LOGIC ----------
# def correct_sentence(student_sentence):
#     global conversation_context

#     prompt = f"""
# You are an English speaking coach for children aged 6 to 15.

# RULES:
# - Always correct wrong sentences.
# - Convert one-word answers into full sentences.
# - Simple English.
# - Friendly and short.

# FORMAT:
# CORRECT: <correct sentence>
# QUESTION: <follow-up question>

# Conversation:
# {conversation_context}

# Child says:
# "{student_sentence}"
# """

#     response = client.chat.completions.create(
#         model="llama-3.1-8b-instant",
#         messages=[{"role": "user", "content": prompt}],
#         temperature=0.2
#     )

#     reply = response.choices[0].message.content.strip()
#     conversation_context += f"\nChild: {student_sentence}\nAssistant: {reply}"
#     conversation_context = conversation_context[-800:]

#     return reply

# # ---------- TEXT TO SPEECH ----------
# def text_to_speech(text, filename):
#     engine = pyttsx3.init()
#     engine.setProperty("rate", 150)
#     engine.save_to_file(text, filename)
#     engine.runAndWait()
#     engine.stop()

# # ---------- ROUTES ----------
# @app.route("/")
# def index():
#     return render_template("index.html")

# @app.route("/chat", methods=["POST"])
# def chat():
#     user_text = request.json["text"]

#     ai_reply = correct_sentence(user_text)

#     audio_path = "static/audio/reply.mp3"
#     text_to_speech(ai_reply, audio_path)

#     return jsonify({
#         "reply": ai_reply,
#         "audio": audio_path
#     })

# if __name__ == "__main__":
#     app.run(debug=True)






# from flask import Flask, render_template, request, jsonify
# import os
# from groq import Groq
# from dotenv import load_dotenv
# import pyttsx3

# load_dotenv()
# client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# app = Flask(__name__)

# def speak_to_file(text):
#     audio_path = "static/audio/reply.wav"
#     engine = pyttsx3.init()
#     engine.setProperty("rate", 150)
#     engine.save_to_file(text, audio_path)
#     engine.runAndWait()
#     engine.stop()
#     return audio_path

# @app.route("/")
# def index():
#     return render_template("index.html")

# @app.route("/process", methods=["POST"])
# def process():
#     data = request.json
#     user_text = data["text"]

#     # AI response (simple for now)
#     ai_reply = f"You said: {user_text}. That is good English."

#     audio_file = speak_to_file(ai_reply)

#     return jsonify({
#         "reply": ai_reply,
#         "audio": "/" + audio_file
#     })

# if __name__ == "__main__":
#     app.run(debug=True)

















from flask import Flask, render_template, request, jsonify
import os
from groq import Groq
from dotenv import load_dotenv
import pyttsx3

# ================= SETUP =================
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)

conversation_context = ""

# ================= TTS =================
def speak_to_file(text):
    audio_path = "static/audio/reply.wav"
    engine = pyttsx3.init()
    engine.setProperty("rate", 150)
    engine.save_to_file(text, audio_path)
    engine.runAndWait()
    engine.stop()
    return audio_path

# ================= AI LOGIC =================
def english_coach(child_text):
    global conversation_context

    prompt = f"""
You are an English speaking coach for children aged 6 to 15.

STRICT RULES:
- Always correct the child's sentence.
- If the child says only ONE WORD or a short phrase, convert it into a full correct sentence.
- Use very simple English.
- Encourage the child.
- Ask ONE follow-up question.
- No grammar explanations.

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

    # Save context
    conversation_context += f"\nChild: {child_text}\nAssistant: {reply}"
    conversation_context = conversation_context[-1000:]

    return reply

# ================= ROUTES =================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/process", methods=["POST"])
def process():
    data = request.json
    user_text = data["text"]

    ai_reply = english_coach(user_text)

    # Parse AI response
    correct = praise = question = ""

    for line in ai_reply.split("\n"):
        if line.startswith("CORRECT:"):
            correct = line.replace("CORRECT:", "").strip()
        elif line.startswith("PRAISE:"):
            praise = line.replace("PRAISE:", "").strip()
        elif line.startswith("QUESTION:"):
            question = line.replace("QUESTION:", "").strip()

    final_text = f"{correct}. {praise} {question}"

    audio_file = speak_to_file(final_text)

    return jsonify({
        "reply": final_text,
        "audio": "/" + audio_file
    })

# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True)







