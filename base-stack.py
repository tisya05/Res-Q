from dotenv import load_dotenv
import os
import glob
import requests
import speech_recognition as sr
import threading
import time
import re
from pydub import AudioSegment
from pydub.playback import _play_with_simpleaudio

load_dotenv()

# --- API Keys ---
import google.generativeai as genai
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")

# --- Initialize recognizer and model ---
recognizer = sr.Recognizer()
model_name = "models/gemini-2.5-flash-lite"

# --- Global flags ---
stop_audio_flag = False
audio_thread = None
listening = True

# --- Clean up old audio files ---
def cleanup_audio_files():
    files = glob.glob("chunk_*.mp3")
    for f in files:
        try:
            os.remove(f)
        except Exception as e:
            print(f"Failed to delete {f}: {e}")

# --- ElevenLabs TTS ---
def elevenlabs_tts(text, filename="output.mp3"):
    url = "https://api.elevenlabs.io/v1/text-to-speech/nPczCjzI2devNBz1zQrb"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
    }
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    with open(filename, "wb") as f:
        f.write(response.content)
    time.sleep(0.05)
    return filename

# --- Play audio interruptibly ---
def play_audio_interruptible(file_path):
    global stop_audio_flag
    sound = AudioSegment.from_file(file_path)
    play_obj = _play_with_simpleaudio(sound)

    while play_obj.is_playing():
        if stop_audio_flag:
            play_obj.stop()
            break
        time.sleep(0.05)

# --- Gemini text generation ---
def gemini_reply(prompt):
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    text = response.text
    print("🤖 Gemini:", text)
    return text

# --- Speak Gemini response in chunks ---
def speak_text_interruptible(prompt):
    """Generate Gemini response and speak in chunks, can be interrupted."""
    cleanup_audio_files()  # delete previous response files
    text = gemini_reply(prompt)
    chunks = re.split(r'(?<=[.?!])\s+', text)

    global stop_audio_flag
    stop_audio_flag = False  # start playing this response

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        # stop if user spoke mid-response
        if stop_audio_flag:
            break
        audio_file = elevenlabs_tts(chunk, filename=f"chunk_{i}.mp3")
        play_audio_interruptible(audio_file)

# --- Continuous listening ---
def listen_loop():
    global listening, stop_audio_flag, audio_thread
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source)
        print("🎙 Listening continuously. Speak and pause to trigger Gemini...")

        while listening:
            try:
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=8)
                user_input = recognizer.recognize_google(audio)
                if user_input.strip():
                    print("📝 You said:", user_input)

                    # stop currently playing Gemini audio
                    stop_audio_flag = True
                    if audio_thread and audio_thread.is_alive():
                        audio_thread.join()

                    # start new Gemini response
                    audio_thread = threading.Thread(target=speak_text_interruptible, args=(user_input,))
                    audio_thread.start()

            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                continue
            except KeyboardInterrupt:
                listening = False
                break

# --- Main ---
if __name__ == "__main__":
    listen_loop()
