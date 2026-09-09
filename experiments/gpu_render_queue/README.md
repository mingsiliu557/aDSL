# Persistent serial GPU Eevee renderer

This service keeps one A800 allocation alive across many aDSL cases while running
exactly one Blender process at a time.

```text
text / image / articulated / edit cases
                  |
            asset_executor
                  |
          filesystem FIFO queue
                  |
       one long-lived GPU worker
                  |
     one foreground Blender child
                  |
 process exit -> GPU memory settles -> next job
```

## Why this avoids cross-case OOM

- Every case type converges on `asset_executor.py`, so there is one integration
  point rather than a separate launcher for each agent.
- Submissions may arrive concurrently, but one worker owns an exclusive queue
  lock and dequeues exactly one request at a time.
- Each render uses a fresh foreground Blender process. The worker waits for its
  full exit and then waits for allocated GPU memory to return to the startup
  baseline (default tolerance: 256 MiB).
- If memory does not settle within 60 seconds, the current job fails and the
  worker enters `blocked`; it does not launch the next case.
- A render has a 900-second hard timeout. A timed-out process group is terminated
  and is not retried automatically.
- If the client or worker disappears, the case fails explicitly. Queue mode
  never silently falls back to CPU rendering.
- Worker heartbeat reads use a bounded five-second grace both before enqueue
  and while waiting. This tolerates one transient `jiigan-hp` metadata read,
  but a sustained stale/missing heartbeat still cancels the job and raises a
  typed infrastructure error; it is never sent to the geometry repair agent.

The default flavor is one A800 (`ml.pni2l.3xlarge`). Eevee uses one graphics
device for this path, and the queue is deliberately single-lane, so requesting
the two-A800 flavor does not make one case faster and normally only wastes the
second card. `ADSL_GPU_FLAVOR_ID` can override the flavor for a future renderer
that genuinely uses two GPUs, without changing the serialization guarantee.

## Start once, run many cases

Run these commands on the login node:

```bash
cd /vepfs_default/chanxueyan/lhp/lms/aDSL
source experiments/gpu_render_queue/activate.sh
bash experiments/gpu_render_queue/control.sh start
bash experiments/gpu_render_queue/control.sh wait
bash experiments/gpu_render_queue/control.sh status
```

Start the aDSL agent/service from the same activated shell. Existing services
must be restarted after activation so they inherit `ADSL_GPU_RENDER_QUEUE`.
After that, different workflows and rounds submit automatically; no per-case
`tmux` or `volc` command is needed.

The manager runs in the fixed tmux session `adsl_gpu_renderer`. It makes one
direct `volc ml_devinstance launch` request, and the GPU worker stays alive for
two idle hours by default. New jobs reset the idle timer.

## Observe and stop

```bash
source experiments/gpu_render_queue/activate.sh
bash experiments/gpu_render_queue/control.sh status
tmux capture-pane -pt adsl_gpu_renderer -S -120

# Graceful release: finish the active render, leave pending jobs queued, then exit.
bash experiments/gpu_render_queue/control.sh stop
bash experiments/gpu_render_queue/control.sh status
```

Do not kill tmux as the normal stop path. If the manager times out or the worker
does not write `WORKER_FINISHED`, inspect the latest directory below
`temp/gpu_render_queue/runs/`; `RELEASE_CHECK_REQUIRED` means the old
allocation must be confirmed released before starting another.

Queue requests, state, renderer logs, and manager markers are under
`/jiigan-hp/lms/aDSL/experiment/gpu_render_queue/`. Case GLBs and PNGs stay in
each case's normal data-disk output directory. Manager and worker both resolve
and verify this exact queue root and require the `/jiigan-hp` `hpvs_fs*`
mount before accepting work.

## Tunables

The activation script sets the normal rendering contract:

- 1024 x 1024, 8 views supplied by the workflow, 256 Eevee samples.
- Queue wait timeout: 3600 seconds.
- Outer asset-executor timeout: 3660 seconds.

Worker/manager overrides include:

```bash
export ADSL_GPU_QUEUE_IDLE_TIMEOUT_SECONDS=7200
export ADSL_GPU_RENDER_JOB_TIMEOUT_SECONDS=900
export ADSL_GPU_QUIESCE_TIMEOUT_SECONDS=60
export ADSL_GPU_MEMORY_TOLERANCE_MIB=256
export ADSL_GPU_ALLOCATION_TIMEOUT_SECONDS=43200
export ADSL_GPU_START_WAIT_SECONDS=1800
```

The queue is FIFO by atomic request filename. Failed, completed, cancelled, and
pending jobs remain inspectable as JSON; the worker never automatically retries
an uncertain render.
