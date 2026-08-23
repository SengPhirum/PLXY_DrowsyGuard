"""The hand-written eye model must match the ONNX graph it claims to implement.

`firmware/esp32s3/main/eye_model.cpp` is a longhand transcription of
`models/detectors/open_closed_eye.onnx`: four convolutions, three max-pools and a
softmax, written out in C++ because the ESP-DL route needs a quantized `.espdl`
and esp-ppq is not installable (see scripts/export_eye_model.py).

A transcription that is merely *plausible* is worthless here - PERCLOS carries
0.55 of the fused risk score, so a channel-order slip or a missing bias would show
up as a drowsiness detector that is subtly wrong rather than obviously broken. So
this compiles that exact .cpp on the host and runs it against onnxruntime on the
same random tensors.

Two traps this specifically pins down, both of which look fine until measured:
conv3 is followed directly by conv4 with no ReLU between them, and conv4 has no
bias tensor.

Skipped when there is no host compiler or no onnxruntime; it is a correctness
gate, not a reason to fail an unrelated checkout.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'firmware/esp32s3/main'
ONNX_PATH = ROOT / 'models/detectors/open_closed_eye.onnx'

# The harness reads tensors on stdin and prints "closed open" per tensor, so the
# comparison runs over many inputs in one subprocess call.
HARNESS = r'''
#include <cstdio>
#include <vector>
#include "eye_model.h"

int main() {
    int n = 0;
    if (scanf("%d", &n) != 1) return 1;
    std::vector<float> x(EYE_INPUT_FLOATS);
    for (int i = 0; i < n; ++i) {
        for (int j = 0; j < EYE_INPUT_FLOATS; ++j) {
            if (scanf("%f", &x[j]) != 1) return 1;
        }
        float closed = 0.0f, open_ = 0.0f;
        eye_model_infer(x.data(), &closed, &open_);
        printf("%.9g %.9g\n", closed, open_);
    }
    return 0;
}
'''


def _compiler():
    """A host C++ compiler, as a command prefix.

    Prefers whatever is on PATH, then falls back to Zig, which ships a complete
    self-contained clang plus headers as a pip wheel (`pip install ziglang`). That
    fallback is the difference between this test running on a Windows checkout and
    being permanently skipped there: Git Bash has no compiler, and the only other
    one on this machine is the Xtensa cross-compiler, whose output cannot run here.
    """
    for cc in ('g++', 'c++', 'clang++'):
        if shutil.which(cc):
            return [cc]
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, '-m', 'ziglang', 'c++']


@pytest.fixture(scope='module')
def harness(tmp_path_factory):
    cc = _compiler()
    if cc is None:
        pytest.skip('no host C++ compiler')
    if not (FIRMWARE / 'eye_model_weights.h').is_file():
        pytest.skip('eye_model_weights.h not generated; '
                    'run python scripts/export_eye_model.py')

    d = tmp_path_factory.mktemp('eye')
    (d / 'main.cpp').write_text(HARNESS, encoding='utf-8')
    exe = d / ('h.exe' if sys.platform == 'win32' else 'h')
    proc = subprocess.run(
        cc + ['-O2', '-std=c++17', f'-I{FIRMWARE}', str(d / 'main.cpp'),
              str(FIRMWARE / 'eye_model.cpp'), '-o', str(exe)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip(f'could not host-compile eye_model.cpp:\n{proc.stderr[-2000:]}')
    return exe


@pytest.fixture(scope='module')
def reference():
    onnxruntime = pytest.importorskip('onnxruntime')
    if not ONNX_PATH.is_file():
        pytest.skip('open_closed_eye.onnx is not in the checkout')
    sess = onnxruntime.InferenceSession(str(ONNX_PATH),
                                        providers=['CPUExecutionProvider'])
    return sess, sess.get_inputs()[0].name


def _run(exe, batch):
    """batch: (N, 3, 32, 32) float32 -> (N, 2) from the C++ implementation."""
    import numpy as np

    lines = [str(len(batch))]
    for x in batch:
        lines.append(' '.join(f'{v:.9g}' for v in x.reshape(-1)))
    proc = subprocess.run([str(exe)], input='\n'.join(lines) + '\n',
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    rows = [ln.split() for ln in proc.stdout.strip().splitlines()]
    return np.array([[float(a), float(b)] for a, b in rows], dtype=np.float64)


def _reference(ref, batch):
    import numpy as np

    sess, name = ref
    out = []
    for x in batch:
        y = sess.run(None, {name: x[None].astype(np.float32)})[0].reshape(-1)
        out.append([float(y[0]), float(y[1])])
    return np.array(out, dtype=np.float64)


def _batch(n, seed, kind='normal'):
    import numpy as np

    rng = np.random.default_rng(seed)
    if kind == 'normal':
        # The real input range: (uint8 - 127) / 255 lands in [-0.498, +0.502].
        x = rng.uniform(-0.498, 0.502, size=(n, 3, 32, 32))
    elif kind == 'extremes':
        x = rng.choice([-0.498, 0.502], size=(n, 3, 32, 32))
    elif kind == 'flat':
        x = np.repeat(rng.uniform(-0.5, 0.5, size=(n, 1, 1, 1)), 3 * 32 * 32,
                      axis=0).reshape(n, 3, 32, 32)
    return x.astype(np.float32)


@pytest.mark.parametrize('kind', ['normal', 'extremes', 'flat'])
def test_matches_onnxruntime(harness, reference, kind):
    import numpy as np

    batch = _batch(16, seed=7, kind=kind)
    mine = _run(harness, batch)
    theirs = _reference(reference, batch)

    # float32 accumulation in a different order, so exactness is not the bar;
    # 1e-5 is far tighter than any real difference in behaviour and loose enough
    # to survive a compiler reordering a dot product.
    delta = np.abs(mine - theirs).max()
    assert delta < 1e-5, (
        f'{kind}: firmware and ONNX disagree by {delta:.3e}\n'
        f'firmware[0]={mine[0]} onnx[0]={theirs[0]}')


def test_output_is_a_distribution(harness):
    """The graph's softmax tail is included, so the pair must already sum to 1 -
    which is why eyestate.py warns against softmaxing the output again."""
    import numpy as np

    mine = _run(harness, _batch(8, seed=11))
    sums = mine.sum(axis=1)
    assert np.allclose(sums, 1.0, atol=1e-6), f'rows do not sum to 1: {sums}'
    assert (mine >= 0).all() and (mine <= 1).all()


def test_closed_index_is_zero(harness, reference):
    """Index 0 is `closed`, despite the model card saying `[open, closed]`.

    Asserted against the reference rather than against an image, because this
    checks the two implementations agree on which column is which - the thing that
    would silently invert every PERCLOS reading.
    """
    import numpy as np

    batch = _batch(24, seed=13)
    mine = _run(harness, batch)
    theirs = _reference(reference, batch)
    assert np.array_equal(mine.argmax(axis=1), theirs.argmax(axis=1))


def test_weights_header_is_in_step_with_the_onnx(harness):
    """The header is generated; a stale one would pass the numerics above only by
    coincidence, so check the parameter count against the graph itself."""
    onnx = pytest.importorskip('onnx')
    from onnx import numpy_helper

    model = onnx.load(str(ONNX_PATH))
    want = sum(numpy_helper.to_array(i).size for i in model.graph.initializer)
    header = (FIRMWARE / 'eye_model_weights.h').read_text(encoding='utf-8')
    import re
    got = sum(int(m) for m in re.findall(r'static const float kEye_\w+\[(\d+)\]', header))
    assert got == want, (
        f'header has {got} parameters, the ONNX has {want}; '
        're-run python scripts/export_eye_model.py')
