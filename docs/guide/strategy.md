# Strategy, rankings, selection and scouting truth

This page covers everything downstream of the video pipeline: how a team's strength is estimated, how matches and
seasons are simulated, what each strategy JSON file contains, how the trust rules decide which video numbers are
shown, and how the defense library measures defense. It describes commit `cc6297c` (2026-10-12). Where the older docs
(`live/strategy/README.md`, `live/strategy/DASHBOARD.md`, `docs/STRATEGIST_REVIEW.md`) disagree with the code, the
code is what is described here, and the difference is listed under **Docs vs code** at the end.

Code covered:

| file | lines | role |
|---|---|---|
| `live/strategy/data.py` | 401 | loads TBA matches and site match JSON into the records everything else uses |
| `live/strategy/model.py` | 677 | the robot model (hierarchical GLS per FUEL component, climbs, descriptive features) |
| `live/strategy/epa.py` | 402 | pre-event EPA prior and the as-of EPA shown beside the blend; Statbotics / TBA HTTP cache |
| `live/strategy/sim.py` | 217 | Monte Carlo match simulator |
| `live/strategy/rank.py` | 139 | 2026 RP rules, TBA tiebreakers, rank projection |
| `live/strategy/baselines.py` | 86 | TBA-only OPR and EPA baselines, prequential win probability |
| `live/strategy/backtest.py` | 244 | honest (prequential) backtest CLI |
| `live/strategy/preview.py` | 302 | per-match preview JSON + plain-English key factors |
| `live/strategy/matchday.py` | 552 | coverage as-of, as-of backtest + footer, queue card, defender choice |
| `live/strategy/dashboard.py` | 734 | `dashboard.json`, snapshots, levers, ratings |
| `live/strategy/district.py` | 291 | district-points rules + TBA district loader |
| `live/strategy/goals.py` | 1117 | season Monte Carlo, DCMP/Worlds odds, `rankings.json`, `schedule.json` |
| `live/strategy/selection.py` | 356 | alliance-selection view `selection.json` |
| `live/coverage.py` | 142 | trust rule (verified video), coverage tiers, TBA reconciliation gate |
| `live/scouting.py` | 1727 | spec sheets, defense library, `scout.json` |

Everything in `live/strategy/` is pure numpy/scipy. `live/strategy/__init__.py` sets `OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS` and `MKL_NUM_THREADS` to 1, because multi-threaded BLAS burned 15 cores on the server for these
small matrices.

---

## 1. Where it runs and in what order

The worker calls the strategy code at the end of every site build (`live/worker.py:build_site`, lines 463–485), after
the per-match and per-team site JSON has been written. The order matters, because each step reads the previous one's
files:

```
build_site(ev)                                  writes site/api/<ev>/{event.json, m/*.json, t/*.json}
 1. python -m live.strategy.dashboard DATA ev --site --snapshots --only-missing   (subprocess, timeout 900 s,
       skipped when env DASHBOARD != "1")      -> dashboard.json, dashboard.frc4911.json, preview/<match>.json,
                                                   dash/<ts>.json (missing ones + always the latest), dash/index.json
 2. scouting.site_build_extra(DATA, ev)        -> sitev2.verified_team_files (rewrites t/<team>.json, event.json teams[])
                                                   then scouting.build: defense.json, spec/<team>.json, spec/index.json,
                                                   scout.json
    python -m live.scouting DATA ev --snapshots (subprocess) -> scout/<ts>.json for EVERY dash/<ts>.json, scout/index.json
 3. goals.site_build(DATA, ev, team=DASH_TEAM) -> rankings.json, schedule.json, selection.json
    python -m live.strategy.goals DATA ev --selection-snapshots (subprocess) -> selection/<ts>.json (missing only),
                                                   selection/index.json
```

Consequences of the order:

* `dashboard.json` reads `defense.json` (`matchday.load_library`) **from the previous site build**, because the
  defense library is rebuilt in step 2. Defense numbers on the dashboard therefore lag one build.
* `scout.json` reads the fresh `dashboard.json` (ratings, EPA) and `dash/<ts>.json` (blend trend).
* `goals` reads the fresh `dashboard.<team>.json` (blend ratings), `scout.json` and `defense.json` (pitch cards).
* The scout snapshot subprocess has no "only missing" mode: every build rebuilds every `scout/<ts>.json` (~3 s each,
  36 snapshots at 2026wasam ≈ 2 min). Dashboard and selection snapshots only build what is missing (the latest
  dashboard snapshot is always rebuilt).

The perspective team is `DASH_TEAM` (default `frc4911`). **The team number and season goal set in the web app
(Admin → settings, stored in `DATA/config/worker.json` as `team_number` / `season_goal`) are not read by any of this
code**: `worker.load_config()` only propagates `TBA_KEY`, `GPU_DEVICE` and `FORCE_CPU`. To change the perspective you
must set `DASH_TEAM` / `DASH_GOAL` in the worker's environment (see the env table in §12). The competitions agent was
editing `live/worker.py` while this was written, so check whether that has changed.

All strategy CLIs take `DATA_DIR` = the production data root (`/opt/stacks/cyber-scout/data`) or any copy with the
same layout (`tba/event__<ev>__matches.json`, `tba/event__<ev>.json`, `tba/event__<ev>__teams__simple.json`,
`site/api/<ev>/...`, `cache/...`). With one event in the copy, the event argument can be omitted
(`data.resolve`).

---

## 2. Inputs (`data.py`)

### 2.1 Game timing and constants

| constant | value | meaning |
|---|---|---|
| `PERIODS` / `PERIOD_T` | auto 0–20, transition 20–30, shift1 30–55, shift2 55–80, shift3 80–105, shift4 105–130, endgame 130–160 | match clock seconds |
| `COMPONENTS` | `auto`, `trans`, `active`, `endgame` | modelled FUEL components; `active` = counted FUEL in the alliance's two active shifts |
| `TOWER_TELEOP_PTS` | `[0, 10, 20, 30]` | endgame tower L0–L3 |
| `TOWER_AUTO_PTS` | 15 | auto L1, max 2 robots per alliance |
| `ID_RAMP` | (0.3, 0.8) | identity confidence → robot-record weight, linear from 0 at 0.3 to 1 at 0.8 |
| `CRED_CONF` | 0.8 | a made shot is "confidently credited" at this attribution probability |
| `MIN_TRACED` | 3 | minimum traced counted FUEL in an alliance-component to use the soft split |
| `LANES` | y 0–1.28 left trench, 1.28–3.40 left bump, 3.40–4.67 centre, 4.67–6.79 right bump, 6.79+ right trench | named lanes (canonical red frame, driver's left = low y) |
| zone lines | x ≥ 12.513 own zone, 4.028–12.513 neutral, < 4.028 opp zone | `shot_zone` regions (canonical frame) |

Hub schedule: the alliance that wins auto has its hub **inactive in shifts 1 and 3** and active in shifts 2 and 4;
the auto loser is active in 1 and 3. Transition, auto and endgame are active for both.

### 2.2 TBA records (`load_tba`)

One record per match, sorted by `(time, order)`, `time = actual_time or time or predicted_time`. Played = a score
breakdown exists and the red score is ≥ 0. Per alliance it keeps: FUEL per period (`hubScore.*Count`, plus
`uncounted`), the four components, `won_auto`, `active_shifts`, per-robot `auto_tower` / `end_tower` levels (station
order = `team_keys` order, so climbs are robot-level and exact), `tower_pts`, `fouls` (foul points received),
`score`, `rp`, `bonus` (energized / supercharged / traversal flags), `tower_total`, plus `post_time`
(`post_result_time`), `surrogates`, `dqs`.

`won_auto` when auto FUEL ties: inferred from which shift pair carried counted FUEL (FMS picks the auto winner on a
tie).

### 2.3 Video records (`load_video`)

Reads every `site/api/<ev>/m/<key>.json` (not `*.fuel.json`) of a played match with tracks. Two outputs:

**Alliance blocks** (only when the match has shots). For every **made** shot into a hub that was not inactive
(`hub_active is not False`), booked to the alliance whose hub it entered:

* `shooter.kind == "human"` → `hp += 1`;
* not a robot → `unknown`, and into the unattributed pool `unc`;
* robot shot from track `k` whose team is in the TBA lineup: P(that team) = `max(c, 1/3)` with `c` the per-frame
  identity confidence at launch (`tracks[k].conf[i]`, else `team_conf`, else `id_conf`), the rest split evenly over
  the two partners. If that probability ≥ `CRED_CONF` (0.8) the shot is **credited** (`cred[k] += 1`), else it goes to
  `unc`. A shot without a valid track gets a uniform split.

Per component `c` the block stores `traced`, `tba`, `coverage` (min `reconcile.coverage` over its periods),
`usable` (traced ≥ 3, coverage ≥ 0.8, TBA > 0), the soft split `y` and covariance `V` scaled to the TBA count
(soft mode only), `cred`, `unc`, `hp_raw`, and `id_info` (mean of `(max p − 1/3)/(2/3)`). Block-level: `v2`
(`identity.method` starts with `ocr`), `recon` = (robot shots + HP) / TBA alliance FUEL.

**Robot records** (one per track whose team is in the lineup): weight
`w = ramp(team_conf) × min(1, observed / 0.5)` (× 0.5 when `observed` is unknown), the analytics block `an`, the
hard-attributed shot stats `_robot_shots` (only shots with `team` set and no `unattributed` flag: the quarantine is
respected), launch origins in the canonical frame, hub-state split (`_robot_shots`), and zone time by own-hub state
from frames with per-frame conf ≥ 0.5 (`_robot_positions`).

`load_routines` reads `t/<team>.json` `routines[]` (clustered autos). `load_event` returns
`{event, paths, tba, by_key, video, teams, routines, info}`.

---

## 3. The robot model (`model.py`)

### 3.1 Scoring model

One hierarchical linear-Gaussian model per FUEL component `c ∈ {auto, trans, active, endgame}` (counted FUEL = points):

```
robot i in a match:  x_ic = mu_c + beta_c * won_auto/3 + delta_ic (+ gamma_c * s) + eps,   eps ~ N(0, sigma_c^2)
team effect:         delta_ic ~ N(m0_i, P0_i^-1)        (event-mean shrinkage, or the EPA prior; see 3.3)
TBA alliance row:    y_ac = h_c + sum_{i in a} x_ic     (h_c = mean human-player FUEL per alliance, from video)
video robot rows:    y_ic = x_ic + eta_i,  eta ~ N(0, S) (S below)
```

* `beta_c` (not for auto) is the event-level **schedule effect**: what winning auto (active in shifts 2+4 instead of
  1+3) is worth in that component. With all 58 quals at 2026wasam: about +5 transition, +22 ± 7 active, −4.5 ± 4
  endgame (README).
* `s` is the optional time trend, off by default (`TREND = frozenset()`; §7.4).
* Everything (`mu`, `beta`, team effects) comes from **one GLS solve** per component (`fit_posterior`):
  precision `A = P0 + Σ rows`, mean `cov · b`. A TBA row has design `[3, won_auto, team indicators]`, target
  `y − h`, precision `w / (3 σ²)`.
* `team_theta(comp, team)` = posterior mean and sd of `mu + delta_team` (schedule-neutral robot FUEL for the
  component). A team never seen: `mu` with sd `sqrt(var(mu) + tau²)`.

`fit_event_model(E, before=None, use_video=True, recency_halflife=None, epa_prior=None, trend=None)` fits everything
from matches played strictly before `before` (unix time; `None` = all played). `recency_halflife` (matches) is an
optional weight `0.5^(age/H)`; nothing uses it in production.

### 3.2 Hyperparameters (`fit_hyper`)

`sigma²` (robot match noise) and `tau²` (between-team spread) come from **all** TBA alliance rows of the component:

* ≥ 6 rows: grid search over `lam = tau²/sigma²` (`LAM_GRID`: 41 log-spaced values 0.02–20) maximising the profiled
  marginal likelihood `y ~ N(F g, s² (3 I + lam Z Zᵀ))` plus a log-normal prior `log lam ~ N(0, 1)`
  (`LAM_PRIOR_SD = 1`), then a REML-style df correction `s² · n/(n − nf)`.
* < 6 rows with an EPA prior: `sigma²`, `tau²` from the prior events (`epa_prior.hyper`).
* 2–5 rows: `lam = 1`, `s² = max(var(y), 25) / (3 + 3 lam)`.
* else: `s² = (0.6 · DEFAULT_ALLIANCE[c])² / 6` with `DEFAULT_ALLIANCE = {auto 25, trans 8, active 70, endgame 40}`
  (pre-event alliance guesses).

### 3.3 Priors (`_prior_terms`)

Without an EPA prior: `mu` prior = (alliance mean − h)/3 with sd 50 % of `DEFAULT_ALLIANCE[c]/3`; `beta` prior
N(·, 40²); team effects N(0, tau²·SHRINK_MULT²) (`SHRINK_MULT = {}` = 1).

With an EPA prior (`epa.pre_event_prior`, §4): each known team's effect starts at `m_team − m̄` (its prior-event
component mean minus the prior field mean) with the prior sd; `mu`'s prior sd becomes `max(0.3 |m̄|, 1)`, and while
the event has < 6 TBA rows its mean is `m̄ − h/3`; `beta` gets the prior events' mean and sd. Teams without a prior
keep the event-mean shrinkage.

