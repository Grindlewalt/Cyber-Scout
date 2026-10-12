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

> **Round 3 (agent M).** Additive unless noted.
> - Defense has one source: the defense library (`defense.json`). A target's retention is "measured" only when the
>   library's per-team placebo-controlled estimate (`teams[].retention = {est, lo, hi, n_eff, significant}`) excludes
>   100%; otherwise the event-wide library effect (`global.ratio / global.placebo.pooled_ratio`), labelled an
>   assumption. `plan.defend[].top_scorer` (bool); `queue.defend.alt` = the top scorer when it is not the pick.
>   `queue.defenders.effect` gained `lo, hi, significant, source ("library"|"video"), placebo`; `likely` = opponents
>   that defended in ≥ half their verified matches (library `defenders[].matches` ∩ visible verified matches);
>   `queue.defenders.if_defended = {defender, target, fuel_lost, win_prob, basis}` and `ignores` (text): the
>   simulator has no defense, so the card says what a dedicated defender on our top scorer would do to the win %.
>   The library is built from all tracked matches, so in a replay its estimates are not strictly as-of.
> - `prediction.models_disagree` / `queue.models_disagree` compare the displayed (5%-step) values, ≥ 15 points.
> - `ratings[].coverage` counts quals only (`of` = quals played), like scout.json; video becomes visible 10 min after
>   posting, so a replay can show one match fewer than scout.json's played-by-time view.
> - The climb lever is dropped when < 5% of the event's robot-matches end on the tower.
> - Backtest cache key = hash of the prior matches' video content + the model's video/trend/shrinkage settings.
> - dash.js: odds shown as "<5%" / ">95%" at the ends, everywhere; plan tabs are linkable as
>   `#/<ev>/dash/<ts|live>/<q51>` (the URL updates when a tab is clicked).

> **Match-day changes (agent M, 2026-10-11; docs/STRATEGIST_REVIEW.md).** All additive except where marked.
> - `plans[]` (new): one plan per remaining match of `team`, in time order, each the same shape as `next_match` plus
>   `as_of` = `{"ts", "asof", "quals_played", "last_result": "Q46"}` and `queue` (below). `next_match` is `plans[0]`
>   (same object). Snapshots (`dash/<ts>.json`) keep every plan; their `preview_url` stays null.
> - `plan.defend[]`: `basis` is `"measured"` only with ≥ 2 effective defended matches (was 0.4), otherwise the generic
>   assumption (75% kept) is used — the per-team "event prior" retention is gone (one defense-effect number).
>   `where.zone_ours` = zone seen from OUR driver station ("their end, our right trench"). `why` text changed.
> - `plan.suggested_defender` gained `basis` and `candidates[]` (`team`, `passes_per_min`, `feed_cost_fuel`,
>   `expected_fuel`, `defense_s`): the defender is the robot that gives up the least feeding while our hub is
>   inactive (verified passing rate × 0.5 assumed conversion), not just the lowest scorer.
> - `plan.key_factors` is filtered for our point of view (no `uncertainty`; no `defense_target` / `defense_resistant` /
>   `inactive_behaviour` about our own alliance). preview.py: `defense_target` needs ≥ 2 effective matches.
> - `prediction` gained `win_prob_epa` (local as-of EPA, prequential normal), `models_disagree` (|Δ| > 15 pts) and
>   `rp` = `{"us"|"them": {"win","energized","supercharged","traversal","rp_mean","fuel": {mean,lo,hi}}}`.
>   `rp.us.win` is the plan's `win_prob` (one win % per match everywhere).
> - `queue` (new, per plan): `{"mode": "win"|"goal", "lever": null|{"bonus","needed","unit","expected","lo","hi","p","text"},
>   "win_prob", "win_prob_epa", "models_disagree",
>   "auto": {"p_win_auto","our_expected","their_expected","if_won"|"if_lost": {"our_active","defend_in","win_prob"},
>            "history": {team: [{"match","alliance_auto"}]} (TBA, last 4), "history_alliance_avg": {"us","them"},
>            "recent_bias": backtest_asof.auto_bias_recent, "caution"?, "text"},
>   "defend": null|{"target","defender","when","when_alt","where": null|{"zone_ours","share","field": [u, v]},
>            "denied_fuel" (int), "basis": "measured"|"assumption", "coverage", "goal_note"?, "skip_note"? (< 2 FUEL), "text"},
>   "defenders": {"teams": [{"team","n_video","n_defended","matches","avg_defense_s","tier","label"}], "likely": [team],
>                 "effect": null|{"retained","sd","n","text"}, "text"},
>   "rp": {"us","them","shown": ["energized","supercharged"(,"traversal")], "hidden", "hidden_why"},
>   "fouls": {"teams": [{"team","side","avg_given","n"}], "event_avg", "flag": [team], "text", "note"}}`.
>   Goal mode when win < 10% or > 90%: the headline is the bonus RP closest to a coin flip. `field` = [u, v] fractions
>   seen from our driver station: u 0 = our left → 1 = our right, v 0 = our wall → 1 = their wall. TRAVERSAL is
>   hidden while no alliance at the event has earned it (as of `now`). Defense data only from verified matches.
> - `ratings[].coverage` (new) = `{"n","of","keys": ["Q12",..],"last","tier": "fact"|"grey"|"none","label"}` from
>   `live/coverage.py` as of `now` (video visible 10 min after posting). `ratings[].disagree` = `{"diff": blend − EPA,
>   "flag": |diff| ≥ max(15, 1.5 sd)}`. `id_conf` is kept for compatibility but no longer shown.
> - `schedule[]`: upcoming matches with a plan take `win_prob` and `bonus_probs` from the plan; new
>   `bonus_probs_them`; played `result` gained `rp_them`, `bonus_them`.
> - `model.backtest_asof` (new; the footer uses only this): `{"n","through","asof","min_n","what",
>   "brier": {"blend","no_video","epa","coin"}, "accuracy": {"blend","epa"}, "blend_minus_epa": {"mean","ci90"},
>   "video_increment": {"mean","ci90"}, "n_with_video", "auto_bias_recent": {"mean","n_alliances","from","to"}}` over
>   quals posted before `asof`, each predicted (Monte Carlo, 1500 sims) from the matches played before it; EPA = the
>   local as-of EPA with prequential sd (`epa_pre` in backtest.py). Rows cached in `DATA/cache/strategy/backtest_<ev>.json`.
>   `model.backtest` (constant 58-match reference) is kept but no longer shown.
> - `our_team.trend[].fuel_made` only from verified matches (live/coverage.py). The plan details hide defense targets
>   worth < 2 FUEL.
> - Display policy (dash.js): win % and bonus odds in 5% steps (round 3: "<5%" / ">95%" at the ends).

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
