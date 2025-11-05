-- app/core/schema.sql

CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY,
  sport TEXT NOT NULL,              -- 'CBB' | 'NFL' | 'NHL' ...
  date_utc TIMESTAMP NULL,
  status TEXT NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  venue TEXT NULL
);

CREATE TABLE IF NOT EXISTS projections (
  id BIGSERIAL PRIMARY KEY,
  game_id TEXT NOT NULL,
  sport TEXT NOT NULL,
  scope TEXT NOT NULL,              -- '1H' | 'FG' | '1P' ...
  proj_total DOUBLE PRECISION,
  proj_spread_home DOUBLE PRECISION,
  win_prob_home DOUBLE PRECISION,
  confidence DOUBLE PRECISION,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proj_game ON projections(game_id);

CREATE TABLE IF NOT EXISTS markets (
  id BIGSERIAL PRIMARY KEY,
  game_id TEXT NOT NULL,
  sport TEXT NOT NULL,
  scope TEXT NOT NULL,              -- aligns with projection scope if applicable
  book TEXT NULL,
  market_total DOUBLE PRECISION,
  market_spread_home DOUBLE PRECISION,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_markets_game ON markets(game_id);

CREATE TABLE IF NOT EXISTS edges (
  id BIGSERIAL PRIMARY KEY,
  game_id TEXT NOT NULL,
  sport TEXT NOT NULL,
  scope TEXT NOT NULL,
  edge_total DOUBLE PRECISION,
  edge_spread_home DOUBLE PRECISION,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_edges_game ON edges(game_id);
