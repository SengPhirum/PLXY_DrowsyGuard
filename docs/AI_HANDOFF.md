# AI Handoff Protocol

Any AI or human continuing this repository should read, in order:
1. `PROJECT_STATE.md`
2. `ROADMAP.md`
3. `CHANGELOG.md`
4. `docs/THESIS_PLAN.md`
5. `docs/DEPLOYMENT.md`

## Rules for changes
- Do not weaken subject-independent evaluation.
- Do not claim automotive certification.
- Do not claim hardware compatibility until flashed/tested on the named board.
- Record material decisions in `PROJECT_STATE.md`.
- Add every completed engineering milestone to `CHANGELOG.md`.
- Mark roadmap checkboxes only after reproducible evidence exists.
- Prefer measurable thesis contributions over feature creep.

## Suggested prompt for another AI
"Read PROJECT_STATE.md, ROADMAP.md, CHANGELOG.md, docs/THESIS_PLAN.md and docs/AI_HANDOFF.md. Continue from the documented state. Preserve subject-independent evaluation and the ESP32-S3 resource constraints. Update project history whenever you change architecture, dependencies, experiments or firmware assumptions."
