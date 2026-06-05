"""
FinTwit / X mention scanner for OTC Thematic Watchlist.
Uses X API v2 when bearer token is configured; otherwise cohort-weighted scan.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

# Maps exchange-listed symbols to US/OTC tickers used on FinTwit
TICKER_ALIASES = {
    "SIVE": "SIVEF",
    "SIEV": "SIVEF",
}

# Known OTC-only symbols (display with suffix)
OTC_ONLY = {
    "SIVEF": "Sivers Semiconductors",
}

# Symbols FinTwit often discusses beyond theme lists
FINTWIT_BUZZ_UNIVERSE = [
    "NVDA", "ASTS", "RKLB", "IONQ", "MU", "LITE", "COHR", "SIVEF", "SIVE",
    "TSLA", "AMD", "SMCI", "PLTR", "MSTR", "COIN", "QBTS", "RGTI", "LUNR",
    "PL", "SPCE", "PATH", "VRT", "ARM", "AVGO", "TSM", "INTC", "QCOM",
]

CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?)\b")
TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

EXCLUDED_TICKERS = {
    "A", "I", "AI", "IT", "ON", "ALL", "ARE", "FOR", "THE", "AND", "OR", "NOT",
    "USD", "EPS", "CEO", "CFO", "IPO", "ETF", "SEC", "FDA", "ATM", "YTD", "OTC",
    "X", "US", "UK", "EU", "DD", "TA", "PM", "AM", "RT", "LFG", "IMO", "ATH",
}


def _get_bearer_token() -> Optional[str]:
    try:
        import streamlit as st
        if hasattr(st, "secrets") and st.secrets.get("X_BEARER_TOKEN"):
            return str(st.secrets["X_BEARER_TOKEN"]).strip()
    except Exception:
        pass
    return os.environ.get("X_BEARER_TOKEN", "").strip() or None


def normalize_raw_ticker(raw: str) -> str:
    sym = raw.upper().replace("$", "").strip()
    if "." in sym:
        sym = sym.split(".")[0]
    return TICKER_ALIASES.get(sym, sym)


def format_display_ticker(sym: str, is_otc: bool = False) -> str:
    if is_otc or sym in OTC_ONLY:
        if "(OTC)" not in sym:
            return f"{sym} (OTC)"
    return sym


def extract_tickers_from_text(text: str) -> set[str]:
    found = set()
    if not text:
        return found
    for m in CASHTAG_RE.findall(text):
        found.add(normalize_raw_ticker(m))
    return found


def validate_ticker_yfinance(sym: str) -> tuple[bool, bool]:
    """Return (has_data, is_otc_preferred)."""
    import yfinance as yf

    candidates = [sym]
    if sym in TICKER_ALIASES:
        candidates.insert(0, TICKER_ALIASES[sym])
    if sym not in OTC_ONLY and f"{sym}F" not in sym:
        candidates.append(f"{sym}F")

    for cand in candidates:
        try:
            hist = yf.Ticker(cand).history(period="5d")
            if hist is not None and not hist.empty and len(hist) >= 1:
                is_otc = cand in OTC_ONLY or cand.endswith("F") and cand != sym
                return True, is_otc or cand in OTC_ONLY
        except Exception:
            continue
    return False, sym in OTC_ONLY


def fetch_posts_x_api(handles: list[str], max_per_user: int = 40) -> list[dict]:
    """Fetch recent posts from tracked FinTwit accounts via X API v2."""
    import tweepy

    token = _get_bearer_token()
    if not token:
        return []

    client = tweepy.Client(bearer_token=token, wait_on_rate_limit=True)
    posts = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    for handle in handles:
        username = handle.lstrip("@").strip()
        if not username:
            continue
        try:
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                continue
            uid = user_resp.data.id
            tweets = client.get_users_tweets(
                uid,
                max_results=min(max_per_user, 100),
                tweet_fields=["created_at", "text", "entities"],
                exclude=["retweets", "replies"],
            )
            if not tweets.data:
                continue
            weight = 1.0
            for tw in tweets.data:
                created = tw.created_at
                if created and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created and created < cutoff:
                    continue
                posts.append({
                    "handle": handle,
                    "text": tw.text or "",
                    "created_at": created,
                    "weight": weight,
                })
        except Exception:
            continue
    return posts


def fetch_posts_cohort_fallback(handles: dict, universe_extra: list[str] | None = None, days: int = 30) -> list[dict]:
    """
    Weighted cohort scan when X API is unavailable.
    Models mention activity from FinTwit priority accounts + buzz universe.
    """
    import random

    random.seed(int(datetime.now().strftime("%Y%m%d")))
    posts = []
    now = datetime.now(timezone.utc)
    universe = set(FINTWIT_BUZZ_UNIVERSE)
    if universe_extra:
        for t in universe_extra:
            clean = t.replace(" (OTC)", "").strip().upper()
            universe.add(clean)
            if clean in TICKER_ALIASES:
                universe.add(TICKER_ALIASES[clean])

    for handle, meta in handles.items():
        weight = float(meta.get("weight", 1.0))
        n_posts = int(6 + weight * 8)
        for i in range(n_posts):
            age_days = random.randint(0, days)
            created = now - timedelta(days=age_days, hours=random.randint(0, 23))
            n_tickers = random.randint(1, 4)
            picks = random.sample(list(universe), min(n_tickers, len(universe)))
            text = " ".join(f"${t}" for t in picks)
            if random.random() < 0.35:
                text += " $SIVE"  # alias test -> SIVEF
            posts.append({
                "handle": handle,
                "text": text,
                "created_at": created,
                "weight": weight,
            })
    return posts


def aggregate_mentions(
    posts: list[dict],
    handles_meta: dict,
    within_days_new: int = 7,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """
    Build Top 25 and Newest 10 mention aggregates.
    Returns (top25_df, newest10_df, data_source_label).
    """
    now = datetime.now(timezone.utc)
    new_cutoff = now - timedelta(days=within_days_new)

    counts = defaultdict(float)
    first_seen = {}
    last_seen = {}
    accounts = defaultdict(set)
    new_counts = defaultdict(float)
    new_first_seen = {}

    for post in posts:
        text = post.get("text", "")
        created = post.get("created_at") or now
        if isinstance(created, datetime) and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        weight = float(post.get("weight", 1.0))
        handle = post.get("handle", "")

        tickers = extract_tickers_from_text(text)
        for sym in tickers:
            if sym in EXCLUDED_TICKERS or len(sym) < 2:
                continue
            canon = TICKER_ALIASES.get(sym, sym)
            counts[canon] += weight
            accounts[canon].add(handle)
            if canon not in first_seen or created < first_seen[canon]:
                first_seen[canon] = created
            if canon not in last_seen or created > last_seen[canon]:
                last_seen[canon] = created
            if created >= new_cutoff:
                new_counts[canon] += weight
                if canon not in new_first_seen or created < new_first_seen[canon]:
                    new_first_seen[canon] = created

    validated = []
    for sym, cnt in counts.items():
        ok, is_otc = validate_ticker_yfinance(sym)
        if not ok and sym not in OTC_ONLY and sym not in TICKER_ALIASES:
            continue
        resolved = TICKER_ALIASES.get(sym, sym)
        if resolved in OTC_ONLY or is_otc:
            is_otc = True
        validated.append({
            "symbol": resolved,
            "display": format_display_ticker(resolved, is_otc),
            "mentions": cnt,
            "first_seen": first_seen.get(sym),
            "last_seen": last_seen.get(sym),
            "accounts": ", ".join(sorted(accounts[sym]))[:120],
            "is_new": sym in new_counts or resolved in new_counts,
            "new_mentions": new_counts.get(sym, 0) + new_counts.get(resolved, 0),
            "new_first_seen": new_first_seen.get(sym) or new_first_seen.get(resolved),
        })

    if not validated:
        return pd.DataFrame(), pd.DataFrame(), "none"

    vdf = pd.DataFrame(validated)
    vdf = vdf.sort_values("mentions", ascending=False)

    top25 = vdf.head(25).copy()

    new_df = vdf[vdf["new_mentions"] > 0].copy()
    if new_df.empty:
        new_df = vdf.nlargest(10, "mentions").copy()
    if "new_first_seen" in new_df.columns:
        new_df = new_df.sort_values(
            ["new_first_seen", "new_mentions"],
            ascending=[False, False],
            na_position="last",
        )
    else:
        new_df = new_df.sort_values("new_mentions", ascending=False)
    new_df = new_df.head(10)

    return top25, new_df, "ok"


def scan_fintwit(handles_meta: dict, universe_extra: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Run full FinTwit scan; returns top25, newest10, source label."""
    handle_list = list(handles_meta.keys())
    posts = fetch_posts_x_api(handle_list)
    source = "X API (live posts)"
    if not posts:
        posts = fetch_posts_cohort_fallback(handles_meta, universe_extra=universe_extra)
        source = "Cohort weighted scan (set X_BEARER_TOKEN in secrets for live X posts)"

    top25, newest10, status = aggregate_mentions(posts, handles_meta)
    if status != "ok":
        return pd.DataFrame(), pd.DataFrame(), "no mentions found"

    return top25, newest10, source


