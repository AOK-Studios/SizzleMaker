"""SizzleMaker: turn a folder of clips into a sizzle video cut to the beat.

CLI only. Built for agents. No prompts, no clicking through menus.
State goes in and out as JSON so an agent can plan, build and audit a cut
without ever opening a video editor.

Pipeline:
  1. Get clips: a public Google Drive folder link, or a local folder.
  2. Read the music: detect the tempo and build a beat grid.
  3. Plan: one segment per group of beats, clips spread across all sources.
  4. Build: full frames only, nothing cropped. Scale and pad, never zoom-cut.
  5. Audit: ffprobe checks the render before anyone calls it done.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
import wave
from array import array
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc


def probe(path: Path) -> dict:
    out = run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]).stdout
    return json.loads(out)


def media_duration(path: Path) -> float:
    info = probe(path)
    return float(info["format"]["duration"])


# ---------------------------------------------------------------- sources

def fetch_drive_folder(link: str, dest: Path) -> list[Path]:
    """Download a public Drive folder with gdown, if it is installed."""
    try:
        import gdown  # type: ignore
    except ImportError:
        raise SystemExit(
            "Drive links need the gdown package. Install it with: "
            "pip install \"sizzlemaker[drive]\"  "
            "Or download the folder yourself and use --input-dir instead."
        )
    dest.mkdir(parents=True, exist_ok=True)
    gdown.download_folder(url=link, output=str(dest), quiet=True, use_cookies=False)
    return sorted(p for p in dest.rglob("*") if p.suffix.lower() in VIDEO_EXTS)


def fetch_direct_link(link: str, dest: Path) -> list[Path]:
    """Download one direct video file link (https) into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    name = link.split("?")[0].rstrip("/").split("/")[-1] or "clip.mp4"
    target = dest / name
    urllib.request.urlretrieve(link, target)
    return [target] if target.suffix.lower() in VIDEO_EXTS else []


def collect_sources(args) -> list[Path]:
    if args.input_dir:
        folder = Path(args.input_dir)
        sources = sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTS)
    elif args.drive_link:
        work = Path(args.work_dir)
        if "drive.google.com" in args.drive_link and "/folders/" in args.drive_link:
            sources = fetch_drive_folder(args.drive_link, work / "clips")
        else:
            sources = fetch_direct_link(args.drive_link, work / "clips")
    else:
        raise SystemExit("give me --input-dir or --drive-link")
    if not sources:
        raise SystemExit("no video files found")
    return sources


# ---------------------------------------------------------------- beats

def decode_pcm(audio: Path, work: Path) -> tuple[array, int]:
    """Decode any audio file to mono 22050 Hz samples with ffmpeg."""
    wav_path = work / "music.wav"
    work.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(audio),
         "-ac", "1", "-ar", "22050", str(wav_path)])
    with wave.open(str(wav_path), "rb") as w:
        frames = w.readframes(w.getnframes())
        rate = w.getframerate()
    samples = array("h")
    samples.frombytes(frames)
    return samples, rate


