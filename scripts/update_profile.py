#!/usr/bin/env python3
"""
Refreshes README.md with LIVE data from TryHackMe and Credly.
Fills the sections between the markers:
    <!-- THM:START -->    ... <!-- THM:END -->
    <!-- CREDLY:START --> ... <!-- CREDLY:END -->

Configuration via environment variables (set in the workflow):
    THM_USER_ID  - your TryHackMe user id. Either works:
                     * the 24-char hash (e.g. 66a20d98...) - from the "sharerId="
                       value in any TryHackMe social-share link, or
                     * the numeric userPublicId from the profile-badge embed iframe.
    THM_USERNAME - your TryHackMe username (used for the profile link).
    CREDLY_USER  - your Credly vanity, e.g. karol-wroblewski.65e1a9b0

The script tries two data sources and uses whichever responds:
   A) badge HTML endpoint  (fresh: rank, streak, badges, rooms)
   B) public-profile JSON  (fallback; fields parsed defensively + logged)

Note: the S3 "badge PNG" is cached by TryHackMe and often stale - not used.
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error

THM_ID = os.environ.get("THM_USER_ID", "").strip()
THM_USERNAME = os.environ.get("THM_USERNAME", "").strip()
CREDLY_USER = os.environ.get("CREDLY_USER", "").strip()
README = "README.md"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://tryhackme.com/",
}


def fetch(url, retries=4, backoff=4):
    """Fetch raw text with retries (THM returns 429 from data-center IPs)."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
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
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


# ---------- TryHackMe ----------

def _thm_from_badge_html():
    """Strategy A: scrape the live badge HTML (order: Rank, Streak, Badges, Rooms)."""
    url = f"https://tryhackme.com/api/v2/badges/public-profile?userPublicId={THM_ID}"
    html = fetch(url)

    def one(p):
        m = re.search(p, html)
        return m.group(1).strip() if m else None

    username = one(r'<span class="user_name">([^<]+)</span>')
    level = one(r'<span class="rank-title">([^<]+)</span>')
    stats = [s.strip() for s in re.findall(r'<span class="details-text">([^<]+)</span>', html)]
    print(f"[THM/A] username={username} level={level} stats(order)={stats}", file=sys.stderr)
    if len(stats) >= 4:
        return {"username": username, "level": level,
                "rank": stats[0], "streak": stats[1],
                "badges": stats[2], "rooms": stats[3]}
    return None


def _thm_from_json():
    """Strategy B: public-profile JSON (fields parsed defensively + raw logged)."""
    prof = json.loads(fetch(f"https://tryhackme.com/api/v2/public-profile?user={THM_ID}"))
    data = prof.get("data", prof) if isinstance(prof, dict) else {}
    print("[THM/B] raw JSON (first 1500 chars, inspect field names):", file=sys.stderr)
    print(json.dumps(data, indent=2)[:1500], file=sys.stderr)

    rooms = None
    try:
        cr = json.loads(fetch(
            f"https://tryhackme.com/api/v2/public-profile/completed-rooms?user={THM_ID}&limit=1&page=1"))
        rooms = pick(cr.get("metadata", {}), "totalCount", "total", "count")
    except Exception as e:
        print(f"[THM/B] rooms unavailable: {e}", file=sys.stderr)

    return {
        "username": pick(data, "username", "userName"),
        "level": pick(data, "rankName", "userRankName", "level"),
        "rank": pick(data, "userRank", "rank", "ranking"),
        "streak": pick(data, "streak", "streakDays", "dayStreak"),
        "badges": pick(data, "badges", "badgeCount", "totalBadges"),
        "rooms": rooms if rooms is not None else pick(data, "completedRooms", "roomsCompleted"),
    }


def fetch_thm():
    if not THM_ID:
        print("[THM] THM_USER_ID not set - skipping.", file=sys.stderr)
        return None
    for name, strat in (("badge-html", _thm_from_badge_html), ("json", _thm_from_json)):
        try:
            res = strat()
            if res and any(res.get(k) not in (None, "") for k in ("rank", "streak", "badges", "rooms")):
                print(f"[THM] using strategy: {name}", file=sys.stderr)
                return res
        except Exception as e:
            print(f"[THM] strategy {name} failed: {e}", file=sys.stderr)
    return None


def render_thm(t):
    link = f"https://tryhackme.com/p/{THM_USERNAME}" if THM_USERNAME else "https://tryhackme.com/"
    if not t:
        return f'_Live TryHackMe stats unavailable right now._ · <a href="{link}">View profile →</a>'

    lvl = f" · Level **{t['level']}**" if t.get("level") else ""
    header = f'<p align="center"><a href="{link}"><b>{t.get("username") or "TryHackMe"}</b></a>{lvl}</p>\n'

    def cell(label, val, emoji):
        if val in (None, ""):
            return None
        return f"| {emoji} **{label}** | {val} |"

    rows = ["| | |", "|---|---|"]
    for r in (cell("World rank", f"#{t.get('rank')}" if t.get("rank") else None, "🏆"),
              cell("Streak (days)", t.get("streak"), "🔥"),
              cell("Badges", t.get("badges"), "🎖️"),
              cell("Rooms completed", t.get("rooms"), "🚪")):
        if r:
            rows.append(r)
    return header + "\n".join(rows)


# ---------- Credly ----------

def fetch_credly():
    if not CREDLY_USER:
        return None
    try:
        return json.loads(fetch(f"https://www.credly.com/users/{CREDLY_USER}/badges.json")).get("data", [])
    except Exception as e:
        print(f"[Credly] error: {e}", file=sys.stderr)
        return None


def render_credly(badges, max_badges=0):
    if badges is None:
        return "_Credly certifications currently unavailable._"
    if not badges:
        return "_No public badges on Credly._"

    badges = sorted(badges, key=lambda b: b.get("issued_at_date") or "", reverse=True)
    total = len(badges)
    if max_badges and total > max_badges:
        badges = badges[:max_badges]

    imgs = []
    for b in badges:
        tmpl = b.get("badge_template", {}) or {}
        name = tmpl.get("name", "badge")
        img = tmpl.get("image_url") or (tmpl.get("image", {}) or {}).get("url", "")
        bid = b.get("id", "")
        link = (f"https://www.credly.com/badges/{bid}/public_url"
                if bid else f"https://www.credly.com/users/{CREDLY_USER}")
        alt = name.replace('"', "'")
        imgs.append(f'  <a href="{link}" title="{alt}"><img src="{img}" width="90" alt="{alt}"/></a>')

    header = f'<p align="center"><b>{total}</b> verified certifications on Credly</p>\n'
    return header + '<p align="center">\n' + "\n".join(imgs) + "\n</p>"


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
