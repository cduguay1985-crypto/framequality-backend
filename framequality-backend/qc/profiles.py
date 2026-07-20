"""
Delivery-spec profiles for FrameQuality Pro.

Each profile declares the thresholds the analyzers are measured against, plus the
weight each check carries in its category score. Weights are relative within a
category, not absolute.

Severity levels:
    fail  -> hard reject by the receiving QC house
    warn  -> passes automated QC but a human will likely flag it
    info  -> observation only, no score impact
"""

from dataclasses import dataclass, field
from typing import Any


CATEGORIES = ["video", "audio", "subtitles", "structure"]

# Category contribution to the overall score.
CATEGORY_WEIGHTS = {
    "video": 0.40,
    "audio": 0.30,
    "subtitles": 0.12,
    "structure": 0.18,
}


@dataclass
class Profile:
    key: str
    name: str
    description: str
    # video
    allowed_codecs: list[str]
    allowed_resolutions: list[tuple[int, int]] | None  # None = any
    allowed_fps: list[float] | None
    min_bit_depth: int
    require_progressive: bool
    legal_luma: tuple[int, int]          # 10-bit code values
    legal_chroma: tuple[int, int]
    max_freeze_seconds: float
    max_black_run_seconds: float
    require_color_tags: bool
    # audio
    target_loudness: float | None        # LUFS / LKFS integrated
    loudness_tolerance: float
    max_true_peak: float                 # dBTP
    max_lra: float | None
    required_channel_layouts: list[str]
    min_audio_bit_depth: int
    silence_floor_db: float              # a channel quieter than this is "dead"
    # subtitles
    min_subtitle_duration: float
    max_subtitle_duration: float
    min_subtitle_gap: float
    max_chars_per_line: int
    max_lines: int
    max_reading_speed: float             # chars per second
    # structure
    require_timecode: bool
    head_black_seconds: tuple[float, float] | None   # (min, max) expected
    tail_black_seconds: tuple[float, float] | None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d.pop("notes", None)
        return {k: v for k, v in d.items()}


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

NETFLIX_IMF = Profile(
    key="netflix_imf",
    name="Netflix / IMF streaming delivery",
    description=(
        "Photon/IMF-aligned source delivery. The strictest common target: "
        "10-bit minimum, progressive only, -27 LKFS integrated, -2 dBTP ceiling."
    ),
    allowed_codecs=["prores", "jpeg2000", "ffv1", "h264", "hevc", "dnxhd"],
    allowed_resolutions=[(1920, 1080), (3840, 2160), (4096, 2160), (2048, 1080)],
    allowed_fps=[23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0],
    min_bit_depth=10,
    require_progressive=True,
    legal_luma=(64, 940),
    legal_chroma=(64, 960),
    max_freeze_seconds=2.0,
    max_black_run_seconds=4.0,
    require_color_tags=True,
    target_loudness=-27.0,
    loudness_tolerance=2.0,
    max_true_peak=-2.0,
    max_lra=None,
    required_channel_layouts=["5.1", "5.1(side)", "7.1", "stereo"],
    min_audio_bit_depth=24,
    silence_floor_db=-60.0,
    min_subtitle_duration=0.833,   # 20 frames @24
    max_subtitle_duration=7.0,
    min_subtitle_gap=0.083,        # 2 frames
    max_chars_per_line=42,
    max_lines=2,
    max_reading_speed=20.0,
    require_timecode=True,
    head_black_seconds=(0.0, 2.0),
    tail_black_seconds=(0.0, 2.0),
    notes=[
        "Netflix expects textless elements and a separate M&E track; this engine "
        "checks the primary deliverable only.",
    ],
)

