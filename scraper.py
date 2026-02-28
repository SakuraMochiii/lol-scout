"""Scrape player data from op.gg."""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlparse

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

_ddragon_version_cache = {"version": None, "fetched_at": 0}
_champion_roles_cache = {"data": None, "fetched_at": 0}
_champion_keys_cache = {"data": None, "fetched_at": 0}


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


def _extract_json_object_at(text: str, start: int) -> dict | None:
    """Extract a JSON object starting from a { in text."""
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _unescape_rsc(text: str) -> str:
    """Unescape double-escaped JSON from RSC payloads."""
    return text.replace('\\"', '"').replace("\\\\/", "/").replace("\\\\n", "\n")


def scrape_tier_from_multisearch(game_name: str, tag_line: str) -> dict:
    """
    Fetch tier/rank data from the op.gg multisearch page.
    Also extracts the real game_name, tagline, and internal_name for use
    in subsequent page fetches (the user-provided tag may be wrong).
    """
    # Use name#tag format (with # URL-encoded as %23) for exact match.
    # Searching by name alone returns multiple results for common names.
    search_term = f"{game_name}#{tag_line}" if tag_line else game_name
    url = f"https://op.gg/lol/multisearch/na?summoners={quote(search_term)}"
    html = _fetch(url)

    result = {
        "tier": "UNRANKED",
        "division": None,
        "lp": 0,
        "resolved_name": None,
        "resolved_tag": None,
        "internal_name": None,
        "puuid": None,
    }

    # The multisearch returns summoner objects in RSC payloads.
    # Data is double-escaped.
    clean = html.replace('\\"', '"').replace("\\\\/", "/")

    # Find the data array with summoner objects
    data_idx = clean.find('"data":[{"id"')

    # If exact name#tag search returned no data, fallback to name-only search
    if data_idx < 0 and tag_line:
        fallback_url = f"https://op.gg/lol/multisearch/na?summoners={quote(game_name)}"
        try:
            html = _fetch(fallback_url)
            clean = html.replace('\\"', '"').replace("\\\\/", "/")
            data_idx = clean.find('"data":[{"id"')
        except ScrapeError:
            pass

    if data_idx < 0:
        return result

    # Find the summoner matching our game_name (case-insensitive)
    target = game_name.lower()
    for m in re.finditer(
        r'"game_name"\s*:\s*"([^"]+)"\s*,\s*"tagline"\s*:\s*"([^"]+)"',
        clean[data_idx:],
    ):
        found_name = m.group(1)
        found_tag = m.group(2)

        # Match by game_name (case-insensitive)
        if found_name.lower() != target:
            continue

        result["resolved_name"] = found_name
        result["resolved_tag"] = found_tag

        # Look for solo_tier_info after this match
        after = clean[data_idx + m.end() : data_idx + m.end() + 1000]
        tier_match = re.search(
            r'"solo_tier_info"\s*:\s*\{\s*"tier"\s*:\s*"(\w+)"\s*,\s*"division"\s*:\s*(\d+)\s*,\s*"lp"\s*:\s*(\d+)',
            after,
        )
        if tier_match:
            result["tier"] = tier_match.group(1)
            result["division"] = int(tier_match.group(2))
            result["lp"] = int(tier_match.group(3))

        # Get internal_name and puuid
        iname_match = re.search(r'"internal_name"\s*:\s*"([^"]+)"', after)
        if iname_match:
            result["internal_name"] = iname_match.group(1)

        # puuid is before the game_name in the summoner object
        before = clean[max(0, data_idx + m.start() - 500) : data_idx + m.start()]
        puuid_match = re.search(r'"puuid"\s*:\s*"([^"]+)"', before)
        if puuid_match:
            result["puuid"] = puuid_match.group(1)

        break

    return result


