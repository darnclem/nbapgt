#!/usr/bin/env python3
"""Produces a reddit markdown formatted scoreblock from an NBA.com gameid (can also use the full web address of the game)

Jupyter Notebook:
    from nba_reddit_thread import generate_thread
    title, body = generate_thread("0022600003")
    print(title)
    print(body)

The script prefers ``curl_cffi`` because the NBA CDN may reject ordinary Python
HTTP clients. It falls back to ``requests`` and Python's standard HTTP client.

It does not submit anything to Reddit; it returns or prints the title and
Markdown body for you to review and paste.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BOX_SCORE_URL = (
    "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
)
SCHEDULE_URLS = (
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)


class GeneratorError(RuntimeError):
    """An error that can be shown directly to the user."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Reddit-formatted NBA post-game thread."
    )
    parser.add_argument(
        "game",
        help="A 10-digit NBA game ID or any NBA.com game URL containing one.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the result to this file instead of standard output.",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--title-only", action="store_true", help="Output only the Reddit title."
    )
    output_group.add_argument(
        "--body-only", action="store_true", help="Output only the Markdown body."
    )
    parser.add_argument(
        "--allow-nonfinal",
        action="store_false",
        help="Generate output even when the NBA feed does not mark the game final.",
    )
    parser.add_argument(
        "--no-records",
        action="store_true",
        help="Skip the schedule lookup and omit team records.",
    )
    parser.add_argument(
        "--boxscore-json",
        type=Path,
        help="Read a previously downloaded NBA box-score JSON file instead of fetching it.",
    )
    parser.add_argument(
        "--schedule-json",
        type=Path,
        help="Read a previously downloaded NBA schedule JSON file for team records.",
    )
    return parser.parse_args()


def extract_game_id(value: str) -> str:
    match = re.search(r"(?<!\d)(\d{10})(?!\d)", value)
    if not match:
        raise GeneratorError(
            "Could not find a 10-digit NBA game ID. Example: 0022600003"
        )
    return match.group(1)