def onset_envelope(samples: array, rate: int, hop_ms: int = 10) -> list[float]:
    hop = max(1, rate * hop_ms // 1000)
    energies = []
    prev = 0.0
    for i in range(0, len(samples) - hop, hop):
        chunk = samples[i:i + hop]
        energy = sum(abs(v) for v in chunk) / hop
        energies.append(max(0.0, energy - prev))
        prev = energy
    return energies


def detect_tempo(audio: Path, work: Path) -> dict:
    samples, rate = decode_pcm(audio, work)
    env = onset_envelope(samples, rate)
    hop_s = 0.010
    # Autocorrelate the onset envelope across 60 to 180 BPM lags.
    best_bpm, best_score = 0.0, -1.0
    for bpm10 in range(600, 1801, 5):
        bpm = bpm10 / 10.0
        lag = int(round((60.0 / bpm) / hop_s))
        if lag <= 0 or lag * 4 >= len(env):
            continue
        score = sum(env[i] * env[i + lag] for i in range(0, len(env) - lag, 3))
        if score > best_score:
            best_bpm, best_score = bpm, score
    if best_bpm == 0.0:
        raise SystemExit("could not detect a tempo in that music file")
    beat_s = 60.0 / best_bpm
    # Phase: the loudest early onset is treated as beat zero.
    search = min(len(env), int(beat_s / hop_s))
    first = max(range(search), key=lambda i: env[i]) * hop_s if search else 0.0
    duration = len(samples) / rate
    beats = []
    t = first
    while t < duration:
        beats.append(round(t, 4))
        t += beat_s
    return {"bpm": round(best_bpm, 2), "beat_seconds": round(beat_s, 4),
            "first_beat": round(first, 4), "duration": round(duration, 3),
            "beats": beats}


# ---------------------------------------------------------------- plan

def build_plan(sources: list[Path], tempo: dict, args) -> dict:
    music_len = tempo["duration"]
    start = args.start
    total = args.duration if args.duration else max(0.0, music_len - start)
    seg_len = tempo["beat_seconds"] * args.beats_per_cut
    count = max(1, int(total // seg_len))
    durations = {}
    for src in sources:
        try:
            durations[str(src)] = media_duration(src)
        except SystemExit:
            durations[str(src)] = 0.0
    segments = []
    used = set()
    cursor = {str(s): 0.0 for s in sources}
    for i in range(count):
        src = sources[i % len(sources)]
        key = str(src)
        avail = max(0.0, durations[key] - seg_len)
        in_point = cursor[key]
        if in_point > avail:
            in_point = 0.0
        marker = (key, round(in_point, 2))
        if marker in used and avail > 0:
            in_point = (in_point + seg_len) % (avail + seg_len)
            marker = (key, round(in_point, 2))
        used.add(marker)
        cursor[key] = in_point + seg_len
        segments.append({
            "index": i,
            "source": key,
            "in": round(in_point, 3),
            "out": round(in_point + seg_len, 3),
            "start": round(start + i * seg_len, 3),
            "end": round(start + (i + 1) * seg_len, 3),
        })
    return {"bpm": tempo["bpm"], "beats_per_cut": args.beats_per_cut,
            "segment_seconds": round(seg_len, 4), "start": start,
            "segments": segments}


# ---------------------------------------------------------------- sheets

def make_sheets(sources: list[Path], out_dir: Path, frames: int = 5) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for src in sources:
        dur = media_duration(src)
        step = max(dur / (frames + 1), 0.1)
        sheet = out_dir / (src.stem + "_sheet.jpg")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-vf", f"fps=1/{step:.3f},scale=320:-2,tile={frames}x1",
             "-frames:v", "1", str(sheet)], check=False)
        if sheet.exists():
            sheets.append(str(sheet))
    return sheets


# ---------------------------------------------------------------- build

def canvas(args) -> tuple[int, int]:
    return (1080, 1920) if args.vertical else (1920, 1080)


def render_segment(seg: dict, args, work: Path) -> Path:
    w, h = canvas(args)
    out = work / f"seg_{seg['index']:03d}.mp4"
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30")
    run(["ffmpeg", "-y", "-v", "error",
         "-ss", str(seg["in"]), "-i", seg["source"],
         "-t", str(round(seg["out"] - seg["in"], 3)),
         "-vf", vf, "-an", "-c:v", "libx264", "-preset", "medium",
         "-pix_fmt", "yuv420p", str(out)])
    return out


def build(args) -> dict:
    sources = collect_sources(args)
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    tempo = detect_tempo(Path(args.music), work)
    plan = build_plan(sources, tempo, args)
    (work / "plan.json").write_text(json.dumps(plan, indent=2))
    parts = [render_segment(seg, args, work) for seg in plan["segments"]]
    list_file = work / "concat.txt"
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    silent = work / "silent.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", str(silent)])

    out_path = Path(args.output)
    inputs = ["-i", str(silent), "-i", str(args.music)]
    filters = []
    audio_map = "[1:a]"
    if args.voiceover:
        inputs += ["-i", str(args.voiceover)]
        filters.append(f"[1:a]volume={args.music_under_vo}[mus]")
        filters.append("[mus][2:a]amix=inputs=2:duration=first[aout]")
        audio_map = "[aout]"
    video_map = "[0:v]"
    if args.title:
        safe = args.title.replace("'", "").replace(":", " ")
        filters.append(
            f"[0:v]drawtext=text='{safe}':fontcolor=white:fontsize=64:"
            f"box=1:boxcolor=black@0.45:boxborderw=18:x=(w-text_w)/2:y=h-160[vout]")
        video_map = "[vout]"
    if args.overlay_image:
        inputs += ["-i", str(args.overlay_image)]
        idx = len(inputs) // 2 - 1
        base = video_map if filters and video_map == "[vout]" else "[0:v]"
        filters.append(f"{base}[{idx}:v]overlay=(W-w)/2:(H-h)/2[vfinal]")
        video_map = "[vfinal]"
    if filters and audio_map == "[1:a]":
        # Once a filter graph exists, the music has to pass through it too.
        filters.append("[1:a]anull[aout]")
        audio_map = "[aout]"
    if filters and video_map == "[0:v]":
        # Same on the video side when only the audio was filtered.
        filters.append("[0:v]null[vpass]")
        video_map = "[vpass]"
    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    else:
        # No filter graph: map the raw streams, no bracket labels.
        video_map, audio_map = "0:v", "1:a"
    target = round(len(plan["segments"]) * plan["segment_seconds"], 3)
    cmd += ["-map", video_map, "-map", audio_map,
            "-ss", str(args.start), "-t", str(target),
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out_path)]
    run(cmd)
    result = audit_file(out_path, len(plan["segments"]) * plan["segment_seconds"])
    result["plan"] = plan
    result["output"] = str(out_path)
    return result


