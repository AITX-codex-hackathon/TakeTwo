"""
Shared utilities: retry decorator for OpenAI calls, robust JSON parsing.
"""
import re
import json
import time
import functools


def retry_api(max_retries: int = 3, base_delay: float = 5.0):
    """
    Decorator: retries the wrapped function on OpenAI rate-limit or transient errors.
    Exponential backoff: 5s, 15s, 45s (base * 3^attempt).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    msg = str(e).lower()
                    is_rate = "rate" in msg or "429" in msg or "quota" in msg
                    is_transient = "timeout" in msg or "502" in msg or "503" in msg or "connection" in msg
                    if attempt == max_retries or not (is_rate or is_transient):
                        raise
                    delay = base_delay * (3 ** attempt)
                    print(
                        f"[api] {'rate limited' if is_rate else 'transient error'} on {func.__name__}, "
                        f"retry {attempt + 1}/{max_retries} in {delay:.0f}s — {e}",
                        flush=True,
                    )
                    time.sleep(delay)
        return wrapper
    return decorator


def parse_json(text: str, context: str = "") -> any:
    """
    Robustly extract JSON from GPT output.
    Strips markdown fences, tries direct parse, then regex extraction.
    Returns None if all attempts fail.
    """
    text = text.strip()

    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            try:
                return json.loads(cleaned)
            except Exception:
                pass

    # Direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Regex: try to find the largest JSON array or object
    for pattern in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass

    label = f" ({context})" if context else ""
    print(f"[api] JSON parse failed{label}: {text[:300]}", flush=True)
    return None
