# Salesbot MVP - Flask Chatbot Server

## Overview

Minimal Flask server that wraps an OpenRouter-powered chatbot with session management, a configurable system prompt, and FAQ context injection.

## File Structure

```
salesbot/
├── app.py              # Flask application (single file, all logic)
├── config.txt          # API key + model name (key=value format)
├── system_prompt.txt   # System prompt loaded at startup
├── faq.txt             # FAQ content, appended to system prompt
├── requirements.txt    # Python dependencies
└── PLAN.md             # This file
```

## Config File Format (`config.txt`)

```
OPENROUTER_API_KEY=sk-or-...
MODEL=anthropic/claude-sonnet-4
```

Plain key=value, one per line. Loaded once at startup.

## System Prompt Assembly

At startup, the server reads `system_prompt.txt` and `faq.txt`, then concatenates them into a single system message:

```
<contents of system_prompt.txt>

## FAQ Reference
<contents of faq.txt>
```

This combined string is used as the `system` message for every OpenRouter API call.

## Session Management

- **Storage**: In-memory Python `dict` keyed by session token (UUID4).
- **Token lifecycle**: Created on first `/ask` call (or explicit `/session/start`), returned in response body.
- **Context window**: Each session stores up to **15 messages** (user + assistant). When the limit is reached, the oldest user+assistant pair is dropped (FIFO) to make room.
- **Reset**: `POST /session/reset` clears the message history for that token. The token itself remains valid.

## API Endpoints

### `POST /session/start`

Creates a new session and returns the token.

**Response:**
```json
{ "session_id": "uuid-string" }
```

### `POST /ask`

Send a user message, get an assistant reply.

**Headers:** `X-Session-Id: <token>` (if omitted, a new session is created automatically)

**Request body:**
```json
{ "message": "Tell me about your product" }
```

**Response:**
```json
{
  "session_id": "uuid-string",
  "reply": "assistant response text",
  "message_count": 4
}
```

**Flow:**
1. Look up or create session by token.
2. Append `{"role": "user", "content": message}` to history.
3. If history exceeds 15 messages, trim oldest messages from the front.
4. Call OpenRouter `/chat/completions` with system prompt + history.
5. Append assistant reply to history.
6. Return reply + current message count.

### `POST /session/reset`

**Headers:** `X-Session-Id: <token>`

Clears message history for the session. Token stays valid.

**Response:**
```json
{ "status": "ok", "session_id": "uuid-string" }
```

## OpenRouter Integration

Single function that calls `https://openrouter.ai/api/v1/chat/completions`:

- Method: POST
- Headers: `Authorization: Bearer <key>`, `Content-Type: application/json`
- Body: `{ "model": "<from config>", "messages": [system + history] }`
- Uses `requests` library, no streaming for MVP.

## Dependencies (`requirements.txt`)

```
flask
requests
```

## Error Handling (minimal for MVP)

- Missing/invalid session ID on `/ask` → auto-create new session.
- Missing `message` field → 400.
- OpenRouter API error → 502 with error detail forwarded.
- Missing config/prompt files → fail fast on startup with clear error message.

## Out of Scope (MVP)

- Authentication / multi-user security
- Rate limiting
- Streaming responses
- Persistent storage
- Frontend UI
- Logging beyond print statements
