from flask import Flask, request, render_template, jsonify, send_file
from backend.core import speak_text_interruptible, elevenlabs_tts, process_user_message, cleanup_audio_files
import threading
import os
import uuid

app = Flask(__name__)

# --- Home Page ---
@app.route("/")
def index():
    return render_template("index.html")

# --- API: Process text and return audio
@app.route("/process_text", methods=["POST"])
def process_text():
    data = request.get_json()
    user_text = data.get("text", "").strip()
    if not user_text:
        return jsonify({"error": "Empty text"}), 400
    
    ai_response = process_user_message(user_text)
    print(f"User: {user_text}\nAI: {ai_response}")

    audio_filename = f"static/audio_{uuid.uuid4().hex}.mp3"
    elevenlabs_tts(ai_response, filename = audio_filename)

    return jsonify({
        "response text": ai_response,
        "audio url": f"/{audio_filename}"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug = True)