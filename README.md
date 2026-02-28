# LOL Team Analysis Tool

A local web app for scouting League of Legends tournament opponents. Pulls player data from op.gg, u.gg, and leagueofgraphs to give you a complete picture of each team's roster.

## Features

- **Player Data** — current rank, peak rank (from past seasons, not including current), last season rank, full season history
- **Champion Stats** — most played champions with games, winrate, KDA, and the role they were actually played in (from match history)
- **Champion Mastery** — mastery levels and points per champion
- **Team Management** — 8 teams per tournament, support for substitutes, reorder teams, editable player names, overwrite rosters by re-pasting op.gg links
- **OTP / Main Detection** — flags one-tricks (20+ games with almost no other champs) and mains (scaled ratio based on game count)
- **Ban/Pick Analysis** *(WIP)* — recommends bans based on one-tricks, comfort picks, and multi-player overlap; suggests picks based on your team's champion pools
- **Export** — export team data as a shareable self-contained HTML page that anyone can open
- **Auto-Refresh** — triggers op.gg profile renewal before scraping to ensure fresh data from Riot's API

## Data Sources

| Source | Data |
|--------|------|
| [op.gg](https://op.gg) | Current rank, champion stats (games/WR/KDA), mastery, profile renewal |
| [u.gg](https://u.gg) | Per-champion role from match history (GraphQL API) |
| [leagueofgraphs](https://leagueofgraphs.com) | Peak rank and season history (actual peak reached during each season, not just end-of-season) |

No API keys required — all data is scraped from public pages.

## Setup

```bash
pip install flask requests
python app.py
```

Open http://localhost:5000

## Usage

### Adding Teams

1. Go to **Manage**
2. Create a team and paste an op.gg multi link (e.g. `https://op.gg/multisearch/na?summoners=Player1%0APlayer2%0A...`)
3. Players are auto-assigned roles (Top/Jgl/Mid/Bot/Sup) based on link order
4. Check **Overwrite** before pasting to replace an existing roster
5. Set one team as **My Team** for ban/pick analysis

### Refreshing Data

- **Refresh All** per team or **Refresh All Teams** to update everyone
- Runs in the background — you can navigate away
- Each refresh triggers an op.gg profile update first, then scrapes the fresh data
- Data is cached in `data/tournament.json`

### Exporting

- Click **Export** in the nav bar to generate a standalone HTML file
- Share it with teammates — opens in any browser, no server needed

### Input Formats

- Individual: `Username#TAG`
- op.gg multi link: paste the full URL
- Supports names with spaces and special characters

## Rank Display

- **Peak** (color-coded badge) — highest rank achieved in any past season (not including current season), sourced from leagueofgraphs
- **Cur** (italic) — current ranked tier and LP
- **Last** — end-of-season rank from the most recent completed season

## Notes

- Only supports **NA** region currently
- Scraping can occasionally fail if op.gg/u.gg rate limits — just retry
- `data/tournament.json` stores all state — back it up if needed
- Ban/Pick analysis is a work in progress
- Thread-safe storage prevents data corruption during parallel refreshes

## Requirements

- Python 3.8+
- Flask, requests (no other dependencies)
- Internet connection for scraping
