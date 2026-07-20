"""Thin wrappers around ffprobe / ffmpeg. Everything shells out; nothing here
holds a whole feature in memory."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 3600) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, errors="replace"
    )
    return p.returncode, p.stdout, p.stderr


# --------------------------------------------------------------------------


@dataclass
class MediaInfo:
    path: str
    format_name: str = ""
    duration: float = 0.0
    size_bytes: int = 0
    bit_rate: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    video: dict[str, Any] | None = None
    audio: list[dict[str, Any]] = field(default_factory=list)
    subtitles: list[dict[str, Any]] = field(default_factory=list)
    data_streams: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    # -- convenience -------------------------------------------------------
    @property
    def width(self) -> int:
        return int(self.video.get("width", 0)) if self.video else 0

    @property
    def height(self) -> int:
        return int(self.video.get("height", 0)) if self.video else 0

    @property
    def fps(self) -> float:
        if not self.video:
            return 0.0
        return _parse_rational(self.video.get("r_frame_rate", "0/1"))

    @property
    def codec(self) -> str:
        return (self.video or {}).get("codec_name", "") or ""

    @property
    def pix_fmt(self) -> str:
        return (self.video or {}).get("pix_fmt", "") or ""

    @property
    def bit_depth(self) -> int:
        return _bit_depth_from_pix_fmt(self.pix_fmt)

    @property
    def start_timecode(self) -> str | None:
        for src in (self.tags, (self.video or {}).get("tags", {}) or {}):
            for key in ("timecode", "TIMECODE", "time_code"):
                if key in src:
                    return src[key]
        for st in self.data_streams:
            tc = (st.get("tags") or {}).get("timecode")
            if tc:
                return tc
        return None


def _parse_rational(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/")
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else 0.0
        return float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _bit_depth_from_pix_fmt(pix_fmt: str) -> int:
    if not pix_fmt:
        return 0
    m = re.search(r"p(\d{1,2})(le|be)$", pix_fmt)
    if m:
        return int(m.group(1))
    if re.search(r"\d{1,2}(le|be)$", pix_fmt):
        m2 = re.search(r"(\d{1,2})(le|be)$", pix_fmt)
        if m2 and int(m2.group(1)) in (9, 10, 12, 14, 16):
            return int(m2.group(1))
    return 8


def probe(path: str) -> MediaInfo:
    code, out, err = _run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-print_format", "json", path,
    ], timeout=300)
    if code != 0:
        raise MediaError(f"ffprobe failed: {err.strip()[:500]}")
    data = json.loads(out or "{}")
    fmt = data.get("format", {}) or {}

    info = MediaInfo(
        path=path,
        format_name=fmt.get("format_name", ""),
        duration=float(fmt.get("duration") or 0.0),
        size_bytes=int(fmt.get("size") or 0),
        bit_rate=int(fmt.get("bit_rate") or 0),
        tags={k.lower(): v for k, v in (fmt.get("tags") or {}).items()},
        raw=data,
    )

    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind == "video" and not _is_cover_art(st):
            if info.video is None:
                info.video = st
        elif kind == "audio":
            info.audio.append(st)
        elif kind == "subtitle":
            info.subtitles.append(st)
        elif kind == "data":
            info.data_streams.append(st)

    if info.video is None and not info.audio:
        raise MediaError("No decodable video or audio streams found in file.")
    return info


def _is_cover_art(stream: dict[str, Any]) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


# --------------------------------------------------------------------------
# Filter-based measurements
# --------------------------------------------------------------------------


def _ffmpeg_filter(
    path: str,
    filtergraph: str,
    *,
    audio: bool = False,
    extra_in: list[str] | None = None,
    timeout: int = 7200,
) -> str:
    """Run a null-output ffmpeg pass and return stderr (where filters log)."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats"]
    cmd += extra_in or []
    cmd += ["-i", path]
    cmd += ["-af" if audio else "-vf", filtergraph]
    cmd += ["-f", "null", "-"]
    code, _out, err = _run(cmd, timeout=timeout)
    if code != 0 and not err:
        raise MediaError("ffmpeg analysis pass failed with no diagnostic output.")
    return err


