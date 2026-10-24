"""Audio → MIDI für FormatFix.

Erkennt Noten in einer Audiodatei und schreibt sie als MIDI-Datei.
Funktioniert am besten mit EINEM Instrument oder EINER Stimme
(Klavier, Gitarre, Gesang, Flöte …). Komplette Band-Songs ergeben
oft viele falsche Noten; Schlagzeug wird nicht erkannt.

Basiert auf "Basic Pitch" von Spotify (Apache License 2.0):
https://github.com/spotify/basic-pitch – Modell (nmp.onnx) unverändert,
Notenerkennung angepasst ohne librosa/scipy/pretty_midi.
Alles läuft offline auf dem eigenen PC.
"""
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

# ---------- Konstanten aus Basic Pitch ----------
FFT_HOP = 256
SR = 22050
WINDOW_SECONDS = 2
FPS = SR // FFT_HOP                          # 86 Bilder pro Sekunde
ANNOT_N_FRAMES = FPS * WINDOW_SECONDS        # 172
AUDIO_N_SAMPLES = SR * WINDOW_SECONDS - FFT_HOP
MIDI_OFFSET = 21                             # tiefste Klaviertaste A0
MAX_FREQ_IDX = 87
N_OVERLAP_FRAMES = 30
OVERLAP_LEN = N_OVERLAP_FRAMES * FFT_HOP
HOP_SIZE = AUDIO_N_SAMPLES - OVERLAP_LEN

# Einstellungen (Basic-Pitch-Standardwerte)
ONSET_THRESH = 0.5
FRAME_THRESH = 0.3
MIN_NOTE_MS = 127.7
ENERGY_TOL = 11
TEMPO_BPM = 120
TICKS_PER_BEAT = 480
PROGRAM = 0                                  # 0 = Klavier

_session = None


def _model_path():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "nmp.onnx"


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        _session = ort.InferenceSession(str(_model_path()), opts, providers=["CPUExecutionProvider"])
    return _session


# ---------- Audio laden (über ffmpeg, das FormatFix ohnehin mitbringt) ----------
def load_audio(path, ffmpeg, creationflags=0):
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True, creationflags=creationflags,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("Audiodatei konnte nicht gelesen werden.")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


