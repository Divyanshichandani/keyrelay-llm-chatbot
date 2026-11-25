import os
import tempfile
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from litellm.types.utils import ModelResponse, Choices, Message, Usage
import litellm

# Set environment before loading app
temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
temp_db_path = temp_db_file.name
temp_db_file.close()

os.environ["DATABASE_URL"] = f"sqlite:///{temp_db_path}"
os.environ["LLM_API_KEYS"] = "test_key_1,test_key_2"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"

from app.config import settings
settings.DATABASE_URL = f"sqlite:///{temp_db_path}"
settings.LLM_API_KEYS = "test_key_1,test_key_2"
settings.ENABLE_FAILURE_SIMULATION = True

from app.main import app
from app.db import init_db
import app.llm as llm_module

init_db(temp_db_path)
client = TestClient(app)


def make_mock_response(content="Paris is the capital of France.", prompt_tokens=42, completion_tokens=9, model="gemini/gemini-2.0-flash"):
    return ModelResponse(
        id="mock-resp-id",
        choices=[Choices(message=Message(content=content, role="assistant"))],
        created=1700000000,
        model=model,
        usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens)
    )


@pytest.fixture(autouse=True)
def clean_cache():
    if hasattr(llm_module.cache, "cache") and hasattr(llm_module.cache.cache, "cache_dict"):
        llm_module.cache.cache.cache_dict.clear()
    yield


# 1. Health check shape
def test_health_check_shape():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["keys_configured"] == 2
    assert data["cache_enabled"] is True


