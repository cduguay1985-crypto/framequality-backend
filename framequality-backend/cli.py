"""Command-line runner — same engine the API uses.

    python cli.py FILM.mov --profile netflix_imf --out ./out --subs FILM.srt
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from qc import run_scan
from qc.profiles import PROFILES
from qc.report import build_html, build_pdf


def main() -> int:
    ap = argparse.ArgumentParser(description="FrameQuality Pro QC scan")
    ap.add_argument("media")
    ap.add_argument("--profile", default="netflix_imf", choices=list(PROFILES))
    ap.add_argument("--out", default="./qc_out")
    ap.add_argument("--subs", default=None, help="sidecar .srt/.vtt")
    ap.add_argument("--title", default=None)
    ap.add_argument("--runtime", type=float, default=None,
                    help="expected runtime in seconds, from the paperwork")
    ap.add_argument("--fast", action="store_true",
                    help="sample the first 5 minutes instead of a full decode")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    def progress(pct: int, stage: str) -> None:
        bar = "#" * (pct // 4)
        print(f"\r[{bar:<25}] {pct:3d}%  {stage:<44}", end="", flush=True)

    report = run_scan(
        args.media, args.profile, args.out,
        subtitle_path=args.subs, expected_runtime=args.runtime,
        title=args.title, progress=progress, deep=not args.fast,
    )
    print()

    with open(os.path.join(args.out, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    with open(os.path.join(args.out, "report.html"), "w") as fh:
        fh.write(build_html(report))
    build_pdf(report, os.path.join(args.out, "qc_report.pdf"), args.out)

    sc = report["score"]
    print(f"\n  {sc['verdict']}   {sc['overall']:.0f}/100  (grade {sc['grade']})")
    print(f"  {sc['counts']['fail']} blocking · {sc['counts']['warn']} review · "
          f"{sc['counts']['pass']} passed\n")
    for a in report["action_list"]:
        mark = "✗" if a["severity"] == "fail" else "!"
        print(f"  {mark} {a['issue']}: {a['detail']}")
        if a["at"]:
            print(f"      at {', '.join(a['at'][:4])}")
    print(f"\n  Report written to {args.out}/qc_report.pdf")
    return 1 if sc["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
