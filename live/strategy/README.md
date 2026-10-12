# live/strategy — match preview + simulator (phase 1)

Pure numpy/scipy. Reads data only, writes nothing outside the `--out` path you give it.

```
python -m live.strategy.backtest DATA_DIR [--event EV] [--out bt.json]          # honest backtest, ~10 s
python -m live.strategy.preview  DATA_DIR qm15 [--asof prior|all] [--out p.json] # match preview JSON
python -m live.strategy.dashboard DATA_DIR [EV] [--team frc4911] [--asof TS] [--out d.json]  # dashboard JSON (DASHBOARD.md)
python -m live.strategy.dashboard DATA_DIR [EV] --site [--snapshots --only-missing]           # site files
```
backtest and preview take `--epa-prior none|cache|fetch` (default `cache`: use the pre-event EPA prior if its cached
inputs exist under DATA_DIR/cache/tba_prior; `fetch` may call TBA with TBA_KEY). Without a cache they behave exactly
like phase 1.
`DATA_DIR` is the production data root (`/opt/stacks/cyber-scout/data`) or a copy with the same layout:
`tba/event__<ev>__matches.json`, `site/api/<ev>/{m,t}/*.json`. The event is inferred if there is only one.

| file | what |
|---|---|
| `data.py` | loads TBA matches and site match JSON; soft-allocates each alliance's counted FUEL to its robots using per-frame identity confidence; extracts robot-match features with an identity weight |
| `model.py` | robot model: hierarchical GLS per FUEL component (auto, transition, active shifts, endgame) on TBA alliance rows plus video robot rows, with a schedule effect; climbs (TBA per robot); shrunk descriptive features |
| `sim.py` | Monte Carlo of one match from six team models: auto, then hub schedule, then teleop, towers and fouls |
| `baselines.py` | TBA-only baselines: ridge OPR and an EPA-style rating, both with a prequential normal win probability |
| `backtest.py` | predicts every qual using only matches played before it; Brier, log loss, accuracy, MAE; subsets and bootstrap CIs |
| `preview.py` | preview JSON (schema below) with plain-English key factors |
| `epa.py` | EPA: pre-event component prior from the teams' earlier events this season (TBA, cached) or Statbotics; as-of EPA for display (Statbotics team_match history or local running EPA) |
| `rank.py` | 2026 RP rules + TBA tiebreakers (reproduces the final Sammamish table exactly); Monte Carlo rank projection |
| `matchday.py` | match-day helpers for the dashboard: verified-video coverage as of a time (`live/coverage.py`), cached prequential backtest rows + as-of footer, queue card (auto swap, defend who/when/where, opposing defenders, RP us/them, fouls, goal mode) |
| `dashboard.py` | dashboard JSON (contract `DASHBOARD.md`): as-of view, blended ratings + video_weight, projection, next-match plan, levers, schedule, replay snapshots |

## EPA prior (phase 2)
`fit_event_model(..., epa_prior=P)` with `P = epa.pre_event_prior(...)` starts each team effect at the team's rating
from its earlier events this season, instead of at 0 (the event mean). That rating comes from the same model fitted
TBA-only to those events. Its variance is the prior-event posterior variance plus between-event drift
(0.5·tau)². The mu, beta and (sigma, tau) priors also come from those events until this event has data. The event's TBA rows
and trusted video then update it as before. Backtest (58 quals, prior-only), Brier: sim_full .159 → sim_epa .118;
warm 48: .155 → .123; early 10: .176 → .095. Video still adds on top of the prior (sim_epa − sim_epa_tba −.014,
90% CI [−.029, −.000]). The placebo with permuted robot identity gives +.001/+.002/−.009.