EBU_R128 = Profile(
    key="ebu_r128",
    name="European broadcast (EBU R128)",
    description="EBU R128 / EBU Tech 3341 broadcast delivery. -23 LUFS, -1 dBTP.",
    allowed_codecs=["prores", "xdcam", "mpeg2video", "h264", "dnxhd", "hevc"],
    allowed_resolutions=[(1920, 1080), (1280, 720), (720, 576), (3840, 2160)],
    allowed_fps=[25.0, 50.0],
    min_bit_depth=8,
    require_progressive=False,
    legal_luma=(64, 940),
    legal_chroma=(64, 960),
    max_freeze_seconds=2.0,
    max_black_run_seconds=3.0,
    require_color_tags=True,
    target_loudness=-23.0,
    loudness_tolerance=1.0,
    max_true_peak=-1.0,
    max_lra=20.0,
    required_channel_layouts=["stereo", "5.1", "5.1(side)"],
    min_audio_bit_depth=16,
    silence_floor_db=-60.0,
    min_subtitle_duration=1.0,
    max_subtitle_duration=8.0,
    min_subtitle_gap=0.04,
    max_chars_per_line=37,
    max_lines=2,
    max_reading_speed=17.0,
    require_timecode=True,
    head_black_seconds=(0.0, 1.0),
    tail_black_seconds=(0.0, 2.0),
    notes=["Interlaced 1080i25 is acceptable for this target."],
)

ATSC_A85 = Profile(
    key="atsc_a85",
    name="US broadcast (ATSC A/85)",
    description="ATSC A/85 CALM Act delivery. -24 LKFS, -2 dBTP, 29.97/59.94.",
    allowed_codecs=["prores", "xdcam", "mpeg2video", "h264", "dnxhd", "hevc"],
    allowed_resolutions=[(1920, 1080), (1280, 720), (720, 486), (3840, 2160)],
    allowed_fps=[23.976, 29.97, 59.94],
    min_bit_depth=8,
    require_progressive=False,
    legal_luma=(64, 940),
    legal_chroma=(64, 960),
    max_freeze_seconds=2.0,
    max_black_run_seconds=3.0,
    require_color_tags=True,
    target_loudness=-24.0,
    loudness_tolerance=2.0,
    max_true_peak=-2.0,
    max_lra=None,
    required_channel_layouts=["stereo", "5.1", "5.1(side)"],
    min_audio_bit_depth=16,
    silence_floor_db=-60.0,
    min_subtitle_duration=1.0,
    max_subtitle_duration=8.0,
    min_subtitle_gap=0.033,
    max_chars_per_line=32,
    max_lines=3,
    max_reading_speed=17.0,
    require_timecode=True,
    head_black_seconds=(0.0, 1.0),
    tail_black_seconds=(0.0, 2.0),
    notes=["CEA-608/708 caption presence is checked; caption text QC is out of scope."],
)

DCP_THEATRICAL = Profile(
    key="dcp_theatrical",
    name="Theatrical / DCP",
    description=(
        "SMPTE DCP delivery. JPEG2000, 24/48 fps, 5.1 or 7.1 discrete, "
        "no loudness normalisation (mixed to reference level)."
    ),
    allowed_codecs=["jpeg2000", "prores"],
    allowed_resolutions=[(2048, 1080), (1998, 1080), (4096, 2160), (3996, 2160)],
    allowed_fps=[24.0, 25.0, 48.0],
    min_bit_depth=12,
    require_progressive=True,
    legal_luma=(0, 4095),   # DCP is full-range XYZ
    legal_chroma=(0, 4095),
    max_freeze_seconds=2.0,
    max_black_run_seconds=5.0,
    require_color_tags=True,
    target_loudness=None,   # theatrical is not loudness-normalised
    loudness_tolerance=0.0,
    max_true_peak=0.0,
    max_lra=None,
    required_channel_layouts=["5.1", "5.1(side)", "7.1"],
    min_audio_bit_depth=24,
    silence_floor_db=-70.0,
    min_subtitle_duration=0.833,
    max_subtitle_duration=7.0,
    min_subtitle_gap=0.083,
    max_chars_per_line=45,
    max_lines=2,
    max_reading_speed=20.0,
    require_timecode=False,
    head_black_seconds=(0.0, 5.0),
    tail_black_seconds=(0.0, 5.0),
    notes=[
        "Full DCP package validation (CPL/PKL/ASSETMAP hashes) requires the "
        "package, not a flat file. This profile QCs the picture/sound essence.",
    ],
)

