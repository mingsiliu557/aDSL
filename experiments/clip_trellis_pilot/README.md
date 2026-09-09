# aDSL versus TRELLIS CLIP pilot

This is a rough local effect check, not an exact reproduction of the aDSL
paper. It compares all five existing independent static text cases and both
existing image-conditioned cases. Text-image and image-image scores are never
pooled.

## Locked protocol

- Clean TRELLIS source commit 6b0d64751ad54d9c32d7b05fec482eb29178f56f.
- Local TRELLIS-text-xlarge-original and TRELLIS-image-large weights.
- Exact existing prompts and input PNGs; no prompt expansion and no retry.
- One TRELLIS output per case with seed 1.
- One external Eevee renderer for both methods: eight 45-degree views, 15-degree
  elevation, 1024 square, a white world-color setting, neutral material, and
  identical AABB normalization. The project's Filmic/exposure defaults can
  encode that nominal white background as gray pixels; this does not create a
  method imbalance because both sides use the same renderer.
- Local openai/clip-vit-large-patch14; cosine times 100 and mean across views.
  Failures score zero in the primary aggregate.
- A mean paired difference within +/-2 points means only close in this pilot.

## Toys4K subset

Only the public 3 MB TRELLIS-500K metadata is downloaded. The selection tool
freezes ten objects from ten categories. If an authorized official archive is
provided later, it extracts only those ten members.

The official raw Toys4K assets are access-controlled. Metadata alone does not
contain conditioning images or ground-truth geometry, so those ten rows are not
silently counted as completed image-to-3D cases.

## Execution

Build a unique manifest with build_manifest.py, download Toys4k.csv through the
127.0.0.1:7892 HTTP proxy into RUN_ROOT/toys4k, then run
prepare_toys4k_subset.py. Prepare the isolated baseline with prepare_runtime.sh
and submit the single-card job with launch.sh.

The GPU worker executes TRELLIS generation, Blender rendering, and CLIP scoring
serially, so peak GPU memory cannot overlap. Primary outputs are
clip/scores.json, clip/scores.csv, and clip/report.md. Logs and exit codes are
kept under logs and status.
