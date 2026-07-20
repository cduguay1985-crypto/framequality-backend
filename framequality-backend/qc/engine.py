"""
FrameQuality Pro — QC engine.

run_scan() takes a media file plus a delivery profile and returns a structured
report: category scores, an overall score, and a list of findings, each with a
plain-English remediation note and (where it is a timecoded defect) an extracted
evidence frame.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from . import probe as P
from .profiles import CATEGORY_WEIGHTS, CHECKS, Profile, get_profile

# Score multiplier applied to a check's weight by outcome.
STATUS_FACTOR = {"pass": 1.0, "warn": 0.55, "fail": 0.0}

# Cap on how many evidence frames we pull per finding.
MAX_FRAMES_PER_FINDING = 4
MAX_OCCURRENCES_STORED = 40


# --------------------------------------------------------------------------


@dataclass
class Occurrence:
    seconds: float
    timecode: str
    note: str = ""
    frame: str | None = None          # relative path to evidence image


@dataclass
class Finding:
    check: str
    label: str
    category: str
    status: str                        # pass | warn | fail | info
    weight: int
    message: str
    measured: Any = None
    expected: Any = None
    fix: str = ""
    occurrences: list[Occurrence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["occurrence_count"] = len(self.occurrences)
        return d


class ScanContext:
    """Carries state through a scan so checks stay small and testable."""

    def __init__(self, path: str, profile: Profile, workdir: str,
                 subtitle_path: str | None = None,
                 expected_runtime: float | None = None,
                 progress: Callable[[int, str], None] | None = None,
                 deep: bool = True):
        self.path = path
        self.profile = profile
        self.workdir = workdir
        self.subtitle_path = subtitle_path
        self.expected_runtime = expected_runtime
        self.deep = deep
        self._progress = progress or (lambda pct, msg: None)
        self.frames_dir = os.path.join(workdir, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.info: P.MediaInfo | None = None
        self.findings: list[Finding] = []
        self._frame_seq = 0

    def progress(self, pct: int, msg: str) -> None:
        self._progress(pct, msg)

    # -- helpers -----------------------------------------------------------
    def tc(self, seconds: float) -> str:
        return seconds_to_tc(seconds, self.info.fps if self.info else 24.0)

    def grab(self, seconds: float, tag: str) -> str | None:
        """Extract an evidence frame; returns the relative path or None."""
        self._frame_seq += 1
        name = f"{self._frame_seq:03d}_{tag}_{int(seconds)}s.jpg"
        dest = os.path.join(self.frames_dir, name)
        ok = P.extract_frame(self.path, seconds, dest)
        return f"frames/{name}" if ok else None

    def add(self, check: str, status: str, message: str, *,
            measured: Any = None, expected: Any = None,
            occurrences: list[Occurrence] | None = None) -> Finding:
        meta = CHECKS[check]
        f = Finding(
            check=check,
            label=meta["label"],
            category=meta["category"],
            status=status,
            weight=meta["weight"],
            message=message,
            measured=measured,
            expected=expected,
            fix=meta["fix"] if status in ("fail", "warn") else "",
            occurrences=occurrences or [],
        )
        self.findings.append(f)
        return f


def seconds_to_tc(seconds: float, fps: float) -> str:
    if fps <= 0:
        fps = 24.0
    seconds = max(0.0, seconds)
    total_frames = int(round(seconds * fps))
    f = total_frames % int(round(fps))
    total_sec = total_frames // int(round(fps))
    s = total_sec % 60
    m = (total_sec // 60) % 60
    h = total_sec // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


# --------------------------------------------------------------------------
# Video checks
# --------------------------------------------------------------------------


def check_container(ctx: ScanContext) -> None:
    info, prof = ctx.info, ctx.profile

    integrity = P.decode_integrity(ctx.path, None if ctx.deep else 300)
    if integrity["clean"]:
        ctx.add("container_integrity", "pass",
                "Full decode completed with no stream errors.",
                measured="0 decode errors", expected="0 decode errors")
    else:
        n = integrity["error_count"]
        ctx.add("container_integrity", "fail",
                f"{n} decode error(s) during playback. The file is corrupt or "
                f"truncated. First: {integrity['errors'][0][:160] if integrity['errors'] else 'non-zero exit'}",
                measured=f"{n} decode errors", expected="0 decode errors")

    # metadata completeness
    missing = [k for k in ("title",) if k not in info.tags]
    if info.video and not info.video.get("display_aspect_ratio"):
        missing.append("display_aspect_ratio")
    if missing:
        ctx.add("metadata_completeness", "warn",
                f"Missing metadata field(s): {', '.join(missing)}.",
                measured=f"missing: {', '.join(missing)}", expected="populated")
    else:
        ctx.add("metadata_completeness", "pass", "Core metadata present.")

    # timecode
    tc = info.start_timecode
    if prof.require_timecode:
        if tc:
            ctx.add("timecode", "pass", f"Start timecode present: {tc}.",
                    measured=tc, expected="present")
        else:
            ctx.add("timecode", "fail",
                    "No embedded start timecode found.",
                    measured="absent", expected="present (e.g. 01:00:00:00)")
    else:
        ctx.add("timecode", "info" if not tc else "pass",
                f"Start timecode {tc}." if tc else
                "No timecode track (not required by this profile).",
                measured=tc or "absent", expected="optional")

    # runtime
    dur = info.duration
    if ctx.expected_runtime:
        delta = abs(dur - ctx.expected_runtime)
        if delta > 2.0:
            ctx.add("duration", "fail",
                    f"Runtime is {fmt_duration(dur)} but paperwork says "
                    f"{fmt_duration(ctx.expected_runtime)} — off by {delta:.1f}s.",
                    measured=fmt_duration(dur),
                    expected=fmt_duration(ctx.expected_runtime))
        else:
            ctx.add("duration", "pass",
                    f"Runtime {fmt_duration(dur)} matches the declared runtime.",
                    measured=fmt_duration(dur))
    elif dur < 3600:
        ctx.add("duration", "warn",
                f"Runtime is {fmt_duration(dur)} — short for a feature. Confirm "
                "this is the full picture and not a reel or excerpt.",
                measured=fmt_duration(dur), expected="> 60 min for a feature")
    else:
        ctx.add("duration", "pass", f"Runtime {fmt_duration(dur)}.",
                measured=fmt_duration(dur))


def check_video_conformance(ctx: ScanContext) -> None:
    info, prof = ctx.info, ctx.profile
    if not info.video:
        ctx.add("codec_conformance", "fail", "No video stream in the file.",
                measured="none", expected=", ".join(prof.allowed_codecs))
        return

    codec = info.codec.lower()
    if any(c in codec for c in prof.allowed_codecs):
        ctx.add("codec_conformance", "pass", f"Codec {codec} is accepted.",
                measured=codec, expected=", ".join(prof.allowed_codecs))
    else:
        ctx.add("codec_conformance", "fail",
                f"Codec {codec} is not on the accepted list for this target.",
                measured=codec, expected=", ".join(prof.allowed_codecs))

    res = (info.width, info.height)
    if prof.allowed_resolutions is None:
        ctx.add("resolution", "pass", f"Raster {res[0]}x{res[1]}.",
                measured=f"{res[0]}x{res[1]}", expected="any")
    elif res in prof.allowed_resolutions:
        ctx.add("resolution", "pass", f"Raster {res[0]}x{res[1]} conforms.",
                measured=f"{res[0]}x{res[1]}")
    else:
        allowed = ", ".join(f"{w}x{h}" for w, h in prof.allowed_resolutions)
        ctx.add("resolution", "fail",
                f"Raster {res[0]}x{res[1]} is not a permitted delivery size.",
                measured=f"{res[0]}x{res[1]}", expected=allowed)

    fps = info.fps
    if prof.allowed_fps is None:
        ctx.add("frame_rate", "pass", f"{fps} fps.", measured=f"{fps} fps")
    elif any(abs(fps - a) < 0.05 for a in prof.allowed_fps):
        ctx.add("frame_rate", "pass", f"{fps} fps conforms.",
                measured=f"{fps} fps")
    else:
        ctx.add("frame_rate", "fail",
                f"{fps} fps is not a permitted rate for this target.",
                measured=f"{fps} fps",
                expected=", ".join(str(a) for a in prof.allowed_fps))

    depth = info.bit_depth
    if depth >= prof.min_bit_depth:
        ctx.add("bit_depth", "pass", f"{depth}-bit ({info.pix_fmt}).",
                measured=f"{depth}-bit", expected=f">= {prof.min_bit_depth}-bit")
    else:
        ctx.add("bit_depth", "fail",
                f"{depth}-bit master; this target requires "
                f"{prof.min_bit_depth}-bit or higher.",
                measured=f"{depth}-bit", expected=f">= {prof.min_bit_depth}-bit")

    # colour tags
    v = info.video
    tags = {
        "primaries": v.get("color_primaries"),
        "transfer": v.get("color_transfer"),
        "matrix": v.get("color_space"),
    }
    missing = [k for k, val in tags.items() if not val or val == "unknown"]
    if not missing:
        ctx.add("color_tags", "pass",
                f"Tagged {tags['primaries']}/{tags['transfer']}/{tags['matrix']}.",
                measured="/".join(str(x) for x in tags.values()))
    elif prof.require_color_tags:
        ctx.add("color_tags", "fail",
                f"Colour {', '.join(missing)} not tagged. Downstream systems will "
                "guess, and they often guess wrong.",
                measured=f"missing: {', '.join(missing)}",
                expected="primaries + transfer + matrix all tagged")
    else:
        ctx.add("color_tags", "warn",
                f"Colour {', '.join(missing)} not tagged.",
                measured=f"missing: {', '.join(missing)}")


def check_scan_type(ctx: ScanContext) -> None:
    ctx.progress(35, "Analysing field order and cadence")
    idet = P.detect_interlacing(ctx.path)
    prof = ctx.profile
    verdict = idet["verdict"]
    ratio = idet["interlaced_ratio"]

    if verdict == "unknown":
        ctx.add("scan_type", "info", "Could not determine scan type.",
                measured="unknown")
        return

    if verdict == "interlaced":
        if prof.require_progressive:
            ctx.add("scan_type", "fail",
                    f"{ratio:.0%} of sampled frames are interlaced; this target "
                    "accepts progressive only.",
                    measured=f"interlaced ({ratio:.0%} of frames)",
                    expected="progressive")
        else:
            ctx.add("scan_type", "pass",
                    f"Interlaced ({'TFF' if idet['tff'] >= idet['bff'] else 'BFF'}), "
                    "which this target permits.",
                    measured="interlaced")
    else:
        if ratio > 0.02:
            ctx.add("scan_type", "warn",
                    f"Predominantly progressive, but {ratio:.1%} of sampled frames "
                    "read as interlaced — likely combed source material cut into "
                    "the timeline.",
                    measured=f"progressive with {ratio:.1%} combed frames",
                    expected="progressive")
        else:
            ctx.add("scan_type", "pass", "Progressive throughout.",
                    measured="progressive")


def check_video_levels(ctx: ScanContext) -> None:
    ctx.progress(45, "Measuring video levels")
    stats = P.measure_signalstats(ctx.path)
    if not stats:
        ctx.add("video_levels", "info", "Level analysis unavailable.")
        return

    prof = ctx.profile
    depth = ctx.info.bit_depth or 8
    scale = (2 ** depth - 1) / 1023.0  # profile limits are expressed 10-bit
    lo = prof.legal_luma[0] * scale
    hi = prof.legal_luma[1] * scale

    ymin = stats.get("ymin_min")
    ymax = stats.get("ymax_max")
    occurrences: list[Occurrence] = []

    below = [(t, v) for t, v in zip(stats["_times"], stats["_ymin_series"]) if v < lo]
    above = [(t, v) for t, v in zip(stats["_times"], stats["_ymax_series"]) if v > hi]

    worst = sorted(below, key=lambda x: x[1])[:2] + \
            sorted(above, key=lambda x: -x[1])[:2]
    for t, v in worst[:MAX_FRAMES_PER_FINDING]:
        occurrences.append(Occurrence(
            seconds=t, timecode=ctx.tc(t),
            note=f"luma {v:.0f} (legal {lo:.0f}–{hi:.0f})",
            frame=ctx.grab(t, "levels"),
        ))

    pct_illegal = (len(below) + len(above)) / max(1, stats["frames_sampled"])
    if not below and not above:
        ctx.add("video_levels", "pass",
                f"Luma stays within legal range ({ymin:.0f}–{ymax:.0f}).",
                measured=f"{ymin:.0f}–{ymax:.0f}",
                expected=f"{lo:.0f}–{hi:.0f}")
    elif pct_illegal < 0.01:
        ctx.add("video_levels", "warn",
                f"Isolated illegal levels on {pct_illegal:.1%} of sampled frames "
                f"(range {ymin:.0f}–{ymax:.0f}).",
                measured=f"{ymin:.0f}–{ymax:.0f}",
                expected=f"{lo:.0f}–{hi:.0f}", occurrences=occurrences)
    else:
        ctx.add("video_levels", "fail",
                f"Illegal video levels on {pct_illegal:.1%} of sampled frames "
                f"(measured {ymin:.0f}–{ymax:.0f}, legal {lo:.0f}–{hi:.0f}).",
                measured=f"{ymin:.0f}–{ymax:.0f}",
                expected=f"{lo:.0f}–{hi:.0f}", occurrences=occurrences)

    # stuck-pixel heuristic: YMAX pinned at ceiling while the frame average is low
    yavg = stats.get("yavg_avg", 0)
    if ymax and ymax >= (2 ** depth - 1) * 0.995 and yavg < (2 ** depth) * 0.25:
        ctx.add("dead_pixels", "warn",
                "Peak luma is pinned at maximum on otherwise dark frames — "
                "consistent with a stuck (hot) pixel.",
                measured=f"ymax {ymax:.0f} on avg-luma {yavg:.0f} content",
                expected="no isolated maximum-value pixels")
    else:
        ctx.add("dead_pixels", "pass", "No stuck-pixel signature detected.")


def check_black_and_freeze(ctx: ScanContext) -> None:
    ctx.progress(55, "Scanning for black and frozen frames")
    prof = ctx.profile
    duration = ctx.info.duration

    blacks = P.detect_black(ctx.path)
    head = [b for b in blacks if b["start"] < 1.0]
    tail = [b for b in blacks if b["end"] > duration - 1.0]
    middle = [b for b in blacks if b not in head and b not in tail]

    long_black = [b for b in middle if b["duration"] > prof.max_black_run_seconds]
    if long_black:
        occ = [
            Occurrence(seconds=b["start"] + b["duration"] / 2,
                       timecode=ctx.tc(b["start"]),
                       note=f"{b['duration']:.1f}s of black",
                       frame=ctx.grab(max(0, b["start"] - 0.5), "black"))
            for b in long_black[:MAX_FRAMES_PER_FINDING]
        ]
        ctx.add("black_frames", "fail",
                f"{len(long_black)} black segment(s) longer than the "
                f"{prof.max_black_run_seconds:.0f}s limit inside the picture.",
                measured=f"longest {max(b['duration'] for b in long_black):.1f}s",
                expected=f"<= {prof.max_black_run_seconds:.0f}s",
                occurrences=occ)
    elif middle:
        ctx.add("black_frames", "warn",
                f"{len(middle)} short black segment(s) inside the picture — "
                "confirm these are intentional transitions.",
                measured=f"{len(middle)} segments",
                expected=f"<= {prof.max_black_run_seconds:.0f}s each",
                occurrences=[
                    Occurrence(seconds=b["start"], timecode=ctx.tc(b["start"]),
                               note=f"{b['duration']:.1f}s")
                    for b in middle[:MAX_OCCURRENCES_STORED]
                ])
    else:
        ctx.add("black_frames", "pass", "No unexpected black inside the picture.")

    # head / tail black
    if prof.head_black_seconds:
        head_len = sum(b["duration"] for b in head)
        lo, hi = prof.head_black_seconds
        tail_len = sum(b["duration"] for b in tail)
        tlo, thi = prof.tail_black_seconds or (0.0, 999.0)
        problems = []
        if head_len > hi:
            problems.append(f"head black {head_len:.1f}s (max {hi:.0f}s)")
        if tail_len > thi:
            problems.append(f"tail black {tail_len:.1f}s (max {thi:.0f}s)")
        if problems:
            ctx.add("head_tail_black", "warn",
                    "; ".join(problems).capitalize() + ".",
                    measured="; ".join(problems),
                    expected=f"head <= {hi:.0f}s, tail <= {thi:.0f}s")
        else:
            ctx.add("head_tail_black", "pass",
                    f"Head and tail black within spec "
                    f"({head_len:.1f}s / {tail_len:.1f}s).")
    else:
        ctx.add("head_tail_black", "info",
                "No head/tail black requirement for this profile.")

    freezes = P.detect_freeze(ctx.path, min_duration=prof.max_freeze_seconds)
    # black segments are trivially "frozen" — don't double-report them
    def overlaps_black(fz):
        return any(b["start"] - 0.5 <= fz["start"] <= b["end"] + 0.5 for b in blacks)

    real = [f for f in freezes if not overlaps_black(f)]
    if real:
        occ = [
            Occurrence(seconds=f["start"] + 0.5, timecode=ctx.tc(f["start"]),
                       note=f"frozen for {f['duration']:.1f}s",
                       frame=ctx.grab(f["start"] + 0.5, "freeze"))
            for f in real[:MAX_FRAMES_PER_FINDING]
        ]
        ctx.add("freeze_frames", "fail",
                f"{len(real)} frozen passage(s) longer than "
                f"{prof.max_freeze_seconds:.0f}s.",
                measured=f"{len(real)} freezes, longest "
                         f"{max(f['duration'] for f in real):.1f}s",
                expected=f"none longer than {prof.max_freeze_seconds:.0f}s",
                occurrences=occ)
    else:
        ctx.add("freeze_frames", "pass", "No frozen or repeated-frame passages.")


# --------------------------------------------------------------------------
# Audio checks
# --------------------------------------------------------------------------


def check_audio(ctx: ScanContext) -> None:
    ctx.progress(70, "Measuring loudness and audio configuration")
    info, prof = ctx.info, ctx.profile

    if not info.audio:
        for chk in ("loudness_integrated", "true_peak", "channel_layout",
                    "audio_bit_depth", "dead_channels"):
            ctx.add(chk, "fail", "No audio stream in the file.",
                    measured="none", expected="present")
        return

    primary = info.audio[0]
    layout = primary.get("channel_layout") or f"{primary.get('channels', 0)}ch"
    channels = int(primary.get("channels") or 0)

    if layout in prof.required_channel_layouts:
        ctx.add("channel_layout", "pass",
                f"Channel layout {layout} ({channels}ch) conforms.",
                measured=layout)
    else:
        ctx.add("channel_layout", "fail",
                f"Channel layout {layout} is not accepted for this target.",
                measured=layout,
                expected=", ".join(prof.required_channel_layouts))

    # bit depth / sample rate
    fmt = primary.get("sample_fmt", "")
    bits = int(primary.get("bits_per_raw_sample") or
               primary.get("bits_per_sample") or 0)
    if not bits:
        bits = {"s16": 16, "s32": 32, "fltp": 32, "s16p": 16, "s32p": 32}.get(fmt, 0)
    rate = int(primary.get("sample_rate") or 0)
    problems = []
    if bits and bits < prof.min_audio_bit_depth:
        problems.append(f"{bits}-bit (need {prof.min_audio_bit_depth}-bit)")
    if rate and rate < 48000:
        problems.append(f"{rate} Hz (need 48000 Hz)")
    if problems:
        ctx.add("audio_bit_depth", "fail", "Audio format: " + "; ".join(problems) + ".",
                measured=f"{bits}-bit / {rate} Hz",
                expected=f"{prof.min_audio_bit_depth}-bit / 48000 Hz")
    else:
        ctx.add("audio_bit_depth", "pass",
                f"{bits or '?'}-bit / {rate} Hz PCM.",
                measured=f"{bits}-bit / {rate} Hz")

    # dead channels
    stats = P.measure_astats(ctx.path)
    dead = [c for c in stats if c.get("rms_db", 0) <= prof.silence_floor_db]
    if dead:
        names = channel_names(layout, channels)
        listed = ", ".join(
            names[c["channel"] - 1] if c["channel"] - 1 < len(names)
            else f"ch{c['channel']}" for c in dead
        )
        ctx.add("dead_channels", "fail",
                f"{len(dead)} silent channel(s): {listed}. RMS at or below "
                f"{prof.silence_floor_db:.0f} dB for the whole runtime.",
                measured=f"silent: {listed}", expected="all channels carry signal")
    else:
        ctx.add("dead_channels", "pass",
                f"All {len(stats) or channels} channels carry signal.")

    # loudness
    loud = P.measure_loudness(ctx.path)
    if not loud:
        ctx.add("loudness_integrated", "info", "Loudness measurement unavailable.")
        ctx.add("true_peak", "info", "True-peak measurement unavailable.")
        return

    integrated = loud["integrated_lufs"]
    peak = loud["true_peak_dbtp"]
    lra = loud["loudness_range"]

    if prof.target_loudness is None:
        ctx.add("loudness_integrated", "info",
                f"Measured {integrated:.1f} LUFS. Theatrical mixes are not "
                "loudness-normalised, so this is informational.",
                measured=f"{integrated:.1f} LUFS", expected="not normalised")
    else:
        delta = integrated - prof.target_loudness
        tol = prof.loudness_tolerance
        target_str = f"{prof.target_loudness:.0f} ±{tol:.0f} LKFS"
        if abs(delta) <= tol:
            ctx.add("loudness_integrated", "pass",
                    f"Integrated loudness {integrated:.1f} LUFS is on target.",
                    measured=f"{integrated:.1f} LUFS", expected=target_str)
        elif abs(delta) <= tol * 2:
            ctx.add("loudness_integrated", "warn",
                    f"Integrated loudness {integrated:.1f} LUFS is {delta:+.1f} dB "
                    f"off target. Apply a {-delta:+.1f} dB static trim.",
                    measured=f"{integrated:.1f} LUFS", expected=target_str)
        else:
            ctx.add("loudness_integrated", "fail",
                    f"Integrated loudness {integrated:.1f} LUFS is {delta:+.1f} dB "
                    f"off target. Apply a {-delta:+.1f} dB static gain trim to the "
                    "full mix.",
                    measured=f"{integrated:.1f} LUFS", expected=target_str)

    if peak <= prof.max_true_peak:
        ctx.add("true_peak", "pass",
                f"True peak {peak:.1f} dBTP is under the ceiling.",
                measured=f"{peak:.1f} dBTP",
                expected=f"<= {prof.max_true_peak:.0f} dBTP")
    else:
        ctx.add("true_peak", "fail",
                f"True peak {peak:.1f} dBTP exceeds the "
                f"{prof.max_true_peak:.0f} dBTP ceiling — this will clip on "
                "consumer playback after codec conversion.",
                measured=f"{peak:.1f} dBTP",
                expected=f"<= {prof.max_true_peak:.0f} dBTP")

    if prof.max_lra is not None:
        if lra <= prof.max_lra:
            ctx.add("loudness_range", "pass", f"Loudness range {lra:.1f} LU.",
                    measured=f"{lra:.1f} LU", expected=f"<= {prof.max_lra:.0f} LU")
        else:
            ctx.add("loudness_range", "warn",
                    f"Loudness range {lra:.1f} LU exceeds {prof.max_lra:.0f} LU — "
                    "dialogue will be hard to hear against the loud passages.",
                    measured=f"{lra:.1f} LU", expected=f"<= {prof.max_lra:.0f} LU")
    else:
        ctx.add("loudness_range", "info", f"Loudness range {lra:.1f} LU.",
                measured=f"{lra:.1f} LU")

    # sync: we can only flag structural mismatch without a reference
    v_dur = float((ctx.info.video or {}).get("duration") or ctx.info.duration)
    a_dur = float(primary.get("duration") or ctx.info.duration)
    drift = abs(v_dur - a_dur)
    if drift > 0.5:
        ctx.add("audio_sync", "fail",
                f"Audio stream is {drift:.2f}s {'longer' if a_dur > v_dur else 'shorter'} "
                "than picture. Sync will drift toward the end of the film.",
                measured=f"{drift:.2f}s stream-length mismatch",
                expected="< 0.04s (1 frame)")
    elif drift > 0.04:
        ctx.add("audio_sync", "warn",
                f"Audio and picture stream lengths differ by {drift:.2f}s.",
                measured=f"{drift:.2f}s", expected="< 0.04s (1 frame)")
    else:
        ctx.add("audio_sync", "pass",
                "Audio and picture stream lengths match within one frame.")


def channel_names(layout: str, count: int) -> list[str]:
    table = {
        "mono": ["C"],
        "stereo": ["L", "R"],
        "5.1": ["L", "R", "C", "LFE", "Ls", "Rs"],
        "5.1(side)": ["L", "R", "C", "LFE", "Ls", "Rs"],
        "7.1": ["L", "R", "C", "LFE", "Lss", "Rss", "Lrs", "Rrs"],
    }
    return table.get(layout, [f"ch{i+1}" for i in range(count)])


# --------------------------------------------------------------------------
# Subtitle checks
# --------------------------------------------------------------------------

TS_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def parse_subtitles(path: str) -> list[dict[str, Any]]:
    """Parse SRT or WebVTT into events."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        raw = fh.read()

    events: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", raw.strip())
    for block in blocks:
        m = TS_RE.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / (1000 if g[3] > 99 else 100)
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / (1000 if g[7] > 99 else 100)
        lines = [
            ln.strip() for ln in block.split("\n")
            if ln.strip() and not TS_RE.search(ln) and not ln.strip().isdigit()
            and not ln.strip().upper().startswith("WEBVTT")
        ]
        text = "\n".join(lines)
        if not text:
            continue
        events.append({
            "start": start, "end": end, "duration": end - start,
            "lines": lines, "text": text,
            "chars": len(re.sub(r"<[^>]+>", "", text.replace("\n", " "))),
        })
    return sorted(events, key=lambda e: e["start"])


