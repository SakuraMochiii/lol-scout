"""JSON file storage for tournament data."""

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "tournament.json"
_lock = threading.Lock()


def generate_id() -> str:
    return secrets.token_hex(4)


def default_tournament() -> dict:
    return {
        "meta": {
            "season_name": "Season 1",
            "my_team_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "teams": [],
    }


def load() -> dict:
    with _lock:
        if not DATA_FILE.exists():
            data = default_tournament()
            save(data)
            return data
        with open(DATA_FILE, "r") as f:
            return json.load(f)


def save(data: dict) -> None:
    with _lock:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(DATA_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, DATA_FILE)


def get_team(data: dict, team_id: str) -> dict | None:
    for t in data["teams"]:
        if t["id"] == team_id:
            return t
    return None


def get_player(data: dict, player_id: str) -> tuple[dict, dict] | tuple[None, None]:
    """Returns (team, player) tuple or (None, None)."""
    for t in data["teams"]:
        for p in t["players"]:
            if p["id"] == player_id:
                return t, p
    return None, None


def add_team(data: dict, name: str) -> dict:
    team = {
        "id": generate_id(),
        "name": name,
        "players": [],
    }
    data["teams"].append(team)
    save(data)
    return team


def delete_team(data: dict, team_id: str) -> bool:
    before = len(data["teams"])
    data["teams"] = [t for t in data["teams"] if t["id"] != team_id]
    if len(data["teams"]) < before:
        if data["meta"]["my_team_id"] == team_id:
            data["meta"]["my_team_id"] = None
        save(data)
        return True
    return False


def add_player(data: dict, team_id: str, game_name: str, tag_line: str,
               role: str = "fill", is_substitute: bool = False) -> dict | None:
    team = get_team(data, team_id)
    if not team:
        return None
    player = {
        "id": generate_id(),
        "game_name": game_name,
        "tag_line": tag_line,
        "role": role,
        "is_substitute": is_substitute,
        "stats": None,
    }
    team["players"].append(player)
    save(data)
    return player


def update_player(data: dict, player_id: str, **kwargs) -> dict | None:
    _, player = get_player(data, player_id)
    if not player:
        return None
    for key in ("game_name", "tag_line", "role", "is_substitute"):
        if key in kwargs:
            player[key] = kwargs[key]
    if "stats" in kwargs:
        player["stats"] = kwargs["stats"]
    save(data)
    return player


def delete_player(data: dict, player_id: str) -> bool:
    for team in data["teams"]:
        before = len(team["players"])
        team["players"] = [p for p in team["players"] if p["id"] != player_id]
        if len(team["players"]) < before:
            save(data)
            return True
    return False
