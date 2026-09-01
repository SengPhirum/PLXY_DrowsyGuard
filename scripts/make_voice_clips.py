#!/usr/bin/env python3
"""Generate the spoken alert clips the firmware plays.

The firmware wants mono 16-bit PCM at 16 kHz - that is what `board_audio.cpp`
streams and what `firmware/esp32s3/assets/audio/README.md` has specified since
before there was any hardware. Anything producing a WAV in that format will do.

Two engines, because one machine cannot cover both languages:

    --engine sapi     Windows' built-in voices. Offline, no account, good quality,
                      and en-US only on this machine (David and Zira).
    --engine google   Google Translate's TTS endpoint. Needs network, and is the
                      only option here that speaks Khmer.

    python scripts/make_voice_clips.py --lang en                    # SAPI
    python scripts/make_voice_clips.py --lang km --engine google    # online
    python scripts/make_voice_clips.py --list                       # SAPI voices
    python scripts/make_voice_clips.py --check                      # validate

**Two things to know about the online engine before relying on it.**

It is an undocumented endpoint meant for the Translate web UI, not a supported API.
It has no stability guarantee, it rate-limits, and it caps the text length - so it
is a way to get a usable Khmer clip today, not a build dependency. The generated
files are committed, so a normal build never touches the network.

And the audio is Google's, produced under Google's terms. For a thesis prototype
that is normally fine; for anything distributed, replace it with a recording from a
fluent speaker. That is also the better clip: this is a synthesised voice reading a
safety warning, and `assets/audio/README.md` has always said the Khmer should be
recorded and reviewed. The pipeline treats both identically - drop a
`km_<reason>.wav` in and it wins on the next boot.

Where the clips end up mattering:

  * everything here is embedded in the firmware binary (EMBED_FILES in
    main/CMakeLists.txt), so the board speaks with no SD card present;
  * the firmware prefers `/sdcard/audio/<lang>_<reason>.wav` when a card is there,
    so a clip can be replaced by copying a file rather than reflashing.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.parse
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'firmware/esp32s3/assets/audio'

SAMPLE_RATE = 16000
CHANNELS = 1
BITS = 16

# One phrase per AlertReason in voice_alert.h. Kept short on purpose: this plays
# while someone is falling asleep at a wheel, so the first word has to carry the
# message and the whole thing has to finish before it stops being useful.
#
# The Khmer column is left empty rather than machine-translated. Fill it in and
# re-run only if you have a Khmer voice installed; otherwise record the clips and
# drop them in as km_<reason>.wav.
PHRASES = {
    'en': {
        'drowsy': 'Warning. You appear drowsy.',
        'microsleep': 'Wake up! Wake up!',
        'yawning': 'You seem tired. Take a break.',
        'head_nod': 'Stay alert. Eyes on the road.',
        # Not a warning, and it is worded so it cannot be mistaken for one. A sneeze
        # closes the eyes for about a second, which the closure detector cannot
        # distinguish from a microsleep on its own - so the device suppresses the
        # drowsiness alarm and says this instead. The driver needs to know the alarm
        # stayed quiet on purpose rather than because the device stopped working.
        'sneeze': 'Sneeze detected.',
        # The one message this device has about itself, so it says what stopped
        # rather than what to do: there may be nobody there to be told what to do.
        'no_driver': 'No driver detected.',
    },
    # Khmer. Translated from the English above, and **it should be checked by a
    # Khmer speaker** - these are spoken safety warnings, and the cost of an
    # awkward phrasing is a driver who ignores the alarm. The firmware does not
    # care where the file came from, so correcting one of these is a re-run of this
    # script or a hand recording, either way.
    'km': {
        'drowsy': 'ប្រយ័ត្ន! អ្នកងងុយគេង។',
        'microsleep': 'ភ្ញាក់ឡើង! ភ្ញាក់ឡើង!',
        'yawning': 'អ្នកអស់កម្លាំង។ សូមសម្រាក។',
        'head_nod': 'ប្រុងប្រយ័ត្ន! មើលផ្លូវ។',
        'sneeze': 'រកឃើញការកណ្ដាស់។',
        'no_driver': 'រកមិនឃើញអ្នកបើកបរ។',
    },
}

# Google Translate's TTS endpoint. Not an API - it is what the Translate page
# itself calls - hence the browser User-Agent, without which it answers 403.
GOOGLE_TTS = 'https://translate.google.com/translate_tts'
GOOGLE_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

PS_LIST = r'''
Add-Type -AssemblyName System.Speech
(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() |
  ForEach-Object { "{0}|{1}" -f $_.VoiceInfo.Name, $_.VoiceInfo.Culture.Name }
'''

# SetOutputToWaveFile with an explicit SpeechAudioFormatInfo writes exactly the
# format the firmware needs, so there is no resampling step to get wrong.
PS_SPEAK = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$want = '{voice}'
if ($want) {{ try {{ $s.SelectVoice($want) }} catch {{ }} }}
$s.Rate = {rate}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    {rate_hz}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile('{path}', $fmt)
$s.Speak([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b64}')))
$s.SetOutputToNull()
$s.Dispose()
'''


def ffmpeg_exe() -> str:
    """An ffmpeg binary, for turning the endpoint's MP3 into the firmware's WAV.

    imageio-ffmpeg ships one as a wheel, which is the difference between this
    working on a bare checkout and needing a system install.
    """
    import shutil

    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError:
        raise SystemExit('need ffmpeg to decode the MP3: pip install imageio-ffmpeg')
    return imageio_ffmpeg.get_ffmpeg_exe()


def speak_google(text: str, path: Path, lang: str) -> None:
    query = urllib.parse.urlencode({
        'ie': 'UTF-8', 'q': text, 'tl': lang, 'client': 'tw-ob',
    })
    req = urllib.request.Request(f'{GOOGLE_TTS}?{query}',
                                headers={'User-Agent': GOOGLE_UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        ctype = resp.headers.get('Content-Type', '')
        mp3 = resp.read()
    if 'audio' not in ctype or len(mp3) < 512:
        raise SystemExit(f'the endpoint did not return audio for {lang!r} '
                         f'(Content-Type {ctype!r}, {len(mp3)} bytes)')

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.mp3')
    tmp.write_bytes(mp3)
    # -ar/-ac/-sample_fmt are the whole point: the firmware accepts exactly one
    # format and rejects everything else rather than playing it as noise.
    proc = subprocess.run(
        [ffmpeg_exe(), '-y', '-loglevel', 'error', '-i', str(tmp),
         '-ac', str(CHANNELS), '-ar', str(SAMPLE_RATE),
         '-sample_fmt', 's16', '-f', 'wav', str(path)],
        capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise SystemExit(f'ffmpeg failed:\n{proc.stderr[-2000:]}')


# -3 dBFS. Not louder: the amplifier applies its own gain and board_audio.h caps
# generated tones at 0.35 of full scale deliberately, because an alert has to be
# unmissable without making a half-asleep driver flinch.
NORMALISE_PEAK = 0.70


def normalise(path: Path) -> float:
    """Scale a clip to a fixed peak, in place. Returns the gain applied.

    Different engines - and different phrases from the same engine - come out at
    noticeably different levels: the first Khmer set peaked at 8711 for the
    microsleep warning against ~21000 for the others, which would have made the
    *most* urgent alert the quietest one. An alarm whose loudness depends on which
    reason fired is a worse alarm, so level is set here rather than left to chance.
    """
    import numpy as np

    with wave.open(str(path), 'rb') as w:
        params = w.getparams()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype='<i2').astype(np.float32)
    peak = float(np.abs(x).max())
    if peak < 1.0:
        return 1.0
    gain = (NORMALISE_PEAK * 32767.0) / peak
    y = np.clip(x * gain, -32768, 32767).astype('<i2')
    with wave.open(str(path), 'wb') as w:
        w.setparams(params)
        w.writeframes(y.tobytes())
    return gain


def audio_is_not_silence(path: Path) -> bool:
    """Cheap sanity check on a clip nobody here can listen to.

    A TTS endpoint that does not know a language can still answer 200 with a
    perfectly valid, perfectly silent MP3. Peak amplitude separates "it spoke" from
    "it returned nothing", without needing to understand the words.
    """
    import numpy as np

    with wave.open(str(path), 'rb') as w:
        raw = w.readframes(w.getnframes())
    if not raw:
        return False
    peak = int(np.abs(np.frombuffer(raw, dtype='<i2').astype(np.int32)).max())
    return peak > 1000        # ~3% of full scale; speech is far above this


def powershell(script: str) -> str:
    if sys.platform != 'win32':
        raise SystemExit('this script uses Windows SAPI; on other platforms record '
                         'the clips by hand as mono 16-bit 16 kHz WAV')
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f'powershell failed:\n{proc.stderr[-2000:]}')
    return proc.stdout


def installed_voices() -> list[tuple[str, str]]:
    out = []
    for line in powershell(PS_LIST).splitlines():
        if '|' in line:
            name, culture = line.strip().split('|', 1)
            out.append((name, culture))
    return out


def pick_voice(lang: str, voices: list[tuple[str, str]]) -> str | None:
    for name, culture in voices:
        if culture.lower().startswith(lang.lower()):
            return name
    return None


def speak(text: str, path: Path, voice: str, rate: int) -> None:
    import base64

    b64 = base64.b64encode(text.encode('utf-8')).decode('ascii')
    path.parent.mkdir(parents=True, exist_ok=True)
    powershell(PS_SPEAK.format(voice=voice, rate=rate, rate_hz=SAMPLE_RATE,
                               path=str(path).replace("'", "''"), b64=b64))


def check(path: Path) -> str:
    """Validate a clip against what the firmware will accept."""
    try:
        with wave.open(str(path), 'rb') as w:
            ch, width, rate, frames = (w.getnchannels(), w.getsampwidth(),
                                       w.getframerate(), w.getnframes())
    except Exception as exc:                       # noqa: BLE001 - report, not raise
        return f'unreadable ({exc})'
    problems = []
    if ch != CHANNELS:
        problems.append(f'{ch} channels, need {CHANNELS}')
    if width * 8 != BITS:
        problems.append(f'{width * 8}-bit, need {BITS}')
    if rate != SAMPLE_RATE:
        problems.append(f'{rate} Hz, need {SAMPLE_RATE}')
    secs = frames / rate if rate else 0
    if secs > 3.5:
        problems.append(f'{secs:.1f} s is too long to be useful as an alert')
    if problems:
        return 'REJECTED: ' + '; '.join(problems)
    return f'ok  {secs:.2f} s, {path.stat().st_size / 1024:.0f} kB'


def main() -> int:
    # A Windows console is cp1252 by default and raises on Khmer, so the script
    # would die printing its own progress. Nothing to do with the audio.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--lang', action='append',
                    help='language code to generate; repeatable (default: all it can)')
    ap.add_argument('--voice', help='exact SAPI voice name to use')
    ap.add_argument('--rate', type=int, default=0,
                    help='SAPI rate, -10..10 (default 0). Slower is clearer.')
    ap.add_argument('--list', action='store_true', help='list installed voices')
    ap.add_argument('--check', action='store_true',
                    help='validate the clips already in assets/audio and exit')
    ap.add_argument('--engine', choices=('sapi', 'google'), default='sapi',
                    help='sapi = offline Windows voices; google = online, does Khmer')
    args = ap.parse_args()

    if args.check:
        found = sorted(OUT.glob('*.wav'))
        if not found:
            print(f'no clips in {OUT.relative_to(ROOT)}')
            return 1
        bad = 0
        for p in found:
            verdict = check(p)
            print(f'  {p.name:24s} {verdict}')
            bad += verdict.startswith('REJECTED')
        return 1 if bad else 0

    voices = installed_voices() if (args.list or args.engine == 'sapi') else []
    if args.list:
        for name, culture in voices:
            print(f'  {culture:8s} {name}')
        return 0

    langs = args.lang or sorted(PHRASES)
    wrote = 0
    for lang in langs:
        phrases = PHRASES.get(lang) or {}
        if not phrases:
            print(f'{lang}: no phrases defined - see the note in this file. Record '
                  f'{lang}_<reason>.wav by hand and drop it in '
                  f'{OUT.relative_to(ROOT)}/ or on the SD card.')
            continue
        if args.engine == 'google':
            print(f'{lang}: Google Translate TTS (network)')
            for reason, text in phrases.items():
                path = OUT / f'{lang}_{reason}.wav'
                speak_google(text, path, lang)
                normalise(path)
                loud = audio_is_not_silence(path)
                print(f'  {path.name:24s} {check(path)}'
                      f'{"" if loud else "   *** SILENT - the endpoint returned no speech ***"}')
                print(f'  {"":24s} "{text}"')
                wrote += 1
            continue

        voice = args.voice or pick_voice(lang, voices)
        if voice is None:
            print(f'{lang}: no installed voice for this language; skipping. '
                  f'Available: {[c for _, c in voices]}. '
                  f'Try --engine google.')
            continue
        print(f'{lang}: using voice "{voice}"')
        for reason, text in phrases.items():
            path = OUT / f'{lang}_{reason}.wav'
            speak(text, path, voice, args.rate)
            normalise(path)
            print(f'  {path.name:24s} {check(path)}   "{text}"')
            wrote += 1

    if wrote:
        print(f'\n{wrote} clip(s) in {OUT.relative_to(ROOT)}')
        print('They are embedded by main/CMakeLists.txt on the next build.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
