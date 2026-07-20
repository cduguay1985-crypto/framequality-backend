"""Printable QC report — PDF (ReportLab) plus a standalone HTML view that
Bubble can drop into an iframe."""

from __future__ import annotations

import html
import os
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

INK = colors.HexColor("#16181d")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d8dbe0")
BAND = colors.HexColor("#f4f5f7")

SEVERITY_COLOR = {
    "fail": colors.HexColor("#b3261e"),
    "warn": colors.HexColor("#9a6700"),
    "pass": colors.HexColor("#1a7f37"),
    "info": colors.HexColor("#57606a"),
}
SEVERITY_LABEL = {"fail": "FAIL", "warn": "REVIEW", "pass": "PASS", "info": "INFO"}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=21, leading=25, textColor=INK,
                                alignment=TA_LEFT, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica",
                              fontSize=9.5, leading=13, textColor=MUTED),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=12.5, leading=15, textColor=INK,
                             spaceBefore=16, spaceAfter=7),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=13.5, textColor=INK),
        "small": ParagraphStyle("sm", parent=base["Normal"], fontName="Helvetica",
                                fontSize=8.2, leading=11, textColor=MUTED),
        "fix": ParagraphStyle("fx", parent=base["Normal"], fontName="Helvetica-Oblique",
                              fontSize=9, leading=12.5,
                              textColor=colors.HexColor("#1f3a5f")),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontName="Helvetica",
                               fontSize=8.6, leading=11.5, textColor=INK),
        "cellb": ParagraphStyle("cb", parent=base["Normal"],
                                fontName="Helvetica-Bold", fontSize=8.6,
                                leading=11.5, textColor=INK),
        "score": ParagraphStyle("sc", parent=base["Normal"],
                                fontName="Helvetica-Bold", fontSize=30,
                                leading=34, textColor=INK),
    }


