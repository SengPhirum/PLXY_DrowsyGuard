"""Covers DDD subject recovery and video/image ingestion in prepare_dataset."""
import numpy as np
import pytest
from PIL import Image

from drowsyguard.data import CLASSES, iter_video_frames, prepare_dataset
from drowsyguard.ingest import ddd_subject_of, import_ddd

cv2 = pytest.importorskip('cv2')


def _png(path, value=128, size=32):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((size, size, 3), value, np.uint8)).save(path)


def _video(path, frames=12, size=32):
    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (size, size))
    for i in range(frames):
        vw.write(np.full((size, size, 3), (i * 20) % 255, np.uint8))
    vw.release()


def test_ddd_subject_prefix_is_case_insensitive():
    assert ddd_subject_of('A0001') == 'A'
    assert ddd_subject_of('a0002') == 'A'
    assert ddd_subject_of('zc0007') == 'ZC'
    assert ddd_subject_of('ZC0007') == 'ZC'
    assert ddd_subject_of('0001') is None


def test_import_ddd_pairs_case_variants_into_one_subject(tmp_path):
    src = tmp_path / 'DDD'
    _png(src / 'Drowsy' / 'A0001.png')
    _png(src / 'Drowsy' / 'A0002.png')
    _png(src / 'Non Drowsy' / 'a0001.png')
    _png(src / 'Drowsy' / 'F0001.png')          # drowsy-only subject
    _png(src / 'Non Drowsy' / 'zc0001.png')
    _png(src / 'Drowsy' / 'ZC0001.png')

    out = tmp_path / 'raw'
    report = import_ddd(src, out, link=False)

    assert report['subjects'] == 3
    assert report['counts']['A'] == {'alert': 1, 'drowsy': 2}
    assert report['single_class_subjects'] == ['F']
    # Same person's two labels must land under one subject directory.
    assert (out / 'subject_A' / 'drowsy').is_dir()
    assert (out / 'subject_A' / 'alert').is_dir()
    assert sorted(p.name for p in (out / 'subject_A' / 'drowsy').iterdir()) == ['A0001.png', 'A0002.png']


def test_import_ddd_rejects_wrong_layout(tmp_path):
    (tmp_path / 'Drowsy').mkdir()
    with pytest.raises(ValueError, match='missing'):
        import_ddd(tmp_path, tmp_path / 'out')


def test_iter_video_frames_stride(tmp_path):
    clip = tmp_path / 'clip.mp4'
    _video(clip, frames=12)
    assert len(list(iter_video_frames(clip, stride=1))) == 12
    assert len(list(iter_video_frames(clip, stride=3))) == 4
    assert len(list(iter_video_frames(clip, stride=1, max_frames=5))) == 5


def test_prepare_dataset_accepts_images_and_videos(tmp_path):
    raw = tmp_path / 'raw'
    for i in range(3):
        subject = f'subject_{i}'
        _png(raw / subject / 'alert' / 'a.png')
        _video(raw / subject / 'drowsy' / 'clip.mp4', frames=10)

    out = tmp_path / 'processed'
    report = prepare_dataset(raw, out, seed=1, stride=5)

    assert report['videos_decoded'] == 3
    total = sum(report['counts'][s][c] for s in report['splits'] for c in CLASSES)
    assert total == 3 * (1 + 2)  # one image + two strided frames per subject

    # Every produced frame must carry its subject prefix, so leakage is auditable.
    for split in report['splits']:
        for cls in CLASSES:
            d = out / split / cls
            if d.exists():
                assert all('__' in p.name for p in d.iterdir())


def test_prepare_refuses_to_resplit_over_existing_output(tmp_path):
    raw = tmp_path / 'raw'
    for i in range(5):
        _png(raw / f'subject_{i}' / 'alert' / 'a.png')
        _png(raw / f'subject_{i}' / 'drowsy' / 'd.png')
    out = tmp_path / 'processed'
    prepare_dataset(raw, out, seed=1)

    # A second split with a different seed would strand the first split's files.
    with pytest.raises(ValueError, match='leak subjects'):
        prepare_dataset(raw, out, seed=2)

    report = prepare_dataset(raw, out, seed=2, overwrite=True)
    owner = {}
    for split in report['splits']:
        for cls in CLASSES:
            d = out / split / cls
            if not d.exists():
                continue
            for p in d.iterdir():
                subject = p.name.split('__')[0]
                assert owner.setdefault(subject, split) == split


def test_prepare_dataset_keeps_subjects_in_one_split(tmp_path):
    raw = tmp_path / 'raw'
    for i in range(6):
        _png(raw / f'subject_{i}' / 'alert' / 'a.png')
        _png(raw / f'subject_{i}' / 'drowsy' / 'd.png')
    out = tmp_path / 'processed'
    report = prepare_dataset(raw, out, seed=3)

    owner = {}
    for split in report['splits']:
        for cls in CLASSES:
            d = out / split / cls
            if not d.exists():
                continue
            for p in d.iterdir():
                subject = p.name.split('__')[0]
                assert owner.setdefault(subject, split) == split
