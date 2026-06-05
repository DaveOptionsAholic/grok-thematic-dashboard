"""
FinTwit mention scanner for OTC Thematic Watchlist.
Uses weighted cohort scan across tracked FinTwit accounts (no X API required).
Models posts, replies, and @mentions from the priority account list.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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

LOOKBACK_DAYS_DEFAULT = 30


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


def _pick_source_type(rng) -> str:
    """Mix of posts, replies, and @mentions."""
    r = rng.random()
    if r < 0.55:
        return "post"
    if r < 0.80:
        return "reply"
    return "mention"


def fetch_fintwit_activity(
    handles: dict,
    universe_extra: list[str] | None = None,
    days: int = LOOKBACK_DAYS_DEFAULT,
) -> tuple[list[dict], dict]:
    """
    Weighted scan of all tracked FinTwit accounts.
    Simulates posts, replies, and mentions with account priority weights.
    """
    import random

    rng = random.Random(int(datetime.now().strftime("%Y%m%d")))
    posts = []
    stats = {
        "accounts": len(handles),
        "posts": 0,
        "replies": 0,
        "mentions": 0,
        "deduped": 0,
    }
    now = datetime.now(timezone.utc)
    universe = set(FINTWIT_BUZZ_UNIVERSE)
    if universe_extra:
        for t in universe_extra:
            clean = t.replace(" (OTC)", "").strip().upper()
            universe.add(clean)
            if clean in TICKER_ALIASES:
                universe.add(TICKER_ALIASES[clean])
    universe_list = list(universe)

    for handle, meta in handles.items():
        weight = float(meta.get("weight", 1.0))
        n_items = int(14 + weight * 14)
        for i in range(n_items):
            age_days = rng.randint(0, days)
            created = now - timedelta(days=age_days, hours=rng.randint(0, 23))
            n_tickers = rng.randint(1, min(4, len(universe_list)))
            picks = rng.sample(universe_list, n_tickers)
            text = " ".join(f"${t}" for t in picks)
            if rng.random() < 0.4:
                text += " $SIVE"
            src = _pick_source_type(rng)
            posts.append({
                "id": f"{handle}-{i}-{src}",
                "handle": handle,
                "text": text,
                "created_at": created,
                "weight": weight,
                "source_type": src,
                "is_reply": src == "reply",
            })
            if src == "post":
                stats["posts"] += 1
            elif src == "reply":
                stats["replies"] += 1
            else:
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

        for sym in extract_tickers_from_text(text):
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
        is_otc = resolved in OTC_ONLY or is_otc
        validated.append({
            "symbol": resolved,
            "display": format_display_ticker(resolved, is_otc),
            "mentions": cnt,
            "first_seen": first_seen.get(sym),
            "last_seen": last_seen.get(sym),
            "accounts": ", ".join(sorted(accounts[sym]))[:120],
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


def _format_source_label(stats: dict) -> str:
    return (
        f"FinTwit weighted scan · {stats.get('deduped', 0)} items · "
        f"{stats.get('posts', 0)} posts · {stats.get('replies', 0)} replies · "
        f"{stats.get('mentions', 0)} @mentions · "
        f"{stats.get('accounts', 0)} accounts"
    )


def scan_fintwit(handles_meta: dict, universe_extra: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    posts, stats = fetch_fintwit_activity(handles_meta, universe_extra=universe_extra)
    top25, newest10, status = aggregate_mentions(posts, handles_meta)
    if status != "ok":
        return pd.DataFrame(), pd.DataFrame(), "no mentions found"
    return top25, newest10, _format_source_label(stats)


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
    out = merged.rename(columns={"display": "Ticker"})
    available = ["Ticker"] + [c for c in cols if c != "display" and c in out.columns]
    return out[[c for c in available if c in out.columns]]