def build_pdf(report: dict[str, Any], out_path: str, media_dir: str) -> str:
    S = _styles()
    doc = BaseDocTemplate(
        out_path, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.62 * inch, bottomMargin=0.62 * inch,
        title=f"QC Report — {report['title']}", author="FrameQuality Pro",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")

    def decorate(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 0.42 * inch,
                          f"FrameQuality Pro — {report['file']}")
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.42 * inch,
                               f"Page {canvas.getPageNumber()}")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, 0.58 * inch,
                    LETTER[0] - doc.rightMargin, 0.58 * inch)
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])

    story: list[Any] = []
    sc = report["score"]

    # ---- header ----------------------------------------------------------
    story.append(Paragraph(html.escape(report["title"]), S["title"]))
    story.append(Paragraph(
        f"Quality-control scan against <b>{html.escape(report['profile']['name'])}</b> "
        f"&nbsp;·&nbsp; {report['source']['duration_tc']} runtime "
        f"&nbsp;·&nbsp; {html.escape(report['file'])}", S["sub"]))
    story.append(Spacer(1, 14))

    # ---- verdict band ----------------------------------------------------
    verdict_color = {
        "PASS": SEVERITY_COLOR["pass"],
        "PASS WITH NOTES": SEVERITY_COLOR["warn"],
        "FAIL": SEVERITY_COLOR["fail"],
    }[sc["verdict"]]

    score_cell = [
        Paragraph(f'<font color="{verdict_color.hexval()}">'
                  f'{sc["overall"]:.0f}</font>'
                  f'<font size="12" color="#6b7280"> /100</font>', S["score"]),
        Spacer(1, 2),
        Paragraph(f'<font size="9" color="#6b7280">GRADE {sc["grade"]}</font>',
                  S["small"]),
    ]
    verdict_cell = [
        Paragraph(f'<font size="14" color="{verdict_color.hexval()}"><b>'
                  f'{sc["verdict"]}</b></font>', S["body"]),
        Spacer(1, 3),
        Paragraph(html.escape(sc["verdict_detail"]), S["body"]),
        Spacer(1, 4),
        Paragraph(
            f'<b>{sc["counts"]["fail"]}</b> blocking &nbsp;·&nbsp; '
            f'<b>{sc["counts"]["warn"]}</b> review &nbsp;·&nbsp; '
            f'<b>{sc["counts"]["pass"]}</b> passed', S["small"]),
    ]
    band = Table([[score_cell, verdict_cell]], colWidths=[1.5 * inch, 5.6 * inch])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("LINEAFTER", (0, 0), (0, 0), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    story.append(band)

    # ---- category scores -------------------------------------------------
    story.append(Paragraph("Category scores", S["h2"]))
    rows = [[Paragraph("<b>Area</b>", S["cellb"]),
             Paragraph("<b>Score</b>", S["cellb"]),
             Paragraph("<b>Rating</b>", S["cellb"]), ""]]
    for cat, val in sc["categories"].items():
        if val is None:
            rows.append([Paragraph(cat.title(), S["cell"]),
                         Paragraph("n/a", S["cell"]),
                         Paragraph("not evaluated", S["cell"]), ""])
            continue
        col = (SEVERITY_COLOR["pass"] if val >= 85 else
               SEVERITY_COLOR["warn"] if val >= 65 else SEVERITY_COLOR["fail"])
        bar = Table([[""]], colWidths=[max(2.0, 2.6 * val / 100) * inch],
                    rowHeights=[7])
        bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        rows.append([
            Paragraph(cat.title(), S["cell"]),
            Paragraph(f'<font color="{col.hexval()}"><b>{val:.0f}</b></font>',
                      S["cell"]),
            Paragraph(_rating_word(val), S["cell"]),
            bar,
        ])
    tbl = Table(rows, colWidths=[1.4 * inch, 0.7 * inch, 1.3 * inch, 3.7 * inch])
    tbl.setStyle(_grid_style())
    story.append(tbl)

    # ---- action list -----------------------------------------------------
    actions = report["action_list"]
    story.append(Paragraph("What needs to be fixed", S["h2"]))
    if not actions:
        story.append(Paragraph(
            "Nothing. The master conforms to every check in this profile.",
            S["body"]))
    else:
        rows = [[Paragraph("<b>#</b>", S["cellb"]),
                 Paragraph("<b>Severity</b>", S["cellb"]),
                 Paragraph("<b>Issue</b>", S["cellb"]),
                 Paragraph("<b>Measured</b>", S["cellb"]),
                 Paragraph("<b>Required</b>", S["cellb"])]]
        by_check = {f["check"]: f for f in report["findings"]}
        for a in actions:
            f = next((x for x in report["findings"]
                      if x["label"] == a["issue"]), {})
            col = SEVERITY_COLOR[a["severity"]]
            rows.append([
                Paragraph(str(a["priority"]), S["cell"]),
                Paragraph(f'<font color="{col.hexval()}"><b>'
                          f'{SEVERITY_LABEL[a["severity"]]}</b></font>', S["cell"]),
                Paragraph(html.escape(a["issue"]), S["cell"]),
                Paragraph(html.escape(str(f.get("measured") or "—")), S["cell"]),
                Paragraph(html.escape(str(f.get("expected") or "—")), S["cell"]),
            ])
        tbl = Table(rows, colWidths=[0.32 * inch, 0.78 * inch, 1.9 * inch,
                                     2.1 * inch, 2.0 * inch], repeatRows=1)
        tbl.setStyle(_grid_style())
        story.append(tbl)

    # ---- detail per finding ---------------------------------------------
    detailed = [f for f in report["findings"] if f["status"] in ("fail", "warn")]
    if detailed:
        story.append(PageBreak())
        story.append(Paragraph("Findings in detail", S["h2"]))
        for i, f in enumerate(detailed, 1):
            story.append(_finding_block(f, i, S, media_dir))

    # ---- source technical summary ---------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Source file specification", S["h2"]))
    src = report["source"]
    spec_rows = [
        ("Container", src["container"]),
        ("Video codec", f'{src["video_codec"]} · {src["pix_fmt"]} · '
                        f'{src["bit_depth"]}-bit'),
        ("Raster / rate", f'{src["resolution"]} @ {src["fps"]} fps'),
        ("Runtime", src["duration_tc"]),
        ("Start timecode", src["start_timecode"] or "not present"),
        ("File size", f'{src["size_bytes"] / 1_000_000_000:.2f} GB'),
        ("Subtitle tracks", str(src["subtitle_streams"])),
    ]
    for i, a in enumerate(src["audio_streams"], 1):
        spec_rows.append((
            f"Audio {i}",
            f'{a["codec"]} · {a["layout"] or a["channels"]} · '
            f'{a["sample_rate"]} Hz · {a["sample_fmt"]}'))

    rows = [[Paragraph(f"<b>{html.escape(k)}</b>", S["cellb"]),
             Paragraph(html.escape(str(v)), S["cell"])] for k, v in spec_rows]
    tbl = Table(rows, colWidths=[1.6 * inch, 5.5 * inch])
    tbl.setStyle(_grid_style())
    story.append(tbl)

    story.append(Paragraph("Passed checks", S["h2"]))
    passed = [f for f in report["findings"] if f["status"] == "pass"]
    if passed:
        story.append(Paragraph(
            " &nbsp;·&nbsp; ".join(html.escape(f["label"]) for f in passed),
            S["small"]))
    else:
        story.append(Paragraph("None.", S["small"]))

    notes = report["profile"].get("notes") or []
    if notes:
        story.append(Paragraph("Scope notes", S["h2"]))
        for n in notes:
            story.append(Paragraph("• " + html.escape(n), S["small"]))

    doc.build(story)
    return out_path


def _rating_word(v: float) -> str:
    if v >= 93:
        return "delivery ready"
    if v >= 85:
        return "minor notes"
    if v >= 75:
        return "needs work"
    if v >= 65:
        return "significant work"
    return "not deliverable"


def _grid_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])


