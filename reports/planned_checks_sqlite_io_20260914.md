# Planned-checks SQLite I/O failure and minimal repair

The failed batch is `planned_checks_20260914T154655Z`. Original trace and database
remain on the data disk; no old database was repaired, deleted, or converted.

## Evidence

- SDK `run.py:878` saves the initial user input to the session before the model call.
  SQLite failed at `_insert_items`. No token reservation ledger, usage event, or
  proposed plan had been created. This was not a model response or physical-checker failure.
- Shared `/jiigan-hp` is `hpvs_fs`; `/tmp` is local tmpfs with about 5.9 GB available
  at diagnosis. The disk was not simply full.
- Six independent small probes: local WAL/DELETE/SDK all passed; shared WAL/SDK
  passed, shared DELETE failed with `disk I/O error`. Thus not every write fails,
  and the evidence does not isolate WAL alone as the root cause.
- Offline reconstruction of the actual SF01 input (6,840,529 serialized bytes,
  one SDK input item containing source/config/images): SDK add/read succeeded
  locally, failed on the shared mount with the same I/O error. No API was called.
- This supports a filesystem/SQLite compatibility or reliability issue under this
  workload. The precise hpvs failing system call was not traced. SQLite itself
  documents that WAL is not supported over network filesystems:
  https://sqlite.org/wal.html

## Repair

SessionManager and AgentRuntime accept an optional local database root; default
behavior is unchanged. Only planned-checks sets `/tmp/adsl-planned-checks-sessions`.
Paths are hashed by full workspace to prevent same-task history collisions.
Data-disk assets, costs, logs and plans stay in place. Plain JSON session snapshots
are archived to the data disk after planning; the live WAL database stays local.
If the temporary DB disappears but its snapshot exists, continuation fails closed
instead of silently dropping context. Automatic transcript restoration is not added.

The explicit one-off pre-API recovery refuses any usage/reservation/proposal or
measurement evidence. It archives the failure/start state, records old/new code
hashes, and retains original batch deadline and consumed planning seconds. It does
not provide general retry or reset capabilities. New failures must stop normally.

Validation: local SDK 2 MB input write/read/reopen, path separation, old database
preservation, unchanged default path, and existing planner/budget/candidate/fault
tests. Shared-disk probe databases are retained as diagnostic artifacts only.
