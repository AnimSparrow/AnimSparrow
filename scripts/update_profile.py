#!/usr/bin/env python3
"""
Odświeża README.md danymi z TryHackMe i Credly.
Wypełnia sekcje między znacznikami:
    <!-- THM:START -->    ... <!-- THM:END -->
    <!-- CREDLY:START --> ... <!-- CREDLY:END -->

Konfiguracja przez zmienne środowiskowe (ustawione w workflow):
    THM_USERNAME   - Twój login z TryHackMe (ten z URL tryhackme.com/p/<username>)
    CREDLY_USER    - Twój vanity z Credly, np. karol-wroblewski.65e1a9b0
"""

import os
import re
import sys
import json
import urllib.request

THM_USERNAME = os.environ.get("THM_USERNAME", "").strip()
CREDLY_USER = os.environ.get("CREDLY_USER", "").strip()
README = "README.md"

UA = "Mozilla/5.0 (compatible; profile-readme-bot/1.0; +https://github.com)"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def pick(d, *keys, default=None):
    """Zwraca pierwszą istniejącą wartość spod podanych kluczy (odporne na zmiany API)."""
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
        print(f"[THM] blad profilu: {e}", file=sys.stderr)
        return None

    # API bywa opakowane w {"data": {...}} albo zwraca plaski obiekt
    data = prof.get("data", prof) if isinstance(prof, dict) else {}
    print("[THM] surowa odpowiedz (do podejrzenia pol):")
    print(json.dumps(data, indent=2)[:1500], file=sys.stderr)

    rank = pick(data, "userRank", "rank", "ranking")
    points = pick(data, "points", "totalPoints")
    rank_name = pick(data, "rankName", "userRankName", "title")
    level = pick(data, "level")

    # liczba ukonczonych roomow - z osobnego endpointu (metadata.totalCount)
    rooms = None
    try:
        uid = pick(data, "userId", "_id", "id", default=THM_USERNAME)
        cr = get_json(f"https://tryhackme.com/api/v2/public-profile/completed-rooms?user={uid}&limit=1&page=1")
        rooms = pick(cr.get("metadata", {}), "totalCount", "total", "count")
    except Exception as e:
        print(f"[THM] rooms niedostepne: {e}", file=sys.stderr)

    return {"rank": rank, "points": points, "rank_name": rank_name,
            "level": level, "rooms": rooms}


def render_thm(t):
    if not t:
        return ("_Statystyki TryHackMe chwilowo niedostepne._\n"
                "_(sprawdz THM_USERNAME albo logi workflow)_")

    def cell(label, val, emoji):
        return f"| {emoji} **{label}** | {val if val not in (None,'') else '—'} |"

    lines = [
        "| | |",
        "|---|---|",
        cell("Ranking swiatowy", f"#{t['rank']}" if t['rank'] else None, "🌍"),
        cell("Punkty", t["points"], "⭐"),
        cell("Ranga", t["rank_name"], "🎖️"),
        cell("Poziom", t["level"], "📶"),
        cell("Ukonczone roomy", t["rooms"], "🧩"),
    ]
    badge = (f'\n<p align="center">\n'
             f'  <img src="https://tryhackme-badges.s3.amazonaws.com/{THM_USERNAME}.png" '
             f'alt="TryHackMe badge"/>\n</p>')
    return "\n".join(lines) + "\n" + badge


# ---------- Credly ----------

def fetch_credly():
    if not CREDLY_USER:
        return None
    try:
        js = get_json(f"https://www.credly.com/users/{CREDLY_USER}/badges.json")
    except Exception as e:
        print(f"[Credly] blad: {e}", file=sys.stderr)
        return None
    return js.get("data", [])


def render_credly(badges, max_badges=0):
    if badges is None:
        return "_Certyfikaty Credly chwilowo niedostepne._"
    if not badges:
        return "_Brak publicznych badge'y na Credly._"

    # najnowsze pierwsze
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

    header = f'<p align="center"><b>{total}</b> zweryfikowanych certyfikatow na Credly</p>\n'
    grid = '<p align="center">\n' + "\n".join(imgs) + "\n</p>"
    return header + grid


# ---------- wstrzykiwanie do README ----------

def inject(content, tag, block):
    pat = re.compile(rf"(<!-- {tag}:START -->)(.*?)(<!-- {tag}:END -->)", re.DOTALL)
    if not pat.search(content):
        print(f"[!] Brak znacznikow {tag} w README - pomijam.", file=sys.stderr)
        return content
    return pat.sub(rf"\1\n{block}\n\3", content)


def main():
    with open(README, encoding="utf-8") as f:
        content = f.read()

    content = inject(content, "THM", render_thm(fetch_thm()))
    content = inject(content, "CREDLY", render_credly(fetch_credly()))

    with open(README, "w", encoding="utf-8") as f:
        f.write(content)
    print("README zaktualizowane.")


if __name__ == "__main__":
    main()
