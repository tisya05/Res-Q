from flask import Flask, jsonify, request
from flask_cors import CORS
import threading
import json
from mock_main import listen_loop, cleanup_audio_files
import mock_main as _mock_main
import os
import uuid
import speech_recognition as sr
from pydub import AudioSegment
from ip_utils import detect_ip_info

app = Flask(__name__)

CORS(app, resources={r"/*": {"origins": "*"}})  # ← This is CRUCIAL

is_listening = False
listen_thread = None

# --- Shared state storage ---
conversation_state = {
    "last_response": "",
    "locations": [],
    "summary": []
}


@app.route("/")
def home():
    return jsonify({"message": "RES-Q backend running"})


@app.route("/start_listening", methods=["POST"])
def start_listening():
    global is_listening, listen_thread
    if is_listening:
        return jsonify({"status": "already listening"}), 200

    is_listening = True
    # Clear any previous conversation state and context so each call is a blank slate
    try:
        # clear in-memory state
        conversation_state["last_response"] = ""
        conversation_state["locations"] = []
        conversation_state["summary"] = []
        # clear saved file if present
        try:
            import os as _os
            _fp = "conversation_state.json"
            if _os.path.exists(_fp):
                _os.remove(_fp)
        except Exception:
            pass
        # clear context_manager session if available
        try:
            from context_manager import clear_session as _clear_sess
            _clear_sess()
        except Exception:
            pass
    except Exception:
        pass

    def run_listening():
        from mock_main import set_callback

        # callback from mock_main when AI responds
        def on_ai_response(text):
            conversation_state["last_response"] = text
            # Mock extraction for demo
            conversation_state["locations"] = [
                "Community Center - 123 Main St",
                "City Hall Shelter - 456 Oak Ave"
            ]
            conversation_state["summary"] = [
                "earthquake", "Amherst", "evacuation", "help"
            ]
            with open("conversation_state.json", "w") as f:
                json.dump(conversation_state, f)

        set_callback(on_ai_response)
        listen_loop()

    listen_thread = threading.Thread(target=run_listening, daemon=True)
    listen_thread.start()

    # Return initial IP/location info so the frontend can pre-populate the call screen
    try:
        ip_info = detect_ip_info()
        return jsonify({"status": "started", "ip_info": ip_info}), 200
    except Exception:
        return jsonify({"status": "started"}), 200


@app.route("/stop_listening", methods=["POST"])
def stop_listening():
    global is_listening
    is_listening = False
    # Some versions of the audio loop expose a stop flag; set it if present.
    try:
        setattr(_mock_main, "stop_audio_flag", True)
    except Exception:
        pass
    cleanup_audio_files()
    return jsonify({"status": "stopped"}), 200


@app.route("/get_latest_response", methods=["GET"])
def get_latest_response():
    try:
        with open("conversation_state.json", "r") as f:
            data = json.load(f)
        # Include a best-effort IP/location hint so frontend can show current location
        try:
            ip_info = detect_ip_info()
            data.setdefault("location_info", {})
            data["location_info"]["public_ip"] = ip_info.get("public_ip")
            geo = ip_info.get("geolocation") or {}
            data["location_info"]["city"] = geo.get("city")
            data["location_info"]["region"] = geo.get("regionName") or geo.get("region")
            data["location_info"]["country"] = geo.get("country")
        except Exception:
            # ignore IP errors; return whatever we have
            pass
        return jsonify(data)
    except Exception:
        return jsonify(conversation_state)


@app.route("/get_ip", methods=["GET"])
def get_ip():
    """Return current IP + geolocation info (cached by ip_utils)."""
    try:
        info = detect_ip_info()
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/upload_audio", methods=["POST"])
def upload_audio():
    """Accept a recorded audio blob from the browser, transcribe it,
    run the assistant, synthesize a TTS response and return text + audio URL.
    """
    try:
        if "audio" not in request.files:
            return jsonify({"error": "no audio file"}), 400

        f = request.files["audio"]
        # ensure directories
        os.makedirs("static", exist_ok=True)

        uid = uuid.uuid4().hex
        orig_path = os.path.join("static", f"upload_{uid}")
        f.save(orig_path)

        wav_path = os.path.join("static", f"upload_{uid}.wav")

        # Try to convert to WAV using pydub (supports webm/ogg/mp4 from browser)
        try:
            aud = AudioSegment.from_file(orig_path)
            aud = aud.set_channels(1).set_frame_rate(16000)
            aud.export(wav_path, format="wav")
        except Exception:
            # If conversion fails and uploaded was already wav-like, try renaming
            try:
                os.rename(orig_path, wav_path)
            except Exception:
                return jsonify({"error": "failed to process audio file"}), 500

        # Transcribe using SpeechRecognition (Google STT)
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
        try:
            transcript = recognizer.recognize_google(audio_data)
        except sr.UnknownValueError:
            transcript = ""
        except Exception as e:
            return jsonify({"error": f"transcription failed: {e}"}), 500

        # Run the AI pipeline (context_manager) to get a reply
        try:
            from context_manager import process_user_message

            reply = process_user_message(transcript)
        except Exception as e:
            reply = f"(assistant error: {e})"

        # Synthesize TTS using existing elevenlabs helper if available
        audio_url = None
        try:
            out_file = os.path.join("static", f"ai_response_{uid}.mp3")
            # prefer mock_main's elevenlabs_tts if present
            if hasattr(_mock_main, "elevenlabs_tts"):
                _mock_main.elevenlabs_tts(reply, filename=out_file)
            else:
                # fallback: write an empty mp3 or skip
                with open(out_file, "wb") as fh:
                    fh.write(b"")
            audio_url = "/static/" + os.path.basename(out_file)
        except Exception:
            audio_url = None

        return jsonify({"transcript": transcript, "reply": reply, "audio_url": audio_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)