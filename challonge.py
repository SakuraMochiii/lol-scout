"""Scrape bracket data from Challonge tournament pages."""

import json
import re
from pathlib import Path

import requests

BRACKET_FILE = Path(__file__).parent / "data" / "bracket.json"


def fetch_bracket(url: str) -> dict:
    """Fetch and parse bracket data from a Challonge tournament URL."""
    import subprocess
    result = subprocess.run(
        ["curl", "-s", "-L", "--compressed", "-A",
         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
         url],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr.decode('utf-8', errors='replace')}")
    html = result.stdout.decode("utf-8", errors="replace")
    if not html or "<title>" not in html:
        raise RuntimeError("Failed to fetch page (possibly blocked)")

    # Extract TournamentStore JSON embedded in the page
    match = re.search(
        r"window\._initialStoreState\['TournamentStore'\]\s*=\s*(\{.*?\});\s*window\._initialStoreState\['",
        html, re.DOTALL,
    )
    if not match:
        raise ValueError("Could not find tournament data in page")

    store = json.loads(match.group(1))
    tournament = store.get("tournament", {})

    # Build participant lookup from matches
    participants = {}
    for matches in store.get("matches_by_round", {}).values():
        for m in matches:
            for key in ("player1", "player2"):
                p = m.get(key)
                if p and isinstance(p, dict):
                    participants[p["id"]] = {
                        "id": p["id"],
                        "name": p["display_name"],
                        "seed": p.get("seed"),
                    }

    # Build rounds with match results
    rounds = []
    for rnd_num in sorted(store.get("matches_by_round", {}).keys(), key=int):
        matches = []
        for m in store["matches_by_round"][rnd_num]:
            p1 = m.get("player1", {}) or {}
            p2 = m.get("player2", {}) or {}
            scores = m.get("scores", [])
            matches.append({
                "id": m["id"],
                "player1": p1.get("display_name", "TBD"),
                "player1_id": p1.get("id"),
                "player2": p2.get("display_name", "TBD"),
                "player2_id": p2.get("id"),
                "state": m.get("state", "open"),
                "score1": scores[0] if len(scores) > 0 else None,
                "score2": scores[1] if len(scores) > 1 else None,
                "winner_id": m.get("winner_id"),
                "winner": participants.get(m.get("winner_id"), {}).get("name"),
            })
        rounds.append({
            "round": int(rnd_num),
            "matches": matches,
        })

    # Compute standings from completed matches
    standings = {}
    for rnd in rounds:
        for m in rnd["matches"]:
            if m["state"] != "complete":
                continue
            for pid, name, is_winner in [
                (m["player1_id"], m["player1"], m["winner_id"] == m["player1_id"]),
                (m["player2_id"], m["player2"], m["winner_id"] == m["player2_id"]),
            ]:
                if pid not in standings:
                    standings[pid] = {"name": name, "wins": 0, "losses": 0}
                if is_winner:
                    standings[pid]["wins"] += 1
                else:
                    standings[pid]["losses"] += 1

    # Sort by wins desc, then losses asc
    sorted_standings = sorted(
        standings.values(), key=lambda s: (-s["wins"], s["losses"])
    )

    # Find next round (first round with open matches)
    next_round = None
    for rnd in rounds:
        if any(m["state"] == "open" for m in rnd["matches"]):
            next_round = rnd["round"]
            break

    # Extract tournament name from page title
    title_match = re.search(r"<title>\s*(.*?)\s*-\s*Challonge", html)
    name = title_match.group(1).strip() if title_match else "Tournament"

    # Fix known typos from Challonge source
    name = name.replace("Leauge", "League").replace("Ims", "IMs")

    bracket = {
        "name": name,
        "url": url,
        "type": tournament.get("tournament_type", "unknown"),
        "state": tournament.get("state", "unknown"),
        "total_rounds": len(rounds),
        "current_round": next_round,
        "rounds": rounds,
        "standings": sorted_standings,
    }

    return bracket


def save_bracket(bracket: dict) -> None:
    BRACKET_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BRACKET_FILE, "w") as f:
        json.dump(bracket, f, indent=2)


def load_bracket() -> dict | None:
    if not BRACKET_FILE.exists():
        return None
    with open(BRACKET_FILE, "r") as f:
        return json.load(f)