def _finding_block(f: dict[str, Any], idx: int, S, media_dir: str):
    col = SEVERITY_COLOR[f["status"]]
    parts: list[Any] = [
        Spacer(1, 9),
        Paragraph(
            f'<font color="{col.hexval()}"><b>{SEVERITY_LABEL[f["status"]]}</b></font>'
            f' &nbsp; <b>{idx}. {html.escape(f["label"])}</b>'
            f' <font color="#6b7280" size="8">({f["category"]})</font>',
            S["body"]),
        Spacer(1, 3),
        Paragraph(html.escape(f["message"]), S["body"]),
    ]
    if f.get("fix"):
        parts += [Spacer(1, 4),
                  Paragraph("<b>Suggested fix:</b> " + html.escape(f["fix"]),
                            S["fix"])]

    occ = f.get("occurrences") or []
    if occ:
        rows = [[Paragraph("<b>Timecode</b>", S["cellb"]),
                 Paragraph("<b>Detail</b>", S["cellb"])]]
        for o in occ[:12]:
            rows.append([Paragraph(o["timecode"], S["cell"]),
                         Paragraph(html.escape(o["note"] or ""), S["cell"])])
        t = Table(rows, colWidths=[1.1 * inch, 6.0 * inch])
        t.setStyle(_grid_style())
        parts += [Spacer(1, 6), t]

        images = [o for o in occ if o.get("frame")]
        if images:
            cells, caps = [], []
            for o in images[:4]:
                p = os.path.join(media_dir, o["frame"])
                if not os.path.exists(p):
                    continue
                cells.append(Image(p, width=1.65 * inch, height=0.93 * inch))
                caps.append(Paragraph(o["timecode"], S["small"]))
            if cells:
                grid = Table([cells, caps], colWidths=[1.75 * inch] * len(cells))
                grid.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ]))
                parts += [Spacer(1, 6), grid]

    parts.append(Spacer(1, 6))
    return KeepTogether(parts)


# --------------------------------------------------------------------------
# HTML view (for embedding in a Bubble page)
# --------------------------------------------------------------------------


