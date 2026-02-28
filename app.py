"""Flask app for LoL Tournament Scout."""

import time
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

import analysis
import scraper
import storage

app = Flask(__name__)


# --- Page routes ---


@app.route("/")
def index():
    data = storage.load()
    return render_template("index.html", data=data)


@app.route("/team/<team_id>")
def team_detail(team_id):
    data = storage.load()
    team = storage.get_team(data, team_id)
    if not team:
        return redirect(url_for("index"))
    is_my_team = data["meta"]["my_team_id"] == team_id
    return render_template("team.html", data=data, team=team, is_my_team=is_my_team)


@app.route("/analysis/<opp_id>")
def analysis_page(opp_id):
    data = storage.load()
    my_team_id = data["meta"]["my_team_id"]
    if not my_team_id:
        return redirect(url_for("index"))
    my_team = storage.get_team(data, my_team_id)
    opp_team = storage.get_team(data, opp_id)
    if not my_team or not opp_team:
        return redirect(url_for("index"))

    ban_recs = analysis.get_ban_recommendations(opp_team)
    pick_recs = analysis.get_pick_recommendations(my_team, opp_team)
    one_tricks = analysis.identify_one_tricks(opp_team)

    return render_template(
        "analysis.html",
        data=data,
        my_team=my_team,
        opp_team=opp_team,
        ban_recs=ban_recs,
        pick_recs=pick_recs,
        one_tricks=one_tricks,
    )


@app.route("/manage")
def manage():
    data = storage.load()
    return render_template("manage.html", data=data)


# --- API routes ---


@app.route("/api/teams", methods=["POST"])
def api_create_team():
    data = storage.load()
    name = request.json.get("name", "New Team").strip()
    if not name:
        return jsonify({"error": "Team name required"}), 400
    team = storage.add_team(data, name)
    return jsonify({"success": True, "team": team})


@app.route("/api/teams/<team_id>", methods=["PUT"])
def api_update_team(team_id):
    data = storage.load()
    team = storage.get_team(data, team_id)
    if not team:
        return jsonify({"error": "Team not found"}), 404

    body = request.json
    if "name" in body:
        team["name"] = body["name"].strip()
    if body.get("set_my_team"):
        data["meta"]["my_team_id"] = team_id
    if "season_name" in body:
        data["meta"]["season_name"] = body["season_name"].strip()

    storage.save(data)
    return jsonify({"success": True})


@app.route("/api/teams/<team_id>", methods=["DELETE"])
def api_delete_team(team_id):
    data = storage.load()
    if storage.delete_team(data, team_id):
        return jsonify({"success": True})
    return jsonify({"error": "Team not found"}), 404


@app.route("/api/players", methods=["POST"])
def api_create_player():
    data = storage.load()
    body = request.json
    team_id = body.get("team_id")
    if not team_id:
        return jsonify({"error": "team_id required"}), 400

    # Support op.gg link or individual name
    player_input = body.get("player_input", "").strip()
    role = body.get("role", "fill")
    is_sub = body.get("is_substitute", False)

    if not player_input:
        return jsonify({"error": "Player input required"}), 400

    parsed = scraper.parse_player_input(player_input)
    added = []
    for game_name, tag_line in parsed:
        player = storage.add_player(data, team_id, game_name, tag_line, role, is_sub)
        if player:
            added.append(player)

    if not added:
        return jsonify({"error": "Could not add players"}), 400
    return jsonify({"success": True, "players": added})


