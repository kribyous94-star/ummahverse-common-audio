#!/usr/bin/env python3
"""Generate known stereo sources with overlapping synthetic voices and M&E.

Requires NumPy and SciPy; install through Claude's pyproject.toml.
This fixture tests mechanics, not perceptual quality on films.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    sample_rate = 48_000
    seconds = 12
    n = sample_rate * seconds
    t = np.arange(n, dtype=np.float64) / sample_rate
    rng = np.random.default_rng(20260926)

    # Music harmonics plus wideband intermittent impacts and a quiet ambience.
    music = (0.10 * np.sin(2 * np.pi * 220 * t)
             + 0.06 * np.sin(2 * np.pi * 330 * t + 0.15)
             + 0.035 * np.sin(2 * np.pi * 440 * t))
    noise = sosfilt(butter(3, [120, 7500], btype='bandpass', fs=sample_rate,
                            output='sos'), rng.normal(size=n))
    ambience = 0.008 * noise / max(np.std(noise), 1e-12)
    impacts = np.zeros(n)
    for sec in (2.1, 5.4, 8.7, 10.2):
        start = int(sec * sample_rate)
        length = min(int(.35 * sample_rate), n - start)
        impacts[start:start + length] += (0.08 * rng.normal(size=length)
                                           * np.exp(-np.arange(length) / 1800))
    shared = music + ambience + impacts
    common = np.column_stack((shared, 0.94 * shared + 0.018 * np.sin(2*np.pi*110*t)))

    def voice(pitch: float, shift: float) -> np.ndarray:
        # Intermittent, speech-like harmonic energy, explicitly overlapping M&E.
        gate = (np.sin(2*np.pi*(1.7 + shift)*t) > -0.3).astype(float)
        gate *= ((t > 0.5) & (t < 11.3)).astype(float)
        gate = np.convolve(gate, np.ones(321)/321, mode='same')
        phase = 2*np.pi*(pitch*t + 6*np.sin(2*np.pi*.7*t))
        signal = 0.08 * gate * (np.sin(phase) + .4*np.sin(2*phase)
                                + .15*np.sin(3*phase))
        return np.column_stack((signal, .88 * signal))

    voices = [voice(143, .00), voice(187, .16), voice(223, .27)]
    offsets = [0, 3600, 6240]  # B: +75 ms; third: +130 ms.
    gains = [1.0, .87, 1.06]

    def delayed(x: np.ndarray, offset: int) -> np.ndarray:
        if offset == 0:
            return x
        y = np.zeros_like(x)
        y[offset:] = x[:-offset]
        return y

    write = lambda name, x: wavfile.write(out / name, sample_rate,
                                           np.asarray(x, dtype=np.float32))
    write('common_truth.wav', common)
    for name, v, offset, gain in zip(('a', 'b', 'third'), voices, offsets, gains):
        write(f'{name}_voice_truth.wav', v)
        write(f'{name}.wav', gain * delayed(common, offset) + v)
    (out / 'fixture.json').write_text(json.dumps({
        'sample_rate': sample_rate, 'duration_s': seconds, 'channels': 2,
        'common_offset_samples': dict(zip(('a','b','third'), offsets)),
        'common_gain': dict(zip(('a','b','third'), gains)),
        'seed': 20260926,
        'note': 'Offsets delay the M&E relative to each fixture voice.'
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Fixtures generated: {out}')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: generate_fixtures.py OUTPUT_DIR')
    main(Path(sys.argv[1]))
