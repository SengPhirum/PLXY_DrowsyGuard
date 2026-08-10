from pathlib import Path
import random, yaml, numpy as np, torch
from torch.utils.data import DataLoader
from sklearn.metrics import classification_report, confusion_matrix
from .model import TinyDrowsyNet
from .data import FolderDataset


def load_cfg(path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def seed_all(seed):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)


def make_loader(path, cfg, shuffle=False, augment=False):
    # Augmentation is training-only; val/test must stay a fixed measurement.
    ds = FolderDataset(path, cfg['image_size'],
                       augment=augment and cfg.get('augment', False),
                       normalize=cfg.get('normalize', False),
                       seed=cfg.get('seed', 0))
    return DataLoader(ds, batch_size=cfg['batch_size'], shuffle=shuffle,
                      num_workers=cfg.get('num_workers', 0))


def evaluate_loader(model, loader, device):
    ys, ps = [], []
    model.eval()
    with torch.no_grad():
        for x, y in loader:
            logits = model(x.to(device))
            pred = logits.argmax(1).cpu().numpy().tolist()
            ys.extend(y.numpy().tolist()); ps.extend(pred)
    return ys, ps


def train_model(config_path):
    cfg = load_cfg(config_path); seed_all(cfg['seed'])
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    tr = make_loader(cfg['train_dir'], cfg, True, augment=True)
    va = make_loader(cfg['val_dir'], cfg, False)
    model = TinyDrowsyNet().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg['learning_rate'], weight_decay=cfg['weight_decay'])
    loss_fn = torch.nn.CrossEntropyLoss()
    best = -1.0
    Path(cfg['checkpoint']).parent.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, cfg['epochs'] + 1):
        model.train(); total = 0.0
        for x, y in tr:
            x, y = x.to(device), y.to(device)
            opt.zero_grad(set_to_none=True)
            loss = loss_fn(model(x), y)
            loss.backward(); opt.step(); total += loss.item() * x.size(0)
        ys, ps = evaluate_loader(model, va, device)
        acc = float(np.mean(np.array(ys) == np.array(ps)))
        # flush: training is usually run with output redirected to a log, where
        # Python block-buffers and progress would otherwise be invisible for hours.
        print(f'epoch={epoch} loss={total/len(tr.dataset):.4f} val_acc={acc:.4f}', flush=True)
        if acc > best:
            best = acc
            torch.save({'model': model.state_dict(), 'cfg': cfg}, cfg['checkpoint'])
    print(f'best_val_acc={best:.4f} checkpoint={cfg["checkpoint"]}')


def evaluate_checkpoint(config_path, checkpoint, per_subject=False, split=None):
    cfg = load_cfg(config_path); device = torch.device('cpu')
    model = TinyDrowsyNet().to(device)
    state = torch.load(checkpoint, map_location=device)
    model.load_state_dict(state['model'])
    split_dir = cfg[f'{split}_dir'] if split else cfg['test_dir']
    te = make_loader(split_dir, cfg, False)
    ys, ps = evaluate_loader(model, te, device)
    print(f'split={split_dir}  images={len(ys)}  normalize={cfg.get("normalize", False)}')
    print(classification_report(ys, ps, target_names=cfg['class_names'], digits=4, zero_division=0))
    print('confusion_matrix=')
    print(confusion_matrix(ys, ps))
    if per_subject:
        print_per_subject(te.dataset, ys, ps, cfg['class_names'])


def print_per_subject(dataset, ys, ps, class_names):
    """Break accuracy down by driver.

    Overall accuracy hides the failure mode that matters for this project: a model
    can average acceptably while being unusable on individual drivers.
    """
    # Loader was built with shuffle=False, so predictions align with dataset.samples.
    per = {}
    for (path, _), y, p in zip(dataset.samples, ys, ps):
        subject = Path(path).name.split('__')[0]
        d = per.setdefault(subject, {c: [0, 0] for c in class_names})
        cell = d[class_names[y]]
        cell[1] += 1
        cell[0] += int(y == p)

    head = f'\n{"subject":<14}' + ''.join(f'{c + " acc":>12}' for c in class_names) + f'{"overall":>10}'
    print(head)
    for subject in sorted(per):
        cells = per[subject]
        correct = sum(v[0] for v in cells.values())
        total = sum(v[1] for v in cells.values())
        row = f'{subject:<14}'
        for c in class_names:
            got, n = cells[c]
            row += f'{(got / n if n else float("nan")):>12.3f}'
        print(row + f'{correct / total:>10.3f}')