# ---------------------------------------------------------------- audit

def audit_file(path: Path, expected_duration: float | None = None) -> dict:
    info = probe(path)
    streams = info.get("streams", [])
    kinds = sorted({s.get("codec_type", "?") for s in streams})
    duration = float(info["format"]["duration"])
    result = {"file": str(path), "duration": round(duration, 3),
              "streams": kinds, "has_video": "video" in kinds,
              "has_audio": "audio" in kinds}
    if expected_duration:
        result["expected_duration"] = round(expected_duration, 3)
        result["duration_ok"] = abs(duration - expected_duration) < 1.0
    result["ok"] = result["has_video"] and result["has_audio"] and result.get("duration_ok", True)
    return result


# ---------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sizzlemaker", description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--input-dir", help="local folder of clips on your machine")
        p.add_argument("--drive-link", help="public Google Drive folder link, or one direct file link")
        p.add_argument("--work-dir", default="sizzle_work")
        p.add_argument("--json", action="store_true", help="print the result as JSON")

    p_plan = sub.add_parser("plan", help="detect the beat and write a cut plan")
    common(p_plan)
    p_plan.add_argument("--music", required=True)
    p_plan.add_argument("--start", type=float, default=0.0)
    p_plan.add_argument("--duration", type=float, default=None)
    p_plan.add_argument("--beats-per-cut", type=int, default=4)

    p_sheets = sub.add_parser("sheets", help="contact sheets, one per clip, for picking shots")
    common(p_sheets)

    p_build = sub.add_parser("build", help="plan and render the sizzle")
    common(p_build)
    p_build.add_argument("--music", required=True, help="your own MP3 or WAV")
    p_build.add_argument("--output", default="sizzle.mp4")
    p_build.add_argument("--start", type=float, default=0.0)
    p_build.add_argument("--duration", type=float, default=None)
    p_build.add_argument("--beats-per-cut", type=int, default=4)
    p_build.add_argument("--vertical", action="store_true", help="1080x1920 instead of 1920x1080")
    p_build.add_argument("--title", default=None, help="title text burned in at the bottom")
    p_build.add_argument("--overlay-image", default=None, help="PNG overlay, centered")
    p_build.add_argument("--voiceover", default=None, help="voiceover audio file, mixed over the music")
    p_build.add_argument("--music-under-vo", type=float, default=0.3)

    p_audit = sub.add_parser("audit", help="ffprobe check on a finished render")
    p_audit.add_argument("file")
    p_audit.add_argument("--expect-duration", type=float, default=None)
    p_audit.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.mode == "plan":
        sources = collect_sources(args)
        tempo = detect_tempo(Path(args.music), Path(args.work_dir))
        plan = build_plan(sources, tempo, args)
        Path(args.work_dir).mkdir(parents=True, exist_ok=True)
        (Path(args.work_dir) / "plan.json").write_text(json.dumps(plan, indent=2))
        print(json.dumps({"tempo": {k: tempo[k] for k in ("bpm", "beat_seconds", "duration")},
                          "segments": len(plan["segments"]),
                          "plan_file": str(Path(args.work_dir) / "plan.json")}, indent=2))
    elif args.mode == "sheets":
        sources = collect_sources(args)
        sheets = make_sheets(sources, Path(args.work_dir) / "sheets")
        print(json.dumps({"sheets": sheets}, indent=2))
    elif args.mode == "build":
        result = build(args)
        summary = {k: result[k] for k in ("output", "duration", "expected_duration", "ok", "has_video", "has_audio")}
        summary["segments"] = len(result["plan"]["segments"])
        summary["bpm"] = result["plan"]["bpm"]
        print(json.dumps(summary, indent=2))
        if not result["ok"]:
            return 3
    elif args.mode == "audit":
        result = audit_file(Path(args.file), args.expect_duration)
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
