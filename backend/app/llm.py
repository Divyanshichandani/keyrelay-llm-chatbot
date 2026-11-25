import logging
import time
from typing import List, Dict, Any, Optional, Tuple
import litellm
from litellm import Router
from litellm.caching.caching import Cache
from app.config import settings

logger = logging.getLogger("keyrelay.llm")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Initialize LiteLLM cache
cache = Cache(type=settings.CACHE_TYPE)
litellm.cache = cache

# Initialize LiteLLM Router with deployments for the key pool
def build_router(keys: List[str], model: str) -> Router:
    model_list = []
    for idx, key in enumerate(keys):
        key_id = f"key_{idx + 1}"
        model_list.append({
            "model_name": model,
            "litellm_params": {
                "model": model,
                "api_key": key,
            },
            "model_info": {
                "id": key_id,
                "order": idx
            }
        })
    return Router(
        model_list=model_list,
        num_retries=0,
        allowed_fails=3,
        cooldown_time=30
    )

router = build_router(settings.key_pool, settings.LLM_MODEL)


class AllProvidersUnavailableException(Exception):
    """Raised when all configured LLM keys have failed."""
    pass


def parse_simulation_header(header_val: Optional[str]) -> Dict[str, int]:
    """
    Parses comma-separated pairs like 'key_1=429,key_2=500' or '*=429'.
    Allowed statuses: 429, 500, 502, 503.
    Malformed pairs are ignored gracefully.
    """
    if not header_val:
        return {}
    sim_map = {}
    for part in header_val.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if not k or not v:
            continue
        try:
            status = int(v)
            if status in (429, 500, 502, 503):
                sim_map[k] = status
        except ValueError:
            continue
    return sim_map


def call_llm(
    messages: List[Dict[str, str]],
    simulation_header: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None
) -> Dict[str, Any]:
    """
    Executes an LLM completion with key failover, LiteLLM response caching,
    and simulation hooks.
    """
    target_model = model or settings.LLM_MODEL
    req_timeout = timeout or settings.LLM_TIMEOUT
    keys = settings.key_pool

    # Check simulation header if enabled
    sim_enabled = settings.ENABLE_FAILURE_SIMULATION
    sim_map = parse_simulation_header(simulation_header) if (sim_enabled and simulation_header) else {}
    has_simulation = bool(sim_map)

    # 1. Check response cache if simulation is NOT present
    # Cache key depends on model + full messages list, NOT api_key
    cache_key = cache.get_cache_key(model=target_model, messages=messages)

    if not has_simulation:
        cached_resp = cache.get_cache(cache_key=cache_key)
        if cached_resp is not None:
            # LiteLLM cache returns a dictionary or ModelResponse object
            if isinstance(cached_resp, dict):
                reply_text = cached_resp["choices"][0]["message"]["content"]
                usage = cached_resp.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0) if usage else 0
                output_tokens = usage.get("completion_tokens", 0) if usage else 0
                resp_model = cached_resp.get("model", target_model)
            else:
                reply_text = cached_resp.choices[0].message.content
                input_tokens = getattr(cached_resp.usage, "prompt_tokens", 0) if cached_resp.usage else 0
                output_tokens = getattr(cached_resp.usage, "completion_tokens", 0) if cached_resp.usage else 0
                resp_model = getattr(cached_resp, "model", target_model)

            logger.info("LiteLLM response cache HIT: model=%s, input_tokens=%d, output_tokens=%d",
                        resp_model, input_tokens, output_tokens)
            return {
                "reply": reply_text,
                "metadata": {
                    "resolution_source": "LLM_CACHE",
                    "model": resp_model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "key_id": None,
                    "fallback_attempts": 0
                }
            }

    # 2. Key pool fallback loop (in order key_1, key_2, ...)
    fallback_attempts = 0
    last_error_detail = None

    for idx, key in enumerate(keys):
        key_id = f"key_{idx + 1}"

        # Check if this key is simulated to fail
        if has_simulation:
            simulated_status = sim_map.get(key_id) or sim_map.get("*")
            if simulated_status is not None:
                logger.warning("Simulated failure for %s with status HTTP %d", key_id, simulated_status)
                fallback_attempts += 1
                last_error_detail = f"Simulated HTTP {simulated_status} on {key_id}"
                continue

        # Execute live inference attempt
        call_start = time.perf_counter()
        try:
            logger.info("Attempting inference with key_id=%s, model=%s", key_id, target_model)
            response = litellm.completion(
                model=target_model,
                messages=messages,
                api_key=key,
                num_retries=0,
                timeout=req_timeout,
            )
            call_duration = int((time.perf_counter() - call_start) * 1000)

            # Check if litellm returned a response
            reply_text = response.choices[0].message.content
            input_tokens = getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
            output_tokens = getattr(response.usage, "completion_tokens", 0) if response.usage else 0
            resp_model = getattr(response, "model", target_model)

            # Check if litellm internal cache hit occurred (if not caught earlier)
            hidden_params = getattr(response, "_hidden_params", {}) or {}
            is_cache_hit = hidden_params.get("cache_hit", False)

            if is_cache_hit and not has_simulation:
                logger.info("LiteLLM internal cache HIT: key_id=None, duration=%dms", call_duration)
                return {
                    "reply": reply_text,
                    "metadata": {
                        "resolution_source": "LLM_CACHE",
                        "model": resp_model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "key_id": None,
                        "fallback_attempts": 0
                    }
                }

            # Store in cache for future identical requests (if not simulating)
            if not has_simulation:
                try:
                    cache.add_cache(response, cache_key=cache_key)
                except Exception as cache_err:
                    logger.warning("Failed to populate cache: %s", cache_err)

            logger.info("Live inference successful: key_id=%s, fallback_attempts=%d, duration=%dms",
                        key_id, fallback_attempts, call_duration)
            return {
                "reply": reply_text,
                "metadata": {
                    "resolution_source": "LIVE_INFERENCE",
                    "model": resp_model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "key_id": key_id,
                    "fallback_attempts": fallback_attempts
                }
            }

        except Exception as exc:
            call_duration = int((time.perf_counter() - call_start) * 1000)
            logger.warning(
                "Key attempt failed: key_id=%s, error_type=%s, duration=%dms, detail=%s",
                key_id, type(exc).__name__, call_duration, str(exc)
            )
            fallback_attempts += 1
            last_error_detail = str(exc)
            continue

    # All keys exhausted
    logger.error("All providers exhausted. Total attempts failed: %d. Last error: %s",
                 fallback_attempts, last_error_detail)
    raise AllProvidersUnavailableException("all providers unavailable")