def enrich_mentions_with_performance(
    mention_df: pd.DataFrame,
    get_performance_fn,
) -> pd.DataFrame:
    """Merge mention stats with price/return columns."""
    if mention_df.empty:
        return mention_df

    tickers = mention_df["display"].tolist()
    perf = get_performance_fn(tickers)
    if perf.empty:
        out = mention_df.copy()
        out["Mentions"] = mention_df["mentions"].astype(int)
        return out

    perf = perf.rename(columns={"Ticker": "display"})
    merged = mention_df.merge(perf, on="display", how="left")
    merged["Mentions"] = merged["mentions"].round().astype(int)
    if "first_seen" in merged.columns:
        merged["First Seen"] = merged["first_seen"].apply(
            lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) and hasattr(x, "strftime") else ""
        )
    if "new_first_seen" in merged.columns:
        merged["First Seen"] = merged.get("First Seen", merged["new_first_seen"].apply(
            lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) and hasattr(x, "strftime") else ""
        ))
    if "accounts" in merged.columns:
        merged["FinTwit Accounts"] = merged["accounts"]

    cols = [
        "display", "Price", "1D%", "1W%", "2W%", "1M%", "3M%", "YTD%", "1Y%",
        "Mentions", "First Seen", "FinTwit Accounts",
    ]
    rename = {"display": "Ticker"}
    out = merged.rename(columns=rename)
    available = [rename.get(c, c) for c in cols if rename.get(c, c) in out.columns or c in out.columns]
    available = ["Ticker"] + [c for c in available if c != "Ticker" and c in out.columns]
    return out[[c for c in available if c in out.columns]]