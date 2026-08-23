---
title: Datasets
---

# Datasets

Every dataset decision in this project follows one rule:

> **Use subject-independent train/validation/test splits. Never leak
> neighbouring frames from the same driver across splits.**

## Input layout

`prepare` expects subject directories, and each class directory may hold images,
videos, or both:

```text
data/raw/subject_01/alert/*.png        data/raw/subject_02/alert/session.mp4
data/raw/subject_01/drowsy/*.png       data/raw/subject_02/drowsy/session.mp4
```

Videos are decoded to frames during `prepare`, so every frame of a clip stays
with its subject in exactly one split.

## Preparing a split

```bash
drowsyguard prepare --input data/raw --output data/processed --stride 5
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--train` | `0.70` | fraction of **subjects** in the training split |
| `--val` | `0.15` | fraction of subjects in validation (the rest is test) |
| `--seed` | `42` | split seed |
| `--stride N` | `1` | keep every Nth frame when a class dir contains videos |
| `--link` | off | hardlink instead of copying where possible |
| `--overwrite` | off | replace an existing split |

`--stride` matters because consecutive video frames are near-duplicates: keeping
all of them inflates the apparent dataset size without adding information.

`--link` matters for multi-GB datasets. Because hardlinks share storage, the raw
corpus, `data/raw` and `data/processed` cost **one copy of the bytes** between
them, and deleting one tree leaves the others intact.

!!! warning "Re-splitting is refused by default"
    Splitting into a non-empty output would leave the previous split's files in
    place and put one subject in two splits. `prepare` refuses; pass
    `--overwrite` to replace it deliberately.

`prepare` prints one line per split with the subject count, per-class image
counts and the subject names, plus the number of videos decoded.

## Driver Drowsiness Dataset (DDD)

DDD ships as two flat class folders, but subject identity is recoverable: the
alphabetic filename prefix is the subject and **case is the label**, so
`A0001.png` in `Drowsy/` and `a0001.png` in `Non Drowsy/` are the same person.
The importer rebuilds the subject layout so splits stay subject-independent:

```bash
drowsyguard import-ddd --input "Driver Drowsiness Dataset (DDD)" --output data/raw
drowsyguard prepare --input data/raw --output data/processed --link
drowsyguard train --config configs/train_ddd.yaml
```

This yields **28 subjects / 41,793 images**. Subjects `F` and `T` have drowsy
frames only. `import-ddd` hardlinks by default; pass `--copy` to duplicate the
bytes instead.

!!! danger "Do not train on the raw class folders"
    A random split over `Drowsy/` and `Non Drowsy/` directly puts the same face —
    and adjacent frames of one video — in both train and test. That inflates
    accuracy and violates the thesis principle. It is why published DDD
    accuracies near 99% are usually not comparable to a subject-independent
    number.

## Inspecting a split per driver

An average hides drivers the model fails on entirely:

```bash
python -m drowsyguard.cli evaluate --config configs/train_ddd.yaml \
    --checkpoint models/<your-checkpoint>.pt --per-subject
```

## What ships in this repository

No trained drowsiness model ships here. A `TinyDrowsyNet` trained from scratch
on DDD did not generalise across drivers, so model selection is open — see
`PROJECT_STATE.md` and [Training](training.md).

Datasets themselves are never committed: `data/raw/`, `data/processed/`, model
weights and the DDD corpus are all in `.gitignore`. Download corpora outside
version control and import them into `data/raw`.