## Model in one paragraph
For each FUEL component c, a robot's counted FUEL in a match is modelled as
`mu_c + beta_c·won_auto/3 + delta_team + noise`, with `delta ~ N(0, tau_c²)` (shrinkage toward the event mean) and
`noise ~ N(0, sigma_c²)`. TBA gives the alliance sum plus a human-player term `h_c` (estimated from video).
Video gives per-robot rows: each made, counted shot is split across the alliance's three robots by identity
confidence. The shot goes to the tracked team with P = max(conf, 1/3), and the remainder is shared by the teammates.
The rows are scaled to the TBA count. Their measurement covariance comes from that split, plus 10% for call noise.
Low-confidence (v1) identity therefore turns into a uniform split that says nothing about individual robots.
`sigma` and `tau` come from the marginal likelihood of the TBA rows. `beta` (won auto, so the hub is active in shifts
2+4 instead of 1+3) is an event-level fixed effect. With all 58 quals, the schedule adds about +5 FUEL in transition,
+22 ± 7 in the active shifts and −4.5 ± 4 in endgame. The simulator draws the parameters from the posterior. Robot
noise is Gamma with the fitted variance, and a Gaussian copula correlates a robot's four components using the
residual correlation from TBA. Auto FUEL decides the schedule. Towers are drawn per robot. Fouls are resampled from
the event's empirical distribution. Defence and congestion are **not** in the simulator: with the current 11 v2
matches, their effects are not estimable (defence slope 0.14 ± 0.54 FUEL per opponent-defending-second, n=20;
congestion p=0.42, n=29).

## Preview JSON schema (`cyber-scout/strategy-preview@1`)
Numbers are expectations unless noted. `dist` = `{mean, sd, q05, q25, q50, q75, q95}`.
`shrunk` = `{v, sd, n_eff, event, prior_only}`: the team value shrunk toward the event value `event` with
`n_eff` identity-weighted matches. When `prior_only` is true there is no trusted data, so `v` is just the event value.
Show it greyed out.

