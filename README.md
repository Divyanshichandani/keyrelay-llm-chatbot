# LLM Chatbot with Key Failover and Response Caching

A production-ready full-stack application delivering resilient LLM interactions with prioritized multi-key failover, zero-cost response caching, thread-safe SQLite persistence, and an interactive React interface.

---

## 1. Live URLs
- **Frontend URL**: https://keyrelay-llm-chatbot.vercel.app
- **Backend URL**: https://keyrelay-llm-chatbot.onrender.com
- **GitHub Repository**: https://github.com/Divyanshichandani/keyrelay-llm-chatbot
- **Demo Video**: (add link after recording)

---

## 2. Run locally: with Docker and without

### How to Get Google Gemini API Keys
1. Visit [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **Get API key** and create at least two API keys (e.g., `key_1` and `key_2`) to test key failover.

### Environment Setup
1. Clone the repository and navigate to the project directory:
   ```bash
   git clone https://github.com/your-username/llm-keyrelay.git
   cd llm-keyrelay
   ```
2. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
3. Open `.env` and configure your API keys:
   ```env
   LLM_API_KEYS=your_first_gemini_key,your_second_gemini_key
   ENABLE_FAILURE_SIMULATION=true
   ```

---

### Option A: Running with Docker (Recommended)
Make sure Docker and Docker Compose are installed.
1. Build and start both backend and frontend containers:
   ```bash
   make up
   ```
   *Or using docker compose directly:*
   ```bash
   docker compose up --build -d
   ```
2. Check running services:
   - Backend API: `http://localhost:8000`
   - Frontend UI: `http://localhost:3000`
   - Health Check: `http://localhost:8000/health`
3. View logs:
   ```bash
   make logs
   ```
4. Stop containers:
   ```bash
   make down
   ```

---

### Option B: Running without Docker

#### Backend Setup
1. Navigate to the backend directory and set up a Python 3.12+ virtual environment:
   ```bash
   cd backend
   python -m venv venv
   # On Linux/macOS:
   source venv/bin/activate
   # On Windows:
   venv\Scripts\activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set local environment variables (or let `.env` in the root manage them):
   ```bash
   export DATABASE_URL="sqlite:///./data/app.db"
   export ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173"
   export ENABLE_FAILURE_SIMULATION="true"
   ```
4. Start the backend with Uvicorn:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

#### Frontend Setup
1. In a separate terminal, navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install Node dependencies:
   ```bash
   npm install
   ```
3. Start Vite dev server:
   ```bash
   npm run dev
   ```
4. Access the web app at `http://localhost:3000` (or `http://localhost:5173`).

---

### Running Automated Backend Tests
Run the test suite with mocks (no live API keys or external calls needed):
```bash
make test
# Or directly:
pytest backend/tests -v
```

---

## 3. Design decisions: key fallback, caching, SQLite session storage

### Ordered Key Failover Loop & LiteLLM Router
- **Prioritized Key Pool**: Configured via `LLM_API_KEYS` as a comma-separated list of keys, sequentially named `key_1`, `key_2`, etc.
- **Failover Triggers**: Any transient or permanent key failure—including HTTP 429 (Rate Limit / Quota Exhaustion), 5xx server errors, timeouts, connection drops, and invalid/revoked API keys—immediately counts a fallback attempt and seamlessly fails over to the next key in the pool.
- **Zero Retries per Key (`num_retries=0`)**: By setting retries to 0 per key, failover is instantaneous without stalling the client.
- **Router vs. Explicit Loop Choice**: LiteLLM's `Router` is initialized with all key deployments (tagged with deployment IDs and order) to manage deployment metadata. However, an explicit sequential execution loop is utilized for inference calls. This architecture guarantees **100% deterministic per-request tracking** of `key_id` and exact `fallback_attempts` metrics, completely eliminating global state leakage and unpredictable round-robin jitter across concurrent requests.
- **Provider Exhaustion**: If all keys in the pool fail, the backend returns a standard HTTP 503 `{"detail": "all providers unavailable"}`.

### LiteLLM Response Caching
- **Cache Scope**: Powered by `litellm.cache = Cache(type="local")` (or disk).
- **Key Formulation**: The cache key is generated strictly from the LLM model name and the full normalized message history (including session system prompt, prior conversation turns, and the new user message).
- **API Key Decoupling**: Crucially, the `api_key` is **excluded** from the cache key computation. This ensures identical queries from different requests or sessions benefit from cache hits regardless of which API key served the original request.
- **Cache Hit Attributes**: Responses served from cache return `resolution_source="LLM_CACHE"`, `key_id=null`, `fallback_attempts=0`, identical output text, and exact stored token counts, executing in sub-5ms latency.
- **Simulation Cache Bypass**: When the `X-Simulate-Failure` header is supplied, the cache is bypassed completely to guarantee that failover logic and multi-key recovery are rigorously exercised.

### SQLite Session Storage & Persistence
- **Database Schema**:
  - `sessions`: Stores `session_id`, `system_prompt`, and `created_at`.
  - `messages`: Stores `id`, `session_id`, `role`, `content`, `metadata` (JSON), and `created_at`.
  - Indexed on `session_id` for fast multi-turn lookups.
- **Atomic Success Writes**: If an LLM call fails (due to 422 validation errors, 503 provider exhaustion, or internal errors), **neither** the user prompt nor assistant message is written to the database. History is committed only after an inference call successfully returns.
- **System Prompt Isolation**: The system prompt is persisted per session so subsequent requests omit it while retaining context, but `GET /sessions/{session_id}/messages` strips the system prompt and returns purely user and assistant turns.
- **Concurrency & WAL Mode**: SQLite is initialized with `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;`, coupled with thread-safe connection management.
- **Persistent Volume**: In Docker, data is mounted at `/data/app.db` via a named Docker volume (`appdata`), ensuring message history persists through container restarts and redeployments.

---

## 4. Verification: how to see failover, cache hits and per-message token usage

Below are ready-to-run `curl` commands to verify every requirement against a running backend (`http://localhost:8000`).

### 1. Health Check
```bash
curl -i http://localhost:8000/health
```
*Expected 200:*
```json
{"status":"ok","keys_configured":2,"cache_enabled":true}
```

---

### 2. Live Chat Completion (Turn 1)
```bash
curl -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s1", "message": "What is the capital of France?"}'
```
*Expected 200:*
```json
{
  "session_id": "test-s1",
  "reply": "The capital of France is Paris.",
  "metadata": {
    "resolution_source": "LIVE_INFERENCE",
    "model": "gemini/gemini-2.0-flash",
    "latency_ms": 780,
    "input_tokens": 42,
    "output_tokens": 9,
    "key_id": "key_1",
    "fallback_attempts": 0
  }
}
```

---

### 3. Response Cache Hit (Same first message in a new session)
```bash
curl -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s2-new", "message": "What is the capital of France?"}'
```
*Expected 200 (sub-5ms, key_id null, fallback_attempts 0, resolution_source LLM_CACHE):*
```json
{
  "session_id": "test-s2-new",
  "reply": "The capital of France is Paris.",
  "metadata": {
    "resolution_source": "LLM_CACHE",
    "model": "gemini/gemini-2.0-flash",
    "latency_ms": 2,
    "input_tokens": 42,
    "output_tokens": 9,
    "key_id": null,
    "fallback_attempts": 0
  }
}
```

---

### 4. Key Failover Simulation: `key_1=429` (Falls over to `key_2`)
```bash
curl -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{"session_id": "test-s3", "message": "Tell me a fun fact about space."}'
```
*Expected 200 (served by key_2 with fallback_attempts=1):*
```json
{
  "session_id": "test-s3",
  "reply": "One day on Venus is longer than one year on Earth.",
  "metadata": {
    "resolution_source": "LIVE_INFERENCE",
    "model": "gemini/gemini-2.0-flash",
    "latency_ms": 820,
    "input_tokens": 44,
    "output_tokens": 12,
    "key_id": "key_2",
    "fallback_attempts": 1
  }
}
```

---

### 5. Key Failover Simulation: `key_1=500` (Falls over to `key_2`)
```bash
curl -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=500" \
  -d '{"session_id": "test-s4", "message": "What is quantum computing?"}'
```
*Expected 200 with `key_id: "key_2"` and `fallback_attempts: 1`.*

---

### 6. Provider Exhaustion Simulation: `*=429` -> 503 (No History Written)
```bash
curl -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: *=429" \
  -d '{"session_id": "test-exhausted", "message": "This should fail completely."}'
```
*Expected 503:*
```json
{"detail":"all providers unavailable"}
```

---

### 7. Session History Endpoint (Oldest First, No System Prompt)
```bash
curl -i http://localhost:8000/sessions/test-s1/messages
```
*Expected 200:*
```json
{
  "session_id": "test-s1",
  "messages": [
    {
      "role": "user",
      "content": "What is the capital of France?"
    },
    {
      "role": "assistant",
      "content": "The capital of France is Paris.",
      "metadata": {
        "resolution_source": "LIVE_INFERENCE",
        "input_tokens": 42,
        "output_tokens": 9,
        "model": "gemini/gemini-2.0-flash",
        "latency_ms": 780,
        "key_id": "key_1",
        "fallback_attempts": 0
      }
    }
  ]
}
```

---

### 8. Validation Error Example (HTTP 422 - Whitespace-only Message)
```bash
curl -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s1", "message": "   "}'
```
*Expected 422:*
```json
{"detail": [{"loc": ["body", "message"], "msg": "Value error, Message must be between 1 and 4000 characters after trimming whitespace", "type": "value_error"}]}
```

---

### 9. Unknown Session Error Example (HTTP 404)
```bash
curl -i http://localhost:8000/sessions/non-existent-session-id/messages
```
*Expected 404:*
```json
{"detail":"session not found"}
```

---

## 5. Setup Used: editor (Google Antigravity) and AI tools
- **Editor**: Google Antigravity IDE (Agentic Pair Programming Environment).
- **AI Tools**: Claude 3.7 Sonnet / Gemini models with full autonomous agent capabilities for planning, implementation, verification, and Dockerization.
- **Python Runtime**: Python 3.12 / 3.13 with FastAPI and Uvicorn.
- **Frontend Stack**: Node.js v24 + React 18 + Vite 6.

---

## 6. Bonus features (if any)
1. **Interactive Metadata Badges**: Each assistant reply visually highlights whether the response was satisfied from `LLM_CACHE` (green) or `LIVE_INFERENCE` (cyan), displaying the exact model, server-measured latency, input prompt tokens, and completion output tokens.
2. **Key ID & Fallback Attempt Indicators**: When a request succeeds after failover, a dedicated badge indicates which key succeeded (`key_2`, `key_3`) and how many failover attempts occurred.
3. **Dynamic Port Adaptation (`${PORT:-8000}`)**: The backend Docker container automatically respects dynamic `PORT` assignment in cloud platforms such as Render, Fly.io, Hugging Face Spaces, and Railway.
4. **Resilient Client Error Handling**: 422 validation, 503 provider outages, and network connection drops display friendly, actionable banner notices inside the conversation feed rather than crashing the client interface.
5. **Session System Prompt Customization**: Users can configure customized persona instructions (persisted in SQLite for subsequent turns).

---

## 7. Cloud Deployment Notes

### Backend (Render / Railway / Fly.io / Hugging Face Spaces)
- **Container Build**: Deploy directly using the provided `backend/Dockerfile`.
- **Environment Variables**:
  - `LLM_API_KEYS`: Your live keys (at least 2).
  - `ENABLE_FAILURE_SIMULATION`: Set to `true`.
  - `ALLOWED_ORIGINS`: Add your deployed frontend URL (e.g. `https://your-app.vercel.app`).
  - `DATABASE_URL`: `sqlite:////data/app.db`.
- **Persistent Storage**: Attach a persistent disk/volume mounted at `/data` so SQLite session history survives redeployments and container restarts.
- **Port**: The backend dynamically binds to the `$PORT` environment variable automatically provided by the hosting platform.

### Frontend (Vercel / Netlify)
- **Build Settings**:
  - Framework Preset: `Vite`
  - Root Directory: `frontend`
  - Build Command: `npm run build`
  - Output Directory: `dist`
- **Environment Variables**:
  - `VITE_API_URL`: The HTTPS URL of your deployed backend service (e.g. `https://your-backend.onrender.com`).
