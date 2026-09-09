# Paper-source overhang feedback pilot

This experiment separates slicer support burden from the MuJoCo progressive-tipping
stress test. It uses original CAP3D and MARVEL level-2 captions, with no
manufacturability wording in baseline prompts.

## Frozen sample

The selector predeclares four feature-bearing groups:

- ShapeNet mug with a handle.
- ShapeNet chair with armrests.
- ShapeNet table/desk with a tabletop and drawer.
- ABO floor lamp with a curved arm.

Both CAP3D and MARVEL captions must mention the defining feature. Candidates are
sorted by SHA256 of `dataset:object_id`; rank 0 is selected before generation.
This produces four objects and eight prompts. The 3:1 ShapeNet:ABO object ratio
preserves the paper's 60:20 ratio after excluding Objaverse.

Objaverse is not silently represented. Its local captions are absent. The
immutable MARVEL Objaverse CSV is 3,789,414,322 bytes, and Hugging Face's row
server currently fails because the repository CSV files have incompatible
columns. Downloading that full file would violate the small-subset constraint.

## Paired comparison

1. Generate a one-round CPU baseline from the untouched source caption.
2. Analyze it with the fixed PrusaSlicer profile.
3. Only baselines with nonzero nominal support-contact area enter repair.
4. Copy the exact baseline `source.py` into `adsl-run edit --check-first`.
5. Add the required overhang checker while retaining the normal Image/Code
   critics.
6. Compare baseline and selected repaired rounds.

The primary gate is at least 1% lower nominal support-contact area, no increase
in geometric overhang area, and a print-space AABB delta of at most 0.01 mm.
The fixed slicer profile uses PLA, 0.4 mm nozzle, 0.2 mm layer height, 45 degree
overhang threshold, and everywhere rectilinear support.

This is a conditional mechanism pilot, not a paper-level success-rate estimate.
It does not model thermal warping, surface scarring, or print certification.

## Freeze prompts

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python \
  experiments/overhang_feedback/select_prompts.py \
  --metadata-root /jiigan-hp/lms/aDSL/datasets/prompt_sources \
  --output local_experiment/overhang_prompt_pilot_20260906/case_manifest.json
```