def scrape_season_history(game_name: str, tag_line: str) -> dict:
    """
    Fetch past season rank data from leagueofgraphs.com.
    Uses actual peak ranks (highest reached during season), not just
    end-of-season ranks like op.gg.
    """
    slug = f"{game_name}-{tag_line}"
    url = f"https://www.leagueofgraphs.com/summoner/na/{quote(slug)}"
    html = _fetch(url)

    result = {
        "previous_season_tier": None,
        "peak_tier": None,
        "season_history": [],
    }

    # Each tagDescription block has "Ranked Solo/Duo | ... | Ranked Flex | ..."
    # We parse each block and extract only the Solo/Duo entry.
    soloq_entries = []
    descs = re.findall(
        r"class=['\"]tagDescription['\"]>(.*?)</span>", html, re.DOTALL
    )
    for desc in descs:
        # Strip HTML tags
        clean = re.sub(r"<[^>]+>", " ", desc)
        # Extract the Solo/Duo portion (before "Ranked Flex" if present)
        solo_part = clean.split("Ranked Flex")[0]
        if "Ranked Solo/Duo" not in solo_part:
            continue
        m = re.search(
            r"This player reached ([\w\s]+?) during (Season [\w\s()]+?)\."
            r"\s*At the end of the season, this player was ([\w\s]+?)\.",
            solo_part,
        )
        if m:
            soloq_entries.append({
                "season": m.group(2).strip(),
                "peak_rank": m.group(1).strip(),
                "end_rank": m.group(3).strip(),
            })

    result["season_history"] = soloq_entries

    # Determine all-time peak and previous season
    tier_order = {
        "challenger": 0, "grandmaster": 1, "master": 2, "diamond": 3,
        "emerald": 4, "platinum": 5, "gold": 6, "silver": 7, "bronze": 8, "iron": 9,
    }

    def tier_sort_key(rank_str: str) -> tuple:
        """Lower = better. Returns (tier_order, division_order)."""
        parts = rank_str.split()
        base = parts[0].lower() if parts else ""
        div_map = {"I": 1, "II": 2, "III": 3, "IV": 4}
        div = div_map.get(parts[1], 5) if len(parts) > 1 else 5
        return (tier_order.get(base, 999), div)

    # All-time peak = best peak_rank across all seasons
    best_peak = None
    best_key = (999, 5)
    for entry in soloq_entries:
        if entry["peak_rank"]:
            key = tier_sort_key(entry["peak_rank"])
            if key < best_key:
                best_key = key
                best_peak = entry["peak_rank"]
    result["peak_tier"] = best_peak

    # Previous season = the last completed season (not the current one).
    # Entries are chronological (oldest first). The last entry might be the
    # current season (S2026) or the last completed one if the player hasn't
    # played this season yet. Check by looking at the year.
    if soloq_entries:
        from datetime import datetime
        current_year = str(datetime.now().year)
        last_entry = soloq_entries[-1]
        if current_year in last_entry["season"]:
            # Last entry is current season — previous is second-to-last
            if len(soloq_entries) >= 2:
                result["previous_season_tier"] = soloq_entries[-2]["end_rank"]
        else:
            # Last entry is NOT current season (player hasn't played this season)
            # So the last entry itself is the previous season
            result["previous_season_tier"] = last_entry["end_rank"]

    return result


def scrape_masteries(game_name: str, tag_line: str) -> list[dict]:
    """Fetch champion mastery data from op.gg mastery page."""
    slug = f"{game_name}-{tag_line}"
    url = f"https://op.gg/lol/summoners/na/{quote(slug)}/mastery"
    html = _fetch(url)

    # Find the masteries array
    idx = html.find('"masteries":[{')
    if idx < 0:
        idx = html.find('masteries\\":[{')
    if idx < 0:
        return []

    arr_start = html.index("[", idx)
    depth = 0
    for i in range(arr_start, min(arr_start + 500000, len(html))):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                arr_text = html[arr_start : i + 1].replace('\\"', '"')
                try:
                    raw = json.loads(arr_text)
                except json.JSONDecodeError:
                    return []

                masteries = []
                for m in raw:
                    name = m.get("champion_name", "")
                    key = ""
                    img = m.get("champion_image_url", "")
                    if img:
                        key = img.split("/")[-1].replace(".png", "")

                    masteries.append({
                        "champion_id": m.get("champion_id"),
                        "champion_name": name,
                        "champion_key": key or name,
                        "level": m.get("level", 0),
                        "points": m.get("points", 0),
                        "last_played": m.get("last_played_at", ""),
                    })
                return masteries

    return []


UGG_API = "https://u.gg/api"
UGG_ROLE_MAP = {1: "Jgl", 2: "Sup", 3: "Bot", 4: "Top", 5: "Mid"}


