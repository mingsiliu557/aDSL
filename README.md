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

This repository is built on the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) and should support all models officially supported by the SDK.

By default, we use [OpenRouter](https://openrouter.ai/) with *Gemini 3.1 Pro* via an OpenAI-compatible API.

Set your OpenRouter API key with:

```bash
echo -n "YOUR_OPENROUTER_API_KEY" > adsl-agents/configs/keys/openrouter_key.txt
```

The default *Gemini 3.1 Pro* profile is defined in `adsl-agents/configs/llm/openrouter-gemini-3.1-pro.yaml`:

```yaml
provider: openai
api: responses
credential:
  file: ../keys/openrouter_key.txt
params:
  base_url: https://openrouter.ai/api/v1
  model: google/gemini-3.1-pro-preview
  timeout: 300
  max_retries: 0
  max_tokens: 32768
  parallel_tool_calls: false
  include_usage: true
```

## Stepcode HTTP API backend

On machines where `stepcode system status` reports an API key and local base URL, aDSL can call the OpenAI-compatible HTTP API directly instead of launching `codex exec` for every model turn. The packaged profile is `adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml`.

The profile uses `credential.stepcode: true`; at startup aDSL runs `stepcode config get apiKey` as an argv-only subprocess, extracts the credential in memory, and never writes it to the repository, runtime metadata, or logs. Do not copy the key into YAML or commit it. The local profile also sets `trust_env: false` so requests to `127.0.0.1` do not get redirected through machine-wide HTTP proxy variables.

Verify the non-secret status, then run the same workflows with the Stepcode profile:

```bash
stepcode system status

adsl-run \
  --model-config adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml \
  create "A simple wooden stool" \
  --max-rounds 1 \
  --output ./temp/stepcode-api-smoke-stool

adsl-chat-run \
  --workspace ./temp/stepcode-api-chat \
  --model-config adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml \
  "Reply briefly to confirm the Stepcode HTTP backend is active"
```

`ADSL_STEPCODE_BIN` can select a non-default binary, and `ADSL_STEPCODE_TIMEOUT_SECONDS` controls only the credential lookup. Model requests use the profile's `timeout`. The Codex CLI backend remains available as a fallback and uses separate configuration.

## Codex CLI backend (no API key)

aDSL can also use an already logged-in [Codex CLI](https://developers.openai.com/codex/noninteractive) as the model transport. This backend still uses the existing OpenAI Agents SDK orchestration: aDSL owns sessions and executes its restricted file tools, while each model turn runs through an ephemeral, read-only `codex exec` subprocess.

On this machine, use the dedicated Python 3.10 environment and the explicit Codex binary:

```bash
conda activate /vepfs_default/chanxueyan/lhp/lms/envs/adsl
export ADSL_CODEX_CLI_BIN=/vepfs_default/chanxueyan/lhp/lms/npm-global/bin/codex
codex login status
```

If login status is not successful, run `codex login`. Do not copy, print, or commit Codex's `auth.json`; the adapter reuses CLI login and does not require `OPENAI_API_KEY`.

The packaged profile is `adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml`. A small first-stage create can run in the repository's ignored `temp/` directory:

```bash
adsl-run \
  --model-config adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml \
  create "A simple wooden stool" \
  --max-rounds 1 \
  --output ./temp/codex-smoke-stool
```

The same profile works for `edit`, `resume`, interactive chat, and one-shot chat:

```bash
adsl-chat \
  --workspace ./temp/codex-chat \
  --model-config adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml

adsl-chat-run \
  --workspace ./temp/codex-chat-once \
  --model-config adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml \
  "Create a compact nightstand"
```

Optional model-transport overrides are `ADSL_CODEX_CLI_TIMEOUT_SECONDS` and `ADSL_CODEX_CLI_MAX_PROMPT_CHARS`. Slow shared filesystems can also raise the isolated asset-process limit with `ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS` (the default remains 300 seconds). Proxy variables are inherited from the launching process; no proxy address or credential is hardcoded. The adapter deliberately ignores the user's Codex configuration while retaining CLI authentication, fixes the Codex sandbox to read-only, records sanitized per-call diagnostics under `<workspace>/codex_cli/`, and refuses to truncate an oversized prompt.

Headless rendering does not require a GPU. If Eevee cannot create an EGL context on a CPU-only machine, select Blender Cycles and optionally lower the preview settings:

```bash
export ADSL_RENDER_ENGINE=CYCLES
export ADSL_RENDER_WIDTH=512
export ADSL_RENDER_HEIGHT=512
export ADSL_RENDER_SAMPLES=16
```

The defaults remain `BLENDER_EEVEE`, 1024×1024, and 256 samples. The effective renderer, dimensions, samples, and asset-executor timeout are recorded in each workspace's `runtime_config.json`.

For now, small cases may stay under `./temp/` because `/jiigan-hp` is inaccessible. When the data mount is healthy again, move substantive experiment outputs to the agreed data-disk location rather than the code repository.

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
| `--model-config PATH` | No       | Saved profile or packaged Gemini 3.1 Pro profile | Select the LLM profile YAML file.                                                                                       |
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
