from cachetools import TTLCache
from typing import Annotated
from fastapi import Depends

# Global instances
# 1. For onboarding invites (1000 items, 24h)
invite_cache: TTLCache = TTLCache(maxsize=1000, ttl=86400)

# 2. For AI Thinking logs (500 items, 15m - short lived)
thinking_cache: TTLCache = TTLCache(maxsize=500, ttl=900)

def get_invite_cache() -> TTLCache:
    return invite_cache

def get_thinking_cache() -> TTLCache:
    return thinking_cache

InviteCache = Annotated[TTLCache, Depends(get_invite_cache)]
ThinkingCache = Annotated[TTLCache, Depends(get_thinking_cache)]