def scrape_champion_roles_ugg(game_name: str, tag_line: str) -> dict:
    """
    Fetch per-champion role data from u.gg match history (GraphQL API).
    Returns {champion_id: "Mid", ...} based on most-played role per champion.
    """
    from collections import Counter, defaultdict

    champ_role_counts = defaultdict(Counter)

    page = 1
    while page <= 5:  # cap at 5 pages (100 games) to avoid long fetches
        query = {
            "query": f'''query {{ fetchPlayerMatchSummaries(
                regionId: "na1",
                riotUserName: "{game_name}",
                riotTagLine: "{tag_line}",
                queueType: 420,
                seasonIds: [26],
                page: {page}
            ) {{ matchSummaries {{ championId role }} }} }}'''
        }
        try:
            resp = requests.post(
                UGG_API, json=query, headers={**HEADERS, "Content-Type": "application/json"}, timeout=15
            )
            if resp.status_code != 200:
                break
            # Guard against HTML error pages
            content_type = resp.headers.get("content-type", "")
            if "json" not in content_type and "application" not in content_type:
                break
            data = resp.json()
            if "errors" in data:
                break
            summaries = data.get("data", {}).get("fetchPlayerMatchSummaries")
            if not summaries:
                break
            matches = summaries.get("matchSummaries", [])
            if not matches:
                break
            for m in matches:
                role_name = UGG_ROLE_MAP.get(m.get("role"), "")
                if role_name:
                    champ_role_counts[m["championId"]][role_name] += 1
            page += 1
            time.sleep(0.3)  # rate limit
        except (requests.RequestException, json.JSONDecodeError, KeyError):
            break

    # For each champion, pick the most common role
    result = {}
    for cid, roles in champ_role_counts.items():
        primary = roles.most_common(1)[0][0]
        result[cid] = primary
    return result


def scrape_champions(game_name: str, tag_line: str, champion_role_map: dict = None) -> dict:
    """
    Fetch champion stats from the op.gg champions page.
    champion_role_map: optional {champion_id: "Mid"} from u.gg match history.
    Returns season games/wins/losses and champion list.
    """
    slug = f"{game_name}-{tag_line}"
    url = f"https://op.gg/lol/summoners/na/{quote(slug)}/champions"
    html = _fetch(url)

    result = {
        "season_games": 0,
        "season_wins": 0,
        "season_losses": 0,
        "season_winrate": 0,
        "champions": [],
    }

    # Find the champion stats data block
    idx = html.find("my_champion_stats")
    if idx < 0:
        return result

    # Extract season totals from the area before my_champion_stats
    search_start = max(0, idx - 500)
    header = _unescape_rsc(html[search_start:idx])
    season_match = re.search(
        r'"game_type"\s*:\s*"RANKED"[^}]*?"play"\s*:\s*(\d+)\s*,\s*"win"\s*:\s*(\d+)\s*,\s*"lose"\s*:\s*(\d+)',
        header,
    )
    if season_match:
        result["season_games"] = int(season_match.group(1))
        result["season_wins"] = int(season_match.group(2))
        result["season_losses"] = int(season_match.group(3))
        if result["season_games"] > 0:
            result["season_winrate"] = round(
                result["season_wins"] / result["season_games"] * 100, 1
            )

    # Extract my_champion_stats array from RAW text (before unescaping).
    # The data is double-escaped (\\" for quotes), so brackets [] are never
    # inside string literals — simple depth counting works on raw text.
    raw_marker = html.find('my_champion_stats\\":[', max(0, idx - 50))
    if raw_marker < 0:
        raw_marker = html.find('"my_champion_stats":[', max(0, idx - 50))
    if raw_marker < 0:
        return result

    arr_start = html.index("[", raw_marker)
    depth = 0
    arr_end = arr_start
    for i in range(arr_start, min(arr_start + 500000, len(html))):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                arr_end = i + 1
                break

    # Unescape the extracted array, then parse.
    # RSC payloads use JS string escaping: \\" → ", \\/ → /, \\\\ → \, \\n → newline, etc.
    raw = html[arr_start:arr_end]
    try:
        # Use codecs.decode which handles standard escape sequences
        import codecs
        arr_text = codecs.decode(raw, "unicode_escape")
    except (UnicodeDecodeError, ValueError):
        # Fallback to manual replacement
        arr_text = raw.replace('\\\\"', '__DBLQUOTE__').replace('\\"', '"').replace('__DBLQUOTE__', '\\"').replace('\\/', '/')
    try:
        champ_list = json.loads(arr_text)
    except json.JSONDecodeError:
        return result

    for c in champ_list:
        if not isinstance(c, dict) or "play" not in c:
            continue

        # Skip the aggregate entry (idx=0, id=0) — it's the "all champions" summary
        champion_id = c.get("champion_id") or c.get("id", 0)
        if champion_id == 0:
            continue

        games = c.get("play", 0)
        wins = c.get("win", 0)
        losses = c.get("lose", 0)
        winrate = c.get("win_rate", 0)
        if winrate == 0 and games > 0:
            winrate = round(wins / games * 100, 1)

        kda_obj = c.get("kda", {})
        if isinstance(kda_obj, dict):
            kda = kda_obj.get("kda", 0)
            avg_kills = kda_obj.get("avg_kill", 0)
            avg_deaths = kda_obj.get("avg_death", 0)
            avg_assists = kda_obj.get("avg_assist", 0)
        else:
            kda = float(kda_obj) if kda_obj else 0
            avg_kills = avg_deaths = avg_assists = 0

        # Champion name/key from op.gg data
        champ_name = c.get("name", "")
        # Extract key from image_url (e.g. ".../champion/Ekko.png" -> "Ekko")
        image_url = c.get("image_url", "")
        champ_key = c.get("key", "")
        if not champ_key and image_url:
            champ_key = image_url.split("/")[-1].replace(".png", "")

        display_name = champ_name or champ_key or f"Champion {champion_id}"
        # Use actual match history role if available, fall back to static data
        role_str = ""
        if champion_role_map and champion_id in champion_role_map:
            role_str = champion_role_map[champion_id]
        else:
            # Fallback to Meraki static roles
            static_roles = get_champion_roles()
            positions = static_roles.get(display_name) or static_roles.get(champ_key) or []
            role_str = "/".join(ROLE_SHORT.get(r, r) for r in positions) if positions else ""

        result["champions"].append({
            "champion_id": champion_id,
            "champion_name": display_name,
            "champion_key": champ_key or champ_name,
            "games": games,
            "wins": wins,
            "losses": losses,
            "winrate": round(float(winrate), 1),
            "kda": round(float(kda), 2),
            "avg_kills": round(float(avg_kills), 1),
            "avg_deaths": round(float(avg_deaths), 1),
            "avg_assists": round(float(avg_assists), 1),
            "role": role_str,
        })

    result["champions"].sort(key=lambda x: -x["games"])
    return result