# 2. Validation errors (422)
def test_chat_validation_errors():
    # Empty message
    r = client.post("/chat", json={"session_id": "s-val-1", "message": ""})
    assert r.status_code == 422
    assert "detail" in r.json()

    # Whitespace-only message
    r = client.post("/chat", json={"session_id": "s-val-2", "message": "    "})
    assert r.status_code == 422

    # 4001-character message
    long_msg = "a" * 4001
    r = client.post("/chat", json={"session_id": "s-val-3", "message": long_msg})
    assert r.status_code == 422

    # Missing session_id
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 422

    # Malformed JSON
    r = client.post("/chat", content="{bad_json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


# 3. Happy-path /chat response shape and types
@patch("litellm.completion")
def test_happy_path_chat(mock_completion):
    mock_completion.return_value = make_mock_response("The capital of France is Paris.", 42, 9)

    payload = {"session_id": "s-happy-1", "message": "What is the capital of France?"}
    r = client.post("/chat", json=payload)
    assert r.status_code == 200
    data = r.json()

    assert data["session_id"] == "s-happy-1"
    assert data["reply"] == "The capital of France is Paris."
    meta = data["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/gemini-2.0-flash"
    assert isinstance(meta["latency_ms"], int)
    assert meta["input_tokens"] == 42
    assert meta["output_tokens"] == 9
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0


# 4. Multi-turn memory: history passed to the LLM contains earlier turns
@patch("litellm.completion")
def test_multi_turn_memory(mock_completion):
    mock_completion.return_value = make_mock_response("Answer 1", 10, 5)
    sid = "s-multiturn-1"

    # Turn 1
    r1 = client.post("/chat", json={"session_id": sid, "message": "Hello my name is Alice"})
    assert r1.status_code == 200

    # Turn 2
    mock_completion.return_value = make_mock_response("Your name is Alice", 25, 6)
    r2 = client.post("/chat", json={"session_id": sid, "message": "What is my name?"})
    assert r2.status_code == 200

    # Verify the last call to litellm.completion received history
    last_call_kwargs = mock_completion.call_args.kwargs
    messages_sent = last_call_kwargs["messages"]

    # Expect: system prompt, turn 1 user, turn 1 assistant, turn 2 user
    roles = [m["role"] for m in messages_sent]
    contents = [m["content"] for m in messages_sent]
    assert roles == ["system", "user", "assistant", "user"]
    assert contents[1] == "Hello my name is Alice"
    assert contents[2] == "Answer 1"
    assert contents[3] == "What is my name?"


# 5. Session isolation
@patch("litellm.completion")
def test_session_isolation(mock_completion):
    mock_completion.return_value = make_mock_response("Reply", 10, 5)

    client.post("/chat", json={"session_id": "s-iso-A", "message": "Secret message for A"})
    client.post("/chat", json={"session_id": "s-iso-B", "message": "Message for B"})

    last_messages = mock_completion.call_args.kwargs["messages"]
    user_msgs = [m["content"] for m in last_messages if m["role"] == "user"]
    assert "Secret message for A" not in user_msgs
    assert user_msgs == ["Message for B"]


# 6. History endpoint: order, no system prompt, metadata on assistant only
@patch("litellm.completion")
def test_history_endpoint(mock_completion):
    sid = "s-hist-1"
    mock_completion.return_value = make_mock_response("Paris is the capital.", 30, 8)

    client.post("/chat", json={"session_id": sid, "message": "What is Paris?"})

    r = client.get(f"/sessions/{sid}/messages")
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == sid
    messages = data["messages"]
    assert len(messages) == 2

    # User message
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "What is Paris?"
    assert "metadata" not in messages[0] or messages[0]["metadata"] is None

    # Assistant message
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Paris is the capital."
    meta = messages[1]["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["input_tokens"] == 30
    assert meta["output_tokens"] == 8

    # Ensure system prompt is NOT present
    for m in messages:
        assert m["role"] != "system"


# 7. 404 for unknown session
def test_unknown_session_404():
    r = client.get("/sessions/unknown-session-999/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


# 8. Cache hit on same first message in a new session
@patch("litellm.completion")
def test_cache_hit_new_session(mock_completion):
    mock_completion.return_value = make_mock_response("Cached answer text", 40, 10)

    # Session 1: Live inference
    r1 = client.post("/chat", json={"session_id": "s-cache-1", "message": "What is photosynthesis?"})
    assert r1.status_code == 200
    assert r1.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert r1.json()["metadata"]["key_id"] == "key_1"
    assert mock_completion.call_count == 1

    # Session 2: Same first message in new session with same default system prompt
    r2 = client.post("/chat", json={"session_id": "s-cache-2", "message": "What is photosynthesis?"})
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["reply"] == "Cached answer text"
    meta2 = data2["metadata"]
    assert meta2["resolution_source"] == "LLM_CACHE"
    assert meta2["key_id"] is None
    assert meta2["fallback_attempts"] == 0
    assert meta2["input_tokens"] == 40
    assert meta2["output_tokens"] == 10
    # litellm.completion should NOT have been called a second time
    assert mock_completion.call_count == 1


# 9. Different message is not a cache hit
@patch("litellm.completion")
def test_different_message_not_cache_hit(mock_completion):
    mock_completion.side_effect = [
        make_mock_response("Answer 1", 20, 5),
        make_mock_response("Answer 2", 22, 6)
    ]
    r1 = client.post("/chat", json={"session_id": "s-diff-1", "message": "Question 1"})
    assert r1.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"

    r2 = client.post("/chat", json={"session_id": "s-diff-2", "message": "Question 2"})
    assert r2.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert mock_completion.call_count == 2


# 10. Failover: key_1=429 -> key_2
@patch("litellm.completion")
def test_failover_simulation_key_1_429(mock_completion):
    mock_completion.return_value = make_mock_response("Answer from key 2", 30, 8)

    headers = {"X-Simulate-Failure": "key_1=429"}
    r = client.post("/chat", json={"session_id": "s-failover-1", "message": "Test failover"}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Answer from key 2"
    assert data["metadata"]["key_id"] == "key_2"
    assert data["metadata"]["fallback_attempts"] == 1
    assert data["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# 11. Failover: key_1=500 -> key_2
@patch("litellm.completion")
def test_failover_simulation_key_1_500(mock_completion):
    mock_completion.return_value = make_mock_response("Answer from key 2 after 500", 30, 8)

    headers = {"X-Simulate-Failure": "key_1=500"}
    r = client.post("/chat", json={"session_id": "s-failover-2", "message": "Test failover 500"}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["metadata"]["key_id"] == "key_2"
    assert data["metadata"]["fallback_attempts"] == 1


# 12. Real 429 raised by mocked provider -> next key
@patch("litellm.completion")
def test_real_exception_failover(mock_completion):
    from litellm.exceptions import RateLimitError
    # First call raises RateLimitError, second succeeds
    mock_completion.side_effect = [
        RateLimitError(message="Rate limit exceeded", llm_provider="gemini", model="gemini/gemini-2.0-flash"),
        make_mock_response("Recovered via key 2", 25, 7)
    ]

    r = client.post("/chat", json={"session_id": "s-real-fail-1", "message": "Live rate limit test"})
    assert r.status_code == 200
    data = r.json()
    assert data["metadata"]["key_id"] == "key_2"
    assert data["metadata"]["fallback_attempts"] == 1
    assert data["reply"] == "Recovered via key 2"


# 13. *=429 -> 503 and NO history written
@patch("litellm.completion")
def test_all_keys_failed_503_no_history(mock_completion):
    headers = {"X-Simulate-Failure": "*=429"}
    sid = "s-all-fail-1"

    r = client.post("/chat", json={"session_id": sid, "message": "Will fail"}, headers=headers)
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}

    # History endpoint must return 404 because no history was written
    r_hist = client.get(f"/sessions/{sid}/messages")
    assert r_hist.status_code == 404


# 14. Header ignored when ENABLE_FAILURE_SIMULATION=false
@patch("litellm.completion")
def test_simulation_disabled_ignores_header(mock_completion):
    mock_completion.return_value = make_mock_response("Normal answer", 20, 5)

    with patch.object(settings, "ENABLE_FAILURE_SIMULATION", False):
        headers = {"X-Simulate-Failure": "*=429"}
        r = client.post("/chat", json={"session_id": "s-ignore-sim", "message": "Ignored simulation"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["metadata"]["key_id"] == "key_1"
        assert r.json()["metadata"]["fallback_attempts"] == 0


# 15. Tokens are per-request (not cumulative)
@patch("litellm.completion")
def test_tokens_are_per_request(mock_completion):
    # Two requests with equal-length first messages in new sessions
    mock_completion.side_effect = [
        make_mock_response("Reply 1", prompt_tokens=50, completion_tokens=10),
        make_mock_response("Reply 2", prompt_tokens=50, completion_tokens=10)
    ]

    r1 = client.post("/chat", json={"session_id": "s-tok-1", "message": "A message of length X"})
    r2 = client.post("/chat", json={"session_id": "s-tok-2", "message": "B message of length X"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["metadata"]["input_tokens"] == 50
    assert r2.json()["metadata"]["input_tokens"] == 50


# 16. DB persists across app restart
@patch("litellm.completion")
def test_db_persistence_across_restart(mock_completion):
    mock_completion.return_value = make_mock_response("Stored reply", 20, 5)
    sid = "s-persist-restart"

    # Send a message to save in SQLite
    r1 = client.post("/chat", json={"session_id": sid, "message": "Remember this"})
    assert r1.status_code == 200

    # Simulate app restart by creating a new TestClient with the same database file
    new_client = TestClient(app)
    r2 = new_client.get(f"/sessions/{sid}/messages")
    assert r2.status_code == 200
    msgs = r2.json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["content"] == "Remember this"
    assert msgs[1]["content"] == "Stored reply"
