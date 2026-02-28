"""Ban and pick recommendation engine."""

import math
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

ROLE_ALIASES = {
    "top": {"top", "Top"},
    "jungle": {"jgl", "Jgl", "jungle", "Jungle"},
    "mid": {"mid", "Mid", "middle", "Middle"},
    "bot": {"bot", "Bot", "adc", "ADC", "bottom", "Bottom"},
    "support": {"sup", "Sup", "support", "Support"},
}


def champ_matches_role(champ_role_str, player_role):
    """Check if a champion's role matches the player's assigned role."""
    if not champ_role_str or not player_role or player_role == "fill":
        return True
    aliases = ROLE_ALIASES.get(player_role, {player_role})
    for part in champ_role_str.split("/"):
        if part.strip() in aliases:
            return True
    return False


def get_ban_recommendations(opponent_team: dict, num_bans: int = 5) -> list[dict]:
    """
    Analyze opponent team and return scored ban recommendations.

    Priority: high-ranked players' most played champions.
    Ranked games are the primary signal, mastery is a bonus but only
    for champions matching the player's assigned tournament role.
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
            otp_ratio = max(5.0 - (top_games - 10) * 0.05, 3.0)
            main_ratio = max(2.0 - (top_games - 10) * 0.0125, 1.5)
            if top_games >= 20 and second_games > 0 and top_games / second_games >= otp_ratio:
                otp_name = sorted_champs[0]["champion_name"]
            elif top_games >= 10 and second_games > 0 and top_games / second_games >= main_ratio:
                main_name = sorted_champs[0]["champion_name"]
        elif len(sorted_champs) == 1 and sorted_champs[0].get("games", 0) >= 20:
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
    Per role, suggest picks that counter the opponent's mains.

    Scoring:
    - Counter matchup vs opponent's main champion (primary)
    - Mastery/comfort for the pick (weighted higher vs lower-ranked opponents)
    - Bot lane: considers synergy between bot and support picks
    Filters out likely bans.
    """
    from scraper import scrape_counters

    ban_recs = get_ban_recommendations(opponent_team, 5)
    likely_bans = {b["champion_name"] for b in ban_recs[:5]}

    # Build opponent main champions per role
    opp_mains = {}  # role -> {name, key, tier, player}
    for player in opponent_team.get("players", []):
        role = player.get("role", "fill")
        if role == "fill":
            continue
        stats = player.get("stats")
        if not stats or not stats.get("champions"):
            continue
        sorted_champs = sorted(stats["champions"], key=lambda c: -c.get("games", 0))
        if sorted_champs:
            main = sorted_champs[0]
            opp_mains[role] = {
                "name": main["champion_name"],
                "key": main.get("champion_key", ""),
                "tier": stats.get("tier", "UNRANKED"),
                "player": player["game_name"],
            }

    # Fetch counter data for opponent mains
    opp_counters = {}  # role -> {my_champ_name: counter_wr}
    for role, main_info in opp_mains.items():
        counters = scrape_counters(main_info["key"], role)
        opp_counters[role] = counters

    # Get support player's champion pool for bot synergy
    support_champ_names = set()
    for player in my_team.get("players", []):
        if player.get("role") == "support":
            stats = player.get("stats")
            if stats:
                for c in stats.get("champions", [])[:5]:
                    support_champ_names.add(c["champion_name"])
                # Also mastery
                for m in stats.get("masteries", [])[:10]:
                    support_champ_names.add(m["champion_name"])

    picks_by_role = {}

    for player in my_team.get("players", []):
        stats = player.get("stats")
        if not stats:
            continue

        role = player.get("role", "fill")
        if role == "fill":
            continue

        # Opponent info for this role
        opp_main = opp_mains.get(role)
        opp_tier = opp_main["tier"] if opp_main else "UNRANKED"
        opp_tier_weight = TIER_WEIGHTS.get(opp_tier, 1.0)
        counter_data = opp_counters.get(role, {})

        # Build mastery lookup
        mastery_map = {}
        for m in stats.get("masteries", []):
            mastery_map[m["champion_name"]] = m.get("points", 0)

        # Collect candidate champions from ranked — only those matching the player's role
        candidates = {}
        for champ in stats.get("champions", []):
            if champ["games"] < 2:
                continue
            champ_role = champ.get("role", "")
            if champ_role and not champ_matches_role(champ_role, role):
                continue  # skip champions played in a different role
            candidates[champ["champion_name"]] = {
                "champion_key": champ.get("champion_key", ""),
                "champion_id": champ.get("champion_id"),
                "games": champ["games"],
                "mastery": mastery_map.get(champ["champion_name"], 0),
            }

        picks = []
        for name, info in candidates.items():
            score = 0
            reasons = []
            banned = name in likely_bans

            # Counter score: how well does this pick do vs opponent's main?
            if counter_data and name in counter_data:
                # counter_data[name] = opponent main's WR vs this pick
                # e.g. Jayce counter page shows Sett at 46% → Jayce has 46% WR vs Sett
                # So Sett has 54% WR vs Jayce → Sett counters Jayce
                opp_main_wr = counter_data[name]
                our_wr = 100 - opp_main_wr
                counter_advantage = our_wr - 50  # positive = we counter them
                if counter_advantage > 2:
                    score += counter_advantage * 3
                    reasons.append(f"Counters {opp_main['name']} ({our_wr:.0f}% WR into {opp_main['name']})")
                elif counter_advantage < -2:
                    score += counter_advantage * 2
                    reasons.append(f"Weak into {opp_main['name']} ({our_wr:.0f}% WR into {opp_main['name']})")

            # Mastery/comfort score
            # Weight mastery higher vs lower-ranked opponents (comfort pick matters more)
            mastery_pts = info["mastery"]
            if mastery_pts >= 10000:
                # Against lower ranked: mastery matters more
                # opp_tier_weight < 1.0 means lower rank → boost mastery
                mastery_multiplier = max(1.5 - opp_tier_weight * 0.5, 0.5)
                mastery_score = math.log10(mastery_pts) * 5 * mastery_multiplier
                score += mastery_score
                reasons.append(f"Mastery {mastery_pts:,} pts")

            # Ranked experience bonus (small)
            score += min(info["games"] * 0.5, 10)

            # Bot lane synergy: check if any support champs synergize
            if role == "bot" and support_champ_names:
                # Fetch synergy data for this bot pick
                # For now, just note if it's a known pairing
                reasons.append(f"{info['games']}g ranked")

            if banned:
                score *= 0.2
                reasons.append("Likely banned")

            picks.append({
                "champion_name": name,
                "champion_key": info["champion_key"],
                "champion_id": info["champion_id"],
                "games": info["games"],
                "mastery": mastery_pts,
                "score": round(score, 1),
                "likely_banned": banned,
                "player": player["game_name"],
                "reasons": reasons,
                "counters": opp_main["name"] if opp_main and name in counter_data and counter_data[name] < 48 else "",
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

        otp_ratio = max(5.0 - (top_games - 10) * 0.05, 3.0)
        main_ratio = max(2.0 - (top_games - 10) * 0.0125, 1.5)
        if top_games >= 20 and second_games > 0 and top_games / second_games >= otp_ratio:
            tag = "OTP"
        elif top_games >= 20 and second_games == 0:
            tag = "OTP"
        elif top_games >= 10 and second_games > 0 and top_games / second_games >= main_ratio:
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