def measure_loudness(path: str, stream_index: int = 0) -> dict[str, float] | None:
    """EBU R128 integrated loudness, LRA and true peak."""
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
        "-map", f"0:a:{stream_index}",
        "-af", "ebur128=peak=true:framelog=verbose",
        "-f", "null", "-",
    ]
    code, _out, err = _run(cmd)
    if code != 0 and "Summary" not in err:
        return None

    tail = err[err.rfind("Summary:"):] if "Summary:" in err else err
    def grab(label: str) -> float | None:
        m = re.search(rf"{label}:\s*(-?\d+\.?\d*)", tail)
        return float(m.group(1)) if m else None

    integrated = grab("I")
    if integrated is None:
        return None
    return {
        "integrated_lufs": integrated,
        "loudness_range": grab("LRA") or 0.0,
        "true_peak_dbtp": grab("Peak") if grab("Peak") is not None else 0.0,
        "threshold": grab("Threshold") or 0.0,
    }


def measure_astats(path: str, stream_index: int = 0) -> list[dict[str, float]]:
    """Per-channel RMS / peak, used to spot dead channels."""
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
        "-map", f"0:a:{stream_index}",
        "-af", "astats=measure_overall=none:measure_perchannel=Peak_level+RMS_level",
        "-f", "null", "-",
    ]
    _code, _out, err = _run(cmd)

    channels: list[dict[str, float]] = []
    current: dict[str, float] | None = None
    for line in err.splitlines():
        line = line.strip()
        if re.match(r"^\[Parsed_astats.*\]\s*Channel:\s*\d+", line) or \
           re.match(r"^Channel:\s*\d+", line):
            if current:
                channels.append(current)
            idx = re.search(r"Channel:\s*(\d+)", line)
            current = {"channel": int(idx.group(1)) if idx else len(channels) + 1}
        elif current is not None:
            m = re.search(r"Peak level dB:\s*(-?[\d.]+|-inf)", line)
            if m:
                current["peak_db"] = _to_db(m.group(1))
            m = re.search(r"RMS level dB:\s*(-?[\d.]+|-inf)", line)
            if m:
                current["rms_db"] = _to_db(m.group(1))
    if current:
        channels.append(current)
    return channels


def _to_db(token: str) -> float:
    return -120.0 if "inf" in token else float(token)


def detect_black(path: str, min_duration: float = 0.5) -> list[dict[str, float]]:
    err = _ffmpeg_filter(
        path, f"blackdetect=d={min_duration}:pic_th=0.98:pix_th=0.10"
    )
    out = []
    for m in re.finditer(
        r"black_start:(\d+\.?\d*)\s+black_end:(\d+\.?\d*)\s+black_duration:(\d+\.?\d*)",
        err,
    ):
        out.append({
            "start": float(m.group(1)),
            "end": float(m.group(2)),
            "duration": float(m.group(3)),
        })
    return out


def detect_freeze(path: str, noise_db: float = -60.0,
                  min_duration: float = 2.0) -> list[dict[str, float]]:
    err = _ffmpeg_filter(path, f"freezedetect=n={noise_db}dB:d={min_duration}")
    starts = [float(m.group(1)) for m in
              re.finditer(r"freeze_start:\s*(\d+\.?\d*)", err)]
    durations = [float(m.group(1)) for m in
                 re.finditer(r"freeze_duration:\s*(\d+\.?\d*)", err)]
    out = []
    for i, s in enumerate(starts):
        d = durations[i] if i < len(durations) else min_duration
        out.append({"start": s, "duration": d, "end": s + d})
    return out