def trigger_opgg_renewal(slug: str, puuid: str) -> bool:
    """
    Trigger op.gg's profile data refresh via their React Server Action.
    This tells op.gg to pull fresh data from Riot's API.
    Returns True if renewal was triggered successfully.
    """
    if not puuid:
        return False
    action_id = "405a04669583947dc03eb8c7f367adf28c8f714e86"
    try:
        resp = requests.post(
            f"https://op.gg/lol/summoners/na/{quote(slug)}",
            headers={
                **HEADERS,
                "Content-Type": "text/plain;charset=UTF-8",
                "Next-Action": action_id,
            },
            data=json.dumps([{"region": "na", "puuid": puuid}]),
            timeout=15,
        )
        if resp.status_code == 200 and "RENEWING" in resp.text:
            # Wait for renewal to complete
            time.sleep(3)
            return True
    except Exception:
        pass
    return False


def scrape_player(game_name: str, tag_line: str) -> dict:
    """Scrape a player's full stats from op.gg."""
    stats = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "tier": "UNRANKED",
        "division": None,
        "lp": 0,
        "previous_season_tier": None,
        "peak_tier": None,
        "season_games": 0,
        "season_wins": 0,
        "season_losses": 0,
        "season_winrate": 0,
        "champions": [],
        "masteries": [],
        "opgg_url": None,
        "scrape_error": None,
    }

    from concurrent.futures import ThreadPoolExecutor

    errors = []
    resolved_name = game_name
    resolved_tag = tag_line
    tier_data = {}

    # Step 1: Fetch tier from multisearch (must go first — resolves real name/tag)
    try:
        tier_data = scrape_tier_from_multisearch(game_name, tag_line)
        stats["tier"] = tier_data["tier"]
        stats["division"] = tier_data["division"]
        stats["lp"] = tier_data["lp"]
        if tier_data.get("resolved_name"):
            resolved_name = tier_data["resolved_name"]
        if tier_data.get("resolved_tag"):
            resolved_tag = tier_data["resolved_tag"]
    except ScrapeError as e:
        errors.append(f"Tier fetch failed: {e}")

    # Build the op.gg URL using resolved name/tag
    slug = f"{resolved_name}-{resolved_tag}"
    stats["opgg_url"] = f"https://op.gg/lol/summoners/na/{quote(slug)}"

    # Trigger op.gg profile renewal so we get fresh data
    puuid = tier_data.get("puuid") if tier_data else None
    if puuid:
        trigger_opgg_renewal(slug, puuid)

    # Step 2: Fetch role data (u.gg), season history, and masteries in parallel
    # Then fetch champion stats with the role map
    with ThreadPoolExecutor(max_workers=4) as pool:
        roles_future = pool.submit(scrape_champion_roles_ugg, resolved_name, resolved_tag)
        history_future = pool.submit(scrape_season_history, resolved_name, resolved_tag)
        mastery_future = pool.submit(scrape_masteries, resolved_name, resolved_tag)

        # Get role map (non-critical — fallback to static data if fails)
        champion_role_map = {}
        try:
            champion_role_map = roles_future.result(timeout=45)
        except Exception:
            pass  # will fall back to Meraki static data

        # Now fetch champions with the role map
        champs_future = pool.submit(
            scrape_champions, resolved_name, resolved_tag, champion_role_map
        )

        try:
            history = history_future.result(timeout=30)
            stats["previous_season_tier"] = history.get("previous_season_tier")
            stats["peak_tier"] = history.get("peak_tier")
            stats["season_history"] = history.get("season_history", [])
        except Exception as e:
            errors.append(f"Season history failed: {e}")

        try:
            champ_data = champs_future.result(timeout=30)
            stats["season_games"] = champ_data["season_games"]
            stats["season_wins"] = champ_data["season_wins"]
            stats["season_losses"] = champ_data["season_losses"]
            stats["season_winrate"] = champ_data["season_winrate"]
            stats["champions"] = champ_data["champions"]
        except Exception as e:
            errors.append(f"Champions fetch failed: {e}")

        try:
            stats["masteries"] = mastery_future.result(timeout=30)
        except Exception as e:
            errors.append(f"Masteries failed: {e}")

    if errors:
        stats["scrape_error"] = "; ".join(errors)

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


