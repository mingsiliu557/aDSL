# DensiCrafter image-only Ours generation

The experiment uses 150 image IDs (0–199 minus the repository's 50 validation IDs), view `{id}_3.png`, following the official single-view example. This is an explicitly constructed non-validation complement, not a separately published test manifest. No dataset caption or GT dimensions enter the model input.

`ImageOnlyWorkflow` subclasses the existing workflow. Its same initial Planner call estimates positive three-axis proportions from the reference. The controller normalizes the longest dimension to 150 mm, persists the input-bound dimensions, and freezes them before Coder. These dimensions are estimates, not ground truth. Resume checks the image/task/configuration and saved plan. Native Planner output schema, Coder, physical algorithms, acceptance rules and repair budget are unchanged. The ordinary workflow is not patched.

The public task is identical for all cases. FixedAssembly method instructions and configured manufacturing units, fit and physics remain method settings. “Image-only” means no case-specific caption or supplied 3D dimensions; it does not mean the VLM has no system/task instructions.

The batch runs Ours directly, without an aDSL gate. It retains internal image/code reviews, Topology, Standing and Overhang checks/repairs. No final CLIP, connectivity, NSV24 sweep or benchmark admission is invoked. Internal checks may produce support/standing data; these are generation evidence, not a new final evaluation table.

Outputs are written under `aDSL/densicraft_output/cases/<case_id>/native/`. `status.json`, per-case logs, Planner input, original plan and `image_only_size_freeze.json` preserve progress and provenance. Generation COMPLETE and approved are distinct. First-case implementation failure or shared API failure pauses the batch; later isolated generation failures are recorded and the next case proceeds. No automatic budget reset or replay of started cases.

The default batch retains one initial generation and nine source repairs (10 rounds), SSH gpt-6.1-sol only. Two existing allocated GPUs are used after the first-case check; no new allocation or GPU release is performed.
