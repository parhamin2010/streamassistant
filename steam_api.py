import asyncio
import httpx

FEATURED_URL = "https://store.steampowered.com/api/featuredcategories/"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"


async def _fetch_genres(client: httpx.AsyncClient, appid: int) -> tuple[int, list[str]]:
    try:
        resp = await client.get(APPDETAILS_URL, params={"appids": appid, "filters": "genres"})
        data = resp.json()
        genres = data.get(str(appid), {}).get("data", {}).get("genres", [])
        return appid, [g["description"] for g in genres]
    except Exception:
        return appid, []


def _parse_items(items: list[dict]) -> list[dict]:
    results = []
    for item in items:
        price_cents = item.get("final_price", 0)
        price_str = "Free" if price_cents == 0 else f"${price_cents / 100:.2f}"
        results.append({
            "id": item.get("id"),
            "name": item.get("name", "Unknown"),
            "price_usd": price_str,
            "discount_percent": item.get("discount_percent", 0),
            "url": f"https://store.steampowered.com/app/{item.get('id')}/",
            "genres": [],
        })
    return results


async def _enrich_genres(client: httpx.AsyncClient, results: list[dict]) -> None:
    genre_pairs = await asyncio.gather(
        *[_fetch_genres(client, r["id"]) for r in results]
    )
    genre_map = dict(genre_pairs)
    for r in results:
        r["genres"] = genre_map.get(r["id"], [])


async def get_new_releases(with_genres: bool = False) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(FEATURED_URL)
        resp.raise_for_status()
        data = resp.json()
        results = _parse_items(data.get("new_releases", {}).get("items", []))
        if with_genres and results:
            await _enrich_genres(client, results)
    return results


async def get_trending(with_genres: bool = False) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(FEATURED_URL)
        resp.raise_for_status()
        data = resp.json()
        results = _parse_items(data.get("top_sellers", {}).get("items", []))
        if with_genres and results:
            await _enrich_genres(client, results)
    return results


def filter_by_genre(games: list[dict], genre: str) -> list[dict]:
    genre_lower = genre.lower()
    return [
        g for g in games
        if any(genre_lower in gen.lower() for gen in g["genres"])
    ]
