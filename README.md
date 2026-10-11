# Cyber-Scout

Fully autonomous quantitative scouting for FRC, from the broadcast video alone.

Cyber-Scout watches an event on The Blue Alliance. Every time a match is played and its
video shows up, it tracks all six robots and every ball in the air from the overhead field
camera, works out which robot is which team from their bumper numbers, reconstructs every
shot (who shot, from where, made / missed / pass), and publishes it all to a scouting site.

Built for 2026 REBUILT; first event: PNW District Sammamish (2026wasam).

## What it measures

- **Scoring** — every shot as a 3D arc: shooter, launch point, made/missed/pass, by period and
  hub state; accuracy, fire rate, volley size, cycle time, shot charts. Checked against TBA's
  official per-period hub counts (held-out: traced/TBA ≈ 0.94–1.00).
- **Identity** — OCR of bumper numbers anchors the tracker (≈96% of answered frames correct),
  with per-frame confidence; low-confidence stretches are quarantined, not guessed.
- **Movement** — paths, speed, zones, lanes (bump vs trench), auto routines (clustered),
  defense / contacts / pins, endgame climbs, dead-robot detection, roles, floor pickups.
- **Analyze view** — the match video with toggleable layers (robots, labels, trails, shot arcs,
  raw ball tracks, minimap, scoreboard, traced-vs-TBA, calibration grid) for checking accuracy
  frame by frame.

## Layout

```
live/        the pipeline: worker (TBA + YouTube), process (detect/track/identity), fuel, shots,
             intakes, analytics, site JSON, calibration, training scripts  — see live/HANDOFF.md
web/         static scouting site (no build step)
deploy/      docker compose stack + runbook (deploy/README.md)
events/      per-event camera calibrations
src/ tools/ docs/   the original autoscout tracker, browser tools and docs (upstream)
```

Start with `deploy/README.md` (running it), `live/HANDOFF.md` (how each module works) and
`live/SPEC_V2.md` (data contracts).

## Credits

Robot tracking builds on [autoscout](https://github.com/samdev-7/autoscout) by Sam Liu (MIT):
its field-space tracklets, identity solver and smoother are used here; its README is in
`docs/AUTOSCOUT_README.md`. Field geometry and AprilTag layouts from
[WPILib](https://github.com/wpilibsuite/allwpilib); match data from
[The Blue Alliance](https://www.thebluealliance.com); bumper OCR models are PP-OCRv3
(Apache-2.0, via rapidocr_onnxruntime). Much of the code was written with Claude Code.

## License

MIT — see [LICENSE](LICENSE).
