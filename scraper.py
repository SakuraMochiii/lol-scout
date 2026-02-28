"""Scrape player data from op.gg."""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
REQUEST_DELAY = 2  # seconds between requests

_ddragon_version_cache = {"version": None, "fetched_at": 0}


class ScrapeError(Exception):
    pass


def _fetch(url: str) -> str:
    """Fetch a URL with retries and backoff."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 429:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                continue
            if resp.status_code == 403:
                raise ScrapeError("Blocked by Cloudflare/op.gg (403)")
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
    raise ScrapeError(f"Failed after {MAX_RETRIES} attempts: {last_error}")


def _extract_rsc_data(html: str) -> str:
    """Extract Next.js RSC push data from op.gg HTML."""
    pushes = re.findall(
        r'self\.__next_f\.push\(\[1,"(.*?)"\]\)',
        html,
        re.DOTALL,
    )
    if not pushes:
        # Try alternative pattern
        pushes = re.findall(
            r'self\.__next_f\.push\(\[1,\s*"(.*?)"\s*\]\)',
            html,
            re.DOTALL,
        )
    # Unescape the strings
    combined = []
    for p in pushes:
        try:
            unescaped = p.encode().decode("unicode_escape")
        except (UnicodeDecodeError, ValueError):
            unescaped = p
        combined.append(unescaped)
    return "\n".join(combined)


def _find_json_objects(text: str, key: str) -> list[dict]:
    """Find JSON objects in text that contain a specific key."""
    results = []
    pattern = re.compile(re.escape(f'"{key}"'))
    for match in pattern.finditer(text):
        # Walk backwards to find opening brace
        start = match.start()
        depth = 0
        obj_start = None
        for i in range(start, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                if depth == 0:
                    obj_start = i
                    break
                depth -= 1
        if obj_start is None:
            continue
        # Walk forward to find closing brace
        depth = 0
        for i in range(obj_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[obj_start : i + 1])
                        results.append(obj)
                    except json.JSONDecodeError:
                        pass
                    break
    return results


def scrape_player(game_name: str, tag_line: str) -> dict:
    """Scrape a player's ranked stats and champion pool from op.gg."""
    slug = f"{game_name}-{tag_line}"
    stats = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "tier": "UNRANKED",
        "division": None,
        "lp": 0,
        "season_games": 0,
        "season_wins": 0,
        "season_losses": 0,
        "season_winrate": 0,
        "champions": [],
        "scrape_error": None,
    }

    # Fetch profile page
    try:
        profile_html = _fetch(f"https://www.op.gg/summoners/na/{slug}")
    except ScrapeError as e:
        stats["scrape_error"] = str(e)
        return stats

    rsc = _extract_rsc_data(profile_html)

    # Parse tier info from RSC data
    tier_match = re.search(
        r'"tier"\s*:\s*"(\w+)"\s*,\s*"division"\s*:\s*(\d+)\s*,\s*"lp"\s*:\s*(\d+)',
        rsc,
    )
    if tier_match:
        stats["tier"] = tier_match.group(1)
        stats["division"] = int(tier_match.group(2))
        stats["lp"] = int(tier_match.group(3))

    # Also try to extract from the HTML directly as fallback
    if stats["tier"] == "UNRANKED":
        tier_html = re.search(
            r'<div[^>]*class="[^"]*tier[^"]*"[^>]*>(\w+)</div>', profile_html
        )
        if tier_html:
            stats["tier"] = tier_html.group(1).upper()

    # Parse win/loss from profile page
    wl_match = re.search(r'"wins"\s*:\s*(\d+)\s*,\s*"losses"\s*:\s*(\d+)', rsc)
    if wl_match:
        stats["season_wins"] = int(wl_match.group(1))
        stats["season_losses"] = int(wl_match.group(2))
        stats["season_games"] = stats["season_wins"] + stats["season_losses"]
        if stats["season_games"] > 0:
            stats["season_winrate"] = round(
                stats["season_wins"] / stats["season_games"] * 100, 1
            )

    # Fetch champions page
    time.sleep(REQUEST_DELAY)
    try:
        champ_html = _fetch(f"https://www.op.gg/summoners/na/{slug}/champions")
    except ScrapeError as e:
        stats["scrape_error"] = f"Profile OK, champions page failed: {e}"
        return stats

    champ_rsc = _extract_rsc_data(champ_html)

    # Parse champion stats
    # Look for champion stat objects with play/win/lose fields
    champ_entries = []

    # Try to find my_champion_stats array
    champ_array_match = re.search(
        r'"my_champion_stats"\s*:\s*\[', champ_rsc
    )
    if champ_array_match:
        start = champ_array_match.start()
        # Find the opening bracket
        bracket_pos = champ_rsc.index("[", start)
        depth = 0
        end = bracket_pos
        for i in range(bracket_pos, len(champ_rsc)):
            if champ_rsc[i] == "[":
                depth += 1
            elif champ_rsc[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            champ_list = json.loads(champ_rsc[bracket_pos:end])
            for c in champ_list:
                if isinstance(c, dict) and "play" in c:
                    champ_entries.append(c)
        except json.JSONDecodeError:
            pass

    # Fallback: search for individual champion stat objects
    if not champ_entries:
        for obj in _find_json_objects(champ_rsc, "champion_id"):
            if "play" in obj and "win" in obj:
                champ_entries.append(obj)

    # Also try HTML page directly for champion data
    if not champ_entries:
        for obj in _find_json_objects(champ_html, "champion_id"):
            if "play" in obj and "win" in obj:
                champ_entries.append(obj)

    # Deduplicate by champion_id and build clean champion list
    seen = set()
    for c in champ_entries:
        cid = c.get("champion_id") or c.get("id")
        if not cid or cid in seen:
            continue
        seen.add(cid)

        games = c.get("play", 0)
        wins = c.get("win", 0)
        losses = c.get("lose", 0)
        winrate = c.get("win_rate") or (
            round(wins / games * 100, 1) if games > 0 else 0
        )

        kda_obj = c.get("kda", {})
        if isinstance(kda_obj, dict):
            kda = kda_obj.get("kda", 0)
            avg_kills = kda_obj.get("avg_kill", 0)
            avg_deaths = kda_obj.get("avg_death", 0)
            avg_assists = kda_obj.get("avg_assist", 0)
        else:
            kda = float(kda_obj) if kda_obj else 0
            avg_kills = avg_deaths = avg_assists = 0

        # Champion name/key
        champ_name = c.get("name", f"Champion {cid}")
        champ_key = c.get("key") or c.get("image_url", "").split("/")[-1].replace(".png", "") or champ_name

        stats["champions"].append({
            "champion_id": cid,
            "champion_name": champ_name,
            "champion_key": champ_key,
            "games": games,
            "wins": wins,
            "losses": losses,
            "winrate": round(float(winrate), 1),
            "kda": round(float(kda), 2),
            "avg_kills": round(float(avg_kills), 1),
            "avg_deaths": round(float(avg_deaths), 1),
            "avg_assists": round(float(avg_assists), 1),
        })

    # Sort by games played descending
    stats["champions"].sort(key=lambda x: -x["games"])

    # Update season totals from champion data if not found earlier
    if stats["season_games"] == 0 and stats["champions"]:
        stats["season_games"] = sum(c["games"] for c in stats["champions"])
        stats["season_wins"] = sum(c["wins"] for c in stats["champions"])
        stats["season_losses"] = sum(c["losses"] for c in stats["champions"])
        if stats["season_games"] > 0:
            stats["season_winrate"] = round(
                stats["season_wins"] / stats["season_games"] * 100, 1
            )

    return stats


def parse_opgg_multi_link(url: str) -> list[tuple[str, str]]:
    """Parse an op.gg multi search URL into (game_name, tag_line) tuples."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    summoners_raw = params.get("summoners", [""])[0]
    names = [
        n.strip()
        for n in re.split(r"\n|%0A|,", unquote(summoners_raw))
        if n.strip()
    ]

    result = []
    for name in names:
        if "#" in name:
            game_name, tag = name.rsplit("#", 1)
        elif "-" in name:
            parts = name.rsplit("-", 1)
            game_name, tag = parts[0], parts[1]
        else:
            game_name, tag = name, "NA1"
        result.append((game_name.strip(), tag.strip()))
    return result


def parse_player_input(text: str) -> list[tuple[str, str]]:
    """Parse player input — either an op.gg link or a username#tag."""
    text = text.strip()
    if "op.gg" in text:
        return parse_opgg_multi_link(text)
    if "#" in text:
        name, tag = text.rsplit("#", 1)
        return [(name.strip(), tag.strip())]
    if "-" in text and not text.startswith("http"):
        parts = text.rsplit("-", 1)
        return [(parts[0].strip(), parts[1].strip())]
    return [(text, "NA1")]


def get_ddragon_version() -> str:
    """Get the latest Data Dragon version (cached for 24h)."""
    now = time.time()
    if (
        _ddragon_version_cache["version"]
        and now - _ddragon_version_cache["fetched_at"] < 86400
    ):
        return _ddragon_version_cache["version"]
    try:
        resp = requests.get(
            "https://ddragon.leagueoflegends.com/api/versions.json", timeout=10
        )
        resp.raise_for_status()
        version = resp.json()[0]
        _ddragon_version_cache["version"] = version
        _ddragon_version_cache["fetched_at"] = now
        return version
    except Exception:
        return _ddragon_version_cache["version"] or "14.24.1"


def champion_icon_url(champion_key: str) -> str:
    """Get Data Dragon CDN URL for a champion icon."""
    version = get_ddragon_version()
    # Capitalize first letter for ddragon
    key = champion_key[0].upper() + champion_key[1:] if champion_key else "Unknown"
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{key}.png"
