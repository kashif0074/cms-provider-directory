import os
from pathlib import Path
import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH)


class SearchError(RuntimeError):
    pass


def search_web(query, engine=None, domains=None, fallback=True):
    engine = engine or os.getenv("SEARCH_PROVIDER", "tavily")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("A search query is required")
    query = query.strip()
    if engine == "tavily":
        return _search_tavily(query, domains)
    if engine == "google":
        try:
            return _search_google(query, domains)
        except SearchError as e:
            if fallback:
                return _search_tavily(query, domains)
            raise
    raise ValueError("Engine must be tavily or google")


def _search_tavily(query, domains):
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise SearchError("TAVILY_API_KEY is missing")
    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
    }
    if domains:
        payload["include_domains"] = domains
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=(10, 30),
        )
    except requests.RequestException:
        raise SearchError("Tavily network request failed") from None
    if response.status_code != 200:
        raise SearchError(
            f"Tavily returned HTTP {response.status_code}; "
            "check your key, quota, and dashboard."
        )
    try:
        data = response.json()
    except ValueError:
        raise SearchError("Tavily returned invalid JSON") from None
    return [
        {
            "title": item.get("title", ""),
            "url": item["url"],
            "snippet": item.get("content", ""),
            "engine": "tavily",
        }
        for item in data.get("results", [])
        if item.get("url")
    ]


def _search_google(query, domains):
    key = os.getenv("GOOGLE_API_KEY")
    cx = os.getenv("GOOGLE_CX")
    if not key or not cx:
        raise SearchError("GOOGLE_API_KEY or GOOGLE_CX is missing")
    if domains:
        sites = " OR ".join(f"site:{domain}" for domain in domains)
        query = f"{query} ({sites})"
    try:
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": key,
                "cx": cx,
                "q": query,
                "num": 5,
            },
            timeout=(10, 30),
        )
    except requests.RequestException:
        # Avoid exposing the URL because it contains the Google key.
        raise SearchError("Google network request failed") from None
    if response.status_code != 200:
        raise SearchError(
            f"Google returned HTTP {response.status_code}; "
            "check API access, key restrictions, CX, and quota."
        )
    try:
        data = response.json()
    except ValueError:
        raise SearchError("Google returned invalid JSON") from None
    return [
        {
            "title": item.get("title", ""),
            "url": item["link"],
            "snippet": item.get("snippet", ""),
            "engine": "google",
        }
        for item in data.get("items", [])
        if item.get("link")
    ]
