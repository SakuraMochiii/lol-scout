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

    Scoring factors:
    - One-trick detection (40%+ games on one champ)
    - High winrate comfort picks (58%+ WR, 15+ games)
    - Multi-player overlap (same champ played by multiple players)
    - High KDA comfort (4.0+ KDA, 15+ games)
    All weighted by player rank.
    """
    champ_scores = defaultdict(lambda: {
        "score": 0.0,
        "reasons": [],
        "players": [],
        "champion_key": "",
        "champion_id": None,
    })

    for player in opponent_team.get("players", []):
        stats = player.get("stats")
        if not stats or not stats.get("champions"):
            continue

        total_games = stats.get("season_games") or sum(
            c["games"] for c in stats["champions"]
        )
        if total_games == 0:
            continue

        tier = stats.get("tier", "UNRANKED")
        tier_weight = TIER_WEIGHTS.get(tier, 1.0)
        pname = player["game_name"]

        # Determine OTP/Main: based on gap between #1 and #2 champion games
        # OTP: #1 has 20+ games, #2 has <=2 (extremely skewed)
        # Main: #1 has 10+ games, #2 has <=2 (pretty skewed)
        sorted_champs = sorted(stats["champions"], key=lambda c: -c.get("games", 0))
        otp_name = None
        main_name = None
        if len(sorted_champs) >= 2:
            top_games = sorted_champs[0].get("games", 0)
            second_games = sorted_champs[1].get("games", 0)
            if top_games >= 20 and second_games <= 2:
                otp_name = sorted_champs[0]["champion_name"]
            elif top_games >= 10 and second_games <= 2:
                main_name = sorted_champs[0]["champion_name"]
        elif len(sorted_champs) == 1 and sorted_champs[0].get("games", 0) >= 10:
            otp_name = sorted_champs[0]["champion_name"]

        for champ in stats["champions"][:10]:
            key = champ["champion_name"]
            games = champ.get("games", 0)
            winrate = champ.get("winrate", 0)
            kda = champ.get("kda", 0)

            entry = champ_scores[key]
            entry["champion_key"] = champ.get("champion_key", key)
            entry["champion_id"] = champ.get("champion_id")

            # OTP / Main detection for ban scoring
            if key == otp_name:
                second_g = sorted_champs[1].get("games", 0) if len(sorted_champs) > 1 else 0
                score = 50 + (games - second_g) * 0.5
                entry["score"] += score * tier_weight
                entry["reasons"].append(
                    f"One-trick: {pname} ({games}g, next most played {second_g}g, {winrate:.0f}% WR)"
                )
            elif key == main_name:
                second_g = sorted_champs[1].get("games", 0) if len(sorted_champs) > 1 else 0
                score = 30 + (games - second_g) * 0.3
                entry["score"] += score * tier_weight
                entry["reasons"].append(
                    f"Main: {pname} ({games}g, next most played {second_g}g, {winrate:.0f}% WR)"
                )

            # High winrate comfort
            if games >= 15 and winrate >= 58:
                score = (winrate - 50) * 1.2
                entry["score"] += score * tier_weight
                entry["reasons"].append(
                    f"High WR: {pname} ({winrate:.0f}% on {games}g)"
                )

            # High KDA comfort
            if games >= 15 and kda >= 4.0:
                entry["score"] += 10 * tier_weight
                entry["reasons"].append(
                    f"Strong KDA: {pname} ({kda:.1f} KDA on {games}g)"
                )

            # Games volume weight
            if games >= 20:
                entry["score"] += min(games / 20, 2.5) * 3 * tier_weight

            entry["players"].append(pname)

    # Multi-player overlap bonus
    for key, data in champ_scores.items():
        unique = list(set(data["players"]))
        if len(unique) >= 2:
            bonus = (len(unique) - 1) * 20
            data["score"] += bonus
            data["reasons"].append(
                f"Multi-player: played by {', '.join(unique)}"
            )

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

        if top_games >= 20 and second_games <= 2:
            tag = "OTP"
        elif top_games >= 10 and second_games <= 2:
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
