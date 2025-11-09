import sounddevice as sd
import numpy as np
import queue
import webrtcvad
import time
import threading
from faster_whisper import WhisperModel
from google import genai
from googletrans import Translator

from main import elevenlabs_tts, play_audio_interruptible  # TTS/play now supports Spanish

# -------------------------------
# Gemini wrapper
# -------------------------------
class GeminiAPI:
    def __init__(self, model="gemini-1.5-pro"):
        self.client = genai.Client()
        self.model = model

    def respond(self, text):
        try:
            response = self.client.models.generate_content(model=self.model, contents=text)
            return response.text.strip()
        except Exception as e:
            print("[GeminiAPI] Error:", e)
            return "माफ़ कीजिए, मैं समझ नहीं पाई।"

# -------------------------------
# Config
# -------------------------------
RATE = 16000
FRAME_DURATION = 30
FRAME_SIZE = int(RATE * FRAME_DURATION / 1000)
SILENCE_TIMEOUT = 2.8
VAD_AGGRESSIVENESS = 1
MAX_SEGMENT_LENGTH = 15

# -------------------------------
# Init
# -------------------------------
vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
audio_q = queue.Queue()
model = WhisperModel("medium", device="cpu", compute_type="int8")
gemini = GeminiAPI()
translator = Translator()

running = True
last_voice_time = 0
is_talking = False

# -------------------------------
# Audio capture
# -------------------------------
def record_audio():
    def callback(indata, frames, time_info, status):
        if status:
            print(status)
        audio_q.put(bytes(indata))

    with sd.RawInputStream(samplerate=RATE, blocksize=FRAME_SIZE, dtype="int16", channels=1, callback=callback):
        while running:
            time.sleep(0.01)

# -------------------------------
# Process VAD stream
# -------------------------------
def vad_process():
    global last_voice_time, is_talking
    voiced_frames = bytearray()
    start_time = None
    print("🎙 Listening... Speak in English, Hindi, or Spanish (auto detect).")

    while running:
        frame = audio_q.get()
        if len(frame) < FRAME_SIZE * 2:
            continue

        is_speech = vad.is_speech(frame, RATE)
        now = time.time()

        if is_speech:
            if not is_talking:
                is_talking = True
                start_time = now
                voiced_frames = bytearray()
            voiced_frames.extend(frame)
            last_voice_time = now

        elif is_talking and (now - last_voice_time) > SILENCE_TIMEOUT:
            duration = len(voiced_frames) / (2 * RATE)
            if duration > 0.8:
                print(f"\n🗣️  Detected full utterance ({duration:.1f}s)")
                process_audio(voiced_frames)
            voiced_frames = bytearray()
            is_talking = False

        if is_talking and start_time and (now - start_time) > MAX_SEGMENT_LENGTH:
            print("\n⚠️  Segment too long — processing partial.")
            process_audio(voiced_frames)
            voiced_frames = bytearray()
            is_talking = False

# -------------------------------
# Transcription + auto language
# -------------------------------
def process_audio(audio_bytes):
    np_audio = np.frombuffer(audio_bytes, np.int16).astype(np.float32) / 32768.0
    segments, info = model.transcribe(np_audio, language=None, task="transcribe")
    detected_lang = info.language or "und"
    text = " ".join([seg.text.strip() for seg in segments]).strip()

    if text and (detected_lang == "en" or detected_lang == "und"):
        try:
            det = translator.detect(text)
            detected_lang = det.lang or detected_lang
        except Exception:
            pass

    print(f"🌍 Detected language: {detected_lang}")
    if text:
        print(f"📝 You said: {text}")
        handle_text(text, detected_lang)
    else:
        print("❌ Could not transcribe — please try again.")

# -------------------------------
# Gemini + TTS
# -------------------------------
def handle_text(text, lang):
    response = gemini.respond(text)
    print(f"🤖 Gemini: {response}")
    synthesize_tts(response, lang)

def synthesize_tts(text, lang):
    print(f"[TTS] Synthesizing ({lang}) audio for: {text[:60]}...")
    audio_file = elevenlabs_tts(text, lang=lang, filename="gemini_response.mp3")
    if audio_file:
        play_audio_interruptible(audio_file)

# -------------------------------
# Start threads
# -------------------------------
threading.Thread(target=record_audio, daemon=True).start()
threading.Thread(target=vad_process, daemon=True).start()

try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    running = False
    print("🧹 Session cleared.")
