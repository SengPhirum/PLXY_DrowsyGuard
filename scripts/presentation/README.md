# Presentation deck generator

Builds `docs/assets/documents/DrowsyGuard-Presentation.pptx` - the six-slide deck
for presenting the research proposal. Rendered previews and a slide-by-slide
summary live in [`docs/presentation.md`](../../docs/presentation.md).

```bash
npm install     # pptxgenjs + sharp, first time only
npm run build   # gen_assets.js, then build_deck.js
```

| File | What it does |
| --- | --- |
| `gen_assets.js` | Draws every illustration into `build/`: component art and cue icons as SVG rasterised with sharp, plus two photo crops taken from `docs/assets/images/drowsy-guard-cover.png`. |
| `build_deck.js` | Lays out the six slides and writes the `.pptx`, including speaker notes. |

`build/` and `node_modules/` are ignored; only the built deck is tracked. Nothing
here reads the firmware or the training pipeline, so it runs without ESP-IDF,
torch or a board attached.

Two things to know before editing:

- **Numbers on slides 2 and 5 are sourced, not invented.** The cue thresholds come
  from `src/drowsyguard/behavior.py` and the risk trigger from
  `src/drowsyguard/risk.py`; the measured timings come from the 2026-08-23
  hardware run recorded in `CHANGELOG.md`. Change them here when those change
  there, not the other way round.
- **Slide 5 carries a deliberate empty panel** for a live-preview screenshot.
  It is a dashed placeholder, not an oversight - replace it once a board is
  running in front of a real face.
