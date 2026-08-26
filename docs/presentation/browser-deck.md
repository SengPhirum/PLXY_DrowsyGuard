---
title: Browser deck
description: Presenting, exporting and editing the standalone HTML slide deck.
---

# Browser deck

`drowsyguard-slides.html` is an eight-slide deck for the lecturer review, built
from `docs/research-proposal.md`, the Word proposal in
`docs/assets/documents/`, and the measured numbers in `PROJECT_STATE.md`.

| # | Slide | Carries |
| --- | --- | --- |
| 01 | Cover | Title, team, lecturer, and the four cue periods as a readout |
| 02 | The problem | Why one frame is not drowsiness: blink vs microsleep strips |
| 03 | Concept — cues | The five landmarks and the period that defines each cue |
| 04 | Concept — decision | Weighted fusion, the 0.55 trigger, and the 0.55 s hold |
| 05 | Process flow | Capture → find → read → fuse → hold → speak, plus support paths |
| 06 | Equipment | Nine parts, one illustration each, USD 27.25 total |
| 07 | Where we are | Bench photo and the measured results, including the open gap |
| 08 | Conclusion | Training the lightweight on-device model, and the KPI targets |

## Presenting it

Open the file in any browser. Arrow keys, PageUp/PageDown, space, Home and End
move between slides; the numbered rail on the right jumps to one directly. Each
slide is a fixed 16:9 frame that scales to the window, so full-screen (F11)
gives a clean projector view.

## Exporting a PDF

Ctrl+P (Cmd+P) with **Landscape** and **Background graphics** enabled prints one
slide per A4 page. `@page { size: A4 landscape }` is already set, so the page
size needs no adjustment.

## Adding the bench photo

Slide 07 looks for a photo at `images/prototype-build.jpg`. Drop the real
photograph of the assembled prototype there and it replaces the drawn build
diagram automatically — no edit to the HTML needed. Any browser-readable JPEG
works; roughly 3:2 landscape matches the slot best.

Without that file the slide falls back to the labelled illustration of the same
build, which is what a viewer sees when the deck is opened outside the repo.

## Editing

Everything is in the one HTML file: the palette is a token block at the top
(inheriting the dodgerblue primary from `docs/stylesheets/extra.css`), and each
slide is a `<section class="frame">`. Sizes inside a slide are in `cqw`
(percent of the slide's own width) so the layout holds at any projector
resolution. Diagrams are hand-authored inline SVG with no dependencies.