```jsonc
{
  "schema": "cyber-scout/strategy-preview@1",
  "event": "2026wasam", "match": "2026wasam_qm15", "comp_level": "qm", "label": "Qualification 15",
  "time": 1774128259,            // TBA actual (or scheduled) unix time
  "played": true, "generated": 1791800000,
  "data": {
    "asof": "prior" | "all" | "all_played",   // prior = only matches before this one (genuine pre-match view)
    "before_time": 1774128259 | null, "n_matches_used": 14,
    "video_matches_used": ["2026wasam_qm1", ...],   // matches whose robot-level video was trusted
    "model": { "<auto|trans|active|endgame>": {"mu", "beta", "beta_sd", "sigma", "tau", "hp_alliance"} },
    "not_modelled": ["..."]
  },
  "prediction": {
    "p_red_win": 0.14, "p_blue_win": 0.86, "margin": dist,          // margin = red − blue
    "p_red_win_if_red_wins_auto": 0.26, "p_red_win_if_blue_wins_auto": 0.12,  // counterfactual schedule sims
    "schedule_value_fuel": {"mean": 22.0, "sd": 19.0},               // teleop FUEL the auto winner gains (event-level)
    "n_sims": 8000
  },
  "alliances": { "red"|"blue": {
    "teams": ["frc949", ...], "score": dist, "score_no_fouls": dist, "fuel": dist,
    "fuel_by_period": {"auto", "transition", "shift1", "shift2", "shift3", "shift4", "endgame"},  // incl. human player
    "p_win_auto": 0.06, "tower_pts_mean": 1.8, "foul_pts_mean": 7.2, "hp_fuel_mean": 25.0 } },
  "robots": [ {                                   // 6 entries: red 1-3, then blue 1-3 (TBA station order)
    "team": "frc3826", "number": 3826, "alliance": "red", "station": 1,
    "role": {"v": "scorer|feeder|collector|defender|hybrid|low-scorer", "scores": {...} | null,
             "basis": "video" | "model (no trusted video)", "w_sum"?: 2.0},
    "expected_fuel": {"total", "sd", "by_period": {7 periods}, "if_won_auto", "if_lost_auto"},  // from the simulation
    "rating": {"<component>": {"mean", "sd", "linear_mean"}},   // posterior robot FUEL per match, schedule-neutral;
                                                                // mean = E[max(theta,0)], linear_mean may be < 0
    "hub_state": null | { "w_sum": 2.0,
        "active"|"inactive": {"shots_per_min", "made_per_min", "passes_per_min",
                              "zone_frac": {"own", "neutral", "opp"} | null} },   // during own hub active/inactive shifts
    "top_zones": null | {"w_made": 8.0, "zones": [ {"zone": "own zone / left bump", "share": 0.64,
                                                    "periods": {"shift3": 0.5, ...}} ]},   // up to 3
        // zone = "<own zone|neutral|opp zone> / <left trench|left bump|centre|right bump|right trench>",
        // left/right as seen from the alliance's own driver station
    "auto": {"expected_fuel", "auto_l1_prob", "start_station",
             "routines": null | {"n_matches", "n_at_station", "conditioned_on_station",
                                 "routines": [{"routine": "A", "name", "n", "freq", "freq_at_station", "stations": [..],
                                               "auto_made_mean"}]}},
    "climb": {"auto_l1", "end": {"L0", "L1", "L2", "L3"}, "expected_pts", "n_obs", "obs_end_levels": [n0, n1, n2, n3]},
    "reliability": {"dead_rate": shrunk, "stalls_per_match": shrunk, "flags": ["..."]},
    "defended": {"rate_retained": shrunk, "defended_s_per_match": shrunk},   // own shot rate kept while defended
    "defense": {"defense_s_per_match": shrunk, "contacts_per_match": shrunk, "pins_per_match": shrunk},
    "shooting": {"fire_rate", "volley_size", "volley_max", "cycle_s", "accuracy", "shot_dist_m", "on_move_rate",
                 "passes", "intakes", "congestion"},          // each shrunk | null
    "data_quality": {"n_matches_tba", "n_video_matches", "video_weight", "video_matches": [...]}
  } ],
  "key_factors": [ {"kind": "outlook|auto_battle|top_scorer|defense_target|defense_resistant|reliability|climb|
                             inactive_behaviour|data_quality|uncertainty",
                    "weight": 0..1, "text": "plain English", "team"?: "frc..", "alliance"?: "red|blue"} ],  // sorted by weight
  "actual": null | {"red_score", "blue_score", "red_win": 1|0|0.5, "red_won_auto",
                    "fuel_by_period": {"red"|"blue": {7 periods}}, "end_tower": {...}, "fouls": {...}}
}
```

## Trust and its limits
- Robot-level video is trusted only through identity confidence. v1 tracks (`id_conf ≤ 0.2`, no per-frame conf) get
  weight 0 for features and a uniform FUEL split. Their alliance totals are still used, through TBA.
- Descriptive per-team numbers (zones, hub-state behaviour, defended retention, dead rate) currently rest on one to
  three tracked matches per team. Check `n_eff` and `prior_only` before showing them as facts.
- Phase 1 has no per-robot cycle-time or fire-rate model inside the simulator. Robot FUEL per period is the fitted
  per-match amount, which already includes those rates over the time available.

## District points, season goals, rankings and schedule (agent C)
`district.py` (FRC district-points rules + TBA loader), `goals.py` (season Monte Carlo, `rankings.json`, `schedule.json`).
```
python -m live.strategy.goals DATA_DIR EV [--team frc4911] [--goal DCMP|Worlds|rank:N] [--asof TS] [--sims 800] [--out-dir D] [--offline]
python -m live.strategy.goals DATA_DIR EV --replay        # goal odds at ~24 points of the season vs who really reached DCMP
python -m live.strategy.goals DATA_DIR EV --goal-only     # print just the goal block
```
Worker: `from live.strategy import goals; goals.site_build(DATA, ev, team=DASH_TEAM)` writes `site/api/<ev>/rankings.json` and
`schedule.json` (~2-5 s; pass `ctx=` a `dashboard.Context` to reuse it; reads `dashboard.<team>.json` for the blended ratings if
present). Goal from `goal=` / env `DASH_GOAL` (default `DCMP`). Needs env `TBA_KEY` (district/event/awards/alliances, cached under
`DATA/cache/tba_district/`, finished events forever). `DCMP_CAPACITY` env or `district.CAPACITY[district]` sets the DCMP size (TBA does not
expose it; 2026pnw = 51 observed, default elsewhere 64); `district.WORLDS_SLOTS` likewise (assumed 14 for pnw). Events outside a
district get rankings/schedule without the `district`/`goal` parts.

