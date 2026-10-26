"""Simple per-user rate limits, kept in memory. Use Redis instead if you run more than one server."""
import time
from collections import defaultdict, deque
from fastapi import Depends, HTTPException
from .security import get_current_user

_calls: dict[tuple[str, str], deque] = defaultdict(deque)


def limit(name: str, times: int, seconds: int = 60):
    def check(user_id: str = Depends(get_current_user)) -> str:
        now = time.monotonic()
        calls = _calls[(name, user_id)]
        while calls and now - calls[0] > seconds:
            calls.popleft()
        if len(calls) >= times:
            raise HTTPException(429, "Too many tries. Please wait a minute and try again.")
        calls.append(now)
        return user_id
    return check