def fetch_json(url: str) -> dict[str, Any]:
    browser_headers = {
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nba.com/",
    }
    errors: list[str] = []

    # curl_cffi reproduces a browser's TLS and HTTP/2 fingerprint, which is
    # distinct from simply copying browser headers into requests or urllib.
    try:
        from curl_cffi import requests as browser_requests
    except ImportError:
        browser_requests = None

    if browser_requests is not None:
        try:
            response = browser_requests.get(
                url,
                headers=browser_headers,
                impersonate="chrome",
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    raise GeneratorError("The NBA data feed returned unexpected JSON.")
                return data
            errors.append(f"curl_cffi returned HTTP {response.status_code}")
        except GeneratorError:
            raise
        except Exception as exc:
            errors.append(f"curl_cffi failed: {exc}")

    headers = {"User-Agent": USER_AGENT, **browser_headers}

    try:
        import requests
    except ImportError:
        requests = None

    if requests is not None:
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    raise GeneratorError("The NBA data feed returned unexpected JSON.")
                return data
            errors.append(f"requests returned HTTP {response.status_code}")
        except GeneratorError:
            raise
        except Exception as exc:
            errors.append(f"requests failed: {exc}")

    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
            if not isinstance(data, dict):
                raise GeneratorError("The NBA data feed returned unexpected JSON.")
            return data
    except urllib.error.HTTPError as exc:
        errors.append(f"urllib returned HTTP {exc.code}")
    except urllib.error.URLError as exc:
        errors.append(f"urllib failed: {exc.reason}")
    except json.JSONDecodeError as exc:
        raise GeneratorError("The NBA data feed returned invalid JSON.") from exc

    details = "; ".join(errors)
    install_hint = " Run `%pip install -U curl-cffi` in Jupyter and restart the kernel."
    manual_hint = (
        " If that still fails, open the URL in your browser, save the JSON file, "
        "and pass it with `boxscore_json=...`."
    )
    raise GeneratorError(
        f"The NBA data feed blocked every available HTTP client for {url}. "
        f"{details}.{install_hint}{manual_hint}"
    )


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except OSError as exc:
        raise GeneratorError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GeneratorError(f"{path} is not valid JSON.") from exc


def find_schedule_game(schedule: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    league_schedule = schedule.get("leagueSchedule", schedule)
    for date_group in league_schedule.get("gameDates", []):
        for game in date_group.get("games", []):
            if str(game.get("gameId")) == game_id:
                return game
    return None


def fetch_schedule_game(game_id: str) -> dict[str, Any] | None:
    last_error: GeneratorError | None = None
    for url in SCHEDULE_URLS:
        try:
            match = find_schedule_game(fetch_json(url), game_id)
            if match:
                return match
        except GeneratorError as exc:
            last_error = exc
    if last_error:
        print(
            f"Warning: team records could not be loaded ({last_error}).",
            file=sys.stderr,
        )
    else:
        print(
            "Warning: game was not found in the current NBA schedule; "
            "team records will be omitted.",
            file=sys.stderr,
        )
    return None


def full_team_name(team: dict[str, Any]) -> str:
    return " ".join(
        part.strip()
        for part in (str(team.get("teamCity", "")), str(team.get("teamName", "")))
        if part and part.strip()
    )


def record_for(schedule_team: dict[str, Any] | None) -> str | None:
    if not schedule_team:
        return None
    wins = schedule_team.get("wins", schedule_team.get("teamWins"))
    losses = schedule_team.get("losses", schedule_team.get("teamLosses"))
    if wins is None or losses is None:
        return None
    return f"{wins}-{losses}"


def display_team(team: dict[str, Any], record: str | None) -> str:
    name = full_team_name(team)
    return f"{name} ({record})" if record else name


def markdown_text(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def number(value: Any, default: str = "0") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def made_attempted(stats: dict[str, Any], prefix: str) -> str:
    return f"{number(stats.get(prefix + 'Made'))}-{number(stats.get(prefix + 'Attempted'))}"


def minutes_text(value: Any) -> str:
    if value is None:
        return "0:00"
    text = str(value)
    iso = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text)
    if iso:
        hours = int(iso.group(1) or 0)
        minutes = int(iso.group(2) or 0) + hours * 60
        seconds = int(float(iso.group(3) or 0))
        return f"{minutes:02d}:{seconds:02d}"
    clock = re.fullmatch(r"(\d+):(\d+(?:\.\d+)?)", text)
    if clock:
        return f"{int(clock.group(1)):02d}:{int(float(clock.group(2))):02d}"
    return text


def plus_minus(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    shown = str(int(numeric)) if numeric.is_integer() else str(numeric)
    return shown


def is_played(player: dict[str, Any]) -> bool:
    played = player.get("played")
    return played in (True, 1, "1", "true", "True")


def player_name(player: dict[str, Any]) -> str:
    name = player.get("name") or player.get("nameI")
    if name:
        return markdown_text(name)
    return markdown_text(
        " ".join(
            part
            for part in (
                str(player.get("firstName", "")).strip(),
                str(player.get("familyName", "")).strip(),
            )
            if part
        )
    )


def player_row(player: dict[str, Any]) -> str:
    stats = player.get("statistics") or {}
    name = player_name(player)
    position = str(player.get("position") or "").strip()
    if player.get("starter") in (True, 1, "1") and position:
        name = f"{name}^{markdown_text(position)}"

    values = [
        name,
        minutes_text(stats.get("minutes") or stats.get("minutesCalculated") or "0:00"),
        number(stats.get("points")),
        made_attempted(stats, "fieldGoals"),
        made_attempted(stats, "threePointers"),
        made_attempted(stats, "freeThrows"),
        number(stats.get("reboundsOffensive")),
        number(stats.get("reboundsDefensive")),
        number(stats.get("reboundsTotal")),
        number(stats.get("assists")),
        number(stats.get("steals")),
        number(stats.get("blocks")),
        number(stats.get("turnovers")),
        number(stats.get("foulsPersonal")),
        plus_minus(stats.get("plusMinusPoints")),
    ]
    return "|" + "|".join(values) + "|"


def player_team_header(team: dict[str, Any]) -> str:
    tricode = str(team.get("teamTricode", "")).upper()
    return (
        f"|**[](/{tricode}) {full_team_name(team)}**|**MIN**|**PTS**|**FGM-A**|"
        "**3PM-A**|**FTM-A**|**ORB**|**DRB**|**REB**|**AST**|**STL**|"
        "**BLK**|**TO**|**PF**|**±**|"
    )


def combined_player_table(away: dict[str, Any], home: dict[str, Any]) -> str:
    lines = [
        player_team_header(away),
        "|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|",
    ]
    for index, team in enumerate((away, home)):
        if index:
            lines.append(player_team_header(team))
        players = sorted(team.get("players", []), key=lambda p: p.get("order", 999))
        lines.extend(player_row(player) for player in players)
    return "\n".join(lines)


def percentage(value: Any) -> str:
    if value in (None, ""):
        return "0.0%"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(numeric) <= 1:
        numeric *= 100
    return f"{numeric:.1f}%"


def team_stats_row(team: dict[str, Any]) -> str:
    stats = team.get("statistics") or {}
    values = [
        full_team_name(team),
        number(stats.get("points", team.get("score"))),
        made_attempted(stats, "fieldGoals"),
        percentage(stats.get("fieldGoalsPercentage")),
        made_attempted(stats, "threePointers"),
        percentage(stats.get("threePointersPercentage")),
        made_attempted(stats, "freeThrows"),
        percentage(stats.get("freeThrowsPercentage")),
        number(stats.get("reboundsOffensive")),
        number(stats.get("reboundsTotal")),
        number(stats.get("assists")),
        number(stats.get("foulsPersonal")),
        number(stats.get("steals")),
        number(stats.get("turnoversTotal", stats.get("turnovers"))),
        number(stats.get("blocks")),
    ]
    return "|" + "|".join(values) + "|"


def team_stats_table(away: dict[str, Any], home: dict[str, Any]) -> str:
    return "\n".join(
        [
            "|**Team**|**PTS**|**FG**|**FG%**|**3P**|**3P%**|**FT**|**FT%**|**OREB**|**TREB**|**AST**|**PF**|**STL**|**TO**|**BLK**|",
            "|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|",
            team_stats_row(away),
            team_stats_row(home),
        ]
    )


def period_scores(team: dict[str, Any]) -> dict[int, str]:
    return {
        int(period.get("period", 0)): number(period.get("score"))
        for period in team.get("periods", [])
        if period.get("period") is not None
    }


def score_table(away: dict[str, Any], home: dict[str, Any]) -> str:
    away_periods = period_scores(away)
    home_periods = period_scores(home)
    highest_period = max([4, *away_periods.keys(), *home_periods.keys()])
    period_numbers = list(range(1, highest_period + 1))
    labels = [f"Q{period}" if period <= 4 else f"OT{period - 4}" for period in period_numbers]

    header = "|**Team**|" + "|".join(f"**{label}**" for label in labels) + "|**Total**|"
    alignment = "|:---|" + "|".join(":---" for _ in labels) + "|:---|"

    def row(team: dict[str, Any], scores: dict[int, str]) -> str:
        cells = [full_team_name(team)]
        cells.extend(scores.get(period, "-") for period in period_numbers)
        cells.append(number(team.get("score")))
        return "|" + "|".join(cells) + "|"

    return "\n".join(
        [header, alignment, row(away, away_periods), row(home, home_periods)]
    )


def game_link(game_id: str, away: dict[str, Any], home: dict[str, Any]) -> str:
    away_code = str(away.get("teamTricode", "away")).upper()
    home_code = str(home.get("teamTricode", "home")).upper()
    return f"https://www.nba.com/game/{away_code}-vs-{home_code}-{game_id}/box-score"


def make_title(
    away: dict[str, Any],
    home: dict[str, Any],
    away_record: str | None,
    home_record: str | None,
) -> str:
    away_score = int(away.get("score", 0))
    home_score = int(home.get("score", 0))
    if away_score == home_score:
        return (
            f"[Post Game Thread] The {display_team(away, away_record)} and "
            f"{display_team(home, home_record)} finish tied, {away_score}-{home_score}"
        )
    winner, loser = (away, home) if away_score > home_score else (home, away)
    winner_record = away_record if winner is away else home_record
    loser_record = home_record if loser is home else away_record
    return (
        f"[Post Game Thread] The {display_team(winner, winner_record)} defeat the "
        f"{display_team(loser, loser_record)}, {winner.get('score')}-{loser.get('score')}"
    )


def comma_list(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def make_body(
    game_id: str,
    game: dict[str, Any],
) -> str:
    away = game["awayTeam"]
    home = game["homeTeam"]
    arena = game.get("arena") or {}
    venue = str(arena.get("arenaName", "")).strip()
    away_code = str(away.get("teamTricode", "")).upper()
    home_code = str(home.get("teamTricode", "")).upper()
    nba_url = game_link(game_id, away, home)
    officials = [
        str(official.get("name"))
        for official in game.get("officials", [])
        if official.get("name")
    ]
    lines = [
        f"|[](/{away_code}) **{away.get('score')} -  {home.get('score')}** [](/{home_code})|",
        "|:---:|",
        f"|**Box Scores: [NBA]({nba_url}) - [Yahoo](https://sports.yahoo.com/nba/scoreboard/)**|",
        "",
        "|**GAME SUMMARY**|",
        "|:---:|",
    ]
    if venue:
        lines.append(f"|**Location:** {markdown_text(venue)}|")
    if officials:
        lines.append(f"|**Officials:** {markdown_text(comma_list(officials))}|")

    lines.extend(
        [
            "",
            score_table(away, home),
            "",
            "|**TEAM STATS**|",
            "|:---:|",
            "|&nbsp;|",
            "",
            team_stats_table(away, home),
            "",
            "",
            "|**PLAYER STATS**|",
            "|:---:|",
            "|&nbsp;|",
            "",
            combined_player_table(away, home),
        ]
    )
    return "\n".join(lines)


def generate(args: argparse.Namespace) -> tuple[str, str]:
    game_id = extract_game_id(args.game)
    boxscore_data = (
        load_json_file(args.boxscore_json)
        if args.boxscore_json
        else fetch_json(BOX_SCORE_URL.format(game_id=game_id))
    )
    game = boxscore_data.get("game")
    if not isinstance(game, dict):
        raise GeneratorError("The box-score response does not contain a game object.")
    if str(game.get("gameId")) != game_id:
        raise GeneratorError("The returned box score does not match the requested game ID.")
    if int(game.get("gameStatus", 0)) != 3 and not args.allow_nonfinal:
        status = game.get("gameStatusText") or "not final"
        raise GeneratorError(
            f"This game is {status!r}, not final. Use --allow-nonfinal to override."
        )

    schedule_game = None
    if not args.no_records:
        if args.schedule_json:
            schedule_game = find_schedule_game(load_json_file(args.schedule_json), game_id)
            if not schedule_game:
                print(
                    f"Warning: {game_id} was not found in {args.schedule_json}; "
                    "team records will be omitted.",
                    file=sys.stderr,
                )
        else:
            schedule_game = fetch_schedule_game(game_id)

    away_record = record_for(schedule_game.get("awayTeam") if schedule_game else None)
    home_record = record_for(schedule_game.get("homeTeam") if schedule_game else None)
    away = game.get("awayTeam")
    home = game.get("homeTeam")
    if not isinstance(away, dict) or not isinstance(home, dict):
        raise GeneratorError("The box score is missing home or away team data.")

    return (
        make_title(away, home, away_record, home_record),
        make_body(game_id, game),
    )


def generate_thread(
    game: str,
    *,
    allow_nonfinal: bool = False,
    no_records: bool = False,
    boxscore_json: str | Path | None = None,
    schedule_json: str | Path | None = None,
) -> tuple[str, str]:
    """Return ``(title, body)`` for convenient use in a Jupyter Notebook.

    ``game`` may be a 10-digit NBA game ID or an NBA.com game URL. The two
    optional JSON arguments are useful if the NBA CDN blocks a direct request:
    download the corresponding JSON in a browser, then pass its local path.
    """
    args = argparse.Namespace(
        game=str(game),
        allow_nonfinal=allow_nonfinal,
        no_records=no_records,
        boxscore_json=Path(boxscore_json) if boxscore_json else None,
        schedule_json=Path(schedule_json) if schedule_json else None,
    )
    return generate(args)


def main() -> int:
    args = parse_args()
    try:
        title, body = generate(args)
        if args.title_only:
            result = title
        elif args.body_only:
            result = body
        else:
            result = f"TITLE\n{title}\n\nBODY\n{body}"

        if args.output:
            args.output.write_text(result + "\n", encoding="utf-8")
            print(f"Wrote {args.output}", file=sys.stderr)
        else:
            print(result)
        return 0
    except (GeneratorError, KeyError, TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
