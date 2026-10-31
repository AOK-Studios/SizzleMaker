import json
import math
import struct
import subprocess
import sys
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sizzlemaker.cli import audit_file, build_plan, detect_tempo


def make_click_track(path: Path, bpm: float = 120.0, seconds: int = 8, rate: int = 22050):
    """A WAV with a loud click on every beat, at a known tempo."""
    samples = array("h", bytes(2 * rate * seconds))
    beat = 60.0 / bpm
    t = 0.0
    while t < seconds:
        start = int(t * rate)
        for i in range(start, min(start + 800, len(samples))):
            samples[i] = 12000
        t += beat
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def make_clip(path: Path, seconds: int = 6, color: str = "red"):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=640x360:r=30:d={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def test_tempo_detection_near_120(tmp_path):
    track = tmp_path / "track.wav"
    make_click_track(track, bpm=120.0)
    tempo = detect_tempo(track, tmp_path / "work")
    assert abs(tempo["bpm"] - 120.0) < 6.0
    assert abs(tempo["beat_seconds"] - 0.5) < 0.03


def test_plan_segments_on_beat_grid(tmp_path):
    clips = []
    for name, color in [("a.mp4", "red"), ("b.mp4", "blue")]:
        p = tmp_path / name
        make_clip(p, seconds=6, color=color)
        clips.append(p)
    tempo = {"bpm": 120.0, "beat_seconds": 0.5, "first_beat": 0.0,
             "duration": 8.0, "beats": []}

    class A:  # minimal args namespace
        start = 0.0
        duration = 8.0
        beats_per_cut = 4

    plan = build_plan(clips, tempo, A())
    assert plan["segment_seconds"] == 2.0
    assert len(plan["segments"]) == 4
    sources = [s["source"] for s in plan["segments"]]
    assert sources[0] != sources[1]  # spreads across clips
    for seg in plan["segments"]:
        assert seg["out"] - seg["in"] == 2.0


def test_audit_flags_silent_file(tmp_path):
    clip = tmp_path / "only_video.mp4"
    make_clip(clip, seconds=2)
    result = audit_file(clip)
    assert result["has_video"] is True
    assert result["has_audio"] is False
    assert result["ok"] is False
