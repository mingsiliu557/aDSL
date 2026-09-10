# aDSL Agent Working Rules

## Scope control

- Do not over-engineer or expand the user's request beyond its stated goal.
- Default to the smallest change that directly solves the requested problem.
- Do not introduce new frameworks, abstractions, schedulers, state machines, experiment suites, or large refactors unless they are necessary for the requested result.
- Do not turn an analysis request into an implementation task unless the user explicitly asks for implementation.
- Do not turn a small validation request into a large benchmark or multi-case experiment.

## Before expanding work

- If a proposed change materially broadens the implementation scope, explain:
  1. what additional code would be changed;
  2. why it is necessary;
  3. what simpler alternative exists.
- Obtain the user's confirmation before performing that expansion.
- Treat useful but nonessential improvements as optional follow-up work, not part of the current task.

## Implementation discipline

- Preserve existing project structure and behavior unless changing them is required.
- Avoid unrelated cleanup and speculative refactoring.
- Reuse existing interfaces and tools when possible.
- Add tests and documentation in proportion to the requested change.
- When the requested outcome is already achieved, stop and report it instead of continuing to add features.

## Communication

- When work is taking longer than expected, promptly state the concrete blocker or source of complexity.
- Clearly distinguish:
  - required changes;
  - validation needed for the required changes;
  - optional future improvements.
- If the scope is ambiguous, pause and confirm it with the user before making broad changes.
