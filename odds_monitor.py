#!/usr/bin/env python3
# odds_monitor.py - samodejno spremlja kvote za vse tekme izbranih lig in te
# obvesti, ko se kvota premakne za X % ali več (privzeto 10 %) od zadnjega preverjanja.
#
# Ničesar ne vnašaš. Samo zaženeš in pustiš teči.
#
# Priprava (enkrat):
#   1. API ključ: https://the-odds-api.com
#   2. (neobvezno) Telegram: v Telegramu odpri @BotFather -> /newbot -> dobiš token.
#      Botu pošlji sporočilo, nato odpri
#      https://api.telegram.org/bot<TOKEN>/getUpdates  in poišči "chat":{"id": ...}
#   3. export ODDS_API_KEY="..."
#      export TELEGRAM_BOT_TOKEN="..."   (neobvezno)
#      export TELEGRAM_CHAT_ID="..."     (neobvezno)
#
# Zagon:
#   python odds_monitor.py                      # vsakih 10 min, privzete lige
#   python odds_monitor.py --threshold 15 --interval 900
#   python odds_monitor.py --sports soccer_epl basketball_nba
#   python odds_monitor.py --once               # en krog (za cron / GitHub Actions)
#
# Stanje se shranjuje v odds_state.json, zato po ponovnem zagonu ne začne iz nič.
# Opozorilo: vsak klic porabi kredite API-ja (število lig x marketov x regij).
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
    # Vrne {izid: kvota}. Pinnacle, ce ga ni pa mediana vseh stavnic.
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
                if v.get("sport") == sport:
                    new_state[k] = v
            continue
        print(f"[{now}] {sport}: {len(events)} tekem | preostalih kreditov: {remaining}",
              flush=True)
        for ev in events:
            title = f'{ev["home_team"]} - {ev["away_team"]}'
            for mk in args.markets.split(","):
                cur = reference_odds(ev, mk, args.book)
                sk = f'{ev["id"]}|{mk}'
                old = state.get(sk, {}).get("odds")
                new_state[sk] = {"sport": sport, "odds": cur}
                if not old:
                    continue
                moves = []
                for outcome, price in cur.items():
                    p0 = old.get(outcome)
                    if not p0:
                        continue
                    change = (price / p0 - 1) * 100
                    if abs(change) >= args.threshold:
                        arrow = "kvota PADA (denar vstopa)" if change < 0 else "kvota RASTE"
                        moves.append(f"  {outcome}: {p0:.2f} -> {price:.2f} "
                                     f"({change:+.1f} %) {arrow}")
                if moves:
                    alerts += 1
                    when = ev.get("commence_time", "")[:16].replace("T", " ")
                    notify(f"KVOTA SE PREMIKA\n{title}\n{sport} | {mk} | start {when} UTC\n"
                           + "\n".join(moves), token, chat)
    return new_state, alerts


def main():
    p = argparse.ArgumentParser(description="Spremljanje premikov kvot")
    p.add_argument("--sports", nargs="+", default=DEFAULT_SPORTS)
    p.add_argument("--markets", default="h2h", help="h2h, totals, spreads (z vejico)")
    p.add_argument("--regions", default="eu")
    p.add_argument("--book", default="pinnacle",
                   help="referenčna stavnica; če je ni, se vzame mediana vseh")
    p.add_argument("--threshold", type=float, default=10.0,
                   help="premik kvote v %% od zadnjega preverjanja (privzeto 10)")
    p.add_argument("--interval", type=int, default=600, help="sekund med krogi (600 = 10 min)")
    p.add_argument("--state", default="odds_state.json")
    p.add_argument("--once", action="store_true")
    p.add_argument("--test", action="store_true",
                   help="pošlje testno Telegram sporočilo in konča")
    args = p.parse_args()

    if args.test:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            sys.exit("NAPAKA: manjka TELEGRAM_BOT_TOKEN ali TELEGRAM_CHAT_ID v Secrets.")
        data = urllib.parse.urlencode({
            "chat_id": chat,
            "text": "Testno obvestilo: povezava s Telegramom deluje."}).encode()
        try:
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
        except Exception as e:
            sys.exit(f"NAPAKA pri pošiljanju na Telegram: {e}")
        print("Testno sporočilo poslano.")
        return

    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("Manjka ODDS_API_KEY (export ODDS_API_KEY=...).")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")

    calls = len(args.sports) * len(args.markets.split(",")) * len(args.regions.split(","))
    print(f"Spremljam {len(args.sports)} lig, prag {args.threshold} %, "
          f"interval {args.interval}s. Poraba: ~{calls} kreditov na krog "
          f"(~{calls * 86400 // args.interval} na dan).", flush=True)

    state = load_state(args.state)
    while True:
        state, alerts = run_round(args, key, token, chat, state)
        save_state(args.state, state)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nKonec.")
