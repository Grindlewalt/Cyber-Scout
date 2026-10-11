# Dashboard data contract (`api/<ev>/dashboard[.<team>].json`)

> **Changes from the first draft (backend agent, 2026-10-11).** These are all additive; no field was removed or renamed.
> - Perspective conventions (UI request): `next_match.prediction.win_prob`, `p_win_auto`, `win_prob_if_auto_won/lost`
>   and `schedule[].win_prob` are from the **perspective team's alliance** view. `prediction.score` is keyed by
>   absolute colour (`red`/`blue`); map Us/Opp with `next_match.alliance`.
> - `schedule[].analyze_url` is the full hash `"#/<ev>/a/<match_key>"` (null for unplayed matches).
>   `preview_url` is relative to `api/<ev>/`. In snapshots (`dash/<ts>.json`) every `preview_url` is **null**, because
>   the preview files on disk reflect current data, not that asof.
> - `our_team.levers[]` gained `unit` (`"s"`, `"frac"`, `"balls/s"`, `"balls"`, `"fuel"`), `lower_better`, `desc`,
>   `gap`, `gap_z`, `gap_pts`, `gap_pts_median`, `percentile`, `n_teams`, `n_eff`, `basis`. `accuracy`, `on_move_rate`
>   and `climb` are fractions 0–1. `gap_rank` ranks by `gap_pts` (rough points per match gained by matching the event
>   best) where it can be computed, then by `gap_z`. `gap_rank` is null when `ours` is null (no trusted video).
> - `ratings[].blend.sd` is 1 standard deviation. `projection.p_top8`/`p_top4`/`p_first` are 0–1.
>   `projection.rp_mean` = expected **final total** RP; `rs_mean` = expected final ranking score (RP/match).
> - `model.backtest.brier.sim_full` is now the dashboard model (EPA prior + TBA + video). The phase-1 model without
>   the EPA prior is `sim_full_no_prior`.
> - Snapshots: `event.asof == ts` exactly; `dash/index.json` = `[{"ts","label","quals_played"}]` sorted ascending.
> - `dashboard.json` is the DASH_TEAM (frc4911) perspective; `dashboard.<team>.json` is written for that team too, and
>   for others only with `--teams`.

Built by `live/strategy/dashboard.py` at site-build time (and on demand for replay). All times are
unix seconds. All fuel/points are expected values, with `lo`/`hi` = 10th/90th percentiles.
`asof` means only results posted (TBA `post_result_time`) before this time were used, which is replay mode. Video
counts only 10 min after posting. `null` means everything was used.

