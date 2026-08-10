"""Importers that convert public datasets into the subject-based raw layout.

The training pipeline requires `<raw>/<subject>/<alert|drowsy>/`, because splits
must be subject-independent. Public drowsiness datasets usually ship as flat
class folders, so an importer's real job is recovering subject identity.
"""
from pathlib import Path

from .data import CLASSES, EXTS, VIDEO_EXTS, place_file

# The Kaggle "Driver Drowsiness Dataset (DDD)" ships two flat class folders.
# Subject is encoded in the alphabetic filename prefix and case encodes the
# class: `A0001.png` in Drowsy and `a0001.png` in Non Drowsy are the same person.
DDD_SOURCE_DIRS = {'drowsy': 'Drowsy', 'alert': 'Non Drowsy'}


def ddd_subject_of(stem):
    """'A0001' -> 'A', 'zc0007' -> 'ZC'. Returns None if there is no prefix."""
    alpha = ''.join(ch for ch in stem if ch.isalpha())
    return alpha.upper() or None


def import_ddd(input_dir, output_dir, link=True):
    """Rewrite DDD into `<output>/subject_<ID>/<class>/`, preserving subjects.

    Uses hardlinks by default: the dataset is a few GB and this layout is an index
    over the same bytes, not a second copy.
    """
    src = Path(input_dir)
    out = Path(output_dir)
    missing = [d for d in DDD_SOURCE_DIRS.values() if not (src / d).is_dir()]
    if missing:
        raise ValueError(f'Not a DDD directory: missing {missing} under {src}')

    counts = {}
    unnamed = 0
    for cls, folder in DDD_SOURCE_DIRS.items():
        for item in sorted((src / folder).iterdir()):
            if item.suffix.lower() not in EXTS | VIDEO_EXTS:
                continue
            subject = ddd_subject_of(item.stem)
            if subject is None:
                unnamed += 1
                continue
            place_file(item, out / f'subject_{subject}' / cls / item.name, link)
            counts.setdefault(subject, {c: 0 for c in CLASSES})[cls] += 1

    single = sorted(s for s, c in counts.items() if not all(c[k] for k in CLASSES))
    return {
        'subjects': len(counts),
        'counts': dict(sorted(counts.items())),
        'single_class_subjects': single,
        'skipped_unnamed': unnamed,
        'total': sum(sum(c.values()) for c in counts.values()),
    }


def summarize_import(report):
    lines = [f"subjects: {report['subjects']}   images: {report['total']}"]
    for subject, c in report['counts'].items():
        lines.append(f"  {subject:<3} alert={c['alert']:>6}  drowsy={c['drowsy']:>6}")
    if report['single_class_subjects']:
        lines.append('single-class subjects (only one label present): '
                     + ', '.join(report['single_class_subjects']))
    if report['skipped_unnamed']:
        lines.append(f"skipped files with no subject prefix: {report['skipped_unnamed']}")
    return '\n'.join(lines)