# ---------- Modell ausführen ----------
def run_model(audio):
    original_length = len(audio)
    audio = np.concatenate([np.zeros(OVERLAP_LEN // 2, dtype=np.float32), audio])
    windows = []
    for i in range(0, len(audio), HOP_SIZE):
        w = audio[i:i + AUDIO_N_SAMPLES]
        if len(w) < AUDIO_N_SAMPLES:
            w = np.pad(w, (0, AUDIO_N_SAMPLES - len(w)))
        windows.append(w)
    batch = np.stack(windows)[:, :, None].astype(np.float32)

    sess = _get_session()
    outs = {"note": [], "onset": []}
    for s in range(0, len(batch), 16):                      # in Päckchen à 16 Fenster
        note, onset, _contour = sess.run(
            ["StatefulPartitionedCall:1", "StatefulPartitionedCall:2", "StatefulPartitionedCall:0"],
            {"serving_default_input_2:0": batch[s:s + 16]},
        )
        outs["note"].append(note)
        outs["onset"].append(onset)

    n_olap = N_OVERLAP_FRAMES // 2
    n_frames = int(np.floor(original_length * (FPS / SR)))
    result = {}
    for k, v in outs.items():
        v = np.concatenate(v)[:, n_olap:-n_olap, :]
        result[k] = v.reshape(-1, v.shape[2])[:n_frames]
    return result


# ---------- Noten aus den Modell-Ausgaben gewinnen ----------
def _infer_onsets(onsets, frames, n_diff=2):
    diffs = []
    for n in range(1, n_diff + 1):
        app = np.concatenate([np.zeros((n, frames.shape[1])), frames])
        diffs.append(app[n:, :] - app[:-n, :])
    fd = np.min(diffs, axis=0)
    fd[fd < 0] = 0
    fd[:n_diff, :] = 0
    if np.max(fd) > 0:
        fd = np.max(onsets) * fd / np.max(fd)
    return np.maximum(onsets, fd)


def _local_maxima(x):
    """Ersatz für scipy.signal.argrelmax entlang der Zeitachse."""
    mask = np.zeros(x.shape, dtype=bool)
    mask[1:-1] = (x[1:-1] > x[:-2]) & (x[1:-1] > x[2:])
    return np.where(mask)


def frames_to_notes(frames, onsets, min_note_len):
    n_frames = frames.shape[0]
    onsets = _infer_onsets(onsets, frames)
    peak = np.zeros(onsets.shape)
    idx = _local_maxima(onsets)
    peak[idx] = onsets[idx]
    on_t, on_f = np.where(peak >= ONSET_THRESH)
    on_t, on_f = on_t[::-1], on_f[::-1]

    energy = frames.copy()
    notes = []
    for start, f in zip(on_t, on_f):
        if start >= n_frames - 1:
            continue
        i, k = start + 1, 0
        while i < n_frames - 1 and k < ENERGY_TOL:
            k = k + 1 if energy[i, f] < FRAME_THRESH else 0
            i += 1
        i -= k
        if i - start <= min_note_len:
            continue
        energy[start:i, max(f - 1, 0):min(f + 2, MAX_FREQ_IDX + 1)] = 0
        notes.append((start, i, f + MIDI_OFFSET, float(np.mean(frames[start:i, f]))))

    # "Melodia-Trick": übrig gebliebene, lang gehaltene Töne einsammeln
    while energy.size and np.max(energy) > FRAME_THRESH:
        mid, f = np.unravel_index(np.argmax(energy), energy.shape)
        energy[mid, f] = 0
        i, k = mid + 1, 0
        while i < n_frames - 1 and k < ENERGY_TOL:
            k = k + 1 if energy[i, f] < FRAME_THRESH else 0
            energy[i, max(f - 1, 0):min(f + 2, MAX_FREQ_IDX + 1)] = 0
            i += 1
        end = i - 1 - k
        i, k = mid - 1, 0
        while i > 0 and k < ENERGY_TOL:
            k = k + 1 if energy[i, f] < FRAME_THRESH else 0
            energy[i, max(f - 1, 0):min(f + 2, MAX_FREQ_IDX + 1)] = 0
            i -= 1
        begin = i + 1 + k
        if end - begin <= min_note_len:
            continue
        notes.append((begin, end, f + MIDI_OFFSET, float(np.mean(frames[begin:end, f]))))
    return notes


def frame_times(n_frames):
    t = np.arange(n_frames) * FFT_HOP / SR
    window_no = np.floor(np.arange(n_frames) / ANNOT_N_FRAMES)
    offset = (FFT_HOP / SR) * (ANNOT_N_FRAMES - (AUDIO_N_SAMPLES / FFT_HOP)) + 0.0018
    return t - offset * window_no


# ---------- MIDI-Datei schreiben (Standard MIDI File, Format 0) ----------
def _varlen(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def write_midi(notes_sec, path):
    ticks_per_sec = TICKS_PER_BEAT * TEMPO_BPM / 60
    events = []
    for start, end, pitch, amp in notes_sec:
        vel = int(np.clip(round(127 * amp), 1, 127))
        s = max(0, int(round(start * ticks_per_sec)))
        e = max(s + 1, int(round(end * ticks_per_sec)))
        events.append((s, 1, pitch, vel))      # 1 = Note an
        events.append((e, 0, pitch, 0))        # 0 = Note aus (vor "an" bei gleicher Zeit)
    events.sort()

    track = bytearray()
    track += b"\x00\xFF\x51\x03" + int(60_000_000 / TEMPO_BPM).to_bytes(3, "big")   # Tempo
    track += b"\x00\xC0" + bytes([PROGRAM])                                         # Instrument
    last = 0
    for tick, on, pitch, vel in events:
        track += _varlen(tick - last)
        track += bytes([0x90 if on else 0x80, pitch, vel if on else 64])
        last = tick
    track += b"\x00\xFF\x2F\x00"                                                   # Ende

    data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, TICKS_PER_BEAT)
    data += b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    Path(path).write_bytes(data)


def convert_to_midi(src, dst, ffmpeg, creationflags=0):
    audio = load_audio(src, ffmpeg, creationflags)
    if len(audio) < SR * 0.25:
        raise RuntimeError("Audiodatei ist zu kurz für eine Notenerkennung.")
    out = run_model(audio)
    min_len = int(np.round(MIN_NOTE_MS / 1000 * (SR / FFT_HOP)))
    notes = frames_to_notes(out["note"], out["onset"], min_len)
    times = frame_times(out["note"].shape[0])
    notes_sec = [(times[s], times[min(e, len(times) - 1)], p, a) for s, e, p, a in notes]
    if not notes_sec:
        raise RuntimeError("Keine Noten erkannt – ist das wirklich Musik mit Melodie?")
    write_midi(notes_sec, dst)
    return len(notes_sec)
