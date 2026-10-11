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
