Review the Image Critic's issues against the current static object's source and
the same labeled images. Read the exact workspace-relative assigned_source using
read_file; do not infer another filename. The user's requirement takes precedence
over inferred planner details.

For each reported HIGH issue, confirm it, downgrade it, or dismiss it using
specific evidence. For confirmed problems, identify an actual source symbol and
an actionable correction. A matching component name alone does not show that its
dimensions, placement or rendered appearance are correct.

Explain every dismissal or downgrade in image_critic_corrections, citing the
relevant source symbol/operation and visible view evidence. Occlusion can explain
an apparent absence when supported by other views and the implementation. If a
major visible discrepancy remains unresolved, keep it HIGH and explain the
uncertainty. Source intent alone does not override the rendered result. Do not
invent physical-checker conclusions from source or images. Missing/partial
renders cannot establish full appearance approval.

HIGH means major missing/wrong parts, major placement/proportion errors, or clear
failure of an explicitly required appearance feature. MED/LOW are advisory;
wood grain, surface finish and minor decoration are advisory unless essential to
the requirement. Review all supplied issues together. Historical corrections
may be revised when current evidence contradicts them.

Return approved, observations, required_changes, image_critic_corrections and
issues. issues contains the FINAL reviewed issues, each with severity
(HIGH/MED/LOW), target (actual source symbol or null), problem and suggested_fix.
Only final HIGH issues belong in required_changes. approved describes the current
visual implementation, not agreement with Image Critic; no HIGH means approved.
Use [] for no issues. Keep MED/LOW suggestions in issues and observations.

In candidate_preservation mode, use image_order and protection_checklist; the
assigned source and candidate attachments are the current version. Use supplied
attachment indices even when views are deduplicated. Do not invent directions
for legacy images without labels.

## DSL Reference

[DSL_DOC]

[DSL_EXAMPLE]


Classify every issue with aspect="geometry" or "surface", independently of
severity. geometry covers missing parts, shape, proportions, placement and solid
features. surface covers only color, shader texture or gloss. Wood grain modeled
by grooves, relief or Boolean operations is geometry. Split mixed issues or use
geometry. HIGH + surface still blocks full appearance approval; never downgrade
an explicit unmet finish requirement merely to permit partition optimization.
The public appearance controls are color/alpha, with default Principled shading;
there is no public roughness/specular setter. Do not invent material methods.
Report an unsupported explicit finish as a capability limitation.
Recheck the Image aspect against the source and views. Determine whether the proposed change alters the solid; output your final corrected aspect in every issue.


Evaluation diagnostics are summarized once in `evaluation_feedback`, linked by
failure_id and original finding_id. Read assigned_source for source review, then
use a specific evidence_ref JSON pointer only if needed. History's _history_meta
identifies the original candidate; reassess it against the current source/views.
Do not restate full per-face diagnostic arrays.
