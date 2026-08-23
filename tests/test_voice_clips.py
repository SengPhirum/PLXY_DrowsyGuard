"""The alert clips must be in the one format the I2S path can stream.

`board_audio.cpp` streams mono 16-bit PCM at 16 kHz and nothing else. A clip in
another format does not fail loudly - `voice_clips.cpp` rejects it and drops back to
a tone pattern, so the only symptom is that the device beeps instead of speaking,
which is easy to mistake for "the clips are not wired up yet".

There is also nobody in CI who can listen to these, which is why several of the
checks below measure the audio rather than just its header. Silence is a real
failure mode and not a hypothetical one: a TTS endpoint that does not know a
language answers 200 with perfectly valid, perfectly silent audio.
"""
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / 'firmware/esp32s3/assets/audio'
CMAKE = ROOT / 'firmware/esp32s3/main/CMakeLists.txt'
CLIPS_CPP = ROOT / 'firmware/esp32s3/main/voice_clips.cpp'

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2          # bytes, i.e. 16-bit

# One per AlertReason in voice_alert.h.
REASONS = ('drowsy', 'microsleep', 'yawning', 'head_nod')

# Both languages ship embedded, so the board speaks with no SD card in it. English
# comes from Windows SAPI, Khmer from an online engine - see
# scripts/make_voice_clips.py. Either can be replaced by a hand recording on the
# card without a rebuild, which is the point of the lookup order.
LANGS = ('en', 'km')

# -3 dBFS, set by scripts/make_voice_clips.py.
EXPECTED_PEAK = int(0.70 * 32767)


def clip(lang: str, reason: str) -> Path:
    return AUDIO / f'{lang}_{reason}.wav'


@pytest.mark.parametrize('reason', REASONS)
@pytest.mark.parametrize('lang', LANGS)
def test_clip_exists(lang, reason):
    p = clip(lang, reason)
    engine = ' --engine google' if lang == 'km' else ''
    assert p.is_file(), (
        f'{p.name} is missing; regenerate with '
        f'python scripts/make_voice_clips.py --lang {lang}{engine}')


@pytest.mark.parametrize('reason', REASONS)
@pytest.mark.parametrize('lang', LANGS)
def test_clip_is_the_format_the_firmware_streams(lang, reason):
    p = clip(lang, reason)
    if not p.is_file():
        pytest.skip('covered by test_clip_exists')
    with wave.open(str(p), 'rb') as w:
        assert w.getnchannels() == CHANNELS, f'{p.name} is not mono'
        assert w.getsampwidth() == SAMPLE_WIDTH, f'{p.name} is not 16-bit'
        assert w.getframerate() == SAMPLE_RATE, f'{p.name} is not {SAMPLE_RATE} Hz'
        assert w.getcomptype() == 'NONE', f'{p.name} is compressed'
        secs = w.getnframes() / w.getframerate()
    # A warning has to finish while it is still a warning. The alert cooldown is
    # 30 s so there is room, but past a few seconds it stops being an alarm and
    # starts being a monologue.
    assert 0.3 < secs < 3.5, f'{p.name} is {secs:.1f} s long'


@pytest.mark.parametrize('reason', REASONS)
@pytest.mark.parametrize('lang', LANGS)
def test_clip_is_embedded_in_the_build(lang, reason):
    """A clip nobody links in is a clip that silently never plays."""
    text = CMAKE.read_text(encoding='utf-8')
    assert f'{lang}_{reason}.wav' in text, (
        f'{lang}_{reason}.wav is not in EMBED_FILES; the firmware will fall back '
        'to a tone for this reason')


@pytest.mark.parametrize('reason', REASONS)
@pytest.mark.parametrize('lang', LANGS)
def test_clip_is_in_the_lookup_table(lang, reason):
    """EMBED_FILES only makes the bytes available. `kEmbedded` in voice_clips.cpp
    is what maps a (language, reason) pair to them, and a clip in one but not the
    other is a clip that cannot be found - it links, it ships, it never plays."""
    src = CLIPS_CPP.read_text(encoding='utf-8')
    assert f'{{"{lang}", "{reason}", {lang}_{reason}_wav_start' in src, (
        f'{lang}_{reason} is not in the kEmbedded table in voice_clips.cpp')


@pytest.mark.parametrize('reason', REASONS)
@pytest.mark.parametrize('lang', LANGS)
def test_clip_contains_speech_and_is_levelled(lang, reason):
    """Measure the audio, because nobody here can hear it.

    Two things this catches that a header check cannot: an engine that returned
    silence for a language it does not support, and a clip at the wrong level. The
    second matters because this is an alarm - the first Khmer set had the
    *microsleep* warning, the most urgent one, at a third of the amplitude of the
    others, which would have made the worst case the quietest.
    """
    p = clip(lang, reason)
    if not p.is_file():
        pytest.skip('covered by test_clip_exists')
    np = pytest.importorskip('numpy')

    with wave.open(str(p), 'rb') as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float64)
        rate = w.getframerate()
    assert x.size, f'{p.name} has no samples'

    peak = float(np.abs(x).max())
    assert peak > 1000, f'{p.name} is silent - the engine returned no speech'
    assert abs(peak - EXPECTED_PEAK) <= 2, (
        f'{p.name} peaks at {peak:.0f}, expected {EXPECTED_PEAK}: it was not '
        'normalised, so alert loudness would vary by reason')

    # Fraction of 20 ms frames carrying signal. A clip that is mostly padding would
    # pass a peak test while saying almost nothing.
    win = rate // 50
    frames = x[:x.size // win * win].reshape(-1, win)
    voiced = float((np.abs(frames).max(axis=1) > 0.02 * 32768).mean())
    assert voiced > 0.20, f'{p.name} is only {voiced * 100:.0f}% voiced'


def test_embedded_clips_stay_within_a_sane_flash_budget():
    """They are raw PCM in the app partition, so the cost is real and visible."""
    total = sum(clip(l, r).stat().st_size
                for l in LANGS for r in REASONS if clip(l, r).is_file())
    assert total < 1400 * 1024, (
        f'the clips total {total / 1024:.0f} kB of flash; shorten the phrases in '
        'scripts/make_voice_clips.py')


def test_the_reason_names_match_the_firmware():
    """The filename stem is voice_alert_clip_name(), so a rename there breaks the
    lookup without breaking the build: the clip is simply never found."""
    src = (ROOT / 'firmware/esp32s3/main/voice_alert.cpp').read_text(encoding='utf-8')
    for reason in REASONS:
        assert f'return "{reason}"' in src, (
            f'voice_alert_clip_name() no longer returns "{reason}", so '
            f'*_{reason}.wav will never be found')


def test_the_language_codes_match_the_firmware():
    """`voice_alert_set_language_code()` is what the web page posts to, and its
    accepted codes have to be the ones the filenames use."""
    src = (ROOT / 'firmware/esp32s3/main/voice_alert.cpp').read_text(encoding='utf-8')
    for lang in LANGS:
        assert f'strcmp(code, "{lang}")' in src, (
            f'"{lang}" is not accepted by voice_alert_set_language_code()')
