import torch
from drowsyguard.model import TinyDrowsyNet

def test_shape():
    m=TinyDrowsyNet(); y=m(torch.zeros(2,1,64,64)); assert tuple(y.shape)==(2,2)
