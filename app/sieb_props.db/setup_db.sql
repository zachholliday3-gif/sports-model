-- === SIEB NFL Props Database Schema ===

CREATE TABLE IF NOT EXISTS teams (
    team_id SERIAL PRIMARY KEY,
    espn_team_id TEXT UNIQUE NOT NULL,
    team_abbr TEXT NOT NULL,
    team_name TEXT NOT NULL,
    conference TEXT,
    division TEXT
);

CREATE TABLE IF NOT EXISTS players (
    player_id SERIAL PRIMARY KEY,
    espn_player_id TEXT UNIQUE NOT NULL,
    player_name TEXT NOT NULL,
    position TEXT,
    current_team_id INT REFERENCES teams(team_id),
    active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS games (
    game_id SERIAL PRIMARY KEY,
    espn_game_id TEXT UNIQUE NOT NULL,
    season INT NOT NULL,
    week INT NOT NULL,
    game_date DATE NOT NULL,
    home_team_id INT REFERENCES teams(team_id),
    away_team_id INT REFERENCES teams(team_id),
    home_score INT,
    away_score INT
);

CREATE TABLE IF NOT EXISTS team_game_stats (
    team_game_id SERIAL PRIMARY KEY,
    game_id INT REFERENCES games(game_id),
    team_id INT REFERENCES teams(team_id),
    opponent_team_id INT REFERENCES teams(team_id),
    is_home BOOLEAN NOT NULL,
    plays_offense INT,
    pass_attempts INT,
    rush_attempts INT,
    total_yards INT,
    time_of_poss_sec INT
);

CREATE TABLE IF NOT EXISTS player_game_stats (
    player_game_id SERIAL PRIMARY KEY,
    game_id INT REFERENCES games(game_id),
    player_id INT REFERENCES players(player_id),
    team_id INT REFERENCES teams(team_id),
    opponent_team_id INT REFERENCES teams(team_id),
    is_home BOOLEAN NOT NULL,
    snap_pct NUMERIC,
    offensive_snaps INT,
    targets INT,
    receptions INT,
    rec_yds INT,
    rec_tds INT,
    long_rec INT,
    rush_att INT,
    rush_yds INT,
    rush_tds INT,
    long_rush INT,
    pass_att INT,
    pass_comp INT,
    pass_yds INT,
    pass_tds INT,
    interceptions INT,
    fumbles INT,
    fumbles_lost INT
);

CREATE INDEX IF NOT EXISTS idx_player_game_stats_player_date
ON player_game_stats (player_id, game_id);

CREATE INDEX IF NOT EXISTS idx_games_season_week
ON games (season, week);