### 3.4 Video as censored evidence (`VIDEO`, `_credited_block`)

```python
VIDEO = {"source": "credited", "mode": "replace", "recon_tol": 0.2, "floor": True, "rel_noise": 0.10, "v2_only": True}
```

Which alliance blocks are used: `v2_only` (OCR identity matches only), and the block's mean `id_info` over usable
components must be ≥ `TRUST_ID_INFO` (0.30). Then, per component, `_credited_block` with `tba` = TBA count,
`rob = tba − hp_raw` (FUEL the three robots scored), `rest = rob − Σ cred`:

| case | condition | what enters the model |
|---|---|---|
| no info | coverage < 0.8, tba ≤ 0, rob ≤ 0 | nothing (the TBA row stays) |
| **under-credited (censored)** | `rest > 0.2 · tba` | **floor rows only**: a robot's credited count `x` (≥ 1) is added as an absolute row, with the soft-split variance, *only if it exceeds the robot's current prediction* (one-sided). The TBA row stays. |
| **over-credited** | `rest < −0.2 · tba` | nothing (double counting: no robot information); TBA row stays |
| ok, some unattributed | `0 ≤ rest ≤ 0.2 · tba` | split rows `y = cred + rest/3`, `V = rest · (I/3 − 1/9)` |
| ok, slightly over | `−0.2 · tba ≤ rest < 0` | `y = cred · rob/Σcred`, `V = 0` |

Split rows replace that alliance's TBA row (`mode = "replace"`; they sum to TBA − HP, so using both would double
count). Their noise is `S = σ² I + V + diag((0.10 · y)² + 1)` (`REL_CALL_NOISE`: attribution / made-call noise).
After the first solve, floor rows are added and the component is re-solved (`comp["n_floor_rows"]`).

`mode = "contrast"` (not default) keeps the TBA row and adds only the two contrasts of the three robot rows.
`source = "soft"` (phase 1) used the identity-weighted soft split and a whole-alliance `recon` gate. Both are
selectable for experiments; the default was chosen on the as-of backtest (§7.3).

The alliance human-player mean `h_c` = mean of `hp` over ≥ 2 usable blocks, else 0. Note: `hp` is only filled for
blocks whose *soft* split is usable, so `h_c` uses the soft-split usability rule even in credited mode.

### 3.5 Information shares and `video_weight`

`fit_posterior` also decomposes each team's posterior precision of `theta = mu + delta`:
`prior = I_prior / I_full`, `video = (I_full − I_novideo) / I_full` (`I_novideo` = same fit with every TBA row and no
video), `tba = rest`. `dashboard.info_weights` averages these over the four components weighted by `tau²`; the
dashboard's `ratings[].weights` = `{prior, tba, video}` and `video_weight` = the video share.

### 3.6 The blend rating (`dashboard.blend_rating`)

The "Blend" shown everywhere is points per match for one robot, schedule-neutral, built from the model:

```
fuel_c   = E[max(theta_c, 0)] + h_c/3 + (beta_c/6 for non-auto)        (won auto half the time)
auto     = fuel_auto + 15 · P(auto L1)
teleop   = fuel_trans + fuel_active + fuel_endgame
endgame  = Σ_L P(end level L) · {0, 10, 20, 30}
total    = auto + teleop + endgame;   sd = sqrt(Σ_c sd(theta_c)²)
```

`E[max(θ,0)] = m Φ(m/sd) + sd φ(m/sd)` (`_pos_mean`). Summing three teams gives roughly an alliance's expected score
without fouls (mean blend at 2026wasam 64.0 vs mean alliance score 199.1 / 3 = 66.4).

### 3.7 Climbs (`_fit_climbs`)

From TBA per-robot tower levels (trusted, exact station mapping). Event rates with pseudo-counts:
`p_auto = (k + 0.5)/(N + 10)`, `p_end = (counts + [5, 0.5, 0.25, 0.25])/(N + 6)`. Team: Beta / Dirichlet shrinkage
with `CLIMB_K = 4` pseudo-matches of the event rate; with an EPA prior, prior-event climb counts are added at weight
`PRIOR_CLIMB_W = 0.5` per prior match.

### 3.8 Other model parts

* **Residual correlation** `M.R` (`_resid_corr`): correlation of the four components' standardized alliance
  residuals, shrunk toward identity with `n0 = 10` pseudo rows, made PSD. Used by the simulator's copula.
* **Active-shift split** `M.first_active_frac`: share of the active total scored in the first active shift
  (`"13"` for the auto loser, `"24"` for the winner), with 4 pseudo-observations at 0.5.
* **Fouls** `M.fouls`: the empirical list of foul points per alliance-match.
* **Descriptive features** (`_fit_descriptive`, from robot records with `w > 0`): `fire_rate`, `volley_size`,
  `volley_max`, `cycle_s`, `accuracy`, `shot_dist_m`, `on_move_rate`, `passes`, `intakes`, `defense_s`, `contacts`,
  `pins`, `defended_s`, `congestion`, `dead`, `stalls`, `auto_made`, `first_shot_teleop` (all from the match
  analytics). Each is `_shrink`: weighted team mean shrunk toward the event mean with `SHRINK_K = 3` pseudo-matches
  → `{v, sd, n_eff, event, prior_only}`. `rate_retained_defended` uses k = 1.5 and extra weight
  `min(1, defended_time/20 s)`. Also `hub_state` (shots/made/passes per minute and zone fractions by own-hub state),
  `zones` (share of counted made shots per `shot_zone`), `by_period_made`, `role` (weighted vote of analytics roles).
* **Auto routines** (`_fit_routines`): per team, each clustered-auto use with station, auto made, weight.

---

## 4. EPA (`epa.py`)

Two different things live here; do not mix them up.

### 4.1 Pre-event component prior (`pre_event_prior`)