**District rules** (`district.py`, exact vs TBA on all 309 team-events of 2026pnw and 126/126 season totals):
qual `ceil(erfinv((N-2R+2)/(1.07N))*10/erfinv(1/1.07)+12)`; alliance captain/1st pick `17-n`, 2nd pick `n`, backup 0; playoffs
(8-alliance double elim) winner 30 / finalist 20 / "Round 5" 13 / "Round 4" 7 (partial participation by backups: empirical table
`PARTIAL`); awards Impact 10, EI 8, Rookie All-Star 8, other judged 5 (`NO_POINT_AWARDS` excluded); DCMP all x3; season = first two
district events + DCMP + rookie bonus + adjustments (from TBA district rankings).

**`rankings.json`** (`cyber-scout/goals@1`, kind `rankings`)
```jsonc
{ "schema", "kind": "rankings", "generated", "event", "team", "asof": null|ts, "quals_played", "quals_total", "district": "2026pnw"|null,
  "teams": [{ "team": "frc4911", "number", "nickname", "rank", "rp", "matches_played", "record": "W-L-T", "rs",
              "projection": {rank_mean, rank_p10, rank_p90, p_top8, p_top4, p_first, rp_mean, rs_mean, ...} | null,   // dashboard schema
              "district": {"current": pts so far (counted events + rookie bonus), "projected": mean final pre-DCMP pts,
                           "p10", "p90", "p_dcmp"} | null }],            // sorted by rank
  "goal": null | {                                  // for `team` (DASH_TEAM)
    "goal": "DCMP", "team", "district",
    "p_goal": 0..1, "p_goal_ci": [lo, hi],           // 90% Monte-Carlo interval
    "current_points", "projected_points": {mean, p10, p90},       // pre-DCMP season total
    "cutoff": {"now": pts of the last points-qualifier if the season ended now,
               "projected": {mean, p10, p50, p90}},                // projected points of the last points-qualifier
    "points_needed": {"p50"|"p80"|"p95": {"final_points", "still_needed"}},   // for that chance of beating the cutoff
    "points_still_needed",                           // = p50.still_needed
    "dcmp": {"capacity", "points_slots_mean", "award_prequalified_now", "is_prequalified"},
    "events": [{"event", "name", "start_date", "status": "done|live|future", "counts": bool (one of the first two),
                "points": number (done) | {mean, p10, p90, rank_mean} (projected)}],
    "value": {"baseline", "matches": [{"key", "label", "time", "p_if_win", "p_if_loss", "delta"}],   // focal-event quals, forced results
              "events": [{"event", "by_points": [{"outcome","points","p_goal"}], "by_rank": [{"outcome","p_goal","share"}]}]},
    "model": {"sims", "theta": source text, "sigma_alliance", "worlds_slots", "theta_team": {mean, sd}},
    "p_dcmp"?: (goal Worlds only) } }
```
**`schedule.json`**: `{"schema","kind":"schedule","event","team","matches":[{ "key","label","short","comp_level","time","played",
"red":[3],"blue":[3],"us":"red"|"blue"|null,"analyze_url":"#/<ev>/a/<key>"|null,
"result":{red,blue,winner,rp:{red,blue},bonus:{red,blue}} (played) | "pred":{p_red, score:{red,blue}, rp?:{red,blue}} (upcoming),
"breakdown": (upcoming matches of `team` only) {"win_prob" (ours),"p_win_auto","score":{ours,theirs,margin,margin_sd},"fouls":{ours,theirs},
  "periods":[{period: auto|transition|shift1..4|endgame, label, ours, theirs, delta, p_ours_ahead, our_active_p, their_active_p,
              ours_if_won_auto, ours_if_lost_auto, theirs_if_won_auto, theirs_if_lost_auto}],
  "summary": plain-English "where you will be beaten"}}]}`. auto includes auto tower, endgame includes climb; shifts 1/3 are
