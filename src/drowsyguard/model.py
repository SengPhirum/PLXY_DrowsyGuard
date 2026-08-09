import torch
from torch import nn


class TinyDrowsyNet(nn.Module):
    """Small CNN intended for 64x64 grayscale input and INT8 deployment."""

    def __init__(self, num_classes: int = 2):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 8, 3, stride=2, padding=1, bias=False),
            nn.BatchNorm2d(8), nn.ReLU(inplace=True),
            nn.Conv2d(8, 16, 3, stride=2, padding=1, groups=8, bias=False),
            nn.BatchNorm2d(16), nn.ReLU(inplace=True),
            nn.Conv2d(16, 24, 3, stride=2, padding=1, groups=8, bias=False),
            nn.BatchNorm2d(24), nn.ReLU(inplace=True),
            nn.Conv2d(24, 32, 3, stride=2, padding=1, groups=8, bias=False),
            nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1),
        )
        self.classifier = nn.Linear(32, num_classes)

    def forward(self, x):
        x = self.features(x)
        x = torch.flatten(x, 1)
        return self.classifier(x)