GENERIC = Profile(
    key="generic",
    name="Generic / festival screener",
    description="Permissive baseline for festival submission and internal review.",
    allowed_codecs=["h264", "hevc", "prores", "dnxhd", "vp9", "av1", "mpeg4"],
    allowed_resolutions=None,
    allowed_fps=None,
    min_bit_depth=8,
    require_progressive=False,
    legal_luma=(0, 1023),
    legal_chroma=(0, 1023),
    max_freeze_seconds=5.0,
    max_black_run_seconds=8.0,
    require_color_tags=False,
    target_loudness=-16.0,
    loudness_tolerance=3.0,
    max_true_peak=-1.0,
    max_lra=None,
    required_channel_layouts=["stereo", "5.1", "5.1(side)", "7.1", "mono"],
    min_audio_bit_depth=16,
    silence_floor_db=-60.0,
    min_subtitle_duration=0.8,
    max_subtitle_duration=8.0,
    min_subtitle_gap=0.04,
    max_chars_per_line=45,
    max_lines=3,
    max_reading_speed=22.0,
    require_timecode=False,
    head_black_seconds=None,
    tail_black_seconds=None,
    notes=[],
)


PROFILES: dict[str, Profile] = {
    p.key: p
    for p in (NETFLIX_IMF, EBU_R128, ATSC_A85, DCP_THEATRICAL, GENERIC)
}


def get_profile(key: str) -> Profile:
    if key not in PROFILES:
        raise KeyError(
            f"Unknown profile '{key}'. Available: {', '.join(PROFILES)}"
        )
    return PROFILES[key]


def list_profiles() -> list[dict[str, str]]:
    """Shape Bubble's dropdown expects."""
    return [
        {"key": p.key, "name": p.name, "description": p.description}
        for p in PROFILES.values()
    ]


# --------------------------------------------------------------------------
# Check registry: weight + category + remediation copy
# --------------------------------------------------------------------------