def check_subtitles(ctx: ScanContext) -> None:
    ctx.progress(82, "Checking subtitles")
    prof = ctx.profile

    events: list[dict[str, Any]] = []
    if ctx.subtitle_path and os.path.exists(ctx.subtitle_path):
        events = parse_subtitles(ctx.subtitle_path)
        ctx.add("subtitle_presence", "pass",
                f"Sidecar subtitle file supplied with {len(events)} events.",
                measured=f"{len(events)} events")
    elif ctx.info.subtitles:
        codecs = ", ".join(s.get("codec_name", "?") for s in ctx.info.subtitles)
        ctx.add("subtitle_presence", "pass",
                f"{len(ctx.info.subtitles)} embedded subtitle/caption track(s): "
                f"{codecs}.",
                measured=f"{len(ctx.info.subtitles)} embedded tracks")
        for chk in ("subtitle_timing", "subtitle_formatting",
                    "subtitle_reading_speed"):
            ctx.add(chk, "info",
                    "Embedded track present; supply the sidecar file to QC "
                    "timing and formatting.")
        return
    else:
        ctx.add("subtitle_presence", "fail",
                "No subtitle or caption track found, and no sidecar file supplied.",
                measured="none", expected="at least one track")
        for chk in ("subtitle_timing", "subtitle_formatting",
                    "subtitle_reading_speed"):
            ctx.add(chk, "info", "Not evaluated — no subtitle source.")
        return

    if not events:
        ctx.add("subtitle_timing", "fail",
                "Subtitle file supplied but no valid events could be parsed.",
                measured="0 parsable events")
        return

    # timing
    short = [e for e in events if e["duration"] < prof.min_subtitle_duration]
    long_ = [e for e in events if e["duration"] > prof.max_subtitle_duration]
    gaps = []
    for a, b in zip(events, events[1:]):
        gap = b["start"] - a["end"]
        if 0 <= gap < prof.min_subtitle_gap:
            gaps.append((a, gap))
        elif gap < 0:
            gaps.append((a, gap))

    timing_issues = len(short) + len(long_) + len(gaps)
    if timing_issues == 0:
        ctx.add("subtitle_timing", "pass",
                f"All {len(events)} events meet duration and gap requirements.")
    else:
        occ = []
        for e in short[:3]:
            occ.append(Occurrence(e["start"], ctx.tc(e["start"]),
                                  f"{e['duration']:.2f}s (min "
                                  f"{prof.min_subtitle_duration:.2f}s): "
                                  f"{e['text'][:60]}"))
        for e, gap in gaps[:3]:
            occ.append(Occurrence(e["end"], ctx.tc(e["end"]),
                                  f"gap {gap:.3f}s to next event"))
        for e in long_[:2]:
            occ.append(Occurrence(e["start"], ctx.tc(e["start"]),
                                  f"{e['duration']:.1f}s (max "
                                  f"{prof.max_subtitle_duration:.0f}s)"))
        status = "fail" if timing_issues > len(events) * 0.02 else "warn"
        ctx.add("subtitle_timing", status,
                f"{len(short)} event(s) below minimum duration, {len(long_)} above "
                f"maximum, {len(gaps)} sub-minimum or negative gap(s).",
                measured=f"{timing_issues} timing issues in {len(events)} events",
                expected=f"{prof.min_subtitle_duration:.2f}–"
                         f"{prof.max_subtitle_duration:.0f}s, gap >= "
                         f"{prof.min_subtitle_gap:.3f}s",
                occurrences=occ)

    # formatting
    bad_lines = [e for e in events if len(e["lines"]) > prof.max_lines]
    bad_len = [
        e for e in events
        if any(len(ln) > prof.max_chars_per_line for ln in e["lines"])
    ]
    if not bad_lines and not bad_len:
        ctx.add("subtitle_formatting", "pass",
                f"All events within {prof.max_lines} lines / "
                f"{prof.max_chars_per_line} characters.")
    else:
        occ = [
            Occurrence(e["start"], ctx.tc(e["start"]),
                       f"{len(e['lines'])} lines, longest "
                       f"{max(len(l) for l in e['lines'])} chars: {e['text'][:60]}")
            for e in (bad_lines + bad_len)[:5]
        ]
        ctx.add("subtitle_formatting",
                "fail" if len(bad_lines) + len(bad_len) > len(events) * 0.02 else "warn",
                f"{len(bad_lines)} event(s) exceed {prof.max_lines} lines; "
                f"{len(bad_len)} exceed {prof.max_chars_per_line} characters per line.",
                measured=f"{len(bad_lines) + len(bad_len)} formatting issues",
                expected=f"<= {prof.max_lines} lines, "
                         f"<= {prof.max_chars_per_line} chars/line",
                occurrences=occ)

    # reading speed
    fast = [
        e for e in events
        if e["duration"] > 0 and e["chars"] / e["duration"] > prof.max_reading_speed
    ]
    if not fast:
        ctx.add("subtitle_reading_speed", "pass",
                f"Reading speed within {prof.max_reading_speed:.0f} chars/sec "
                "throughout.")
    else:
        worst = sorted(fast, key=lambda e: -e["chars"] / e["duration"])[:5]
        occ = [
            Occurrence(e["start"], ctx.tc(e["start"]),
                       f"{e['chars'] / e['duration']:.1f} cps: {e['text'][:60]}")
            for e in worst
        ]
        ctx.add("subtitle_reading_speed",
                "fail" if len(fast) > len(events) * 0.05 else "warn",
                f"{len(fast)} of {len(events)} events exceed "
                f"{prof.max_reading_speed:.0f} characters/second.",
                measured=f"{len(fast)} events too fast, peak "
                         f"{max(e['chars'] / e['duration'] for e in fast):.1f} cps",
                expected=f"<= {prof.max_reading_speed:.0f} cps",
                occurrences=occ)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def score(findings: list[Finding]) -> dict[str, Any]:
    cat_scores: dict[str, float | None] = {}
    for cat in CATEGORY_WEIGHTS:
        rel = [f for f in findings if f.category == cat and f.status in STATUS_FACTOR]
        if not rel:
            cat_scores[cat] = None
            continue
        total_w = sum(f.weight for f in rel)
        earned = sum(f.weight * STATUS_FACTOR[f.status] for f in rel)
        cat_scores[cat] = round(100 * earned / total_w, 1) if total_w else None

    scored = {k: v for k, v in cat_scores.items() if v is not None}
    if scored:
        wsum = sum(CATEGORY_WEIGHTS[k] for k in scored)
        overall = round(
            sum(CATEGORY_WEIGHTS[k] * v for k, v in scored.items()) / wsum, 1
        )
    else:
        overall = 0.0

    fails = [f for f in findings if f.status == "fail"]
    warns = [f for f in findings if f.status == "warn"]

    if fails:
        verdict, verdict_detail = "FAIL", (
            f"{len(fails)} blocking issue(s) must be corrected before delivery."
        )
    elif warns:
        verdict, verdict_detail = "PASS WITH NOTES", (
            f"No blocking issues. {len(warns)} item(s) a human QC operator will "
            "likely query."
        )
    else:
        verdict, verdict_detail = "PASS", "Clean scan against this delivery spec."

    return {
        "overall": overall,
        "grade": grade_letter(overall),
        "categories": cat_scores,
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "counts": {
            "fail": len(fails),
            "warn": len(warns),
            "pass": len([f for f in findings if f.status == "pass"]),
            "info": len([f for f in findings if f.status == "info"]),
        },
    }


