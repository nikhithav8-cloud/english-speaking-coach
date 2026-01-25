
from flask import Flask, render_template, request, jsonify
import os
from groq import Groq
from dotenv import load_dotenv
from gtts import gTTS
import uuid 
import time

# import pyttsx3

# ================= SETUP =================
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__)

conversation_context = ""

# ================= TTS =================
# def speak_to_file(text):
#     audio_path = "static/audio/reply.wav"
#     engine = pyttsx3.init()
#     engine.setProperty("rate", 150)
#     engine.save_to_file(text, audio_path)
#     engine.runAndWait()
#     engine.stop()
#     return audio_path


# def speak_to_file(text):
#     filename = f"reply_{int(time.time())}.mp3"
#     audio_path = f"static/audio/{filename}"

#     tts = gTTS(text=text, lang="en")
#     tts.save(audio_path)
#     return audio_path

def speak_to_file(text):
    os.makedirs("static/audio", exist_ok=True)

    filename = f"{uuid.uuid4()}.mp3"
    audio_path = f"static/audio/{filename}"

    tts = gTTS(text=text, lang="en")
    tts.save(audio_path)

    return audio_path


# ================= AI LOGIC =================
def english_coach(child_text):
    global conversation_context

    prompt = f"""
You are an English speaking coach for children aged 6 to 15.

STRICT RULES:
- Always correct the child's sentences.
- If the child says only ONE WORD or a short phrase, convert it into a full correct sentence from the child's input.
- Use very simple English.
- Encourage the child (Good).
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

