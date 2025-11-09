from flask import Flask, jsonify, request
from flask_cors import CORS
import threading
import json
from mock_main import listen_loop, stop_audio_flag, cleanup_audio_files

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
    return jsonify({"status": "started"}), 200


@app.route("/stop_listening", methods=["POST"])
def stop_listening():
    global is_listening, stop_audio_flag
    is_listening = False
    stop_audio_flag = True
    cleanup_audio_files()
    return jsonify({"status": "stopped"}), 200


@app.route("/get_latest_response", methods=["GET"])
def get_latest_response():
    try:
        with open("conversation_state.json", "r") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception:
        return jsonify(conversation_state)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)