def build_html(report: dict[str, Any], media_base: str = "") -> str:
    sc = report["score"]
    vcol = {"PASS": "#1a7f37", "PASS WITH NOTES": "#9a6700",
            "FAIL": "#b3261e"}[sc["verdict"]]

    def sev_chip(s: str) -> str:
        c = {"fail": "#b3261e", "warn": "#9a6700",
             "pass": "#1a7f37", "info": "#57606a"}[s]
        return (f'<span class="chip" style="background:{c}1a;color:{c};'
                f'border-color:{c}55">{SEVERITY_LABEL[s]}</span>')

    cats = "".join(
        f'<div class="cat"><div class="catname">{c.title()}</div>'
        f'<div class="bar"><i style="width:{(v or 0):.0f}%;background:'
        f'{"#1a7f37" if (v or 0) >= 85 else "#9a6700" if (v or 0) >= 65 else "#b3261e"}">'
        f'</i></div><div class="catval">{"n/a" if v is None else f"{v:.0f}"}</div></div>'
        for c, v in sc["categories"].items()
    )

    blocks = []
    for i, f in enumerate(
            [x for x in report["findings"] if x["status"] in ("fail", "warn")], 1):
        occ_rows = "".join(
            f'<tr><td class="tc">{html.escape(o["timecode"])}</td>'
            f'<td>{html.escape(o["note"] or "")}</td></tr>'
            for o in (f.get("occurrences") or [])[:12]
        )
        imgs = "".join(
            f'<figure><img src="{media_base}{o["frame"]}" alt="frame at '
            f'{o["timecode"]}"><figcaption>{o["timecode"]}</figcaption></figure>'
            for o in (f.get("occurrences") or []) if o.get("frame")
        )
        blocks.append(f"""
        <section class="finding">
          <h3>{sev_chip(f['status'])} {i}. {html.escape(f['label'])}
            <small>{f['category']}</small></h3>
          <p>{html.escape(f['message'])}</p>
          <p class="meta"><b>Measured:</b> {html.escape(str(f.get('measured') or '—'))}
             &nbsp;·&nbsp; <b>Required:</b> {html.escape(str(f.get('expected') or '—'))}</p>
          {f'<p class="fix"><b>Suggested fix:</b> {html.escape(f["fix"])}</p>' if f.get('fix') else ''}
          {f'<table class="occ"><tr><th>Timecode</th><th>Detail</th></tr>{occ_rows}</table>' if occ_rows else ''}
          {f'<div class="frames">{imgs}</div>' if imgs else ''}
        </section>""")

    return f"""<!doctype html><meta charset="utf-8">
<title>QC Report — {html.escape(report['title'])}</title>
<style>
 :root{{--ink:#16181d;--muted:#6b7280;--rule:#e3e5e9}}
 *{{box-sizing:border-box}}
 body{{font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
   color:var(--ink);margin:0;padding:32px;max-width:1000px}}
 h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:16px;margin:34px 0 10px}}
 h3{{font-size:14px;margin:0 0 6px;display:flex;align-items:center;gap:8px}}
 h3 small{{color:var(--muted);font-weight:400}}
 .sub{{color:var(--muted);margin:0 0 22px}}
 .band{{display:flex;gap:26px;align-items:center;background:#f4f5f7;
   border:1px solid var(--rule);border-radius:10px;padding:20px 24px}}
 .score{{font-size:42px;font-weight:700;line-height:1;color:{vcol}}}
 .score span{{font-size:15px;color:var(--muted);font-weight:400}}
 .verdict{{font-size:18px;font-weight:700;color:{vcol}}}
 .cat{{display:grid;grid-template-columns:120px 1fr 46px;align-items:center;
   gap:12px;margin:7px 0}}
 .catname{{color:var(--muted)}} .catval{{text-align:right;font-variant-numeric:tabular-nums}}
 .bar{{height:9px;background:#eceef1;border-radius:5px;overflow:hidden}}
 .bar i{{display:block;height:100%}}
 .chip{{font-size:10.5px;font-weight:700;letter-spacing:.4px;padding:2px 7px;
   border-radius:4px;border:1px solid}}
 .finding{{border:1px solid var(--rule);border-radius:10px;padding:16px 18px;
   margin:12px 0}}
 .finding p{{margin:6px 0}}
 .meta,.occ{{font-size:12.5px;color:var(--muted)}}
 .fix{{background:#f2f6fc;border-left:3px solid #1f3a5f;padding:9px 12px;
   border-radius:0 6px 6px 0;font-size:13px}}
 table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:12.5px}}
 th,td{{border:1px solid var(--rule);padding:6px 9px;text-align:left}}
 th{{background:#f4f5f7}} .tc{{font-variant-numeric:tabular-nums;white-space:nowrap}}
 .frames{{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}}
 figure{{margin:0;width:220px}}
 figure img{{width:100%;border:1px solid var(--rule);border-radius:6px;display:block}}
 figcaption{{font-size:11.5px;color:var(--muted);margin-top:4px}}
</style>
<h1>{html.escape(report['title'])}</h1>
<p class="sub">{html.escape(report['profile']['name'])} &nbsp;·&nbsp;
  {report['source']['duration_tc']} &nbsp;·&nbsp; {html.escape(report['file'])}</p>
<div class="band">
  <div><div class="score">{sc['overall']:.0f}<span>/100</span></div>
    <div style="color:var(--muted);font-size:12px">GRADE {sc['grade']}</div></div>
  <div><div class="verdict">{sc['verdict']}</div>
    <div>{html.escape(sc['verdict_detail'])}</div>
    <div style="color:var(--muted);font-size:12.5px;margin-top:4px">
      <b>{sc['counts']['fail']}</b> blocking ·
      <b>{sc['counts']['warn']}</b> review ·
      <b>{sc['counts']['pass']}</b> passed</div></div>
</div>
<h2>Category scores</h2>{cats}
<h2>What needs to be fixed</h2>
{"".join(blocks) or "<p>Nothing. The master conforms to every check in this profile.</p>"}
"""
