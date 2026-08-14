"""
Task 3 requirement: for every submitted audio file, extract duration,
sample rate, bitrate, and loudness (dB) - plus a bonus rough noise/quality
estimate.

Implementation note: no internet access was available in the build
environment to pip-install librosa/pydub, so this uses ffprobe/ffmpeg
directly via subprocess. This is actually the leaner choice for
deployment too - ffmpeg is preinstalled on Render/Railway, whereas
librosa drags in numpy/scipy/numba and is overkill for 4 numbers.
"""

import json
import re
import subprocess


def _run(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout, result.stderr
    except FileNotFoundError:
        return "", ""


def analyze_audio(file_path: str) -> dict:
    """Returns dict with duration_sec, sample_rate_hz, bitrate_kbps,
    loudness_dbfs, noise_estimate. Any field that can't be determined
    is left as None rather than faked."""

    out = {
        "duration_sec": None,
        "sample_rate_hz": None,
        "bitrate_kbps": None,
        "loudness_dbfs": None,
        "noise_estimate": None,
    }

    # --- ffprobe: container-level metadata (duration, sample rate, bitrate) ---
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", file_path,
    ]
    stdout, stderr = _run(probe_cmd)
    try:
        meta = json.loads(stdout)
    except json.JSONDecodeError:
        meta = {}

    if not stdout and not stderr:
        return out

    fmt = meta.get("format", {})
    streams = meta.get("streams", [])
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    if fmt.get("duration"):
        out["duration_sec"] = round(float(fmt["duration"]), 2)
    if audio_stream.get("sample_rate"):
        out["sample_rate_hz"] = int(audio_stream["sample_rate"])
    # prefer stream-level bit_rate, fall back to format-level (container overall)
    bitrate = audio_stream.get("bit_rate") or fmt.get("bit_rate")
    if bitrate:
        out["bitrate_kbps"] = round(int(bitrate) / 1000, 1)

    # --- ffmpeg volumedetect: mean/max volume in dBFS, used for loudness
    # and as a rough noise/quality heuristic ---
    vol_cmd = [
        "ffmpeg", "-i", file_path, "-af", "volumedetect",
        "-f", "null", "-",
    ]
    _, stderr = _run(vol_cmd)

    mean_match = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", stderr)
    max_match = re.search(r"max_volume:\s*(-?\d+\.?\d*)\s*dB", stderr)

    mean_db = float(mean_match.group(1)) if mean_match else None
    max_db = float(max_match.group(1)) if max_match else None

    if mean_db is not None:
        out["loudness_dbfs"] = mean_db

    # Rough, honestly-labelled heuristic (NOT a real noise-floor measurement):
    #   - crest factor (max - mean) approximates dynamic range.
    #   - a very quiet mean volume usually means a weak/distant mic signal.
    #   - a very small crest factor on a quiet clip often means the "signal"
    #     is mostly background hiss, not speech peaks.
    if mean_db is not None and max_db is not None:
        crest = max_db - mean_db
        if mean_db < -40:
            out["noise_estimate"] = "noisy"  # very quiet overall -> likely poor capture
        elif crest < 8:
            out["noise_estimate"] = "moderate_noise"  # flat dynamics -> hiss-dominated
        else:
            out["noise_estimate"] = "clean"

    return out
