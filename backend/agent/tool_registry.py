"""
Sia AI — Tool Registry
========================
All external tools live here.
Adding a new tool = add one @tool function below. Nothing else changes.

Current tools:
  ✅ get_current_time   — instant, no API
  ✅ get_weather        — stub, ready for OpenWeatherMap key
  ✅ web_search         — stub, ready for Tavily/DuckDuckGo key
  ✅ get_news           — stub, ready for NewsAPI key
  ✅ get_stock_price    — stub, ready for Alpha Vantage key

Future tools to add here (one @tool each):
  ⬜ send_whatsapp
  ⬜ send_email
  ⬜ get_calendar_events
  ⬜ play_youtube
  ⬜ play_spotify
  ⬜ create_reminder

HOW TO ADD A NEW TOOL:
  1. Write a function with @tool decorator below
  2. Add it to ALL_TOOLS list at the bottom
  3. Done — LangGraph picks it up automatically
"""
from __future__ import annotations

from datetime import datetime
from langchain_core.tools import tool

from backend.core.logger import get_logger

log = get_logger("tools")


# ── Time ──────────────────────────────────────────────────────────────────────

@tool
def get_current_time() -> str:
    """Get the current date and time."""
    now = datetime.now()
    return now.strftime("It's %I:%M %p on %A, %d %B %Y")


# ── Weather ───────────────────────────────────────────────────────────────────

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city. Input: city name like 'Hyderabad'."""
    try:
        from backend.core.config import settings
        api_key = getattr(settings, "OPENWEATHER_API_KEY", "")
        if not api_key:
            return f"Weather for {city}: API key not configured. Add OPENWEATHER_API_KEY to .env"

        import httpx
        resp = httpx.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": api_key, "units": "metric"},
            timeout=5,
        )
        data = resp.json()
        temp = data["main"]["temp"]
        desc = data["weather"][0]["description"]
        humidity = data["main"]["humidity"]
        return f"{city}: {temp}°C, {desc}, humidity {humidity}%"
    except Exception as e:
        log.warning("Weather tool error: %s", e)
        return f"Couldn't fetch weather for {city} right now."


# ── Web Search ────────────────────────────────────────────────────────────────

@tool
def web_search(query: str) -> str:
    """Search the web for current information. Use for news, facts, recent events."""
    try:
        from backend.core.config import settings
        api_key = getattr(settings, "TAVILY_API_KEY", "")

        if api_key:
            # Tavily — best for AI agents, free tier available
            from tavily import TavilyClient
            client = TavilyClient(api_key=api_key)
            results = client.search(query, max_results=3)
            snippets = [r.get("content", "") for r in results.get("results", [])]
            return "\n".join(snippets[:3]) or "No results found."

        # DuckDuckGo fallback — no API key needed
        import httpx
        resp = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1},
            timeout=5,
        )
        data = resp.json()
        abstract = data.get("AbstractText", "")
        if abstract:
            return abstract
        # Try related topics
        topics = data.get("RelatedTopics", [])
        if topics:
            return topics[0].get("Text", "No results found.")
        return "No results found. Add TAVILY_API_KEY to .env for better search."

    except Exception as e:
        log.warning("Web search error: %s", e)
        return f"Search failed: {e}"


# ── News ──────────────────────────────────────────────────────────────────────

@tool
def get_news(topic: str = "technology") -> str:
    """Get latest news headlines on a topic. Default topic: technology."""
    try:
        from backend.core.config import settings
        api_key = getattr(settings, "NEWS_API_KEY", "")
        if not api_key:
            # Fallback to DuckDuckGo news
            return web_search.invoke(f"latest news {topic} today")

        import httpx
        resp = httpx.get(
            "https://newsapi.org/v2/top-headlines",
            params={"q": topic, "apiKey": api_key, "pageSize": 3, "language": "en"},
            timeout=5,
        )
        articles = resp.json().get("articles", [])
        if not articles:
            return f"No news found for '{topic}'."
        headlines = [f"• {a['title']}" for a in articles[:3]]
        return f"Top news on {topic}:\n" + "\n".join(headlines)

    except Exception as e:
        log.warning("News tool error: %s", e)
        return f"Couldn't fetch news right now."


# ── Stock Price ───────────────────────────────────────────────────────────────

@tool
def get_stock_price(symbol: str) -> str:
    """Get current stock price. Input: ticker symbol like 'AAPL', 'TSLA', 'RELIANCE.BSE'."""
    try:
        from backend.core.config import settings
        api_key = getattr(settings, "ALPHA_VANTAGE_KEY", "")
        if not api_key:
            return f"Stock price for {symbol}: Add ALPHA_VANTAGE_KEY to .env"

        import httpx
        resp = httpx.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": api_key,
            },
            timeout=5,
        )
        data = resp.json().get("Global Quote", {})
        price = data.get("05. price", "N/A")
        change = data.get("10. change percent", "N/A")
        return f"{symbol}: ${price} ({change} today)"

    except Exception as e:
        log.warning("Stock tool error: %s", e)
        return f"Couldn't fetch price for {symbol}."


# ── System actions bridge ──────────────────────────────────────────────────────
# These bridge LangGraph tools to Sia's existing action system

@tool
def execute_system_action(action: str, params: str = "{}") -> str:
    """
    Execute a system action like volume control, screenshot, app launch.
    action: one of volume_up, volume_down, volume_mute, take_screenshot,
            system_lock, system_sleep, open_app, check_battery, check_system
    params: JSON string of parameters e.g. '{"amount": 10}'
    """
    import json
    import asyncio
    try:
        from backend.actions.system_actions import execute_action
        p = json.loads(params) if params else {}
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(execute_action(action, p))
        loop.close()
        return result or "Done."
    except Exception as e:
        log.warning("System action error: %s", e)
        return f"Action failed: {e}"


# ── ALL_TOOLS — LangGraph reads this list ─────────────────────────────────────
# Add any new @tool function here and it's instantly available to Sia

ALL_TOOLS = [
    get_current_time,
    get_weather,
    web_search,
    get_news,
    get_stock_price,
    execute_system_action,
]