CHECKS: dict[str, dict[str, Any]] = {
    # ---- video ----
    "codec_conformance": {
        "category": "video", "weight": 10,
        "label": "Video codec / wrapper",
        "fix": "Re-encode or rewrap to an accepted mezzanine codec. ProRes 422 HQ "
               "or 4444 covers nearly every delivery target.",
    },
    "resolution": {
        "category": "video", "weight": 10,
        "label": "Raster size",
        "fix": "Re-export at the spec raster. Never upscale a finished master — "
               "go back to the conform and re-render.",
    },
    "frame_rate": {
        "category": "video", "weight": 10,
        "label": "Frame rate",
        "fix": "Retime in the NLE with correct pulldown/cadence handling. A blind "
               "frame-rate conversion will introduce judder.",
    },
    "bit_depth": {
        "category": "video", "weight": 7,
        "label": "Bit depth",
        "fix": "Re-render from the online at 10-bit or higher. 8-bit masters band "
               "in gradients and will be rejected.",
    },
    "scan_type": {
        "category": "video", "weight": 8,
        "label": "Scan type / interlacing",
        "fix": "Deinterlace with a motion-compensated filter (QTGMC or equivalent), "
               "or re-conform progressive if source allows.",
    },
    "video_levels": {
        "category": "video", "weight": 9,
        "label": "Legal video levels",
        "fix": "Apply a broadcast-safe limiter in the grade. Do not use a blunt "
               "clamp — soft-clip the highlights and lift crushed blacks.",
    },
    "freeze_frames": {
        "category": "video", "weight": 8,
        "label": "Frozen / repeated frames",
        "fix": "Check the render at these timecodes for a dropped-frame render "
               "error or a stuck source clip, then re-render the affected reel.",
    },
    "black_frames": {
        "category": "video", "weight": 6,
        "label": "Unexpected black",
        "fix": "Confirm these are intentional. Mid-content black longer than the "
               "spec limit reads as a dropout to automated QC.",
    },
    "color_tags": {
        "category": "video", "weight": 6,
        "label": "Colour primaries / transfer / matrix",
        "fix": "Rewrap with explicit colour tags (e.g. bt709 / bt709 / bt709). "
               "Untagged masters get misinterpreted downstream.",
    },
    "dead_pixels": {
        "category": "video", "weight": 5,
        "label": "Stuck / dead pixels",
        "fix": "Paint out the offending pixels in the VFX pass, or apply a "
               "targeted pixel-fix filter before re-render.",
    },
    # ---- audio ----
    "loudness_integrated": {
        "category": "audio", "weight": 12,
        "label": "Integrated loudness",
        "fix": "Apply a single static gain trim to the full mix to hit target. "
               "Do not re-compress — that changes the mix the director approved.",
    },
    "true_peak": {
        "category": "audio", "weight": 10,
        "label": "True peak ceiling",
        "fix": "Insert a true-peak limiter at the spec ceiling on the master bus "
               "and re-render the mix stems.",
    },
    "loudness_range": {
        "category": "audio", "weight": 5,
        "label": "Loudness range (LRA)",
        "fix": "Excessive LRA means quiet dialogue against loud effects. Ride the "
               "dialogue stem up rather than compressing the whole mix.",
    },
    "channel_layout": {
        "category": "audio", "weight": 10,
        "label": "Channel configuration",
        "fix": "Remap to the spec layout in the correct SMPTE channel order "
               "(L R C LFE Ls Rs). Wrong channel order is the single most common "
               "audio reject.",
    },
    "audio_bit_depth": {
        "category": "audio", "weight": 6,
        "label": "Audio bit depth / sample rate",
        "fix": "Deliver 24-bit / 48 kHz PCM. Never upsample a 16-bit mix and call "
               "it 24-bit — re-render from the session.",
    },
    "dead_channels": {
        "category": "audio", "weight": 10,
        "label": "Silent or dead channels",
        "fix": "A fully silent channel usually means a muted bus or a bad stem "
               "export. Check the mix session routing and re-export.",
    },
    "audio_sync": {
        "category": "audio", "weight": 8,
        "label": "A/V sync drift",
        "fix": "Slip the audio to match the 2-pop / sync reference. Drift that "
               "grows over runtime indicates a sample-rate mismatch at export.",
    },
    # ---- subtitles ----
    "subtitle_presence": {
        "category": "subtitles", "weight": 8,
        "label": "Subtitle / caption track present",
        "fix": "Attach the sidecar subtitle file or embed the caption track. Most "
               "streaming targets reject delivery without one.",
    },
    "subtitle_timing": {
        "category": "subtitles", "weight": 8,
        "label": "Subtitle duration and gaps",
        "fix": "Extend short events to the spec minimum and enforce the minimum "
               "inter-event gap. Most subtitle editors can batch-fix this.",
    },
    "subtitle_formatting": {
        "category": "subtitles", "weight": 6,
        "label": "Line length and line count",
        "fix": "Re-break the offending events. Break on grammatical clauses, not "
               "at the character limit.",
    },
    "subtitle_reading_speed": {
        "category": "subtitles", "weight": 6,
        "label": "Reading speed",
        "fix": "Condense the text or extend the event. Reading speed above spec "
               "is the most common subtitle QC failure.",
    },
    # ---- structure ----
    "timecode": {
        "category": "structure", "weight": 8,
        "label": "Embedded timecode",
        "fix": "Rewrap with a start timecode track (01:00:00:00 for the first "
               "frame of picture is the usual convention).",
    },
    "head_tail_black": {
        "category": "structure", "weight": 6,
        "label": "Head / tail black",
        "fix": "Trim or pad the head and tail to the spec. Extra black at head "
               "throws off the receiving system's frame-count validation.",
    },
    "duration": {
        "category": "structure", "weight": 6,
        "label": "Runtime",
        "fix": "Confirm the runtime matches the delivery paperwork. A mismatch "
               "means a missing or duplicated reel.",
    },
    "container_integrity": {
        "category": "structure", "weight": 10,
        "label": "Container / stream integrity",
        "fix": "Decode errors mean a corrupt or truncated file. Re-transfer from "
               "the source, and verify the checksum before re-submitting.",
    },
    "metadata_completeness": {
        "category": "structure", "weight": 4,
        "label": "Metadata completeness",
        "fix": "Populate title, aspect ratio and creation metadata at rewrap.",
    },
}
