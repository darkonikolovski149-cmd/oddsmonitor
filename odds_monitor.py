#!/usr/bin/env python3
odds_monitor.py - samodejno spremlja kvote za vse tekme izbranih lig in te
obvesti, ko se kvota premakne za X % ali več (privzeto 10 %) od zadnjega preverjanja.

Ničesar ne vnašaš. Samo zaženeš in pustiš teči.

Zagon:
  python odds_monitor.py                      # vsakih 10 min, privzete lige
  python odds_monitor.py --threshold 15 --interval 900
  python odds_monitor.py --sports soccer_epl basketball_nba
  python odds_monitor.py --once               # en krog (za cron / GitHub Actions)
  python odds_monitor.py --test               # testno Telegram sporočilo

Stanje se shranjuje v odds_state.json, zato po ponovnem zagonu ne začne iz nič.
Opozorilo: vsak klic porabi kredite API-ja (število lig x marketov x regij).
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
DEFAULT_SPORTS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "basketball_nba",
]


def http_json(url):
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.loads(r.read().decode()), r.headers.get("x-requests-remaining")


def fetch(sport, key, regions, markets):
    q = urllib.parse.urlencode({"apiKey": key, "regions": regions,
                                "markets": markets, "oddsFormat": "decimal"})
    return http_json(BASE.format(sport=sport) + "?" + q)


def reference_odds(event, market, ref_book):
    """Vrne {izid: kvota}. Pinnacle, če ga ni pa mediana vseh stavnic."""
    per_outcome = {}
    ref = {}
    for bm in event.get("bookmakers", []):
        for m in bm.get("markets", []):
            if m["key"] != market:
                continue
            for o in m["outcomes"]:
                name = o["name"] + (f' {o["point"]}' if "point" in o else "")
                per_outcome.setdefault(name, []).append(o["price"])
                if bm["key"] == ref_book:
                    ref[name] = o["price"]
    out = {}
    for name, prices in per_outcome.items():
        out[name] = ref.get(name) or statistics.median(prices)
    return out


def notify(text, token, chat):
    print(text + "\n", flush=True)
    if token and chat:
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
        except Exception as e:
            print(f"[telegram napaka] {e}", file=sys.stderr)


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def run_round(args, key, token, chat, state):
    now = datetime.now().strftime("%H:%M:%S")
    new_state = {}
    alerts = 0
    for sport in args.sports:
        try:
            events, remaining = fetch(sport, key, args.regions, args.markets)
        except Exception as e:
            print(f"[{now}] napaka pri {sport}: {e}", file=sys.stderr)
            # ohrani staro stanje te lige, da ne izgubimo primerjave
            for k, v in state.items():
                if