def get_champion_roles() -> dict:
    """
    Fetch champion -> role mapping from Meraki Analytics (cached for 24h).
    Returns dict like {"Vex": ["MIDDLE"], "Jinx": ["BOTTOM"], ...}
    Keys are champion names (as used in op.gg data).
    """
    now = time.time()
    if (
        _champion_roles_cache["data"]
        and now - _champion_roles_cache["fetched_at"] < 86400
    ):
        return _champion_roles_cache["data"]
    try:
        resp = requests.get(
            "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions.json",
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
        roles = {}
        for key, champ in raw.items():
            name = champ.get("name", key)
            positions = champ.get("positions", [])
            roles[name] = positions
            # Also map by key for fallback
            roles[key] = positions
        _champion_roles_cache["data"] = roles
        _champion_roles_cache["fetched_at"] = now
        return roles
    except Exception:
        return _champion_roles_cache["data"] or {}


ROLE_SHORT = {
    "TOP": "Top",
    "JUNGLE": "Jgl",
    "MIDDLE": "Mid",
    "BOTTOM": "Bot",
    "SUPPORT": "Sup",
}


def _get_champion_key_map() -> dict:
    """Build a mapping from lowercase keys to correct ddragon filenames (cached 24h)."""
    now = time.time()
    if _champion_keys_cache["data"] and now - _champion_keys_cache["fetched_at"] < 86400:
        return _champion_keys_cache["data"]
    try:
        version = get_ddragon_version()
        resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        mapping = {}
        for key in data:
            # key is the correct filename (e.g. "DrMundo", "AurelionSol", "LeeSin")
            mapping[key.lower()] = key
        _champion_keys_cache["data"] = mapping
        _champion_keys_cache["fetched_at"] = now
        return mapping
    except Exception:
        return _champion_keys_cache["data"] or {}


def champion_icon_url(champion_key: str) -> str:
    """Get Data Dragon CDN URL for a champion icon."""
    version = get_ddragon_version()
    # Map lowercase op.gg key to correct ddragon filename
    key_map = _get_champion_key_map()
    correct_key = key_map.get(champion_key.lower(), "")
    if not correct_key:
        # Fallback: capitalize first letter
        correct_key = champion_key[0].upper() + champion_key[1:] if champion_key else "Unknown"
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{correct_key}.png"
