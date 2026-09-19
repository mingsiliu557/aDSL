#!/usr/bin/env bash
# A persistent interactive parent shell; tasks run as foreground children.
set -euo pipefail
if (( $# < 3 )); then
    echo 'usage: tmux_session.sh SESSION WORKDIR COMMAND [ARG ...]' >&2
    exit 2
fi
session=$1
workdir=$2
shift 2
[[ "$session" =~ ^[A-Za-z0-9_-]+$ ]] || { echo 'invalid session name' >&2; exit 2; }
[[ -d "$workdir" ]] || { echo "missing working directory: $workdir" >&2; exit 2; }
printf -v task '%q ' "$@"
# Run once when the shell regains its prompt, including after Ctrl-C. A simple
# `task; rc=$?` tail is skipped by interactive Bash on SIGINT and loses the code.
on_return='adsl_task_exit_code=$?; unset PROMPT_COMMAND; tmux set-option -p -t "$TMUX_PANE" @adsl_task_exit_code "$adsl_task_exit_code"; printf "\n[aDSL] task exited: rc=%s; interactive shell ready (host=%s).\n" "$adsl_task_exit_code" "${HOSTNAME:-unknown}"'
printf -v input 'PROMPT_COMMAND=%q; ( %s)' "$on_return" "$task"
# No login/root startup scripts, no inherited errexit, no global tmux settings.
# Refusing an existing name also ensures we never type into another user's pane.
tmux new-session -d -s "$session" -c "$workdir" bash --noprofile --norc +e +u -i
tmux set-option -w -t "$session:0" remain-on-exit off
tmux set-option -p -t "$session:0.0" @adsl_task_exit_code running
tmux send-keys -t "$session:0.0" -l "$input"
tmux send-keys -t "$session:0.0" Enter
printf 'Interactive session: %s\nAttach: tmux attach -t %s\n' "$session" "$session"
