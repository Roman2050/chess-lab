import httpx
from typing import Optional
from app.models.enums import StandardPerfType

async def fetch_games_from_lichess(
    username: str, 
    max_games: int = 50, 
    perf_type: Optional[StandardPerfType] = None
) -> str:
    url = f"https://lichess.org/api/games/user/{username}"
    params = {
        "max": max_games,
        "tags": "true",
        "clocks": "false",
        "evals": "false",
        "opening": "true"
    }
    
    if perf_type:
        params["perfType"] = perf_type.value
    else:
        params["perfType"] = "ultraBullet,bullet,blitz,rapid,classical,correspondence"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        return response.text