```jsonc
{
  "schema": "cyber-scout/dashboard@1", "generated": <ts>,
  "event": {"key", "name", "short_name", "start", "end",      // start/end = first/last match time (unix)
            "start_date", "end_date", "timezone",
            "now": <asof or wall time>, "asof": <ts|null>,
            "quals_played": n, "quals_total": n, "playoffs": bool},
  "team": "frc4911",                       // perspective (DASH_TEAM, default frc4911)
  "next_match": null | {                   // next unplayed match for `team` as of `now`
    "key", "label", "short", "time": <TBA time>, "alliance": "red"|"blue",
    "partners": [team...], "opponents": [team...], "teams": {"red": [..3], "blue": [..3]},
    "prediction": {"win_prob",               // OUR alliance wins (ties count 1/2)
                   "score": {"red": {"mean","lo","hi"}, "blue": {...}},
                   "p_win_auto",               // OUR alliance wins auto
                   "win_prob_if_auto_won", "win_prob_if_auto_lost",   // counterfactual: same robots, auto winner forced
                   "margin_mean",              // ours - theirs
                   "schedule_value_fuel": {"mean","sd"}, "n_sims"},
    "plan": {                                // concrete, ranked recommendations with the numbers behind them
      "defend": [{"target", "when": ["shift2","shift4"],   // THEIR most likely active shifts (= our inactive shifts)
                  "when_alt": [...], "p_when": 0..1,
                  "where": null | {"zone", "share", "xy": [x,y] | null},  // xy: field metres, TBA frame (blue wall X=0)
                  "expected_gain_fuel",        // their active-shift FUEL x (1 - rate_retained) x 0.7 engagement
                  "active_fuel", "rate_retained", "basis": "measured"|"prior", "why"}],   // sorted by gain
      "suggested_defender": {"team", "why"},
      "auto": {"our_expected", "their_expected", "p_win_auto", "note",
               "matchups": [{"team", "alliance", "station", "likely_routine": "Auto A"|null, "routine_id", "p",
                             "expected_fuel", "note"}]},           // all 6 robots
      "endgame": [{"team", "alliance", "p_climb": {"L1","L2","L3"}, "p_any", "auto_l1", "n_obs", "expected_pts"}],
      "key_factors": [{"kind","weight","text", "team"?, "alliance"?}]   // = preview key_factors
    },
    "preview_url": "preview/<match>.json" | null     // full live/strategy preview JSON (README.md schema)
  },
  "ratings": [{                            // every team at the event, sorted by current rank
    "team", "number", "nickname", "rank": n|null, "record": "W-L-T", "rp", "matches_played",
    "epa": {"total","auto","teleop","endgame","source": "statbotics"|"local"},   // EPA as of `now`
    "blend": {"total","auto","teleop","endgame","sd"},     // EPA prior updated by event TBA results + video
    "video_weight": 0..1,                    // share of the rating's information that comes from video (0 = none)
    "weights": {"prior", "tba", "video"},    // full information split (sums to 1)
    "prior_source": "epa-local"|"epa-statbotics"|"event-mean", "prior_events": ["2026wabon", ...],
    "video_matches": n, "id_conf": 0..1 | null,
    "projection": {"rank_mean", "rank_p10", "rank_p90", "p_top8", "p_top4", "p_first", "rp_mean", "rs_mean",
                   "rp_now", "matches_played", "matches_total"}
  }],
  "our_team": {                            // `team` vs the event
    "team", "blend": {...}, "projection": {...},
    "levers": [{"name": "cycle_s"|"accuracy"|"fire_rate"|"volley_size"|"auto_fuel"|"teleop_fuel"|"on_move_rate"|"climb",
                "desc", "unit", "lower_better", "ours", "event_best", "event_best_team", "event_median",
                "gap", "gap_z", "gap_pts", "gap_pts_median", "percentile", "n_teams", "n_eff", "basis",
                "gap_rank": n|null}],      // gap_rank 1 = biggest gap to close
    "trend": [{"match", "short", "time", "blend_total", "blend_sd",   // rating right after that match
               "fuel_made": n|null,          // our robot's counted made FUEL (trusted video only)
               "alliance_fuel", "won"}]
  },
  "schedule": [{"key", "label", "short", "comp_level", "time", "played": bool, "alliance", "partners", "opponents",
                "win_prob",                  // OUR win prob; for played matches the genuine pre-match prediction
                "result": null|{"red","blue","won": bool,"tie": bool,"rp","bonus": {"energized","supercharged","traversal"}},
                "bonus_probs": null|{"win","energized","supercharged","traversal","rp_mean"},   // unplayed quals
                "preview_url", "analyze_url"}],
  "model": {"backtest": {"event", "n", "brier": {"sim_full", "sim_full_no_prior", "epa_pre", "epa", "opr"},
                         "warm": {...}, "early": {...}},
            "epa_source": {"display", "prior", "prior_events", "prior_teams", "statbotics_error"},
            "n_matches_used", "video_matches_used", "sims": {"rank","match"}, "build_s", "notes": [...]}
}
```

Replay: `python -m live.strategy.dashboard DATA_DIR [EVENT] --team frc4911 [--asof TS] [--out FILE]`.
Site: `... --site [--snapshots [--only-missing]] [--previews team|all|none] [--teams all|4911,...]` writes
`api/<ev>/dashboard.json`, `dashboard.<team>.json`, `preview/<match>.json`. It also writes `dash/<ts>.json` for every
30 min of each event day, listed in `dash/index.json` = `[{"ts","label","quals_played"}]`, for the replay slider.
Python entry point for the worker: `live.strategy.dashboard.site_build(DATA, ev)`.