What we believed about each team before the event, per component, as `[mean, sd]` of schedule-neutral robot FUEL.
It must contain nothing from the event itself (otherwise the event's TBA rows would count twice).

1. **local** (preferred): `fetch_prior_events` lists each team's official events (`event_type` 0–5) of the season
   that ended before this event's start (TBA `/team/<t>/events/<year>/simple`, cached 1 day), downloads their matches
   (cached forever), and fits the same model TBA-only (`fit_event_model(Ep, None, use_video=False)`) to each event with
   ≥ 10 quals. A team's prior is the precision-weighted mean over its earlier events, with an extra
   `age · (0.5)² · tau²` variance per event of age, and a final **between-event drift** `(DRIFT_FRAC · tau)²`,
   `DRIFT_FRAC = 0.5` (week-to-week correlation of true strength ~0.8; fixed a priori, not tuned). Climb pseudo-counts
   at `PRIOR_CLIMB_W = 0.5`. `hyper[c]` = mean `sigma²`, `tau²` and `beta [mean, max(sd, 0.5|b|, 3)]` of the prior
   events.
2. **statbotics** (only for teams without a local prior): Statbotics `team_events.epa.stats.start`, minus a tower
   guess of 3 points, split over components with the local field shares (or `DEFAULT_ALLIANCE` shares), sd
   `0.35 · fuel` (or Statbotics' sd) plus drift.

`event_mean[c]` = mean over teams with a prior. Returns `None` when nobody has a prior; the model then shrinks to the
event mean.

### 4.2 As-of EPA for display

`dashboard.display_epa`: Statbotics when its API answered (`sb_asof`: the last `team_match` post-EPA at least 300 s
before `asof`, else the pre-event start EPA), otherwise **local**: `ComponentEPA`, a Statbotics-style running EPA per
points component (auto = FUEL + auto tower, teleop = transition + shifts + endgame FUEL, endgame = endgame tower),
started from the teams' prior-event values (`local_epa(prior_events=...)`, default auto 10 / teleop 40 / endgame 2)
and updated through this event's matches before `asof` with `K = 0.5 → 1/3` over a team's first 6 matches,
error / 3 per team.

### 4.3 Network and caches

IPv4 only (`_ipv4`), ≥ 0.25 s between requests, 20 s timeout, user agent names FRC 4911. `cached_get` writes
`{fetched, url, data}` JSON files; a fetch failure falls back to the cache. Statbotics responses that are empty `{}`
(its outage mode) are rejected and not cached. Caches: `DATA/cache/statbotics/` (600 s), `DATA/cache/tba_prior/`
(team lists 1 day, event matches forever). `offline=True` reads caches only.

Production state on 2026-10-12: Statbotics returned HTTP 503, so the display EPA is local; the prior is local from
`2026orwil`, `2026wabon`, `2026wasno` and covers 26 teams.

---

## 5. The match simulator (`sim.py`)

`simulate(M, red, blue, n=4000, seed=0, fouls=True, param_uncertainty=True, schedule_effect=True,
force_red_auto=None, raw=False)`. Per simulated match:

1. **Parameter draw** (`_param_draws`): each robot's component means `mu + delta` and `beta` are drawn from the joint
   GLS posterior (Cholesky of the sub-covariance). A team the model never saw gets `mu + N(0, tau²)`. With
   `param_uncertainty=False` the posterior means are used.
2. **Correlated uniforms**: per robot, 4 standard normals correlated by `chol(M.R)` → `Φ` (Gaussian copula).
3. **Auto**: robot auto FUEL = Gamma quantile with the drawn mean and the component's `sigma²` (moment matched:
   shape `mean²/var`, scale `var/mean`, mean floored at `MIN_MEAN = 0.05`); alliance auto = Σ + `h_auto`. More auto
   FUEL wins auto; ties are a coin flip. `force_red_auto=True/False` overrides the winner (counterfactual schedule;
   auto FUEL unchanged).
4. **Teleop**: transition, active and endgame per robot = Gamma with mean `theta + beta · won_auto/3`
   (`schedule_effect=False`: `beta/6`). The active total is split into the two active shifts with
   `first_active_frac`; the inactive shifts get 0. Human-player means are added per period.
5. **Towers**: auto L1 Bernoulli per robot (`climb.team.auto_l1`), at most 2 per alliance count, 15 points each;
   endgame level categorical over L0–L3 → 0/10/20/30.
6. **Fouls**: resampled from the event's empirical foul distribution (`fouls=False` turns this off). Fouls are not
   per team.

Output: per alliance `score`, `score_no_fouls`, `fuel` (each a `dist` = mean, sd, q05–q95), `tower_pts_mean`,
`foul_pts_mean`, `fuel_by_period_mean`, `hp_fuel_mean`, `p_win_auto`; `p_red_win` (ties count ½),
`p_red_win_if_red_wins_auto` / `..._blue_wins_auto` (conditional on the simulated auto winner), `margin`; per robot
expected FUEL by period, if won / lost auto, tower points. `raw=True` adds the per-sim arrays.

`simulate_schedule(M, matches, n=2000)` draws each team's strength **once per simulated event**, so a strong draw
carries through all of a team's matches (needed for rank distributions).

**Not modelled**: defense and congestion between robots (with the 11 v2 matches available when this was decided, the
defence slope was 0.14 ± 0.54 FUEL per opponent-defending-second, n = 20; congestion p = 0.42, n = 29); an explicit
dead-robot mixture (dead matches live inside the fitted variance); per-team fouls.

---

## 6. Ranking points and rank projection (`rank.py`)

2026 REBUILT RP per alliance per qual (checked against all 116 Sammamish alliance-matches: TBA `rp` =
3·win + 1·tie + bonuses, no exceptions):

| RP | rule | evidence |
|---|---|---|
| win / tie | 3 / 1 | |
| **ENERGIZED** | ≥ 100 FUEL counted in the hub | Sammamish: min achieved 101, max not achieved 99 |
| **SUPERCHARGED** | ≥ 360 FUEL counted | min achieved 373, max not 349 |
| **TRAVERSAL** | ≥ 50 TOWER points | never achieved at Sammamish: threshold from the manual, unverified |

`BONUS = {"energized": ("fuel", 100), "supercharged": ("fuel", 360), "traversal": ("tower", 50)}`. A disqualified
team gets 0 RP for that match (and 0 in its tiebreak sums); surrogate appearances do not count. Ranking score =
RP / matches. Tiebreakers (TBA `sort_order_info`, reproduces the final Sammamish table exactly): 1 average match
points without fouls, 2 average auto FUEL, 3 average tower points, then random (`current_ranks` uses team number
instead of random so the "now" table is deterministic).

`project(M, E, quals_played, quals_left, n=2000)`: played quals use TBA's actual values (`actual_table`); remaining
quals are simulated with `simulate_schedule`; RP and tiebreak sums accumulate per sim; ranks with a random final
tiebreak. Per team: `rank_mean`, `rank_p10`, `rank_p90`, `p_top8`, `p_top4`, `p_first`, `rp_mean` (expected final
total RP), `rs_mean`, `rp_now`, `matches_total`, `matches_played`; per remaining match `bonus_probs` (P(win), P(each
bonus), E[RP]).

---

## 7. Backtests and measured accuracy

### 7.1 Baselines (`baselines.py`)

* **OPR**: ridge least squares of alliance score on team indicators, shrunk toward (mean score)/3 with
  `OPR_ALPHA = 1` pseudo-match per team. `FALLBACK_SCORE = 100` before any data.
* **EPA**: running rating, `pred = Σ EPA`, each team moves `K (actual − pred)/3`, `K = 0.5 → 1/3` over 6 matches.
* **Prequential win probability**: `Φ(margin / s)`, `s` = RMS of this method's own out-of-sample margin errors so
  far, with a prior of 80 points worth 4 matches (`S_PRIOR`, `S_PRIOR_N`). Nothing tuned on the backtest matches.

### 7.2 `backtest.py` (offline research CLI)

```
python -m live.strategy.backtest DATA_DIR [--event EV] [--out bt.json] [--sims 4000] [--recency H] [--epa-prior none|cache|fetch] [-q]
```

Every played qual is predicted from matches played before it (TBA `actual_time` order). Methods: `coin`, `opr`, `epa`,
`sim_tba` (model, TBA only), `sim_full` (+ video), `full_preq` (sim_full's mean margin with the prequential normal),
and with an EPA prior: `epa_pre` (component EPA from the earlier events), `sim_epa_tba`, `sim_epa` (= the dashboard
model). Metrics: Brier, log loss (p clipped 0.01–0.99), accuracy (ties excluded), alliance-score MAE, margin MAE.
Subsets: all quals, warm (all six teams ≥ 2 prior matches), early, first 20, ≥ 1 / ≥ 4 teams with trusted prior
video. Paired bootstrap (4000) 90 % CIs of Brier differences, calibration bins on the warm subset, and an auto-winner
check. ~10 s per run with cached inputs.

### 7.3 As-of backtest and the honest footer (`matchday.backtest_rows`, `backtest_asof`)

The dashboard footer uses only this. `backtest_rows(ctx, sims=1500)` predicts every played qual from the matches
before it three ways: blend (`p_blend`, model with video + EPA prior), no-video (`p_novideo`), and the local as-of
EPA (`p_epa`, prequential normal). Rows are cached in `DATA/cache/strategy/backtest_<ev>.json`, keyed per match by a
SHA-1 of: the prior matches' keys, a digest of each prior match's video blocks (`v2`, `recon`, `cred`, `unc`,
`hp_raw`, `tba`, `coverage`, `y`, `usable` per component), the model's `VIDEO`, `TREND`, `SHRINK_MULT` and the sim
count — so a re-track or a setting change re-fits exactly the affected rows. A row is also refreshed when the
match's post time changes.

`backtest_asof(rows, asof, min_n=8)` keeps quals whose result was **posted before `asof`** and reports `n`,
`through`, `brier {blend, no_video, epa, coin: 0.25}`, `accuracy {blend, epa}`, `blend_minus_epa` and
`video_increment` (bootstrap mean + 90 % CI of the Brier difference, 2000 resamples), `n_with_video`, and
`auto_bias_recent` = mean (actual − predicted) alliance auto FUEL over the last 10 posted quals.

The old constant 58-match reference (`dashboard.BACKTEST_REF`) is still written as `model.backtest` but not shown.

### 7.4 Measured numbers (2026wasam, 58 quals)

Live production footer, `dashboard.json` generated 2026-10-12 18:58 PDT, all 74 matches v2-tracked:

| metric | blend | no video | EPA (local, as-of) | coin |
|---|---|---|---|---|
| Brier | **.1185** | .1333 | .1474 | .25 |
| accuracy | .81 | – | .793 | |
| blend − EPA Brier | −.0288, 90 % CI [−.0507, −.0087] | | | |
| video increment (blend − no video) | −.0148, 90 % CI [−.0356, +.0040] | | | |
| recent auto bias (actual − predicted, Q49–Q58, 20 alliances) | +9.7 FUEL | | | |

History of the measurements (from the READMEs and `docs/STRATEGIST_REVIEW.md`):

| when | finding |
|---|---|
| phase 2 (EPA prior added) | Brier `sim_full` .159 → `sim_epa` .118 (58 quals); warm 48: .155 → .123; early 10: .176 → .095. Video on top of the prior: −.014, 90 % CI [−.029, −.000]. Placebo with permuted robot identity: +.001 / +.002 / −.009. `BACKTEST_REF`: sim_full .1181, without prior .1587, epa_pre .1474, epa .189, OPR .1974. |
| strategist review round 1 (11 verified quals) | live replay 58 quals: blend .119 vs fair EPA .147, gap −.028 [−.048, −.009]; blind 09:00 → Q47–58: .140 vs .208. Rank projection 27/29 inside p10–p90; top-8 7/8. Video −.014 (n.s.) and only as positive evidence. |
| round 2 (43 verified day-1 quals) | more video made it **worse**: blind 09:00 .140 → .157; live .119 → .125; no-video .133; EPA .147; video increment −.008 [−.033, +.016]. Cause: under- and over-credited alliance results used as true counts. |
| round 3 (`VIDEO` = credited + floors, current) | table below |

Round 3 as-of backtest (Brier / score MAE; `.work/agentM/video_bt.py`):

| subset | no video | old (soft split) | **credited + floors (default)** | Δ default − old, 90 % CI |
|---|---|---|---|---|
| live quals (58) | .1345 / 53.5 | .1261 / 48.8 | **.1224 / 47.6** | −.0037 [−.0127, +.0047] |
| live playoffs (16) | .1907 / 59.3 | .1793 / 58.5 | **.1772 / 58.2** | −.0021 [−.0145, +.0089] |
| blind 09:00 → Q47–58 | .1910 / 49.6 | .1523 / 44.1 | **.1418 / 42.4** | −.0105 [−.0358, +.0132] |
| snapshots (728) | .1327 / 55.5 | .1223 / 52.0 | **.1182 / 51.3** | −.0041 [−.0106, +.0023] |

The default beats no-video on every subset (score MAE significantly: live −5.9 [−8.2, −3.7]). Q51 at 09:00: old 59 %,
now 44 % (actual: lost 168–314; no-video 10 %). Tried and rejected: 20 % gate without credited counts, contrast-only
split, credited without floors, extra noise 0.3/0.6.

**Auto under-prediction** (2026-10-11): the model under-predicts alliance auto FUEL by about 4.9 (Q3–46, n = 88
alliances) and 10.3 (Q47–58, n = 24); the README prints these as negative "actual − predicted", see item 12 below.
Autos rose over the event (alliance mean 37.5 day 1 → 47.8 day 2): drift, not a shrinkage bug. The dashboard shows TBA auto history and a caution when `|auto_bias_recent| ≥ 5`.

**Time trend experiment** (not adopted; `TREND = frozenset({"auto"})` would turn it on and change the backtest cache
key): fixes most of the auto bias (live +5.8 → +1.0) but no Brier gain (live quals +.0005 [−.0024, +.0036]; blind
09:00 −.0044 [−.0132, +.0029]; snapshots −.0003) and overshoots in playoffs (+3.8 → −6.2). Halving auto shrinkage
(`SHRINK_MULT = {"auto": 2}`) was slightly worse (+.0024).

---

## 8. Match previews (`preview.py`)

```
python -m live.strategy.preview DATA_DIR qm15 [--event EV] [--asof prior|all] [--sims 8000] [--epa-prior none|cache|fetch] [--out FILE]
```

`build_preview(E, key, asof="prior", sims=8000, seed=0, epa_prior=None, M=None)`: `asof=prior` fits on matches
before this one (a genuine pre-match view; for an unplayed match = everything played). Runs the simulation plus two
counterfactual simulations (n/2 each) with the auto winner forced. Schema `cyber-scout/strategy-preview@1`: `data`
(as-of, model hyperparameters per component, `not_modelled`), `prediction` (`p_red_win`, `margin`, counterfactual
win probabilities, `schedule_value_fuel` = Σ beta over trans/active/endgame with its sd), `alliances`, six `robots`
(role, expected FUEL by period and if won/lost auto, `rating` per component with `mean = E[max(θ,0)]` and
`linear_mean`, hub state, top 3 zones, auto with routines, climb, reliability, defended, defense, shooting
features, data quality), `key_factors`, and `actual` for played matches. Full field list:
`live/strategy/README.md` (still accurate).

Key factors (sorted by weight): `outlook` (toss-up when the favourite is < 60 %), `auto_battle`, `top_scorer` per
alliance (with its top zone when ≥ 5 weighted made shots), `defense_target` (retained shot rate < 0.75 with
≥ 2 effective defended matches and ≥ 20 expected FUEL), `defense_resistant` (retained > 1.0), `reliability` (shrunk
dead rate ≥ 0.15), `climb` (P(climb) or P(auto L1) ≥ 0.3), `inactive_behaviour` (≥ 5 passes/min while inactive),
`data_quality` (teams with video weight < 0.5), `uncertainty` (widest active-shift rating).

---

## 9. The dashboard (`dashboard.py`, `matchday.py`)

### 9.1 Commands and files

```
python -m live.strategy.dashboard DATA_DIR [EV] [--team frc4911] [--asof TS] [--out FILE]                 # one JSON to stdout/file
python -m live.strategy.dashboard DATA_DIR [EV] --site [--snapshots [--only-missing]] [--previews none|team|all]
        [--teams all|4911,...] [--sims-rank 2000] [--sims-match 6000] [--offline] [--no-statbotics] [--out-dir D] [-v]
```

`--site` writes `site/api/<ev>/dashboard.json` and `dashboard.<team>.json` (same content, perspective `--team`),
`preview/<match>.json` for every match of the team (`--previews team`, the default with `--site`), and
`dashboard.<t>.json` for extra perspectives with `--teams`. `--snapshots` writes `dash/<ts>.json` every 30 min
(`SNAP_STEP = 1800`) of each event day (days = runs of matches separated by > 6 h, from the first match rounded down
to the last posted result + one step, never in the future) and `dash/index.json = [{ts, label "Sat 11:30",
quals_played}]`. `--only-missing` skips existing snapshot files except the latest. In snapshots every `preview_url`
is null (the preview files on disk reflect current data). Worker entry: `dashboard.site_build(DATA, ev)` (unused by
the worker, which runs the CLI as a subprocess). At 2026wasam: 36 snapshots, Sat 11:00 onward.

### 9.2 Build steps (`Context`, `build`)

`Context` (once per process, reused by all snapshots): `load_event`, Statbotics fetch, `pre_event_prior`, the prior
events' matches (offline), nicknames, `load_coverage_matches` (compact identity info of every site match), the
defense library, and lazily the as-of backtest rows.

`build(ctx, team, asof=None, sims_rank=2000, sims_match=6000)`:

1. `asof_view(E, asof)`: only results **posted** before `asof` count as played (`post_result_time`, else match time
   + `POST_LAG` 300 s); video exists only `VIDEO_LAG` = 600 s after posting; a playoff match appears only once all
   quals are posted and `asof ≥ match time − PLAYOFF_VISIBLE` (1500 s).
2. Fit the model (EPA prior + TBA + video), local display EPA, rank projection (`rank.project`, 2000 sims).
3. Ratings for every team (§9.3), coverage as of `asof` (§11.2).
4. A plan for **every** remaining match of the team (`plans[]`, first one with `sims_match`, the rest with
   `max(2000, sims_match/2)`); `next_match = plans[0]`.
5. Schedule and trend: for each played match of ours, the pre-match win probability comes from a fit on matches before
   it (2000 sims) and the trend point from a fit including it.
6. Levers, model block.

### 9.3 Top-level JSON (`cyber-scout/dashboard@1`)

| key | content |
|---|---|
| `event` | key, names, first/last match time, dates, timezone, `now` (asof or wall time), `asof`, `quals_played`, `quals_total`, `playoffs` |
| `team` | perspective |
| `ratings[]` | every team sorted by current rank: `rank`, `record`, `rp`, `matches_played`, `epa {total, auto, teleop, endgame, source statbotics\|local}`, `blend {total, auto, teleop, endgame, sd}`, `video_weight`, `weights {prior, tba, video}`, `prior_source epa-local\|epa-statbotics\|event-mean`, `prior_events`, `video_matches`, `id_conf` (kept, not shown), `coverage {n, of, keys, last, tier, label}`, `projection`, `disagree {diff = blend − EPA, flag = |diff| ≥ max(15, 1.5 sd)}` |
| `next_match`, `plans[]` | plans (§9.4) |
| `our_team` | `blend`, `projection`, `levers[]` (§9.6), `trend[] {match, short, time, blend_total, blend_sd, fuel_made (verified matches only), alliance_fuel, won}` |
| `schedule[]` | our matches: `win_prob` (ours; played = genuine pre-match prediction; unplayed with a plan = the plan's number), `result {red, blue, won, tie, rp, bonus, rp_them, bonus_them}`, `bonus_probs`, `bonus_probs_them`, `preview_url`, `analyze_url "#/<ev>/a/<key>"` |
| `model` | `backtest` (constant reference), `backtest_asof` (footer, §7.3), `epa_source {display, prior, prior_events, prior_teams, statbotics_error}`, `n_matches_used`, `video_matches_used`, `sims`, `notes[]`, `build_s` |

### 9.4 A plan (`next_match_plan`)

Same shape for `next_match` and each `plans[]` entry:

* `key`, `label`, `short`, `time`, `alliance` (ours), `partners`, `opponents`, `teams`, `as_of {ts, asof,
  quals_played, last_result}`, `preview_url`.
* `prediction` (our perspective): `win_prob`, `score {red, blue: {mean, lo = p10, hi = p90}}` (keyed by colour),
  `p_win_auto`, `win_prob_if_auto_won` / `_lost` (counterfactual sims), `margin_mean` (ours − theirs),
  `schedule_value_fuel`, `n_sims`, and from the queue card `win_prob_epa`, `models_disagree`, `rp {us, them}`.
* `plan.defend[]` for each opponent, sorted by gain: `when` = their most likely active shifts (= our inactive
  shifts; 2+4 if they are more likely to win auto), `when_alt`, `p_when`, `where {zone, share, xy (field m, TBA
  frame), zone_ours}` when ≥ 3 weighted made shots, `active_fuel` = their expected FUEL in shifts 1–4,
  `rate_retained` and `basis` from `matchday.retention_for` (§9.5), `expected_gain_fuel = active_fuel ×
  (1 − retained) × ENGAGE` with `ENGAGE = 0.7` (share of the target's active time a dedicated defender affects,
  a priori), `top_scorer`, `why`.
* `plan.suggested_defender` (`matchday.pick_defender`): the robot on our alliance that gives up the least feeding
  while our hub is inactive: `feed_cost = passes_per_min(inactive, verified video) × 50/60 min × FEED_CONV 0.5`
  (unknown → the alliance median); ties broken by lower expected FUEL. Without any passing data: the lowest expected
  scorer. Fields `team`, `why`, `basis`, `candidates[] {team, passes_per_min, feed_cost_fuel, expected_fuel,
  defense_s}`.
* `plan.auto`: expected auto FUEL ours/theirs, `p_win_auto`, `note`, `matchups[]` per robot with its most likely
  clustered routine (conditioned on the start station when that station was seen).
* `plan.endgame[]`: per robot `p_climb {L1, L2, L3}`, `p_any`, `auto_l1`, `n_obs`, `expected_pts`.
* `plan.key_factors`: preview factors from our side (`_our_factors` drops `uncertainty`, and defense /
  inactive-behaviour advice about our own robots).
* `queue`: the queue card (§9.5).

### 9.5 The queue card (`matchday.queue_card`)

Six lines from our alliance's point of view, built from the plan's own simulation (`raw` arrays):

| field | content |
|---|---|
| `mode` | `"goal"` when `win_prob < 0.10` or `> 0.90` (`GOAL_LO/HI`), else `"win"` |
| `lever` | goal mode only: the bonus RP (among those shown) whose P(us) is in [0.03, 0.97] and closest to 0.5 (default ENERGIZED), with `needed` (100 / 360 FUEL or 50 tower pts), `unit`, `expected`, `lo`, `hi` (p10/p90 counted FUEL), `p`, `text` |
| `win_prob`, `win_prob_epa`, `models_disagree` | blend and EPA win probability; disagree = the **displayed** (5 %-step) values differ by ≥ 15 points (`DISAGREE_PTS`) |
| `auto` | `p_win_auto`, `our_expected`, `their_expected`, `if_won {our_active [2,4], defend_in [1,3], win_prob}`, `if_lost {...}`, `history {team: last 4 TBA alliance autos}`, `history_alliance_avg {us, them}`, `recent_bias` (= `backtest_asof.auto_bias_recent`), `caution` (when \|bias\| ≥ 5), `text` |
| `defend` | top `plan.defend` target: `target`, `defender` (suggested), `when`, `when_alt`, `where {zone_ours, share, field [u, v]}` (u 0 = our left → 1 our right, v 0 = our wall → 1 their wall; centroid of the target's verified made shots in its top zone), `denied_fuel`, `basis measured\|assumption`, `coverage` label, `alt` (their top scorer when it is not the pick), `skip_note` (< 2 FUEL denied), `goal_note` (goal mode: keep scoring for the bonus), `text` |
| `defenders` | opponents' defense history from verified video: `teams[] {team, n_video, n_defended (matches with ≥ DEF_MIN_S = 10 s of defense, or the library's defended matches restricted to verified ones), matches, avg_defense_s, tier, label}`, `likely` (defended in ≥ half their verified matches), `effect` (event-wide library effect), `text`, and when someone is likely: `if_defended {defender, target (our top active scorer), fuel_lost = active FUEL × (1 − retained) × 0.7, win_prob, basis}` + `ignores` text (the simulator has no defense) |
| `rp` | `us`, `them` = `{win, energized, supercharged, traversal, rp_mean, fuel {mean, lo, hi}}` (bonus odds from the plan's simulated FUEL / tower points; `us.win` = the plan's win %), `shown`, `hidden` (TRAVERSAL while no alliance at the event has earned it, as of `now`), `hidden_why` |
| `fouls` | per team the average foul points its alliances gave away (`teams[] {team, side, avg_given, n}`), `event_avg`, `flag` (n ≥ 3 and avg ≥ max(1.5 × event, event + 5)), `text`, `note` (TBA fouls are alliance-level) |

**Defense effect source** (`retention_for`, `library_effect`, `event_extras`): a target's retention is
"measured" when the defense library's per-team placebo-controlled estimate has `significant` **or** its 90 % upper
bound `hi < 1.0`; otherwise the event-wide library effect (`global.ratio / global.placebo.pooled_ratio`, clamped to
0.3–1.0) labelled an assumption; with no library at all, the weighted mean `rate_retained` of verified robot-matches
defended ≥ 5 s (needs ≥ 3), then 0.75 (`GENERIC_RETAINED`). The library is built from all tracked matches, so in a
replay its estimates are not strictly as-of.

Display rounding (`matchday.disp`, mirrors `dash.js`): probabilities in 5 % steps, "<5%" below 0.05 and ">95%"
above 0.95. Plan tabs are linkable as `#/<ev>/dash/<ts|live>/<q51>`.

### 9.6 Levers (`levers`)

Our value vs the event for `cycle_s` (lower better), `accuracy`, `fire_rate`, `volley_size`, `on_move_rate`
(descriptive video features, shrunk; `null` when prior-only), `auto_fuel` and `teleop_fuel` (model), and `climb`
(P(any endgame climb); dropped when < 5 % of the event's robot-matches end on the tower, `CLIMB_MATTERS`). Each:
`ours`, `event_best` (+ team), `event_median`, `gap`, `gap_z` = gap / max(IQR, sd, `FLOOR[unit]`), `gap_pts` (rough
points per match from matching the best: FUEL difference; climb × 10; accuracy / volley size scale our FUEL
proportionally; cycle time inversely), `gap_pts_median`, `percentile`, `n_teams`, `n_eff`, `basis`, `gap_rank`
(1 = biggest, by `gap_pts` where computable, then `gap_z`; null when we have no value).

---

## 10. Rankings, district points and season goals (`district.py`, `goals.py`)

```
python -m live.strategy.goals DATA_DIR EV [--team frc4911] [--goal DCMP|Worlds|rank:N] [--asof TS] [--sims 800] [--out-dir D] [--offline]
python -m live.strategy.goals DATA_DIR EV --replay        # DCMP odds at points of the season vs who really qualified
python -m live.strategy.goals DATA_DIR EV --goal-only     # print just the goal block
python -m live.strategy.goals DATA_DIR EV --selection-snapshots [--all] [--asof TS]
```

Worker entry: `goals.site_build(DATA, ev, team=DASH_TEAM)` writes `rankings.json`, `schedule.json`,
`selection.json` (and with `asof`, `selection/<ts>.json` only). Goal from `goal=` / env `DASH_GOAL` (default `DCMP`).
Events outside a district get rankings and schedule without the district / goal / selection parts.

### 10.1 District-points rules (`district.py`)

| part | rule |
|---|---|
| qualification | `ceil(erfinv((N − 2R + 2)/(1.07 N)) · 10/erfinv(1/1.07) + 12)`, N teams, R final rank |
| alliance selection | alliance n: captain and first pick `17 − n`, second pick `n`, backup 0 |
| playoffs (8-alliance double elim) | winner 30, finalist 20, TBA "Round 5" 13, "Round 4" 7, earlier 0; partial participation (backups) from the empirical table `PARTIAL = {30: (24, 7), 20: (15, 5), 13: (13, 7), 7: (7, 4)}` (more than half the alliance's playoff matches → first value) |
| awards | Impact 10, Engineering Inspiration 8, Rookie All-Star 8, other judged 5; no points for TBA award types in `NO_POINT_AWARDS` (winner, finalist, Woodie Flowers, Dean's List, Volunteer, Founders, Bart Kamen, Make It Loud, highest rookie seed, #1 seed, Incredible Play, People's Choice) |
| DCMP | every component × 3 |
| season | first **two** district events by date + DCMP + rookie bonus + adjustments (TBA district rankings) |
| DCMP bids | Impact / EI / RAS winners at district events pre-qualify; the rest of `capacity` goes by points. Capacity is not in TBA: `DCMP_CAPACITY` env, else `CAPACITY = {"2026pnw": 51}` (observed), else 64. `WORLDS_SLOTS = {"2026pnw": 14}` (assumption), else 15 |

Verified exact against TBA on all 309 team-events of 2026pnw and 126/126 season totals (`district.validate`,
`.work/goals/validate_district.py`).

`load_district(data_dir, key, asof=None, offline=False)`: TBA via `epa.cached_get` into `DATA/cache/tba_district/`
(district event list 1 day; matches / alliances / awards / district points of a live event 120 s; finished events
— two days past the end — forever; district rankings 1 h). With `asof`, results posted after it are hidden
(`_hide`) and each event is `done` / `live` / `future` as of then.

### 10.2 The season Monte Carlo (`goals.Season`)

Built once (`n = 800` sims by default), then queried for any team / goal.

* **Strength** `theta` per team = points it contributes to an alliance. Default: a running EPA over every visible
  district match (`district_strengths`: start at mean alliance score / 3, `K 0.5 → 1/3`, sd
  `THETA_SD0 = 11 / sqrt(1 + n/5)`); `sigma` = RMS alliance residual of the last two thirds of matches (fallback 38;
  73.7 in production). When `asof` is None and Statbotics answers for > 20 teams, Statbotics EPAs replace it. For the
  focal event, `build_event_json` passes the dashboard blend (`ratings`) and `Season` adds the event's mean foul
  points / 3 (see the scale warning in **Docs vs code**). One draw per team per simulated season.
* **Remaining events** (`sim_event`): remaining quals from the posted schedule (10 random rounds if none is posted
  and nothing was played); alliance score = Σ theta + N(0, sigma); RP = 3 win / 1 tie + Bernoulli bonus RPs from
  logistic fits of P(bonus | alliance score) on the district's played quals (`fit_bonus`); ranking by average RP;
  qual points by the formula; draft (captains = top 8 by simulated rank; first round picks the best remaining
  rating in seed order, second round in reverse seed order; no declines; TBA alliances used when known);
  double-elimination bracket `BRACKET` with set win probability `Φ(ΔS/(σ√2))`, finals best of 3, known playoff
  results forced; awards drawn at random teams (Impact, EI pre-qualify; RAS with the district's observed rate, to
  first-year rookies only; `k5` five-point awards = mean count at finished events, default 12). DCMP: the same
  without pre-qualifying awards, everything × 3.
* **DCMP outcome** (`dcmp_outcome`): pre-qualifiers plus the top `capacity − prequalified` by pre-DCMP points among
  teams that attended; cutoff = points of the last points-qualifier.
* **Worlds** (`worlds`): surrogate DCMP with performance = theta + N(0, 14²), points × 3 by finish; top
  `worlds_slots` of the final totals.
* `rank:N`: district rank ≤ N of pre-DCMP points.

**Award conditioning.** `p_goal` (the headline) = P(goal | we win no award at our remaining events), computed over
the ≥ 20 sims where that holds, unless `DASH_AWARD_CONTENDER=1` (then with random awards). The block also reports
`p_with_awards`, `p_if_impact_or_ei`, `p_we_win_award`. In `rankings.json`, our row's `district.p_dcmp` is the
headline; every other team's is the with-awards number (better calibrated across the event: 09:00 replay Brier .133
with vs .147 without).

### 10.3 `rankings.json` (`cyber-scout/goals@1`, kind `rankings`)

`teams[]` sorted by current rank: `rank`, `rp`, `matches_played`, `record`, `rs`, `projection` (from a separate
`rank.project` run with 1500 sims), `district {current, projected, p10, p90, p_dcmp, p_dcmp_with_awards}`. `goal`
(our team): `p_goal`, `p_goal_ci` (±1.64 binomial se), `awards {...}`, `current_points`, `projected_points`,
`cutoff {now, projected {mean, p10, p50, p90}}`, `points_needed {p50, p80, p95: {final_points, still_needed}}`
(percentile of the cutoff + 1), `points_still_needed`, `dcmp {capacity, points_slots_mean, award_prequalified_now,
is_prequalified}`, `events[]` (done: points; future: projected mean / p10 / p90 / rank_mean), `value`, `model {sims,
theta (source text), sigma_alliance, worlds_slots, theta_team}`, and `p_dcmp` for a Worlds goal. The full JSON
contract is in `live/strategy/README.md`.

`value` (`value_of_matches`): for each remaining qual of ours at the focal event, the event is re-simulated with the
result forced (the sampled scores are swapped so our side has the higher one; common random numbers) →
`p_if_win`, `p_if_loss`, `delta`. Per remaining event, P(goal) by points quartile and by rank band.

### 10.4 `schedule.json` (kind `schedule`)

Every match: `key`, `label`, `short`, `comp_level`, `time`, `played`, `red`, `blue`, `us`, `analyze_url`; played →
`result {red, blue, winner, rp, bonus}`; unplayed → `pred {p_red, score, rp}` (1500 sims). Our upcoming matches also
get `breakdown` (`period_breakdown`, 3000 sims): `win_prob`, `p_win_auto`, `score {ours, theirs, margin,
margin_sd}`, `fouls`, `periods[]` per period (`ours`, `theirs`, `delta`, `tower` inside auto / endgame,
`p_ours_ahead`, `our_active_p`, `their_active_p`, values if we win / lose auto), and `summary` ("where you will be
beaten", `narrative`).

### 10.5 Replay validation (`goals.replay`)

`--replay` computes DCMP odds at three points around each district event (1 h before its first match, mid-event,
4 h after) against the actual DCMP team list: Brier vs base rate, log loss, AUC, accuracy at 0.5, expected vs actual
qualifiers. No result of this is recorded in the repo docs.

---

## 11. Selection and scouting truth

### 11.1 Trust rule (`live/coverage.py`)

A team's robot-level video number may only come from **verified** matches:

* the match was tracked by the v2 tracker (`identity.method` starts with `ocr`; v1 tracks are chance-level and feed
  nothing except the replay), **and**
* that team's track has `team_conf ≥ VERIFIED_CONF = 0.6`.

Per-frame quarantine (frames / shots with identity conf < 0.5 uncredited, `enrich.quarantine`) is applied before this,
by the worker. Display tiers (`tier(n)`, `SHOW_N = 3`): **fact** n ≥ 3, **grey** n = 1–2 (labelled
"n=2 (Q12, Q17)"), **none** n = 0 (hidden, "no verified video"). Coverage counts quals only.

`coverage.reconcile(match)` per alliance of a verified match: `credited` = made robot shots whose `team` is the
verified team on that track; `human` = HP made shots from the shot reconcile, capped at `tba − credited`;
`ratio = (credited + human_capped)/tba`.

| `gate` | condition | effect |
|---|---|---|
| `ok` | otherwise | the alliance result feeds team FUEL stats |
| `under` | ratio < 1 − `RECON_GATE` (> 20 % of the alliance's FUEL uncredited) | dropped from FUEL stats |
| `over` | credited > (1 + `RECON_GATE`) × TBA | dropped |
| `no_tba` | no TBA count | dropped |

`RECON_GATE` env, default 0.2. Also returned: `lower_bound` (unattributed > gate), `over` (credited > 103 % of TBA)
and `scale = tba/credited` for over-credited results.

### 11.2 `scout.json` (`cyber-scout/scout@1`, `scouting.build_truth`)

The one canonical per-team source for the Teams list, team page, spec sheets, Compare, Picklist and Selection pitch
evidence. Reads verified matches (`scouting.load_matches` skips v1), `dashboard.json`, `dash/<ts>.json`, TBA matches.

* `verified_matches`, `last_verified`, `quals_played`, `policy {verified_conf 0.6, show_n 3, shrink_matches 1,
  acc_prior_shots 20, lower_bound_unattributed 0.2}`, `event_stats {made_pm_mean, acc, auto_made_mean,
  cycle_median, tower_pts_max, climb_matters (tower_pts_max > 10), any_climb}`, `dash_generated`, `asof`.
* `teams[]`: `coverage {n, keys, last, of, tier, label}`; `rating` from `dashboard.json` (`blend`, `sd`, components,
  `epa`, `epa_source`, `opr` (event.json; as-of: least-squares OPR of score − fouls), `video_weight`, `record`,
  `p_top8`); `climb` from TBA per robot over every played qual (`n`, `levels`, `rate`, `pts_mean`, `auto_l1_rate`,
  `keys`); `flags[]`; `trend`; `video`.
* `video` metrics (each `{v (shrunk), raw, n, tier}`):
  * `made_pm`: mean TBA-scaled credited made FUEL over `ok`-gated alliance results, shrunk toward the event mean with
    1 pseudo-match; `n` = reconciled count; `gates` tally; `per_match [{m, made, scaled, gate, un}]`.
  * `acc`: made / (made + missed) shrunk with 20 pseudo-shots of the event accuracy.
  * `auto_made` (null when "auto not tracked"), `cycle_s`, `fire`, `hopper` (volley p90), `pts_active_min`,
    `speed_p95`, `trench`, `on_move`, `def_played`, `def_received`, `def_matches`, `def_secs`.
  * `dead`: share of verified matches with a dead stretch ≥ 30 s (`DEAD_MIN_S`) **and** the alliance scoring < 85 %
    (`DEAD_TBA_FRAC`) of the sum of its teams' blends; shown only at n ≥ 3.
* `flags`: `auto_not_tracked` (model auto FUEL ≥ 12 and video ≤ 30 % of it), `video_higher` (≥ 2 reconciled
  matches, raw ≥ 25 and ≥ 1.15 × EPA FUEL + 8), `video_lower` (≥ 3 reconciled, not over-credited, raw ≤ 0.6 × EPA
  FUEL − 10; the text warns that low video counts were attribution loss every time at 2026wasam), `no_reconciled`.
  Positive evidence comes first: the review found every "video says worse" call wrong.
* `trend.tba`: per qual, alliance score − fouls drawn − partners' blends; ≥ 6 matches → last 4 vs earlier with a
  90 % CI, `verdict up|down|flat`. `trend.blend`: `[ts, blend, sd, epa]` from the dashboard snapshots (≤ 14 points,
  shown at ≥ 3).

Replay: `python -m live.scouting DATA EV --snapshots` writes `scout/<ts>.json` for every dashboard snapshot plus
`scout/index.json {event, times}`; `--asof TS` writes one. `build_asof` uses verified matches with TBA time ≤ ts, the
latest dashboard snapshot ≤ ts, as-of OPR, and as-of defense played/received (spec sheets and the defense library
are not snapshotted).

### 11.3 Spec sheets (`scouting.spec_sheets` → `spec/<team>.json`, `spec/index.json`)

Built from verified tracks (`Trk.team` is None unless the team is verified in that match). A robot frame counts when
observed, identity conf ≥ `ID_CONF_MIN` (0.5) and the feed is live; gaps ≤ 1.5 s between such frames are
interpolated. Positions are 5-point smoothed; speed over 4 frames; > 6 m/s is a glitch; < 0.08 m/s is 0.

| block | definitions |
|---|---|
| `drive` | top speed (p99), p95 / mean of moving (> 0.3 m/s), accel p90 / p99, % moving, distance per match |
| `scoring` | FUEL counts only from `ok`-gated alliance results (`n_fuel`): att / made / missed / passes, accuracy + Wilson 90 % CI (≥ 5 shots), made/att per match, points per active-hub minute, attempts per teleop minute; volleys = launches ≤ 1.2 s apart (`VOLLEY_GAP_S`); fire rate = median (n−1)/duration of volleys ≥ 3; volley mean / p90 / max, `hopper_est` = p90 with ≥ 4 volleys; cycle = median gap between teleop volley starts (< 45 s) + IQR; on-the-move share (speed > 0.5 m/s at launch); median shot distance to the hub; by period |
| `auto` | per-match rows, made / att averages over `ok` matches, `climb_rate` (overwritten by TBA auto L1 rate), routines from the team file |
| `climb` | overwritten from TBA (`source: "TBA"`) |
| `lanes`, `crossings` | hub-line crossings (±0.6 m hysteresis, completed within 2.5 s): lane = nearest trench/bump band within 0.6 m else open; driver side; `trench_share`, `left_share`, `trench_capable` |
| `shots` | `[x_cm, y_cm, m\|x\|u, period code, match, moving]` canonical frame |
| `paths` | shooting paths: the drive segment into each volley, starting at the latest of: previous volley end, last intake, neutral-zone exit, 10 s cap, last tracking hole (≥ 0.8 s); 5 Hz canonical cm, with approach lane |
| `approach`, `first_shot` | how volleys are approached; whether teleop volleys start moving and lead time |
| `defense` | received / played pools, baseline, advice (from the library) |
| `coverage`, `truth` | from `scout.json` (`video`, `flags`, `rating`, `trend`) |

`spec/index.json` rows: `made_pm`, `acc`, `pts_min`, `fire`, `hopper`, `cycle`, `speed`, `trench`, `left`,
`on_move`, `climb`, `def_recv`, `def_play`, `cov`, `made_v` (canonical made/match from `scout.json`), `flags`.

Team files `t/<team>.json` are rewritten from verified matches only by `sitev2.verified_team_files` (called first in
`scouting.site_build_extra`): `analytics`, `shots_map`, `intakes_map`, `heat`, `autos`, `routines`, `q`, `id_conf`;
`matches[]` keeps every tracked match with `verified: bool`; `verified_only: true`, `verified_n`.

### 11.4 Selection (`selection.py` → `selection.json`, `cyber-scout/selection@1`)

Built inside `goals.build_event_json` from the same `Season` base run as the DCMP odds, so every number is
conditional on one set of simulated seasons.

* `status`: `projected`, `fixed` (TBA alliances known; draft fixed, known playoff results forced), `done` (event
  finished: only `actual[]` from TBA alliances with district points earned, and `actual_us {team, event_points,
  seed, role, p_dcmp}`), `unavailable`.
* `teams[]`: `rank_mean`, `rank_p10/p90`, `p_captain`, `p_r1`, `p_r2`, `p_selected`, `rating` / `rating_sd`
  (`3 × theta` mean / sd), `p_dcmp`, `likely_captain`.
* `seeds[]`: top-3 likely captains and first picks per seed, playoff outcome distribution (win 30, finalist 20,
  top 4 (13), top 6 (7), out early).
* `us`: `rank_mean`, `p_captain`, `p_r1`, `p_r2`, `p_unpicked`, `p_dcmp` (no awards, as the headline),
  `p_dcmp_with_awards`; `outcomes[]` per (seed, role) with share, n, event points, `p_dcmp` (only from ≥ 15 sims,
  `MIN_N`, conditioned on no award), playoffs, `needs` (the grid cell); `by_role`; `picked_by[]` (captains that pick
  us, most likely slots).
* `us.need`: `final_points` (median cutoff + 1), `current_points`, `need_here`, `qual_points_median`, `grid[]` per
  seed × role = the least playoff result such that median qual points + alliance points + playoff points ≥
  need_here (with `p_result` = P(that alliance gets at least that)), and a `text` like "need ~44 event points (qual
  ~12): R1 pick of A1–A5 + finalist; ...". Excludes award points.
* `us.captains[]` (`captain_resim`, top 5 captains from `picked_by`): the focal event re-simulated twice with the same
  random numbers, captain C's first pick forced to us ("take") vs forbidden ("skip" = their next best), only over
  sims where C captains and we are still available at C's pick (≥ 20): `take` / `next` = `{p_win, p_final, p_dcmp}`,
  `delta_win`, `delta_dcmp`, `alt` (their next best), `seed_mode`.
* `us.pitches[]` (up to 4): per likely captain, its weakest blend component vs the top-8 median (endgame skipped when
  that median ≤ 5), what we bring (or our best component if we are below median in that one), `pitch` text (replaced
  by the re-sim line "If C takes us instead of X: alliance wins the event a % vs b %"), `evidence` (our video facts
  with n from `scout.json`), `fit` (trench shares, auto FUEL, on-the-move share, our defended retention and
  as-defender effect from `defense.json`).

Replay: `selection/<ts>.json` (one per dashboard snapshot, `goals --asof ts`, missing only unless `--all`) and
`selection/index.json {event, times}`.

---

## 12. Defense library (`scouting.py` → `defense.json`)

### 12.1 Episodes (`detect_episodes`, `_features`)

For every pair (defender D, target T) of opposing, verified tracks in a verified match: frames where both are valid,
centre distance < `DEF_R = 1.5 m`, T is in its own scoring area (canonical x > lane band start − 0.5 m), teleop, and
not both jammed at T's tower after match time 125 s. Runs bridged over gaps ≤ `DEF_MERGE_S = 1 s`, kept with
≥ `DEF_MIN_S = 2 s` of closeness, and re-engagements of the same pair within `DEF_JOIN_S = 3 s` joined.

Features: close / contact (< `CONTACT_R = 1.0 m`) / pin seconds (contact runs ≥ 2 s with the target's median speed
< 0.3 m/s), shadowing (velocity cosine > 0.5 while both move), pushed metres, defender zone and speed, defender
intakes, defender at T's outpost corner. **Method**: `pin` (pin ≥ 2 s), `contact` (≥ 1 s), `shadow` (shadow
fraction ≥ 0.45 and ≥ 1 s), `zone-denial` (defender in T's zone / lane band ≥ 60 %), else shadow.
`maybe_collecting` = defender at the outpost ≥ 50 % with < 1 s contact (in the hand check those were the "defender"
collecting spilled fuel). `overlap` = min distance < 0.5 m (physically impossible for 0.91 m robots: merged tracking)
→ verdict `tracking`, not scored. `double` = another defender on the same target overlapping ≥ 50 %.

### 12.2 Effect per episode: placebo windows (`Baseline`, `_null_stats`)

Expected output comes from the **same robot's** undefended time: eligible frames are valid teleop frames with no
opponent within `FREE_R = 2 m` and not inside any episode. Placebo windows have the episode's length, ≥ 80 %
eligible frames, a similar zone mix (alliance zone / lane band / own-half neutral within ±0.35) and hub-active share
(±0.25); relaxed to ±0.6 / ±0.4, then "any", until ≥ 40 windows (step 2 frames). The windows form an empirical null
that keeps the burstiness of volleys. With ≥ 15 windows: `exp` = mean, 90 % interval = 5–95 %, one-sided mid-p
(defense lowers output).

Per episode `effect`: `pts_obs` (made FUEL in an active hub), `att_obs`, `pts_exp`, `pts_ci`, `pts_p`, `d_rate`
(per active second), `ratio`, `denied`, attempts equivalents, `null_n`, `null_level`. Also `cycle` (gap between the
volleys around the episode vs the team's median cycle) and `disp` (`displaced` = pushed ≥ 0.75 m or time in its
usual shooting spots down ≥ 0.4).

**Verdict** (`_verdicts`): metric = points when ≥ 2 points were expected and ≥ 2 s of active hub; else attempts when
≥ 2 attempts expected; else `untestable`. `success` = ratio ≤ 0.5 and p ≤ 0.10; `no effect` = ratio ≥ 1.0; else
`weak`. `sig` = p ≤ 0.05 and Benjamini–Hochberg q ≤ 0.2 over all testable episodes.

### 12.3 Calibration and per-team retention

`placebo_check(base, eps, n=1500)` scores random undefended windows (near the scoring area) exactly like episodes:
the "success" rate there is the verdict rule's false-positive rate; `pooled_ratio` is the pipeline's own bias;
the windows feed `team_retention`.

`team_retention(eps, placebo_windows, B=2000, alpha=0.10, min_n=3)`: for each team as **target** (`retention`) and
as **defender** (`as_defender`, the retention of its victims), with ≥ 3 testable episodes:

* `est` = (Σ obs / Σ exp over its testable episodes) / (same ratio over the placebo windows);
* bootstrap: resample episodes, add each episode's count noise (sd = its placebo-interval width / 3.29, ≥ 0.5;
  not clipped per episode — clipping biased the interval so it could exclude its own estimate), divide by
  bootstrap samples of the placebo ratio; `lo`/`hi` = 90 % interval (always widened to contain `est`);
* `p` = two-sided bootstrap p vs 1.0; `q` = BH over every team estimate in the file; `lo_adj`/`hi_adj` =
  FCR-adjusted interval at level 1 − α·R/m (Benjamini–Yekutieli 2005, R discoveries of m tests);
  `significant` = q ≤ 0.10 and the adjusted interval excludes 1.0; `n_episodes`, `n_testable`, `n_eff` (Kish over
  expected points).

`teams_meta`: event-wide placebo-controlled retention `{est, lo, hi, n_testable, n_eff}`, `placebo_ratio`,
`placebo_ci`, `placebo_windows`, `alpha`, `tests`, `discoveries`, `adj_level`, `min_testable_episodes`.

### 12.4 `defense.json` shape

```
{ event, generated, matches[],
  params {radius_m, min_s, contact_m, free_m, id_conf_min, success, significant},
  global {n, tested, succ, sig, obs, exp, ratio, ratio_ci, placebo {tested, false_success, rate, pooled_ratio},
          verdicts {...}, methods {...}},
  episodes[] {id "Q12-003", m, key, a, b, t_video, t_match, dur, defender, target, d_al, t_al, d_k, t_k, shift,
              active, act_frac, zone, side, zc, method, double, feat {...}, maybe_collecting, effect {...}, cycle {...},
              disp {...}, clip {v0, v1, data?}, conf, overlap, verdict, sig},        sorted by denied FUEL
  targets {team: {baseline, received (pool + ci), by_method, by_zone, by_state, defenders[≤6], advice[]}},
  defenders {team: {pool..., ci, methods, targets, matches}},
  teams[] {team, retention?, as_defender?},  teams_meta {...} }
```

Pools (`_pool`): `n`, `tested`, `succ`, `sig`, `obs`, `exp`, `ratio`, `denied`, `secs`; `ci` = bootstrap 90 %
(500 resamples) containing the estimate. `advice` = short plain-language bullets, each with its sample size.

**Top-down clips**: the 30 (`EMBED_CLIPS`) episodes with `success` first, then by FUEL denied, embed `clip.data`
(`clip_data`): all six robots at 5 Hz over the episode ± 3 s (`PAD_S`) in absolute field cm with a validity bit,
plus shots `[t, x0, y0, x1, y1, m|x|p|u, track_idx, credited]`; `clip.v0/v1` are video seconds for jumping into the
real video.

### 12.5 Measured (production `defense.json`, 2026-10-12, 74 verified matches)

| quantity | value |
|---|---|
| episodes / testable / success / BH-significant | 421 / 228 / 10 / 0 |
| verdicts | weak 171, untestable 138, no effect 74, tracking 28, success 10 |
| methods | contact 163, shadow 145, zone-denial 101, pin 12 |
| pooled obs / exp | 876 / 1239.2 → ratio 0.71, 90 % CI [0.58, 0.84] |
| placebo check | 190 windows, 7 false successes (3.7 %), pooled ratio 1.00 |
| event-wide placebo-controlled retention | **0.71** [0.56, 0.88], n_testable 228, n_eff 165.7 |
| per-team tests | 39, discoveries 0 |

So: defended robots keep about 71 % of their active-hub scoring rate on average, and no individual team's effect
survives multiple-testing correction.

---

## 13. Environment variables

| variable | default | read by | effect |
|---|---|---|---|
| `DASH_TEAM` | `frc4911` | dashboard, goals, worker | perspective team for `dashboard.json`, previews, goal block, selection |
| `DASH_GOAL` | `DCMP` | goals | `DCMP`, `Worlds`, `rank:N` |
| `DASH_AWARD_CONTENDER` | `0` | goals, selection | `1` = headline odds include random awards for us |
| `DCMP_CAPACITY` | – | goals | DCMP size override (else `district.CAPACITY`, else 64) |
| `RECON_GATE` | `0.2` | coverage | reconciliation gate fraction |
| `ID_CONF_MIN` | `0.5` | scouting (also worker, enrich) | per-frame identity threshold for frames / shots |
| `TBA_KEY` (or `X_TBA_AUTH_KEY`) | – | epa, district | TBA API; set by `worker.load_config` from the app's stored key |
| `DASHBOARD` | `1` | worker | `0` skips the dashboard subprocess |
| `OMP/OPENBLAS/MKL_NUM_THREADS` | forced 1 | `live/strategy/__init__` | single-threaded BLAS |

Tunable constants without env (edit the code): `model.VIDEO`, `TREND`, `SHRINK_MULT`, `TRUST_ID_INFO`, `SHRINK_K`,
`CLIMB_K`; `data.CRED_CONF`, `ID_RAMP`; `epa.DRIFT_FRAC`; `dashboard.VIDEO_LAG`, `POST_LAG`, `PLAYOFF_VISIBLE`,
`ENGAGE`, `CLIMB_MATTERS`, `DISAGREE_RATING`, `GENERIC_RETAINED`; `matchday.FEED_CONV`, `DEF_MIN_S`,
`DISAGREE_PTS`, `GOAL_LO/HI`; `coverage.VERIFIED_CONF`, `SHOW_N`; `scouting.DEF_*`, `CONTACT_R`, `FREE_R`,
`DEAD_MIN_S`, `DEAD_TBA_FRAC`, `VH_MULT`, `VH_ADD`, `SHRINK_K`, `ACC_PRIOR`, `EMBED_CLIPS`; `district.CAPACITY`,
`WORLDS_SLOTS`; `goals.THETA_SD0`, `SIGMA_FALLBACK`.

## 14. Files written and caches

Under `DATA/site/api/<ev>/` (all written atomically via `.tmp` + rename; compact JSON):

| file | writer | read by (web) |
|---|---|---|
| `dashboard.json`, `dashboard.<team>.json` | `dashboard.main --site` | `dash.js`, `app.js`; `goals`, `scouting.build_truth` |
| `preview/<match>.json` | `dashboard.main --site` (previews) | `dash.js` (details link) |
| `dash/<ts>.json`, `dash/index.json` | `dashboard.main --snapshots` | `dash.js` replay slider, `app.js`; `scouting` (trend, as-of) |
| `defense.json` | `scouting.build` | `defense.js`; `matchday.load_library`; `goals` (pitch fit) |
| `spec/<team>.json`, `spec/index.json` | `scouting.build` | `teams2.js` |
| `scout.json` | `scouting.build_truth` | `event.js`, `app.js`; `goals` (pitches) |
| `scout/<ts>.json`, `scout/index.json` | `python -m live.scouting --snapshots` | `event.js` (replay-aware picklist) |
| `rankings.json` | `goals.site_build` | `rankings.js` |
| `schedule.json` | `goals.site_build` | `schedule.js` |
| `selection.json` | `goals.site_build` | `rankings.js` (Selection tab) |
| `selection/<ts>.json`, `selection/index.json` | `goals --selection-snapshots` | `rankings.js`, `app.js` |
| `t/<team>.json`, `event.json` (teams[] fields) | `sitev2.verified_team_files` (rewrite) | team pages |

Caches under `DATA/cache/` (all disposable; deleting them forces refetch / refit):

| path | content | lifetime |
|---|---|---|
| `statbotics/` | Statbotics `team_events` / `team_matches` | 600 s (district team_events 3600 s) |
| `tba_prior/` | earlier events' team lists and matches | team lists 1 day, event matches forever |
| `tba_district/` | district events, matches, teams, alliances, awards, district points, rankings | live 120 s, finished forever, lists 1 day, rankings 1 h |
| `strategy/backtest_<ev>.json` | prequential backtest rows for the footer | content-keyed (§7.3) |

---

## Docs vs code (and things to know)

1. **App team / goal settings are ignored by strategy.** `worker.json` `team_number` / `season_goal` are never read;
   the dashboard, goals and selection use `DASH_TEAM` / `DASH_GOAL` env (defaults frc4911 / DCMP).
2. **Likely scale bug in the season sim's focal-event strengths.** `goals.build_event_json` passes
   `ratings[t] = (blend_total / 3, max(sd, 3) / 3)` and `Season` adds `foul_mean / 3`, while every other team's
   `theta` from `district_strengths` is a per-robot share whose alliance sum is the alliance score. The blend is
   already per robot (mean blend 64.0 vs alliance mean 199.1 / 3 = 66.4). Production confirms it: 4911 blend 85.6 →
   `theta_team.mean` 30.7 (= 85.6/3 + 7.2/3), with `sigma_alliance` 73.7. Focal-event teams therefore enter at about
   one third of the scale of other events' teams with the full alliance noise, so focal-event quals and playoffs come
   out much closer to coin flips and the bonus-RP logits (fitted on real ~200-point scores) are evaluated at ~70-point
   scores. `selection.json` shows `rating = 3 × theta` (= blend + fouls), which hides it. Verify and fix (likely: drop
   the `/ 3`, and use `+ foul/3` only, and show `rating = theta`).
3. **"Measured" defense is looser than the round-3 spec.** `DASHBOARD.md` / `README.md` say use `significant`
   before calling a team's defense effect measured. `matchday.retention_for` also accepts `hi < 1.0` (the unadjusted
   90 % interval). In production that labels 5 teams (488, 4469, 5588, 5937, 9317; q 0.42–0.44) as "measured" while
   the file reports 0 discoveries.
4. **"One win % per match everywhere" holds only inside `dashboard.json`.** `schedule.json` runs its own simulations
   of the same matches (`pred.p_red`: 1500 sims; `breakdown.win_prob`: 3000 sims) and `rankings.json` its own rank
   projection (1500 sims vs the dashboard's 2000), so those pages can differ from the dashboard by Monte Carlo noise.
5. The dashboard's defense numbers come from the previous build's `defense.json` (build order, §1).
6. Scout snapshots are fully rebuilt on every site build (no `--only-missing`), unlike dashboard and selection
   snapshots.
7. `DASHBOARD.md`'s early "match-day" note says `plan.defend[].basis` is "measured" only with ≥ 2 effective defended
   matches; round 3 replaced that with the library rule above. `plan.defend[].basis` is `"measured"` or `"prior"`,
   while `queue.defend.basis` is `"measured"` or `"assumption"` for the same thing.
8. `live/strategy/README.md` lists `scout.json` `made_pm.per_match` items as `{m, made, scaled, lb, un}`; the code
   writes `{m, made, scaled, gate, un}`. It also quotes the season-sim sigma as ~61; production computes 73.7.
9. `selection.py`'s module docstring still describes a client-side "manual scenario" re-draft; the `draft` sample it
   used was removed (round 3) and nothing in `selection.json` supports it now.
10. `scout.json` `policy.lower_bound_unattributed` is hard-coded 0.2 even when `RECON_GATE` is changed.
11. `model.h_c` (human-player mean) uses the soft-split usability rule even though the default video source is
    "credited" (§3.4); harmless at 2026wasam but worth knowing when changing `VIDEO`.
12. The README's auto-bias numbers (−4.9 / −10.3) are written as "actual − predicted" but have the opposite sign of
    the production footer's `auto_bias_recent` (+9.7, actual − predicted, autos under-predicted). The text
    ("under-prediction") matches the footer; the README sign is the inconsistent one.
13. `goals.replay` exists but no result is recorded in the docs; the only DCMP-odds calibration number quoted is the
    09:00 replay Brier .133 (with awards) vs .147 (without).
