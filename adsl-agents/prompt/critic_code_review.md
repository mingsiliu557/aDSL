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
