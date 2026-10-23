# Independent CLIP-I validation (v7)

This small evaluator reads an existing paired output ZIP, freezes the first three
cases in `cases.csv` by default, rerenders complete GLBs with the existing
`adsl.tools.render`, and scores eight-view image similarity. It does not generate
assets, execute supplied sources, call an Agent, or run a physics checker.

Use `python -m experiments.clip_i_validation.run --phase PHASE --output RUN`.
Phases are `prepare` (requires `--zip`, optional `--limit`), `render-first`,
`preview`, `review` (requires `--review-note`), `render`, and `score` (requires
`--model-path`). Rendering and scoring belong inside an allocated Slurm job.
The evaluator never releases the allocation or exits the parent interactive shell.

The renderer receives explicit existing CLI flags for orbit8, elevation15,
1024x1024, 256 samples, transparency and native materials. Its camera radius1.5,
azimuth offset-90, normalization0.8, Sun1.25, World0.35 and exposure-0.8 follow
the frozen public renderer. The same-process worker captures effective Blender
settings, versions and graphics renderer after rendering. It does not extend the
renderer API. A renderer failure is preserved and reported, without automatically
changing engines or using archived PNGs. First-case contact sheets are review
material only and never become encoder input.

Only `openai/clip-vit-large-patch14` is used, loaded from a predownloaded local
snapshot in FP32. Images use white alpha compositing and the same CLIPProcessor.
The view score is 100 times cosine similarity; case scores average all eight
views; method scores average the same paired measurable cases. Missing images or
invalid embeddings yield null, never zero or partial-view means. No bootstrap,
VLM, score-based case selection or official-paper score comparison is performed.

This is an independent validation using public renderer settings. Native materials
remain a known difference from the paper's stated neutral-material setup. Camera
coverage and CLIP-I scores do not prove geometric or manufacturing quality.

The explicitly selected `--engine cycles` prepare option uses the separate
`clip_i_adsl_render_orbit8_cycles_v1` protocol and selects exactly one visible
CUDA rendering device. It never mixes those views with EEVEE results.

The subsequently authorized `--software-opengl` prepare option keeps the EEVEE
protocol but requests Mesa llvmpipe with `LIBGL_ALWAYS_SOFTWARE=1`. The launch
environment selects the system Mesa EGL vendor configuration. Both methods use
the same software backend; actual settings and the backend request are recorded.
GPU rendering confirmation is required for the Cycles GPU protocol, while EEVEE
software rendering does not require it. CLIP inference still uses the allocated
CUDA GPU. Software OpenGL is a driver backend, not EEVEE's own CPU renderer.