active for the auto loser (`our_active_p` = P our hub is active); FUEL points include the human player.

**Model notes.** Strength: dashboard blend (+ foul share) for the event's teams, else running district EPA (Statbotics team_events when
up and `asof` is None); one draw per team per season. Match = sum(theta) + N(0, sigma) (sigma ~ 61 from district residuals); RP bonus
probabilities logistic in score (fitted on the district). Alliance selection: captains by rank, picks best remaining rating (no
declines). Awards at unplayed events: random teams (Impact, EI, RAS ~ 30%, ~12 judged); they pre-qualify for DCMP. Worlds: surrogate
DCMP (performance = theta + noise), approximate. Not modelled: team withdrawals, DCMP declines, tie-break details beyond avg RP.

## Auto under-prediction (agent M, 2026-10-11)
Prequential check (model refit before every qual, EPA prior, 2000 sims): alliance auto FUEL actual − predicted = −4.9
(Q3–46, n=88 alliances) and −10.3 (Q47–58, n=24). A naive "mean of each team's earlier alliance autos" is biased the
same way (−7.8 / −10.6): autos at 2026wasam rose over the event (alliance mean 37.5 day 1 → 47.8 day 2). It is drift,
not a shrinkage bug; recency weighting (half-life 12 matches) only cuts it to −3.3 / −6.5. The dashboard now shows the
TBA auto history beside the prediction and a caution when the last 10 posted quals were off by ≥ 5 FUEL
(`model.backtest_asof.auto_bias_recent`). The levers' "auto FUEL 13.9" is per robot (one third of an alliance without
the human player), so it is not comparable with alliance autos of 37–58.

## Scouting truth and alliance selection (agent S)
**Trust rule** (`live/coverage.py`, shared): a team's robot-level video number comes only from *verified* matches (v2 tracker,
`identity.method` `ocr*`, and that team's track `team_conf >= 0.6`). v1 tracks feed nothing but the replay (`scouting.load_matches`
skips them; `Trk.team` is None for unverified tracks, so no shots/frames/defense episodes are credited). n >= 3 fact, 1-2 grey, 0 hidden.
`coverage.reconcile(match)` compares credited robot FUEL + human player with the TBA alliance count (`reconcile.total.tba`):
> 20% unattributed -> `lower_bound` ("≥X", never read as under-rated); credited > TBA -> `over`, scaled to the TBA total.

