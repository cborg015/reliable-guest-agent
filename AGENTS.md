# Project engineering guidelines

## Design decisions

For consequential product, workflow, architecture, data, privacy, reliability, API-contract, human-in-the-loop, AI-boundary, or state-model changes:

- explain the relevant tradeoffs before changing the design;
- obtain approval before implementing a material design decision;
- record accepted workflow behavior and invariants in `docs/product-spec.md`;
- record accepted architectural decisions in `docs/architecture-decisions.md`.

Approval is not required for trivial syntax, formatting, mechanical refactoring, or implementation choices already implied by an approved design.

## Milestone completion

At the end of every milestone:

- run the relevant tests and Ruff checks;
- summarize what changed and why;
- summarize the verification results;
- identify anything contributors should inspect or test manually;
- stop before creating a commit so commits and pushes remain human-controlled.
