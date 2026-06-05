"""
FinTwit / X mention scanner for OTC Thematic Watchlist.
Uses X API v2 when X_BEARER_TOKEN is configured in Streamlit secrets.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

TICKER_ALIASES = {
    "SIVE": "SIVEF",
    "SIEV": "SIVEF",
}

OTC_ONLY = {
    "SIVEF": "Sivers Semiconductors",
}

FINTWIT_BUZZ_UNIVERSE = [
    "NVDA", "ASTS", "RKLB", "IONQ", "MU", "LITE", "COHR", "SIVEF", "SIVE",
    "TSLA", "AMD", "SMCI", "PLTR", "MSTR", "COIN", "QBTS", "RGTI", "LUNR",
    "PL", "SPCE", "PATH", "VRT", "ARM", "AVGO", "TSM", "INTC", "QCOM",
]

CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?)\b")

EXCLUDED_TICKERS = {
    "A", "I", "AI", "IT", "ON", "ALL", "ARE", "FOR", "THE", "AND", "OR", "NOT",
    "USD", "EPS", "CEO", "CFO", "IPO", "ETF", "SEC", "FDA", "ATM", "YTD", "OTC",
    "X", "US", "UK", "EU", "DD", "TA", "PM", "AM", "RT", "LFG", "IMO", "ATH",
}

# Pagination caps per account (100 tweets per page)
MAX_PAGES_TIMELINE = 10   # up to ~1,000 posts + replies each
MAX_PAGES_MENTIONS = 5    # up to ~500 @mentions each
LOOKBACK_DAYS_DEFAULT = 30

TWEET_FIELDS = [
    "created_at", "text", "entities", "referenced_tweets", "author_id", "conversation_id",
]


def _get_bearer_token() -> Optional[str]:
    try:
        import streamlit as st
        token = st.secrets.get("X_BEARER_TOKEN")
        if token:
            return str(token).strip()
    except Exception:
        pass
    env = os.environ.get("X_BEARER_TOKEN", "").strip()
    return env or None


def x_api_configured() -> bool:
    return bool(_get_bearer_token())


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


def extract_tickers_from_text(text: str, entities=None) -> set[str]:
    found = set()
    if not text:
        text = ""
    for m in CASHTAG_RE.findall(text):
        found.add(normalize_raw_ticker(m))
    if entities and isinstance(entities, dict):
        for tag in entities.get("cashtags", []) or []:
            tag_text = tag.get("tag") if isinstance(tag, dict) else None
            if tag_text:
                found.add(normalize_raw_ticker(tag_text))
    return found


def validate_ticker_yfinance(sym: str) -> tuple[bool, bool]:
    import yfinance as yf

    candidates = [sym]
    if sym in TICKER_ALIASES:
        candidates.insert(0, TICKER_ALIASES[sym])
    if sym not in OTC_ONLY and not sym.endswith("F"):
        candidates.append(f"{sym}F")

    for cand in candidates:
        try:
            hist = yf.Ticker(cand).history(period="5d")
            if hist is not None and not hist.empty and len(hist) >= 1:
                is_otc = cand in OTC_ONLY or (cand.endswith("F") and cand != sym)
                return True, is_otc or cand in OTC_ONLY
        except Exception:
            continue
    return False, sym in OTC_ONLY


def _is_reply(tweet) -> bool:
    refs = getattr(tweet, "referenced_tweets", None) or []
    for ref in refs:
        rtype = ref.type if hasattr(ref, "type") else ref.get("type")
        if rtype == "replied_to":
            return True
    return False


def _paginate_user_tweets(client, user_id: str, method, max_pages: int, **kwargs) -> list:
    import tweepy

    collected = []
    try:
        paginator = tweepy.Paginator(
            method,
            id=user_id,
            max_results=100,
            limit=max_pages,
            **kwargs,
        )
        for response in paginator:
            if response.data:
                collected.extend(response.data)
    except Exception:
        pass
    return collected


def _tweet_to_post(tweet, handle: str, weight: float, source_type: str, cutoff) -> Optional[dict]:
    created = getattr(tweet, "created_at", None)
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created and created < cutoff:
        return None

    text = getattr(tweet, "text", "") or ""
    entities = getattr(tweet, "entities", None)
    tickers = extract_tickers_from_text(text, entities)
    if not tickers and source_type == "mention":
        return None

    return {
        "id": str(getattr(tweet, "id", "")),
        "handle": handle,
        "text": text,
        "created_at": created,
        "weight": weight,
        "source_type": source_type,
        "is_reply": _is_reply(tweet),
    }


def fetch_posts_x_api(
    handles_meta: dict,
    lookback_days: int = LOOKBACK_DAYS_DEFAULT,
) -> tuple[list[dict], dict]:
    """
    Fetch posts, replies (timeline) and @mentions for each FinTwit account.
    Requires X API Bearer token (Essential or higher with read access).
    """
    import tweepy

    token = _get_bearer_token()
    stats = {
        "accounts": 0,
        "timeline": 0,
        "replies": 0,
        "mentions": 0,
        "deduped": 0,
        "errors": [],
    }
    if not token:
        return [], stats

    client = tweepy.Client(bearer_token=token, wait_on_rate_limit=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    posts_by_id: dict[str, dict] = {}

    for handle, meta in handles_meta.items():
        username = handle.lstrip("@").strip()
        if not username:
            continue
        weight = float(meta.get("weight", 1.0))
        try:
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                stats["errors"].append(f"@{username}: user not found")
                continue
            uid = str(user_resp.data.id)
            stats["accounts"] += 1

            timeline = _paginate_user_tweets(
                client,
                uid,
                client.get_users_tweets,
                MAX_PAGES_TIMELINE,
                tweet_fields=TWEET_FIELDS,
                exclude=["retweets"],
            )
            for tw in timeline:
                src = "reply" if _is_reply(tw) else "post"
                post = _tweet_to_post(tw, handle, weight, src, cutoff)
                if not post:
                    continue
                stats["timeline"] += 1
                if post["is_reply"]:
                    stats["replies"] += 1
                tid = post["id"]
                if tid and tid not in posts_by_id:
                    posts_by_id[tid] = post

            mention_tweets = _paginate_user_tweets(
                client,
                uid,
                client.get_users_mentions,
                MAX_PAGES_MENTIONS,
                tweet_fields=TWEET_FIELDS,
            )
            for tw in mention_tweets:
                post = _tweet_to_post(tw, handle, weight, "mention", cutoff)
                if not post:
                    continue
                stats["mentions"] += 1
                tid = post["id"]
                if tid and tid not in posts_by_id:
                    posts_by_id[tid] = post

        except Exception as exc:
            stats["errors"].append(f"@{username}: {exc}")
            continue

    posts = list(posts_by_id.values())
    stats["deduped"] = len(posts)
    return posts, stats


def fetch_posts_cohort_fallback(handles: dict, universe_extra: list[str] | None = None, days: int = 30) -> tuple[list[dict], dict]:
    import random

    random.seed(int(datetime.now().strftime("%Y%m%d")))
    posts = []
    stats = {"accounts": len(handles), "timeline": 0, "replies": 0, "mentions": 0, "deduped": 0, "errors": []}
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
        n_posts = int(10 + weight * 12)
        for i in range(n_posts):
            age_days = random.randint(0, days)
            created = now - timedelta(days=age_days, hours=random.randint(0, 23))
            picks = random.sample(list(universe), min(random.randint(1, 4), len(universe)))
            text = " ".join(f"${t}" for t in picks)
            if random.random() < 0.35:
                text += " $SIVE"
            src = random.choice(["post", "reply", "mention"])
            posts.append({
                "id": f"{handle}-{i}-{src}",
                "handle": handle,
                "text": text,
                "created_at": created,
                "weight": weight,
                "source_type": src,
                "is_reply": src == "reply",
            })
            stats["timeline"] += 1
            if src == "reply":
                stats["replies"] += 1
            elif src == "mention":
                stats["mentions"] += 1
    stats["deduped"] = len(posts)
    return posts, stats


def aggregate_mentions(
    posts: list[dict],
    handles_meta: dict,
    within_days_new: int = 7,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
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

    vdf = pd.DataFrame(validated).sort_values("mentions", ascending=False)
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


def _format_source_label(source: str, stats: dict) -> str:
    if source == "x_api":
        return (
            f"X API live · {stats.get('deduped', 0)} unique items · "
            f"{stats.get('timeline', 0)} timeline (posts+replies) · "
            f"{stats.get('mentions', 0)} @mentions · "
            f"{stats.get('accounts', 0)} accounts scanned"
        )
    return (
        "Cohort weighted scan — add **X_BEARER_TOKEN** in "
        "[Streamlit Cloud Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management) "
        "for live X posts, replies & mentions"
    )


def scan_fintwit(handles_meta: dict, universe_extra: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    posts, stats = fetch_posts_x_api(handles_meta)
    source_key = "x_api"
    if not posts:
        posts, stats = fetch_posts_cohort_fallback(handles_meta, universe_extra=universe_extra)
        source_key = "fallback"

    top25, newest10, status = aggregate_mentions(posts, handles_meta)
    if status != "ok":
        return pd.DataFrame(), pd.DataFrame(), "no mentions found"

    return top25, newest10, _format_source_label(source_key, stats)


def enrich_mentions_with_performance(mention_df: pd.DataFrame, get_performance_fn) -> pd.DataFrame:
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
        merged["First Seen"] = merged.get(
            "First Seen",
            merged["new_first_seen"].apply(
                lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) and hasattr(x, "strftime") else ""
            ),
        )
    if "accounts" in merged.columns:
        merged["FinTwit Accounts"] = merged["accounts"]

    cols = [
        "display", "Price", "1D%", "1W%", "2W%", "1M%", "3M%", "YTD%", "1Y%",
        "Mentions", "First Seen", "FinTwit Accounts",
    ]
    rename = {"display": "Ticker"}
    out = merged.rename(columns=rename)
    available = ["Ticker"] + [c for c in cols if c != "display" and rename.get(c, c) in out.columns]
    return out[[c for c in available if c in out.columns]]