import uuid
from pathlib import Path

import requests as http
from flask import Flask, request, jsonify

BASE_DIR = Path(__file__).resolve().parent
MAX_MESSAGES = 15

# ---------------------------------------------------------------------------
# Startup: load config + prompts
# ---------------------------------------------------------------------------

def load_config():
    path = BASE_DIR / "config.txt"
    if not path.exists():
        raise SystemExit(f"Missing config file: {path}")
    cfg = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip()
    for required in ("OPENROUTER_API_KEY", "MODEL"):
        if required not in cfg:
            raise SystemExit(f"Missing '{required}' in config.txt")
    return cfg


def load_text_file(name):
    path = BASE_DIR / name
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")
    return path.read_text().strip()


config = load_config()
system_prompt_text = load_text_file("system_prompt.txt")
faq_text = load_text_file("faq.txt")

SYSTEM_MESSAGE = {
    "role": "system",
    "content": f"{system_prompt_text}\n\n## FAQ Reference\n{faq_text}",
}

# ---------------------------------------------------------------------------
# Session store (in-memory)
# ---------------------------------------------------------------------------

sessions: dict[str, list[dict]] = {}


def get_or_create_session(session_id: str | None) -> tuple[str, list[dict]]:
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]
    new_id = str(uuid.uuid4())
    sessions[new_id] = []
    return new_id, sessions[new_id]

# ---------------------------------------------------------------------------
# OpenRouter call
# ---------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def chat_completion(history: list[dict]) -> str:
    messages = [SYSTEM_MESSAGE] + history
    resp = http.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {config['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={"model": config["MODEL"], "messages": messages, "temperature": 0.3},
        timeout=60,
    )
    if resp.status_code != 200:
        detail = resp.text[:500]
        raise RuntimeError(f"OpenRouter {resp.status_code}: {detail}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.post("/session/start")
def session_start():
    sid, _ = get_or_create_session(None)
    return jsonify({"session_id": sid})


@app.post("/ask")
def ask():
    session_id = request.headers.get("X-Session-Id")
    body = request.get_json(silent=True) or {}
    message = body.get("message", "").strip()
    if not message:
        return jsonify({"error": "missing 'message' field"}), 400

    sid, history = get_or_create_session(session_id)

    history.append({"role": "user", "content": message})

    # Trim oldest pairs to stay within limit
    while len(history) > MAX_MESSAGES:
        history.pop(0)  # user
        if history and history[0]["role"] == "assistant":
            history.pop(0)  # assistant

    try:
        reply = chat_completion(history)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    history.append({"role": "assistant", "content": reply})

    return jsonify({
        "session_id": sid,
        "reply": reply,
        "message_count": len(history),
    })


@app.post("/session/reset")
def session_reset():
    session_id = request.headers.get("X-Session-Id")
    if not session_id or session_id not in sessions:
        return jsonify({"error": "invalid or missing session_id"}), 400
    sessions[session_id].clear()
    return jsonify({"status": "ok", "session_id": session_id})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
