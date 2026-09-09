# aDSL: Agentic 3D Creation via Joint Agent-Program Design

<a href="http://arxiv.org/abs/2608.17975"><img src='https://img.shields.io/badge/arXiv-Paper-red?logo=arxiv&logoColor=white' alt='arXiv'></a>
    
[Rui-Huan Wang](https://dylanwrh.github.io/), [Si-Tong Wei](https://wst2001.github.io/), Jia-Qi He,
Heng-Yi Wei, [Baoquan Chen](https://baoquanchen.info/), [Peng-Shuai Wang*](https://wang-ps.github.io/)

![alt text](asset/teaser.png)

## Installation

1. Install [Conda](https://www.anaconda.com/) and create a Python 3.10 environment.
    ```bash
    conda create -n adsl python=3.10
    conda activate adsl
    ```

2. Clone this repository
    ```bash
    git clone https://github.com/sig-pku/aDSL.git
    cd aDSL
    ```

3. Install the core package with 3D modeling and Blender rendering dependencies
    ```bash
    pip install -e "./adsl-core[mesh,render]" \
      --extra-index-url https://download.blender.org/pypi/
    ```

4. Install the agent workflow dependencies
    ```bash
    pip install -e .
    ```

## LLM Configuration

This repository keeps the OpenAI Agents SDK orchestration, but the machine-local
default transport is an authenticated
[`codex exec`](https://developers.openai.com/codex/noninteractive) process.
No API key is copied into the repository.

```bash
export ADSL_CODEX_CLI_BIN=/vepfs_default/chanxueyan/lhp/lms/npm-global/bin/codex
codex login status

adsl-run create "A laptop" \
  --output /jiigan-hp/lms/aDSL/experiment/example_laptop
```

The default profile is
`adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml`. It runs ephemeral,
read-only Codex subprocesses while aDSL remains responsible for tools, sessions,
asset execution, rendering, and critic/refinement rounds. OpenAI-compatible
HTTP profiles such as
`adsl-agents/configs/llm/openrouter-gemini-3.1-pro.yaml` remain available only
when selected explicitly with `--model-config`.

### StepCode HTTP API backend

When `stepcode system status` exposes a local OpenAI-compatible endpoint, aDSL
can call it directly instead of launching `codex exec` for every model turn.
Select `adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml` explicitly:

```bash
adsl-run create "A simple wooden stool" \
  --model-config adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml \
  --max-rounds 1 \
  --output ./temp/stepcode-api-smoke-stool
```

The profile resolves the credential at runtime with `stepcode config get
apiKey`; it does not store the key in YAML, runtime metadata, or logs. Set
`ADSL_STEPCODE_BIN` only when selecting a non-default binary. The Codex CLI
profile remains the packaged default and fallback.

### GPU Eevee rendering

For GPU Eevee rendering, start one persistent serial worker from the login node:

```bash
source experiments/gpu_render_queue/activate.sh
bash experiments/gpu_render_queue/control.sh start
bash experiments/gpu_render_queue/control.sh wait
```

The queue is fixed to
`/jiigan-hp/lms/aDSL/experiment/gpu_render_queue`. It uses one A800 and launches
only one foreground Blender process at a time. The next render starts only after
the previous process exits and GPU memory returns to baseline.

## Agentic 3D Generation

The generation process follows an execute–critic–refine loop. By default, the agent runs for up to `2` rounds.

For complex or highly constrained generation tasks, consider increasing the max round to allow additional critic-refinement iterations.

### General Arguments

* `--model-config <path-to-yaml>`: Specify a custom LLM configuration.
* `--max-rounds <N>`: Set the maximum number of execute–critic–refine rounds (default: `2`).

### Text-Conditioned 3D Generation

Generate a 3D asset from a text prompt:

```bash
adsl-run create "A laptop" \
  --output "./outputs/laptop"
```

### Image-Conditioned 3D Generation

Generate a 3D asset from an image:

```bash
adsl-run create "Build the object in this image" \
  --image "./asset/example.png" \
  --output "./outputs/desk-from-image"
```

### Articulated Asset Generation

Use `--articulation` to enable articulated asset generation for either text- or image-conditioned generation.

```bash
adsl-run create "Build the object in this image" \
  --image "./asset/example-arti.png" \
  --output "./outputs/nightstand-arti" \
  --articulation
```

### Engineering checker gates

Object workflows can register external engineering checkers. A required checker
participates in the publication decision; the final asset is published only when
appearance review and every required checker return `PASS`.

For an existing model, `--check-first` preserves the supplied source as the
untouched round-1 baseline:

```bash
adsl-run edit "Preserve appearance; repair only mandatory checker failures." \
  --source path/to/source.py \
  --output local_experiment/checker-repair \
  --check-first --max-rounds 4 \
  --checker-config experiments/workflow_checkers/specs/fea_chair.json \
  --repair-policy-config experiments/workflow_checkers/repair_policy.json
```

Repeat `--checker-config` to combine standing, FEA, and support gates. Checker
`ERROR` stops as an infrastructure failure. `FAIL` or `INDETERMINATE` is
normalized into typed findings. A runtime/AST `SourceIndex` then maps direct
part IDs, transformed regions, or conservative global heuristics to candidate
source constructs. The Engineering Critic can propose only those localized
targets. Each proposal is patched and checked in an isolated candidate folder;
the controller rejects out-of-scope edits, changed analysis inputs, target
non-improvement, regressions, and visual/function failures before atomically
promoting an accepted source. Materials, loads, boundary conditions, print
orientation, and thresholds remain fixed unless the explicit repair policy says
otherwise.

See [the checker documentation](experiments/workflow_checkers/README.md) and
[the physical-analysis notes](physics_analysis.md).

## Chat (Experimental)

> This feature is experimental. CLI behavior, session state, and workspace interfaces may change in future versions.

`adsl-chat` provides a persistent conversational interface for 3D generation. Each turn is routed either to regular chat or to an asset action: `create`, `continue`, `extend`, or `variant`.

Start an interactive chat:

```bash
adsl-chat \
  --workspace outputs/chat
```

The path passed to `--workspace` is the conversation workspace. All assets generated within the session are stored under:

```text
<workspace>/assets/
```

For example, with `--workspace outputs/chat`, generated assets are stored under `outputs/chat/assets/`.

Each asset action uses an independent workspace inside this directory, so the source asset is never overwritten. Conversation state and model-session history are also persisted in the conversation workspace, allowing the same session to be resumed later.

### Interactive Commands

The interactive CLI supports the following slash commands:

* `/help`: Show command help.
* `/paste`: Enter a multiline message, ending with `/end`.
* `/open PATH`: Attach an existing asset workspace.
* `/target PATH`: Reserve a new output path for the next generated or edited asset.
* `/resume PATH`: Load another `chat_state.json` session.
* `/config PATH`: Switch the active model profile.
* `/where`: Show the conversation, active, and pending workspaces.
* `/memories`: List remembered asset workspaces.
* `/exit`: Exit the interactive CLI.

After `/open`, regular chat queries can inspect information from the attached workspace, including the saved request, plan, source, critic results, articulation settings, and run status.

The chat agent can also read a user-specified workspace or its text files when the requested information is not already available in the active conversation context.

### CLI Arguments

```text
adsl-chat --workspace PATH [OPTIONS]
```

| Parameter             | Required | Default                                          | Description                                                                                                             |
| --------------------- | -------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `--workspace PATH`    | Yes      | —                                                | Conversation workspace. Generated assets are stored under `PATH/assets/`.                                               |
| `--session-file PATH` | No       | `<workspace>/chat_state.json`                    | Load and save chat state using a specific file.                                                                         |
| `--task-id ID`        | No       | Saved task ID or generated UUID                  | Override the task identifier for the session.                                                                           |
| `--model-config PATH` | No       | Saved profile or packaged Codex CLI profile      | Select the LLM profile YAML file.                                                                                       |
| `--max-rounds N`      | No       | `2`                                              | Set the maximum number of execute–critic–refine rounds for each asset action initiated from chat. Must be at least `1`. |
| `-h`, `--help`        | No       | —                                                | Show help for the interactive chat CLI.                                                                                 |

## Citation

If you find this work helpful, please consider citing our paper:

```bibtex
@article{wang2026adsl,
  title   = {aDSL: Agentic 3D Creation via Joint Agent-Program Design},
  author  = {Wang, Rui-Huan and Wei, Si-Tong and He, Jia-Qi and Wei, Heng-Yi and Chen, Baoquan and Wang, Peng-Shuai},
  journal = {arXiv preprint arXiv:2608.17975},
  year    = {2026}
}
```
