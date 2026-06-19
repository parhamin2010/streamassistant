import httpx

FEATURED_URL = "https://store.steampowered.com/api/featuredcategories/"


async def get_new_releases() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(FEATURED_URL)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("new_releases", {}).get("items", [])
    results = []
    for item in items:
        price_cents = item.get("final_price", 0)
        if price_cents == 0:
            price_str = "Free"
        else:
            price_str = f"${price_cents / 100:.2f}"

        results.append({
            "id": item.get("id"),
            "name": item.get("name", "Unknown"),
            "price_usd": price_str,
            "discount_percent": item.get("discount_percent", 0),
            "url": f"https://store.steampowered.com/app/{item.get('id')}/",
        })

    return results