def grade_letter(s: float) -> str:
    for cutoff, letter in ((93, "A"), (85, "B"), (75, "C"), (65, "D")):
        if s >= cutoff:
            return letter
    return "F"


def fmt_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_scan(path: str, profile_key: str, workdir: str, *,
             subtitle_path: str | None = None,
             expected_runtime: float | None = None,
             title: str | None = None,
             progress: Callable[[int, str], None] | None = None,
             deep: bool = True) -> dict[str, Any]:
    prof = get_profile(profile_key)
    ctx = ScanContext(path, prof, workdir, subtitle_path=subtitle_path,
                      expected_runtime=expected_runtime, progress=progress,
                      deep=deep)

    ctx.progress(5, "Reading container and stream metadata")
    ctx.info = P.probe(path)

    ctx.progress(15, "Checking container integrity")
    check_container(ctx)
    check_video_conformance(ctx)
    check_scan_type(ctx)
    check_video_levels(ctx)
    check_black_and_freeze(ctx)
    check_audio(ctx)
    check_subtitles(ctx)
    ctx.progress(95, "Scoring")

    sc = score(ctx.findings)
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
    findings = sorted(
        ctx.findings, key=lambda f: (order[f.status], -f.weight, f.category)
    )

    info = ctx.info
    return {
        "title": title or os.path.basename(path),
        "file": os.path.basename(path),
        "profile": {"key": prof.key, "name": prof.name,
                    "description": prof.description, "notes": prof.notes},
        "score": sc,
        "source": {
            "duration": info.duration,
            "duration_tc": fmt_duration(info.duration),
            "size_bytes": info.size_bytes,
            "container": info.format_name,
            "video_codec": info.codec,
            "resolution": f"{info.width}x{info.height}" if info.video else None,
            "fps": info.fps,
            "pix_fmt": info.pix_fmt,
            "bit_depth": info.bit_depth,
            "start_timecode": info.start_timecode,
            "audio_streams": [
                {
                    "codec": a.get("codec_name"),
                    "channels": a.get("channels"),
                    "layout": a.get("channel_layout"),
                    "sample_rate": a.get("sample_rate"),
                    "sample_fmt": a.get("sample_fmt"),
                }
                for a in info.audio
            ],
            "subtitle_streams": len(info.subtitles),
        },
        "findings": [f.to_dict() for f in findings],
        "action_list": [
            {
                "priority": i + 1,
                "severity": f.status,
                "area": f.category,
                "issue": f.label,
                "detail": f.message,
                "fix": f.fix,
                "at": [o.timecode for o in f.occurrences[:5]],
            }
            for i, f in enumerate(
                [f for f in findings if f.status in ("fail", "warn")]
            )
        ],
    }
