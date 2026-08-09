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


def make_loader(path, cfg, shuffle=False):
    ds = FolderDataset(path, cfg['image_size'])
    return DataLoader(ds, batch_size=cfg['batch_size'], shuffle=shuffle, num_workers=cfg.get('num_workers', 0))


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
    tr = make_loader(cfg['train_dir'], cfg, True); va = make_loader(cfg['val_dir'], cfg, False)
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
        print(f'epoch={epoch} loss={total/len(tr.dataset):.4f} val_acc={acc:.4f}')
        if acc > best:
            best = acc
            torch.save({'model': model.state_dict(), 'cfg': cfg}, cfg['checkpoint'])
    print(f'best_val_acc={best:.4f} checkpoint={cfg["checkpoint"]}')


def evaluate_checkpoint(config_path, checkpoint):
    cfg = load_cfg(config_path); device = torch.device('cpu')
    model = TinyDrowsyNet().to(device)
    state = torch.load(checkpoint, map_location=device)
    model.load_state_dict(state['model'])
    te = make_loader(cfg['test_dir'], cfg, False)
    ys, ps = evaluate_loader(model, te, device)
    print(classification_report(ys, ps, target_names=cfg['class_names'], digits=4, zero_division=0))
    print('confusion_matrix=')
    print(confusion_matrix(ys, ps))
