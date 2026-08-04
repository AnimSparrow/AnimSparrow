#!/usr/bin/env python3
"""
Refreshes README.md with data from TryHackMe and Credly.
Fills the sections between the markers:
    <!-- THM:START -->    ... <!-- THM:END -->
    <!-- CREDLY:START --> ... <!-- CREDLY:END -->

Configuration via environment variables (set in the workflow):
    THM_USERNAME   - your TryHackMe username (from tryhackme.com/p/<username>)
    CREDLY_USER    - your Credly vanity, e.g. karol-wroblewski.65e1a9b0
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error

THM_USERNAME = os.environ.get("THM_USERNAME", "").strip()
CREDLY_USER = os.environ.get("CREDLY_USER", "").strip()
README = "README.md"

# Browser-like headers - helps get past Cloudflare / THM rate limits
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://tryhackme.com/",
}


def get_json(url, retries=4, backoff=4):
    """Fetch JSON with retries. THM often returns 429 from data-center IPs;
    a few attempts with a delay usually get through."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 403, 503) and attempt < retries:
                wait = backoff * attempt
                print(f"[retry] {e.code} on attempt {attempt}, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(backoff)
                continue
            raise
    if last:
        raise last


def pick(d, *keys, default=None):
    """Return the first present value among the given keys (resilient to API changes)."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


# ---------- TryHackMe ----------

def fetch_thm():
    if not THM_USERNAME:
        return None
    try:
        prof = get_json(f"https://tryhackme.com/api/v2/public-profile?user={THM_USERNAME}")
    except Exception as e:
        print(f"[THM] profile error: {e}", file=sys.stderr)
        return None

    # The API may wrap data in {"data": {...}} or return a flat object
    data = prof.get("data", prof) if isinstance(prof, dict) else {}
    print("[THM] raw response (to inspect field names):")
    print(json.dumps(data, indent=2)[:1500], file=sys.stderr)

    rank = pick(data, "userRank", "rank", "ranking")
    points = pick(data, "points", "totalPoints")
    rank_name = pick(data, "rankName", "userRankName", "title")
    level = pick(data, "level")

    # number of completed rooms - from a separate endpoint (metadata.totalCount)
    rooms = None
    try:
        uid = pick(data, "userId", "_id", "id", default=THM_USERNAME)
        cr = get_json(f"https://tryhackme.com/api/v2/public-profile/completed-rooms?user={uid}&limit=1&page=1")
        rooms = pick(cr.get("metadata", {}), "totalCount", "total", "count")
    except Exception as e:
        print(f"[THM] rooms unavailable: {e}", file=sys.stderr)

    return {"rank": rank, "points": points, "rank_name": rank_name,
            "level": level, "rooms": rooms}


def thm_badge():
    # S3-hosted image - served outside the THM API, so it always renders (no 429).
    return (f'<p align="center">\n'
            f'  <a href="https://tryhackme.com/p/{THM_USERNAME}">\n'
            f'    <img src="https://tryhackme-badges.s3.amazonaws.com/{THM_USERNAME}.png" '
            f'alt="TryHackMe badge"/>\n'
            f'  </a>\n</p>')


def render_thm(t):
    badge = thm_badge()
    # no API data (e.g. 429) -> show just the live badge, no ugly error
    if not t or all(t.get(k) in (None, "") for k in ("rank", "points", "rank_name", "level", "rooms")):
        return badge

    def cell(label, val, emoji):
        return f"| {emoji} **{label}** | {val if val not in (None,'') else '—'} |"

    lines = [
        "| | |",
        "|---|---|",
        cell("World rank", f"#{t['rank']}" if t['rank'] else None, "🌍"),
        cell("Points", t["points"], "⭐"),
        cell("Rank", t["rank_name"], "🎖️"),
        cell("Level", t["level"], "📶"),
        cell("Rooms completed", t["rooms"], "🧩"),
    ]
    return "\n".join(lines) + "\n\n" + badge


# ---------- Credly ----------

def fetch_credly():
    if not CREDLY_USER:
        return None
    try:
        js = get_json(f"https://www.credly.com/users/{CREDLY_USER}/badges.json")
    except Exception as e:
        print(f"[Credly] error: {e}", file=sys.stderr)
        return None
    return js.get("data", [])


def render_credly(badges, max_badges=0):
    if badges is None:
        return "_Credly certifications currently unavailable._"
    if not badges:
        return "_No public badges on Credly._"

    # newest first
    def keyf(b):
        return b.get("issued_at_date") or ""
    badges = sorted(badges, key=keyf, reverse=True)
    total = len(badges)
    if max_badges and total > max_badges:
        badges = badges[:max_badges]

    imgs = []
    for b in badges:
        tmpl = b.get("badge_template", {}) or {}
        name = tmpl.get("name", "badge")
        img = tmpl.get("image_url") or tmpl.get("image", {}).get("url", "")
        bid = b.get("id", "")
        link = f"https://www.credly.com/badges/{bid}/public_url" if bid else "https://www.credly.com/users/" + CREDLY_USER
        alt = name.replace('"', "'")
        imgs.append(f'  <a href="{link}" title="{alt}"><img src="{img}" width="90" alt="{alt}"/></a>')

    header = f'<p align="center"><b>{total}</b> verified certifications on Credly</p>\n'
    grid = '<p align="center">\n' + "\n".join(imgs) + "\n</p>"
    return header + grid


# ---------- inject into README ----------

def inject(content, tag, block):
    pat = re.compile(rf"(<!-- {tag}:START -->)(.*?)(<!-- {tag}:END -->)", re.DOTALL)
    if not pat.search(content):
        print(f"[!] Markers {tag} not found in README - skipping.", file=sys.stderr)
        return content
    return pat.sub(rf"\1\n{block}\n\3", content)


def main():
    with open(README, encoding="utf-8") as f:
        content = f.read()

    content = inject(content, "THM", render_thm(fetch_thm()))
    content = inject(content, "CREDLY", render_credly(fetch_credly()))

    with open(README, "w", encoding="utf-8") as f:
        f.write(content)
    print("README updated.")


if __name__ == "__main__":
    main()
