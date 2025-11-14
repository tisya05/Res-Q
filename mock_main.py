from dotenv import load_dotenv
import os, glob, threading, time, re, shutil, subprocess, requests

try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    from pydub import AudioSegment
    from pydub.playback import _play_with_simpleaudio
    pydub_available = True
except Exception:
    AudioSegment = None
    _play_with_simpleaudio = None
    pydub_available = False

from ip_utils import start_ip_check
from context_manager import process_user_message, clear_session, memory

try:
    from googletrans import Translator
    _gtrans = Translator()
except Exception:
    _gtrans = None

# --- Load environment variables ---
load_dotenv()

# --- Optional callback for Flask updates ---
ai_callback = None

def set_callback(callback_fn):
    """Frontend sets this to get AI updates via Flask."""
    global ai_callback
    ai_callback = callback_fn


# --- API Keys ---
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")
ELEVEN_VOICE_ID_EN = os.getenv("ELEVEN_VOICE_ID_EN") or "Z3R5wn05IrDiVCyEkUrK"
ELEVEN_VOICE_ID_HI = os.getenv("ELEVEN_VOICE_ID_HI") or "mfMM3ijQgz8QtMeKifko"
ELEVEN_VOICE_ID_SP = os.getenv("ELEVEN_VOICE_ID_SP") or "2VUqK4PEdMj16L6xTN4J"

recognizer = sr.Recognizer() if sr else None
audio_thread = None
audio_lock = threading.Lock()
mute_until = 0


# --- Cleanup ---
def cleanup_audio_files():
    for f in glob.glob("chunk_*.mp3"):
        try:
            os.remove(f)
        except Exception as e:
            print(f"[cleanup] Failed to delete {f}: {e}")

# --- ElevenLabs TTS ---
def elevenlabs_tts(text, lang="en", filename="output.mp3"):
    if not ELEVEN_API_KEY:
        print("[TTS] ELEVEN_API_KEY not set — skipping TTS.")
        return None

    # Choose voice by language
    if lang.startswith("hi"):
        voice_id = ELEVEN_VOICE_ID_HI
    elif lang.startswith("es"):
        voice_id = ELEVEN_VOICE_ID_SP
    else:
        voice_id = ELEVEN_VOICE_ID_EN

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {"text": text, "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        with open(filename, "wb") as f:
            f.write(resp.content)
        print(f"[TTS] Synthesized ({lang}) audio for: {text[:50]}...")
        return filename
    except Exception as e:
        print("[TTS] ElevenLabs request failed:", e)
        return None

# --- Play audio ---
def play_audio_interruptible(file_path):
    if not file_path or not os.path.exists(file_path):
        print("[play] Audio file missing:", file_path)
        return 0.0

    if pydub_available and AudioSegment and _play_with_simpleaudio:
        sound = AudioSegment.from_file(file_path)
        duration = sound.duration_seconds
        play_obj = _play_with_simpleaudio(sound)
        while play_obj.is_playing():
            time.sleep(0.05)
        return duration

    player = shutil.which("afplay")
    if player:
        try:
            start = time.time()
            subprocess.run([player, file_path])
            return time.time() - start
        except Exception as e:
            print("[play] afplay failed:", e)
            return 0.0

    print("[play] No audio playback available.")
    return 0.0

# --- Emergency-safe ---
def enforce_safety_response(text):
    if memory.get("emergency_type"):
        first_sentence = re.split(r"[.?!]", text)[0]
        return first_sentence.strip() + "."
    return text

# --- Speak (thread-safe) ---
def speak_text_interruptible(prompt, detected_lang="en"):
    global mute_until
    with audio_lock:
        cleanup_audio_files()
        text = process_user_message(prompt, detected_lang)
        text = enforce_safety_response(text)

        # Notify any optional Flask callback so the web UI can pick up the
        # assistant's reply via polling or saved state. This allows the
        # frontend AI response box to show Gemini replies.
        try:
            if not text:
                text = "I heard you — please tell me more about what's happening." \
                       " If this is an emergency, call local emergency services immediately."
            if ai_callback:
                try:
                    ai_callback(text)
                except Exception as _e:
                    print("[mock_main] ai_callback failed:", _e)
        except NameError:
            pass

        chunks = re.split(r"(?<=[.?!])\s+", text)
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            audio_file = elevenlabs_tts(chunk, lang=detected_lang, filename=f"chunk_{i}.mp3")
            if not audio_file:
                continue

            duration = 0
            if pydub_available and AudioSegment:
                try:
                    seg = AudioSegment.from_file(audio_file)
                    duration = seg.duration_seconds
                except Exception:
                    pass
            duration = duration or max(0.5, len(chunk.split()) * 0.42)
            mute_until = time.time() + duration + 0.35
            play_audio_interruptible(audio_file)

        mute_until = max(mute_until, time.time() + 0.25)

# --- Listening loop ---
def listen_loop():
    global audio_thread
    if not recognizer:
        while True:
            try:
                user_input = input("You: ").strip()
                if not user_input:
                    continue
                audio_thread = threading.Thread(target=speak_text_interruptible, args=(user_input, "en"))
                audio_thread.start()
            except KeyboardInterrupt:
                break
    else:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source)
            print("🎙 Listening (Google STT, auto Hindi/English/Spanish)...")
            while True:
                try:
                    if time.time() < mute_until:
                        time.sleep(0.1)
                        continue

                    audio = recognizer.listen(source, timeout=5, phrase_time_limit=8)
                    try:
                        user_input = recognizer.recognize_google(audio)
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError as e:
                        print("[STT] Google STT request failed:", e)
                        continue

                    detected_lang = "en"
                    if _gtrans and user_input:
                        try:
                            det = _gtrans.detect(user_input)
                            detected_lang = det.lang or "en"
                        except Exception:
                            pass

                    print(f"📝 You said ({detected_lang}): {user_input}")
                    if not audio_lock.locked():
                        audio_thread = threading.Thread(
                            target=speak_text_interruptible,
                            args=(user_input, detected_lang),
                        )
                        audio_thread.start()

                except sr.WaitTimeoutError:
                    continue
                except KeyboardInterrupt:
                    break

# --- IP callback ---
def ip_callback(info):
    geo = info.get("geolocation", {})
    org = geo.get("org") or geo.get("isp") or ""
    if "Vultr" in org or "Vultr" in geo.get("as", ""):
        print("Detected Vultr cloud — consider low-latency region routing.")

if __name__ == "__main__":
    try:
        start_ip_check(callback=ip_callback)
        listen_loop()
    finally:
        if audio_thread and audio_thread.is_alive():
            audio_thread.join()
        clear_session()