@app.route("/api/players/<player_id>", methods=["PUT"])
def api_update_player(player_id):
    data = storage.load()
    body = request.json
    updates = {}
    for key in ("game_name", "tag_line", "role", "is_substitute"):
        if key in body:
            updates[key] = body[key]

    # Manual stats override
    if "manual_stats" in body:
        ms = body["manual_stats"]
        stats = {
            "last_updated": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "tier": ms.get("tier", "UNRANKED"),
            "division": ms.get("division"),
            "lp": ms.get("lp", 0),
            "season_games": ms.get("season_games", 0),
            "season_wins": ms.get("season_wins", 0),
            "season_losses": ms.get("season_losses", 0),
            "season_winrate": 0,
            "champions": ms.get("champions", []),
            "scrape_error": None,
            "manual_override": True,
        }
        if stats["season_games"] > 0:
            stats["season_winrate"] = round(
                stats["season_wins"] / stats["season_games"] * 100, 1
            )
        updates["stats"] = stats

    player = storage.update_player(data, player_id, **updates)
    if not player:
        return jsonify({"error": "Player not found"}), 404
    return jsonify({"success": True, "player": player})


@app.route("/api/players/<player_id>", methods=["DELETE"])
def api_delete_player(player_id):
    data = storage.load()
    if storage.delete_player(data, player_id):
        return jsonify({"success": True})
    return jsonify({"error": "Player not found"}), 404


@app.route("/api/players/<player_id>/refresh", methods=["POST"])
def api_refresh_player(player_id):
    data = storage.load()
    _, player = storage.get_player(data, player_id)
    if not player:
        return jsonify({"error": "Player not found"}), 404

    try:
        stats = scraper.scrape_player(player["game_name"], player["tag_line"])
        storage.update_player(data, player_id, stats=stats)
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/teams/<team_id>/refresh", methods=["POST"])
def api_refresh_team(team_id):
    data = storage.load()
    team = storage.get_team(data, team_id)
    if not team:
        return jsonify({"error": "Team not found"}), 404

    results = []
    for player in team["players"]:
        try:
            stats = scraper.scrape_player(player["game_name"], player["tag_line"])
            storage.update_player(data, player["id"], stats=stats)
            results.append({"player": player["game_name"], "success": True})
        except Exception as e:
            results.append({"player": player["game_name"], "success": False, "error": str(e)})
        time.sleep(0.5)  # Brief delay between players

    return jsonify({"success": True, "results": results})


@app.route("/api/import/multi-link", methods=["POST"])
def api_import_multi():
    url = request.json.get("url", "")
    try:
        players = scraper.parse_opgg_multi_link(url)
        return jsonify({
            "success": True,
            "players": [{"game_name": g, "tag_line": t} for g, t in players],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.template_filter("champion_icon")
def champion_icon_filter(champion_key):
    return scraper.champion_icon_url(champion_key)


@app.template_filter("tier_color")
def tier_color_filter(tier):
    colors = {
        "CHALLENGER": "#f4c874",
        "GRANDMASTER": "#ef4444",
        "MASTER": "#a855f7",
        "DIAMOND": "#60a5fa",
        "EMERALD": "#34d399",
        "PLATINUM": "#5eead4",
        "GOLD": "#fbbf24",
        "SILVER": "#94a3b8",
        "BRONZE": "#d97706",
        "IRON": "#78716c",
        "UNRANKED": "#6b7280",
    }
    return colors.get(tier, "#6b7280")


@app.template_filter("tier_display")
def tier_display_filter(player):
    stats = player.get("stats")
    if not stats or stats.get("tier") == "UNRANKED":
        return "Unranked"
    tier = stats["tier"].capitalize()
    div = stats.get("division", "")
    lp = stats.get("lp", 0)
    roman = {1: "I", 2: "II", 3: "III", 4: "IV"}.get(div, "")
    if stats["tier"] in ("CHALLENGER", "GRANDMASTER", "MASTER"):
        return f"{tier} {lp} LP"
    return f"{tier} {roman}" + (f" {lp} LP" if lp else "")


if __name__ == "__main__":
    Path("data").mkdir(exist_ok=True)
    if not storage.DATA_FILE.exists():
        storage.save(storage.default_tournament())
    app.run(debug=True, host="127.0.0.1", port=5000)
