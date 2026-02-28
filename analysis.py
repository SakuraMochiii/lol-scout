"""Ban and pick recommendation engine."""

from collections import defaultdict

TIER_WEIGHTS = {
    "CHALLENGER": 2.0,
    "GRANDMASTER": 1.8,
    "MASTER": 1.6,
    "DIAMOND": 1.3,
    "EMERALD": 1.1,
    "PLATINUM": 1.0,
    "GOLD": 0.9,
    "SILVER": 0.8,
    "BRONZE": 0.7,
    "IRON": 0.6,
    "UNRANKED": 0.8,
}

ROLES = ["top", "jungle", "mid", "bot", "support"]


def get_ban_recommendations(opponent_team: dict, num_bans: int = 5) -> list[dict]:
    """
    Analyze opponent team and return scored ban recommendations.

    Priority: high-ranked players' most played champions.
    Ranked games are the primary signal, mastery is a bonus but only
    for champions matching the player's assigned tournament role.
    """
    import math

    champ_scores = defaultdict(lambda: {
        "score": 0.0,
        "reasons": [],
        "players": [],
        "champion_key": "",
        "champion_id": None,
    })

    # Role name normalization for matching
    role_aliases = {
        "top": {"top", "Top"},
        "jungle": {"jgl", "Jgl", "jungle", "Jungle"},
        "mid": {"mid", "Mid", "middle", "Middle"},
        "bot": {"bot", "Bot", "adc", "ADC", "bottom", "Bottom"},
        "support": {"sup", "Sup", "support", "Support"},
    }

    def champ_matches_role(champ_role_str, player_role):
        """Check if a champion's role matches the player's assigned role."""
        if not champ_role_str or not player_role or player_role == "fill":
            return True  # no role info or fill = always matches
        aliases = role_aliases.get(player_role, {player_role})
        for part in champ_role_str.split("/"):
            if part.strip() in aliases:
                return True
        return False

    for player in opponent_team.get("players", []):
        stats = player.get("stats")
        if not stats:
            continue

        tier = stats.get("tier", "UNRANKED")
        tier_weight = TIER_WEIGHTS.get(tier, 1.0)
        pname = player["game_name"]
        player_role = player.get("role", "fill")

        # Build mastery lookup: champion_name → {level, points, ...}
        mastery_map = {}
        for m in stats.get("masteries", []):
            mastery_map[m["champion_name"]] = {
                "level": m.get("level", 0),
                "points": m.get("points", 0),
                "champion_key": m.get("champion_key", ""),
            }

        # Detect OTP/Main
        sorted_champs = sorted(stats.get("champions", []), key=lambda c: -c.get("games", 0))
        otp_name = None
        main_name = None
        if len(sorted_champs) >= 2:
            top_games = sorted_champs[0].get("games", 0)
            second_games = sorted_champs[1].get("games", 0)
            ratio_needed = max(2.0 - (top_games - 10) * 0.0125, 1.5)
            if top_games >= 20 and second_games <= 2:
                otp_name = sorted_champs[0]["champion_name"]
            elif top_games >= 10 and second_games > 0 and top_games / second_games >= ratio_needed:
                main_name = sorted_champs[0]["champion_name"]
        elif len(sorted_champs) == 1 and sorted_champs[0].get("games", 0) >= 10:
            otp_name = sorted_champs[0]["champion_name"]

        # Primary: score by ranked games × rank weight
        for i, champ in enumerate(stats.get("champions", [])[:10]):
            key = champ["champion_name"]
            games = champ.get("games", 0)
            if games < 3:
                continue

            entry = champ_scores[key]
            entry["champion_key"] = champ.get("champion_key", key)
            entry["champion_id"] = champ.get("champion_id")

            # Ranked games score
            score = games * tier_weight
            if i == 0:
                score *= 1.5
            elif i == 1:
                score *= 1.2

            reasons = [f"{pname} ({tier.capitalize()}, {games}g)"]

            # Mastery bonus: only if champion fits the player's tournament role
            champ_role = champ.get("role", "")
            m_data = mastery_map.get(key)
            if m_data and m_data["points"] >= 10000 and champ_matches_role(champ_role, player_role):
                mastery_bonus = math.log10(max(m_data["points"], 1)) * 5 * tier_weight
                score += mastery_bonus
                reasons.append(f"Mastery Lvl {m_data['level']}, {m_data['points']:,} pts")

            # Small OTP/Main bonus
            if key == otp_name:
                score *= 1.15
                reasons.append("OTP")
            elif key == main_name:
                score *= 1.1
                reasons.append("Main")

            entry["score"] += score
            entry["reasons"].extend(reasons)
            entry["players"].append(pname)

    results = []
    for name, data in champ_scores.items():
        results.append({
            "champion_name": name,
            "champion_key": data["champion_key"],
            "champion_id": data["champion_id"],
            "score": round(data["score"], 1),
            "reasons": data["reasons"],
            "players": list(set(data["players"])),
        })

    results.sort(key=lambda x: -x["score"])
    return results[:num_bans * 2]  # Return extra for context