def detect_interlacing(path: str, sample_frames: int = 2000) -> dict[str, Any]:
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
        "-vf", "idet", "-frames:v", str(sample_frames), "-an",
        "-f", "null", "-",
    ]
    _code, _out, err = _run(cmd)
    block = err[err.rfind("Multi frame detection"):] if \
        "Multi frame detection" in err else err
    def grab(name: str) -> int:
        m = re.search(rf"{name}:\s*(\d+)", block)
        return int(m.group(1)) if m else 0

    tff, bff, prog, undet = grab("TFF"), grab("BFF"), grab("Progressive"), grab("Undetermined")
    total = tff + bff + prog + undet
    interlaced = tff + bff
    return {
        "tff": tff, "bff": bff, "progressive": prog, "undetermined": undet,
        "total": total,
        "interlaced_ratio": (interlaced / total) if total else 0.0,
        "verdict": (
            "interlaced" if total and interlaced / total > 0.25
            else "progressive" if total else "unknown"
        ),
    }


def measure_signalstats(path: str, sample_frames: int = 3000) -> dict[str, Any]:
    """Luma/chroma extremes — catches illegal levels and stuck pixels."""
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
        "-vf", f"signalstats,metadata=mode=print:file=-",
        "-frames:v", str(sample_frames), "-an", "-f", "null", "-",
    ]
    code, out, _err = _run(cmd)
    if code != 0 and not out:
        return {}

    keys = ("YMIN", "YMAX", "YAVG", "UMIN", "UMAX", "VMIN", "VMAX", "SATMAX")
    acc: dict[str, list[float]] = {k: [] for k in keys}
    frame_times: list[float] = []
    current_time = 0.0
    illegal_frames: list[float] = []

    for line in out.splitlines():
        line = line.strip()
        tm = re.match(r"frame:\d+\s+pts:\d+\s+pts_time:([\d.]+)", line)
        if tm:
            current_time = float(tm.group(1))
            frame_times.append(current_time)
            continue
        m = re.match(r"lavfi\.signalstats\.(\w+)=(-?[\d.]+)", line)
        if m and m.group(1) in acc:
            acc[m.group(1)].append(float(m.group(2)))

    stats: dict[str, Any] = {}
    for k, vals in acc.items():
        if not vals:
            continue
        stats[k.lower() + "_min"] = min(vals)
        stats[k.lower() + "_max"] = max(vals)
        stats[k.lower() + "_avg"] = round(sum(vals) / len(vals), 2)
    stats["frames_sampled"] = len(frame_times)
    stats["_ymin_series"] = acc["YMIN"]
    stats["_ymax_series"] = acc["YMAX"]
    stats["_times"] = frame_times
    return stats


def extract_frame(path: str, timestamp: float, out_path: str,
                  width: int = 960) -> bool:
    """Grab a single evidence frame. Seeks fast, decodes accurately."""
    pre = max(0.0, timestamp - 5.0)
    offset = timestamp - pre
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{pre:.3f}", "-i", path, "-ss", f"{offset:.3f}",
        "-frames:v", "1", "-vf", f"scale={width}:-2",
        "-q:v", "3", out_path,
    ]
    code, _out, _err = _run(cmd, timeout=180)
    return code == 0


def decode_integrity(path: str, sample_seconds: int | None = None) -> dict[str, Any]:
    """Full (or sampled) decode to surface corrupt frames."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats",
           "-err_detect", "explode", "-xerror" if False else "-v", "warning"]
    cmd += ["-i", path]
    if sample_seconds:
        cmd += ["-t", str(sample_seconds)]
    cmd += ["-f", "null", "-"]
    code, _out, err = _run(cmd)

    patterns = [
        "error while decoding", "corrupt", "invalid data found",
        "concealing", "no frame", "truncated", "missing picture",
    ]
    errors = [
        ln.strip() for ln in err.splitlines()
        if any(p in ln.lower() for p in patterns)
    ]
    return {
        "exit_code": code,
        "error_count": len(errors),
        "errors": errors[:50],
        "clean": code == 0 and not errors,
    }
