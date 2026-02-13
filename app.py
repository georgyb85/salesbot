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
    for required in ("PROVIDER", "MODEL"):
        if required not in cfg:
            raise SystemExit(f"Missing '{required}' in config.txt")
    provider = cfg["PROVIDER"]
    if provider == "glm":
        for k in ("GLM_API_KEY", "GLM_BASE_URL"):
            if k not in cfg:
                raise SystemExit(f"Missing '{k}' in config.txt for provider=glm")
    elif provider == "openrouter":
        if "OPENROUTER_API_KEY" not in cfg:
            raise SystemExit("Missing 'OPENROUTER_API_KEY' in config.txt for provider=openrouter")
    else:
        raise SystemExit(f"Unknown PROVIDER '{provider}' — use 'glm' or 'openrouter'")
    return cfg


def load_text_file(name):
    path = BASE_DIR / name
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8-sig").strip()


def parse_faq_tsv(raw: str) -> str:
    """Parse tab-separated FAQ into structured Q/A blocks.

    Handles multi-line quoted answers by joining continuation lines
    (lines with fewer than 3 tab characters) back onto the previous row.
    """
    raw_lines = raw.splitlines()
    if not raw_lines:
        return raw

    # --- pass 1: merge continuation lines into full rows ---------------
    rows: list[str] = []
    for line in raw_lines[1:]:            # skip header
        if line.count("\t") >= 3:
            rows.append(line)             # new row
        elif rows:
            rows[-1] += "\n" + line       # continuation of previous answer

    # --- pass 2: parse each row into a Q/A block ----------------------
    entries = []
    for row in rows:
        cols = row.split("\t", 3)         # split into exactly 4 parts
        if len(cols) < 4:
            continue
        main_q = cols[0].strip()
        syn2 = cols[1].strip()
        syn3 = cols[2].strip()
        answer = cols[3].strip().strip('"')
        if not main_q and not answer:
            continue
        block = f"Q: {main_q}" if main_q else ""
        synonyms = [s for s in (syn2, syn3) if s]
        if synonyms:
            block += f"\n(также: {'; '.join(synonyms)})"
        block += f"\nA: {answer}"
        entries.append(block)
    return "\n\n".join(entries)


config = load_config()
system_prompt_text = load_text_file("system_prompt.txt")
faq_text = parse_faq_tsv(load_text_file("faq.txt"))

SYSTEM_MESSAGE = {
    "role": "system",
    "content": f"{system_prompt_text}\n\n{faq_text}",
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
# LLM call (GLM or OpenRouter)
# ---------------------------------------------------------------------------

def _api_url() -> str:
    if config["PROVIDER"] == "glm":
        return config["GLM_BASE_URL"].rstrip("/") + "/chat/completions"
    return "https://openrouter.ai/api/v1/chat/completions"


def _api_key() -> str:
    if config["PROVIDER"] == "glm":
        return config["GLM_API_KEY"]
    return config["OPENROUTER_API_KEY"]


def chat_completion(history: list[dict]) -> str:
    messages = [SYSTEM_MESSAGE] + history
    resp = http.post(
        _api_url(),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json={"model": config["MODEL"], "messages": messages, "temperature": 0.3},
        timeout=120,
    )
    if resp.status_code != 200:
        detail = resp.text[:500]
        raise RuntimeError(f"API {resp.status_code}: {detail}")
    data = resp.json()
    content = data["choices"][0]["message"].get("content", "")
    if not content:
        raise RuntimeError("Model returned empty content (reasoning may have exhausted token budget)")
    return content

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