def get_pick_recommendations(my_team: dict, opponent_team: dict) -> dict:
    """
    Per role, suggest picks from my team's champion pools.

    Scores each champion by:
    - Winrate (base score)
    - Games experience
    - KDA bonus
    Filters out top ban recommendations.
    """
    ban_recs = get_ban_recommendations(opponent_team, 5)
    likely_bans = {b["champion_name"] for b in ban_recs[:5]}

    picks_by_role = {}

    for player in my_team.get("players", []):
        stats = player.get("stats")
        if not stats or not stats.get("champions"):
            continue

        role = player.get("role", "fill")
        if role == "fill":
            continue

        picks = []
        for champ in stats["champions"]:
            if champ["games"] < 3:
                continue

            name = champ["champion_name"]
            score = champ.get("winrate", 50)
            score += min(champ.get("kda", 0) * 3, 15)
            score += min(champ["games"] / 5, 15)

            banned = name in likely_bans
            if banned:
                score *= 0.3  # Heavily penalize likely bans

            picks.append({
                "champion_name": name,
                "champion_key": champ.get("champion_key", name),
                "champion_id": champ.get("champion_id"),
                "winrate": champ.get("winrate", 0),
                "games": champ["games"],
                "kda": champ.get("kda", 0),
                "score": round(score, 1),
                "likely_banned": banned,
                "player": player["game_name"],
            })

        picks.sort(key=lambda x: -x["score"])
        picks_by_role[role] = picks[:5]

    return picks_by_role


def identify_one_tricks(team: dict) -> list[dict]:
    """
    Find players with extremely skewed champion pools.
    OTP: #1 has 20+ games, #2 has <=2 games
    Main: #1 has 10+ games, #2 has <=2 games
    """
    results = []
    for player in team.get("players", []):
        stats = player.get("stats")
        if not stats or not stats.get("champions"):
            continue

        sorted_champs = sorted(stats["champions"], key=lambda c: -c.get("games", 0))
        if not sorted_champs:
            continue

        top = sorted_champs[0]
        top_games = top.get("games", 0)
        second_games = sorted_champs[1].get("games", 0) if len(sorted_champs) >= 2 else 0
        total = stats.get("season_games") or sum(c["games"] for c in stats["champions"])

        ratio_needed = max(2.0 - (top_games - 10) * 0.0125, 1.5)
        if top_games >= 20 and second_games <= 2:
            tag = "OTP"
        elif top_games >= 10 and second_games > 0 and top_games / second_games >= ratio_needed:
            tag = "MAIN"
        else:
            continue

        results.append({
            "player": player["game_name"],
            "role": player.get("role", "?"),
            "champion": top["champion_name"],
            "champion_key": top.get("champion_key", ""),
            "games": top_games,
            "pct": round(top_games / max(total, 1) * 100, 1),
            "winrate": top.get("winrate", 0),
            "tag": tag,
        })
    return results
