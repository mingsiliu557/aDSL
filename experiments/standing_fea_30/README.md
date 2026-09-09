# 30-object standing + FEA paired experiment

This protocol compares independently generated vanilla aDSL and checker-unified outputs for 30 unique objects: six prompts in each of five instability-prone, load-bearing categories. Both arms use the same repository revision, StepCode model profile, render settings, and maximum four rounds. Only `ours` enables the required standing and FEA checkers plus source repair.

The exact 200 prompt IDs used by the aDSL paper are not public. This is a targeted CAP3D/MARVEL same-source/different-sample comparison, not an exact paper reproduction. Prompt selection is deterministic; model generation is not bitwise reproducible because the API exposes no formal seed.

Freeze the prompt manifest:

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python experiments/standing_fea_30/select_prompts.py \
  --metadata-root /jiigan-hp/lms/aDSL/datasets/prompt_sources \
  --output experiments/standing_fea_30/case_manifest.json
```

Run the five-category checker preflight:

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python experiments/standing_fea_30/preflight.py \
  --output-root local_experiment/standing_fea_30_preflight_20260909
```

Run selected cases through StepCode:

```bash
bash experiments/standing_fea_30/run_batch.sh \
  --manifest experiments/standing_fea_30/case_manifest.json \
  --output-root local_experiment/standing_fea_30_20260909 \
  --case SF01 --case SF07 --case SF13 --case SF19 --case SF25
```

The wrapper starts and always stops the user-local proxy. Use `--resume-existing` only after reviewing the terminal status. The runner stops at the first genuine failure and labels quota, GPU, checker, and other errors separately. A four-round `WorkflowGateError` with a valid source is an expected unpublished outcome and is still evaluated offline.

Summarize a partial or complete run:

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python experiments/standing_fea_30/summarize.py \
  --manifest experiments/standing_fea_30/case_manifest.json \
  --output-root local_experiment/standing_fea_30_20260909
```

`REPORT.md`, `results.json`, and `results.csv` preserve FEA `INDETERMINATE` separately. Paired joint pass requires both checkers to pass. `make_contact_sheets.py` creates presentation images after renders exist.

Active SQLite workspaces stay on the project disk. Only terminal immutable evidence should later be copied to `/jiigan-hp/lms/aDSL/experiment/`.