**`scout.json`** (`cyber-scout/scout@1`, written by `live/scouting.py` `build()` -> `build_truth()`, i.e. the existing worker hook
`scouting.site_build_extra(DATA, ev)`; reads `site/api/<ev>/{m/*.json, dashboard.json, dash/*.json}` and `tba/event__<ev>__matches.json`).
The one canonical per-team source for Teams, team page, Specs, Compare, Picklist and Selection pitch evidence.
```jsonc
{ "schema", "event", "generated", "verified_matches": [keys in play order], "last_verified", "quals_played",
  "policy": {"verified_conf": 0.6, "show_n": 3, "shrink_matches": 1, "acc_prior_shots": 20, "lower_bound_unattributed": 0.2},
  "event_stats": {"made_pm_mean", "acc", "auto_made_mean", "cycle_median", "tower_pts_max", "climb_matters": tower_pts_max > 10, "any_climb"},
  "teams": [{ "team", "number", "nickname", "rank",
    "coverage": {"n", "keys", "last", "of" (quals played), "tier": "fact|grey|none", "label"},
    "rating": {"blend", "sd", "auto", "teleop", "endgame", "epa", "epa_source", "opr", "video_weight", "record", "p_top8"} | null,  // dashboard.json
    "climb": {"n", "levels": {"0": k, ...}, "rate", "pts_mean", "auto_l1_rate", "keys"} | null,      // TBA per robot, every played qual
    "video": null | {               // metric = {v (shrunk toward event), raw, n, tier, lower_bound?, over?}
       "made_pm" (+per_match [{m, made, scaled, lb, un}]), "acc" {v, raw, made, shots, n, tier}, "auto_made" (null when it contradicts TBA),
       "cycle_s", "fire", "hopper", "pts_active_min", "speed_p95", "trench", "on_move", "dead" ({v, n} only when n >= 3), "def_played", "def_received"},
    "flags": [{"k": "auto_not_tracked|video_higher|video_lower|lower_bound|over", "text"}],   // video vs EPA (TBA-only), positive evidence first
    "trend": {"tba": {"last_n": 4, "last", "before", "diff", "ci": [lo, hi], "verdict": "up|down|flat"} | null,   // alliance score - fouls drawn - partners' blend
              "blend": [[ts, blend, sd, epa], ...] | null } }] }                                  // from dash/<ts>.json snapshots, >= 3 points
```
Spec sheets (`spec/<team>.json`) additionally carry `coverage` and `truth` ({video, flags, rating, trend}); their `climb` is from TBA
(`source: "TBA"`), `auto.climb_rate` = TBA auto L1 rate; `spec/index.json` rows carry `cov`, `made_v` (canonical made/match) and `flags`.

**`selection.json`** (`cyber-scout/selection@1`, `live/strategy/selection.py`, written by `goals.site_build` next to rankings.json;
replay snapshots `selection/<ts>.json` + `selection/index.json {times}` from `python -m live.strategy.goals DATA EV --selection-snapshots [--all] [--asof TS]`,
one per `dash/<ts>.json`, each built with `--asof ts`; `goals --asof TS` also writes `selection/<ts>.json`). Same Season Monte Carlo as the goal block.
```jsonc
{ "schema", "event", "team", "asof", "sims", "generated", "quals_played", "quals_total",
  "status": "projected" | "fixed" (alliances known) | "done" (event finished; nothing else below) | "unavailable",
  "model": {"draft", "playoffs"},
  "teams": [{ "team", "number", "nickname", "rank_mean", "rank_p10", "rank_p90", "p_captain", "p_r1", "p_r2", "p_selected",
              "rating", "rating_sd" (pts/match incl. foul share), "p_dcmp", "likely_captain": {"team", "p"}? }],
  "seeds": [{ "seed", "captain": [{team, p}] x3, "first_pick": [{team, p}] x3, "playoffs": {"win","finalist","top 4 (13)","top 6 (7)","out early"} }],
  "us": { "team", "rank_mean", "p_captain", "p_r1", "p_r2", "p_unpicked", "p_dcmp",
          "outcomes": [{ "seed"|null, "role": "captain|first pick|second pick|not selected", "share", "n", "event_points", "p_dcmp" (null if n < 15), "playoffs" }],
          "by_role": {role: {share, p_dcmp, n}},
          "picked_by": [{ "captain", "seed", "role", "p", "slots": [{seed, role, p}] }],
          "need": { "final_points" (median cutoff + 1), "current_points", "need_here", "qual_points_median",
                    "grid": [{ "seed", "captain"|"first pick"|"second pick": {"result": least playoff result reaching the need | null, "points", "p_result"} }],
                    "text": "need ~44 event points (qual ~12): R1 pick of A1–A5 + finalist; ...", "note" } | null,
          "pitches": [{ "captain", "nickname", "seed", "role", "p", "captain_rating", "lacks", "we_bring", "pitch", "evidence": [video facts with n] }] },
  "draft": { "teams", "order": [[team idx by simulated final rank] x 300], "theta": [[rating draw per team] x 300],
             "p_dcmp_by_outcome": {"<seed>:<role>": p}, "p_dcmp_by_role": {role: p} } }   // client-side "captain picks X" re-draft
```
