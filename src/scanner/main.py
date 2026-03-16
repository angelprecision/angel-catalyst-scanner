from __future__ import annotations

import json
import time
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from .config import FINNHUB_API_KEY, DAYS_AHEAD, MAX_TICKERS

FINNHUB_BASE = "https://finnhub.io/api/v1"
REQUEST_SLEEP_SEC = 0.25


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_date(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d")


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def require_key(name: str, value: str):
    if not value:
        raise RuntimeError(
            f"Missing {name}. Set it as an environment variable.\n"
            f"Example:\n  export {name}='your_key_here'"
        )


class FinnhubClient:
    def __init__(self, api_key: str):
        require_key("FINNHUB_API_KEY", api_key)
        self.api_key = api_key
        self.s = requests.Session()

    def _get(self, path: str, params: Dict | None = None):
        params = params or {}
        params["token"] = self.api_key
        url = f"{FINNHUB_BASE}{path}"
        r = self.s.get(url, params=params, timeout=(3.05, 20))
        r.raise_for_status()
        time.sleep(REQUEST_SLEEP_SEC)
        return r.json()

    def earnings_calendar(self, start: datetime, end: datetime) -> List[dict]:
        data = self._get("/calendar/earnings", {
            "from": iso_date(start),
            "to": iso_date(end),
        })
        return data.get("earningsCalendar", []) or []

    def company_news(self, symbol: str, start: datetime, end: datetime) -> List[dict]:
        return self._get("/company-news", {
            "symbol": symbol,
            "from": iso_date(start),
            "to": iso_date(end),
        }) or []


NEWS_KEYWORDS = {
    "m&a": ["acquire", "acquisition", "buyout", "merger", "takeover", "strategic alternatives"],
    "legal": ["lawsuit", "court", "appeal", "settlement", "injunction", "ftc", "doj", "sec investigation"],
    "product": ["launch", "partnership", "contract", "award", "guidance", "forecast", "upgrade", "raised", "raises"],
    "biotech": ["fda", "phase", "trial", "pdufa", "clinical", "approval", "drug", "biotech"],
    "war_macro": ["war", "missile", "strike", "sanctions", "oil", "defense", "military", "ceasefire", "attack"],
}


def keyword_hits(headlines: List[str]) -> Dict[str, int]:
    hits = {k: 0 for k in NEWS_KEYWORDS.keys()}
    for h in headlines:
        hl = h.lower()
        for k, words in NEWS_KEYWORDS.items():
            if any(w in hl for w in words):
                hits[k] += 1
    return hits


def score_candidate(days_to_event: int, news_hits: Dict[str, int]) -> Tuple[float, Dict[str, float]]:
    imminence = 0.0
    if 0 <= days_to_event <= 2:
        imminence = 3.0
    elif 3 <= days_to_event <= 7:
        imminence = 2.5
    elif 8 <= days_to_event <= 14:
        imminence = 2.0
    else:
        imminence = 0.5

    mna = clamp(news_hits.get("m&a", 0) * 0.8, 0, 2.0)
    legal = clamp(news_hits.get("legal", 0) * 0.6, 0, 1.5)
    biotech = clamp(news_hits.get("biotech", 0) * 0.7, 0, 2.0)
    product = clamp(news_hits.get("product", 0) * 0.4, 0, 1.2)
    war_macro = clamp(news_hits.get("war_macro", 0) * 0.5, 0, 1.5)

    news_score = mna + legal + biotech + product + war_macro
    total = clamp(imminence + news_score, 0, 10)

    breakdown = {
        "imminence": imminence,
        "news": news_score,
    }
    return total, breakdown


@dataclass
class Candidate:
    symbol: str
    event_date: str
    days_to_event: int
    score: float
    breakdown: Dict[str, float]
    news_hits: Dict[str, int]
    top_headlines: List[str]


def run_scanner() -> List[Candidate]:
    fh = FinnhubClient(FINNHUB_API_KEY)

    start = utc_now()
    end = start + timedelta(days=DAYS_AHEAD)
    news_start = start - timedelta(days=7)

    earnings = fh.earnings_calendar(start, end)

    tickers = []
    seen = set()

    for e in earnings:
        sym = (e.get("symbol") or "").strip().upper()
        date = (e.get("date") or "").strip()
        if not sym or not date:
            continue
        if sym in seen:
            continue
        seen.add(sym)
        tickers.append((sym, date))
        if len(tickers) >= MAX_TICKERS:
            break

    results = []

    for sym, event_date in tickers:
        try:
            news_items = fh.company_news(sym, news_start, start)
        except Exception:
            news_items = []

        headlines = []
        for n in news_items[:50]:
            h = (n.get("headline") or "").strip()
            if h:
                headlines.append(h)

        hits = keyword_hits(headlines)

        try:
            event_dt = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dtevent = (event_dt - start).days
        except Exception:
            dtevent = 999

        score, breakdown = score_candidate(dtevent, hits)

        results.append(Candidate(
            symbol=sym,
            event_date=event_date,
            days_to_event=dtevent,
            score=score,
            breakdown=breakdown,
            news_hits=hits,
            top_headlines=headlines[:5],
        ))

    results.sort(key=lambda c: c.score, reverse=True)
    return results


def print_results(cands: List[Candidate], top_n: int = 15):
    print("\n=== TOP CATALYST CANDIDATES ===\n")
    for i, c in enumerate(cands[:top_n], 1):
        print(f"{i:>2}. {c.symbol} | Earnings: {c.event_date} ({c.days_to_event}d) | Score: {c.score:.2f}")
        print(f"    Breakdown: {json.dumps(c.breakdown)}")
        print(f"    News hits: {json.dumps(c.news_hits)}")
        for h in c.top_headlines:
            print(f"    - {h}")
        print("")


def export_json(cands: List[Candidate], path: str = "scanner_output.json"):
    payload = []
    for c in cands:
        payload.append({
            "symbol": c.symbol,
            "event_date": c.event_date,
            "days_to_event": c.days_to_event,
            "score": c.score,
            "breakdown": c.breakdown,
            "news_hits": c.news_hits,
            "top_headlines": c.top_headlines,
        })

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"\nSaved {path}\n")


if __name__ == "__main__":
    require_key("FINNHUB_API_KEY", FINNHUB_API_KEY)
    candidates = run_scanner()
    print_results(candidates)
    export_json(candidates)
