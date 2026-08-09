from pathlib import Path
import random
import shutil
from PIL import Image, ImageOps
from torch.utils.data import Dataset
import torch
import numpy as np

EXTS = {'.jpg', '.jpeg', '.png', '.bmp'}


def subject_split(subjects, train=0.70, val=0.15, seed=42):
    subjects = sorted(subjects)
    rnd = random.Random(seed)
    rnd.shuffle(subjects)
    n = len(subjects)
    n_train = max(1, int(n * train)) if n >= 3 else max(1, n - 2)
    n_val = max(1, int(n * val)) if n >= 3 else (1 if n >= 2 else 0)
    train_s = subjects[:n_train]
    val_s = subjects[n_train:n_train+n_val]
    test_s = subjects[n_train+n_val:]
    if n >= 3 and not test_s:
        test_s = [val_s.pop()]
    return {'train': train_s, 'val': val_s, 'test': test_s}


def prepare_dataset(input_dir, output_dir, train=0.70, val=0.15, seed=42):
    src = Path(input_dir)
    out = Path(output_dir)
    subjects = [p.name for p in src.iterdir() if p.is_dir()]
    if not subjects:
        raise ValueError('No subject_* directories found')
    splits = subject_split(subjects, train, val, seed)
    for split, names in splits.items():
        for subject in names:
            for cls in ('alert', 'drowsy'):
                cdir = src / subject / cls
                if not cdir.exists():
                    continue
                for img in cdir.iterdir():
                    if img.suffix.lower() in EXTS:
                        dest = out / split / cls
                        dest.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(img, dest / f'{subject}__{img.name}')
    return splits


class FolderDataset(Dataset):
    def __init__(self, root, image_size=64):
        self.samples = []
        self.image_size = image_size
        root = Path(root)
        for label, cls in enumerate(('alert', 'drowsy')):
            cdir = root / cls
            if cdir.exists():
                for p in sorted(cdir.iterdir()):
                    if p.suffix.lower() in EXTS:
                        self.samples.append((p, label))
        if not self.samples:
            raise ValueError(f'No images found under {root}')

    def __len__(self): return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = Image.open(path).convert('L')
        img = ImageOps.fit(img, (self.image_size, self.image_size))
        arr = np.asarray(img, dtype=np.float32) / 255.0
        x = torch.from_numpy(arr).unsqueeze(0)
        return x, torch.tensor(label, dtype=torch.long)
