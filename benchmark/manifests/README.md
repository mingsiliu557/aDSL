# Selection v1 manifests (2026-10-06 structural-risk recommendations)

55 metadata candidates: ABO 30 + Toys4K 25. Current verified sources: ABO29/Toys25;
successful previews: ABO29/Toys23. One ABO SHA mismatch and two Toys preview failures
remain pending review. Twenty development recommendations (10 per source) are based
on clear reference inputs, use/task interpretation, visible structural risks and
category coverage, with one ordinary control per source. All twenty serve appearance
and printing tasks; nine per source serve independent-standing tasks. A wall-mounted
shelf and an airborne dragon have standing marked not applicable. No user confirmation
or measured split/merge comparison has occurred.

The Toys archive is fully downloaded and verified. These lightweight Git lists are a
snapshot of the persistent results under `/jiigan-hp/lms/aDSL/benchmark/selection_v1/`.
See `review/summary.json`, `review/README.md`,
`review/structural_risk_20261006/report.md` and
`logs/structural_risk_20261006/`. No models or images are committed here.

`recommended_dev20.jsonl` records actual recommendations, never padded to twenty.
`task_applicability` and the compatible `metric_eligibility` alias describe generated
model tasks, not GT volume/physics eligibility. GT closure, islands, volume, missing
measurements and standing FAIL do not gate selection. All GT measurements are optional
diagnostics; recommendations require user review. Coding Agent preview reviews identify
their actual input images/hashes and are not human confirmations. No VLM calls, physics
runs, downloads or 3D renders were added in this adjustment. Read
`../selection_v1_structural_risk_20261006.md` for the current protocol and test results;
the earlier results files remain historical checkpoints.
