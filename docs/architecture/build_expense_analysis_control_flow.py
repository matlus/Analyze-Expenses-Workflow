# /// script
# requires-python = ">=3.14"
# dependencies = ["markdown-it-py>=3.0", "Pygments>=2.0"]
# ///

"""Build the standalone diagram and explorer from expense-analysis-control-flow.md.

Run with a Python environment containing markdown-it-py and Pygments. The
generated HTML has no runtime dependencies and works when opened as a file.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "expense-analysis-control-flow.md"
HTML_OUTPUT = HERE / "expense-analysis-control-flow.html"


@dataclass(frozen=True)
class Box:
    key: str
    title: str
    actor: str
    short_description: str
    body_markdown: str
    x: int
    y: int
    width: int
    height: int
    tone: str


TONES = {
    "code": ("#ecf7f8", "#197487"),
    "jev": ("#fff6df", "#aa7215"),
    "llm": ("#f3effc", "#7351a5"),
    "scatter": ("#edf7ee", "#397356"),
    "check": ("#fff0ef", "#b85454"),
    "output": ("#eef4fc", "#426c9a"),
}

MAIN_YS = (240, 345, 450, 555, 660, 1150, 1255, 1360, 1465, 1570, 1675, 1780, 1885)
BRANCH_XS = (80, 610, 1140)
ROW_CELL_COUNT = 2
REPEATED_ROW_COUNT = 3
CATEGORY_STEP_NUMBER = 5
REPEATED_BOX_COUNT = 6
COMPACT_BOX_WIDTH = 500


def _actor_tag(actor: str) -> str:
    if actor == "Code":
        return "CODE"
    if actor == "System 1 / Jev":
        return "SYSTEM 1 · JEV"
    if actor == "Large language model (LLM)":
        return "LLM"
    raise ValueError(f"Unknown actor: {actor}")


def _actor_tone(actor: str) -> str:
    return {
        "Code": "code",
        "System 1 / Jev": "jev",
        "Large language model (LLM)": "llm",
    }[actor]


def _extract_actor_and_description(section: str, actor_level: int) -> tuple[str, str]:
    actor_match = re.search(rf"^{'#' * actor_level} Actor: (.+)$", section, re.MULTILINE)
    short_match = re.search(r"^\*\*Short description:\*\* (.+)$", section, re.MULTILINE)
    if actor_match is None or short_match is None:
        raise ValueError("Every numbered box needs an Actor heading and Short description")
    return actor_match.group(1).strip(), short_match.group(1).strip()


def _repeated_boxes(section: str, kind: str, y: int) -> list[Box]:
    actor_match = re.search(r"^##### Actor: (.+)$", section, re.MULTILINE)
    if actor_match is None:
        raise ValueError(f"The {kind} group needs an Actor heading")
    actor = actor_match.group(1).strip()
    rows = []
    for line in section.splitlines():
        if line.startswith(f"| {kind} "):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == ROW_CELL_COUNT:
                rows.append((cells[0], cells[1]))
    if len(rows) != REPEATED_ROW_COUNT:
        raise ValueError(f"Expected three visual {kind} cards, found {len(rows)}")
    return [
        Box(
            key=f"{kind.lower().replace(' ', '-')}-{index + 1}",
            title=title,
            actor=actor,
            short_description=short,
            body_markdown=section,
            x=x,
            y=y,
            width=380,
            height=70,
            tone=_actor_tone(actor),
        )
        for index, ((title, short), x) in enumerate(zip(rows, BRANCH_XS, strict=True))
    ]


def _parse_document(source: str) -> tuple[str, list[Box], str]:
    numbered_flow, _, source_links = source.partition("## Where to inspect the implementation")
    overview, marker, flow = numbered_flow.partition("## The numbered flow")
    if not marker:
        raise ValueError("Missing 'The numbered flow' section")
    headings = list(re.finditer(r"^### (\d{2}) (.+)$", flow, re.MULTILINE))
    if [int(match.group(1)) for match in headings] != list(range(1, 14)):
        raise ValueError("Expected numbered boxes 01 through 13 in order")

    boxes: list[Box] = []
    repeated: list[Box] = []
    for index, match in enumerate(headings):
        number = int(match.group(1))
        end = headings[index + 1].start() if index + 1 < len(headings) else len(flow)
        section = flow[match.start() : end].strip()
        actor, short = _extract_actor_and_description(section, 4)
        body_start = re.search(r"^\*\*Short description:\*\* .+$", section, re.MULTILINE)
        if body_start is None:
            raise ValueError(f"Step {number:02d} is missing its short description")
        body = section[body_start.end() :].strip()

        if number == CATEGORY_STEP_NUMBER:
            choose_match = re.search(r"^#### Choose category .+$", section, re.MULTILINE)
            policy_match = re.search(r"^#### Apply category rules .+$", section, re.MULTILINE)
            if choose_match is None or policy_match is None:
                raise ValueError("Step 05 must define the repeated Jev and policy boxes")
            body = section[body_start.end() : choose_match.start()].strip()
            repeated.extend(
                _repeated_boxes(
                    section[choose_match.start() : policy_match.start()],
                    "Choose category",
                    860,
                )
            )
            repeated.extend(_repeated_boxes(section[policy_match.start() :], "Apply category rules", 965))

        tone = {5: "scatter", 6: "scatter", 9: "check", 13: "output"}.get(number, _actor_tone(actor))
        boxes.append(
            Box(
                key=f"step-{number:02d}",
                title=f"{number:02d}  {match.group(2).strip()}",
                actor=actor,
                short_description=short,
                body_markdown=body,
                x=465,
                y=MAIN_YS[index],
                width=670,
                height=78,
                tone=tone,
            )
        )
    if len(repeated) != REPEATED_BOX_COUNT:
        raise ValueError("Expected three Jev and three coded policy cards")
    source_notes = "## Where to inspect the implementation" + source_links if source_links else ""
    return overview.strip(), boxes + repeated, source_notes


def _svg_node(box: Box) -> str:
    fill, stroke = TONES[box.tone]
    tag = _actor_tag(box.actor)
    tag_width = 116 if tag.startswith("SYSTEM") else 70
    tag_x = box.x + box.width - tag_width - 18
    text_x = box.x + 22
    title_y = box.y + 31
    body_y = box.y + 57
    accent_y = box.y + 15
    accent_height = box.height - 30
    size = 16 if box.width < COMPACT_BOX_WIDTH else 19
    body_size = 13 if box.width < COMPACT_BOX_WIDTH else 14
    attrs = (
        f'class="flow-node" data-key="{html.escape(box.key, quote=True)}" tabindex="0" role="button" '
        f'aria-label="{html.escape(box.title + ", " + box.actor, quote=True)}"'
    )
    return (
        f"<g {attrs}>"
        f'<rect class="card" x="{box.x}" y="{box.y}" width="{box.width}" height="{box.height}" rx="16" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        f'<rect x="{box.x + 2}" y="{accent_y}" width="6" height="{accent_height}" rx="3" fill="{stroke}"/>'
        f'<text x="{text_x}" y="{title_y}" class="title" font-size="{size}">{html.escape(box.title)}</text>'
        f'<rect x="{tag_x}" y="{box.y + 12}" width="{tag_width}" height="22" rx="11" fill="#fff" stroke="{stroke}"/>'
        f'<text x="{tag_x + tag_width / 2}" y="{box.y + 27}" text-anchor="middle" class="tag" fill="{stroke}">'
        f"{html.escape(tag)}</text>"
        f'<text x="{text_x}" y="{body_y}" class="body" font-size="{body_size}">'
        f"{html.escape(box.short_description)}</text></g>"
    )


def _svg_markup(boxes: list[Box]) -> str:
    edges = (
        "M 800 318 L 800 337",
        "M 800 423 L 800 442",
        "M 800 528 L 800 547",
        "M 800 633 L 800 652",
        "M 800 738 C 800 797.28, 270 792.72, 270 852",
        "M 800 738 L 800 852",
        "M 800 738 C 800 797.28, 1330 792.72, 1330 852",
        "M 270 930 L 270 957",
        "M 800 930 L 800 957",
        "M 1330 930 L 1330 957",
        "M 270 1035 C 270 1090.64, 800 1086.36, 800 1142",
        "M 800 1035 L 800 1142",
        "M 1330 1035 C 1330 1090.64, 800 1086.36, 800 1142",
        "M 800 1228 L 800 1247",
        "M 800 1333 L 800 1352",
        "M 800 1438 L 800 1457",
        "M 800 1543 L 800 1562",
        "M 800 1648 L 800 1667",
        "M 800 1753 L 800 1772",
        "M 800 1858 L 800 1877",
    )
    parts = [
        (
            '<svg class="diagram-svg" xmlns="http://www.w3.org/2000/svg" width="1600" height="2020" '
            'viewBox="0 0 1600 2020" role="group" aria-labelledby="diagram-title diagram-desc">'
        ),
        '<title id="diagram-title">Expense analysis control flow</title>',
        (
            '<desc id="diagram-desc">A top-down flow from file input through coded parsing, Jev and LLM decisions, '
            "parallel line categorization, reconciliation, calculations, findings, and report output.</desc>"
        ),
        (
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8.2" refY="5" markerWidth="8" markerHeight="8" '
            'orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#677b8a"/></marker>'
            '<filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">'
            '<feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#1d3550" flood-opacity=".10"/></filter></defs>'
        ),
        (
            '<style>text{font-family:Inter,"Segoe UI",Arial,sans-serif}.title{font-weight:700;fill:#172d3d}'
            ".body{fill:#435c6d}.minor{fill:#607486}.edge{fill:none;stroke:#677b8a;stroke-width:2.8;"
            "marker-end:url(#arrow)}.flow-node{filter:url(#shadow);cursor:pointer}.flow-node>.card{transition:stroke-width .15s,filter .15s}"
            ".flow-node:hover>.card,.flow-node:focus>.card,.flow-node.is-active>.card{stroke-width:4;filter:brightness(.94)}"
            ".flow-node:focus{outline:none}.tag{font-size:11px;font-weight:700;letter-spacing:.7px}</style>"
        ),
        '<rect width="1600" height="2020" fill="#f7fafc"/>',
        '<rect x="44" y="30" width="1512" height="103" rx="21" fill="#ffffff" stroke="#dce7ed"/>',
        '<text x="76" y="75" class="title" font-size="29">Expense analysis · control flow</text>',
        '<text x="77" y="107" class="minor" font-size="15">Document-defined steps · hover or focus a box for details</text>',
        '<rect x="44" y="148" width="1512" height="64" rx="15" fill="#ffffff" stroke="#dce7ed"/>',
    ]
    legend = (
        (75, "CODE", "code"),
        (331, "SYSTEM 1 / JEV", "jev"),
        (637, "LLM", "llm"),
        (803, "CHECK", "check"),
        (977, "SCATTER / GATHER", "scatter"),
        (1332, "OUTPUT", "output"),
    )
    for x, label, tone in legend:
        fill, stroke = TONES[tone]
        parts.append(f'<rect x="{x}" y="169" width="19" height="19" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        parts.append(f'<text x="{x + 29}" y="185" class="body" font-size="13" font-weight="700">{html.escape(label)}</text>')
    parts.extend(f'<path class="edge" d="{path}"/>' for path in edges)
    parts.extend(_svg_node(box) for box in boxes)
    parts.append(
        '<text x="76" y="1994" class="minor" font-size="13">'
        "Recovery stages pass through when unneeded. Scatter starts one task per line; up to eight Jev calls run at once. "
        "Every task contributes to the gather.</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _markdown_renderer() -> tuple[MarkdownIt, str]:
    formatter = HtmlFormatter(style="friendly", nowrap=True, cssclass="syntax")
    markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

    def render_fence(tokens: list[object], index: int, _options: object, _env: object) -> str:
        token = tokens[index]
        language = token.info.strip().split(" ")[0].lower()
        try:
            lexer = get_lexer_by_name(language) if language else TextLexer()
        except ClassNotFound:
            lexer = TextLexer()
        label = language.upper() if language else "TEXT"
        highlighted = highlight(token.content, lexer, formatter)
        return f'<div class="code-block"><div class="code-label">{html.escape(label)}</div><pre><code class="syntax">{highlighted}</code></pre></div>'

    markdown.renderer.rules["fence"] = render_fence
    pygments_css = formatter.get_style_defs(".detail-entry .syntax")
    return markdown, pygments_css


def _details_markup(overview: str, boxes: list[Box], source_notes: str, markdown: MarkdownIt) -> str:
    overview_html = markdown.render(overview + "\n\n" + source_notes)
    parts = [
        (
            '<article class="detail-entry" data-key="overview" id="detail-overview">'
            '<div class="detail-kicker">Architecture overview</div>'
            f'<div class="detail-body">{overview_html}</div></article>'
        )
    ]
    for box in boxes:
        body = markdown.render(box.body_markdown)
        parts.append(
            f'<article class="detail-entry" data-key="{html.escape(box.key, quote=True)}" hidden>'
            f'<div class="detail-kicker">{html.escape(box.actor)}</div>'
            f"<h2>{html.escape(box.title)}</h2>"
            f'<p class="detail-summary">{html.escape(box.short_description)}</p>'
            f'<div class="detail-body">{body}</div>'
            "</article>"
        )
    return "\n".join(parts)


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    overview, boxes, source_notes = _parse_document(source)
    svg = _svg_markup(boxes)
    markdown, pygments_css = _markdown_renderer()
    details = _details_markup(overview, boxes, source_notes, markdown)
    page = PAGE_TEMPLATE.replace("__SVG__", svg).replace("__DETAILS__", details).replace("__PYGMENTS_CSS__", pygments_css)
    HTML_OUTPUT.write_text(page, encoding="utf-8", newline="\n")


PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Expense analysis control flow</title>
  <style>
    :root { color-scheme: light; --ink: #172d3d; --muted: #536978; --line: #d9e4ea; --paper: #fff;
      --canvas: #f7fafc; --accent: #197487; --detail-width: 560px; }
    * { box-sizing: border-box; }
    html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
    body { font: 15px/1.55 Inter, "Segoe UI", Arial, sans-serif; color: var(--ink); background: var(--paper); }
    button { font: inherit; }
    .workspace { display: grid; grid-template-columns: minmax(0, 1fr) 12px var(--detail-width); width: 100%; height: 100dvh; }
    .diagram-pane { min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--canvas); }
    .pane-bar { min-height: 58px; display: flex; align-items: center; justify-content: space-between; gap: 16px;
      padding: 8px 18px; border-bottom: 1px solid var(--line); background: var(--paper); }
    .pane-title { margin: 0; font-size: 17px; line-height: 1.2; font-weight: 700; }
    .pane-hint { color: var(--muted); font-size: 12px; }
    .zoom-controls { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
    .zoom-controls button, .overview-button { border: 1px solid var(--line); background: var(--paper); color: var(--ink);
      border-radius: 7px; padding: 4px 10px; cursor: pointer; }
    .zoom-controls button:hover, .overview-button:hover { border-color: var(--accent); color: var(--accent); }
    .zoom-controls button:focus-visible, .overview-button:focus-visible, .splitter:focus-visible { outline: 3px solid #66bbca; outline-offset: -2px; }
    .zoom-value { min-width: 52px; text-align: center; color: var(--muted); font-variant-numeric: tabular-nums; font-size: 13px; }
    .diagram-viewport { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; }
    .diagram-surface { padding: 0; }
    .diagram-svg { display: block; width: 100%; height: auto; }
    .splitter { position: relative; background: #e8eff3; cursor: col-resize; touch-action: none; border: 0; padding: 0; }
    .splitter:hover, .splitter.dragging { background: #c8dce4; }
    .splitter::after { content: ""; position: absolute; left: 4px; top: calc(50% - 25px); width: 4px; height: 50px;
      border-radius: 4px; background: #8aa5b3; }
    .detail-pane { min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--paper); }
    .detail-header { min-height: 58px; display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 8px 22px; border-bottom: 1px solid var(--line); }
    .detail-scroll { overflow: auto; flex: 1; min-height: 0; padding: 22px 26px 52px; }
    .detail-entry[hidden] { display: none; }
    .detail-kicker { color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; }
    .detail-entry h2 { margin: 4px 0 8px; line-height: 1.22; font-size: 25px; }
    .detail-summary { margin: 0 0 21px; color: var(--muted); font-size: 16px; }
    .detail-body h1 { font-size: 25px; line-height: 1.2; margin: 0 0 16px; }
    .detail-body h2 { font-size: 20px; margin: 25px 0 8px; }
    .detail-body h3, .detail-body h4, .detail-body h5 { font-size: 16px; margin: 23px 0 8px; }
    .detail-body p { margin: 0 0 14px; }
    .detail-body ul, .detail-body ol { margin: 0 0 16px; padding-left: 23px; }
    .detail-body li { margin: 5px 0; }
    .detail-body table { display: block; width: max-content; max-width: 100%; overflow-x: auto; border-collapse: collapse;
      margin: 14px 0 20px; font-size: 13px; }
    .detail-body th, .detail-body td { padding: 8px 10px; border: 1px solid var(--line); text-align: left; vertical-align: top; }
    .detail-body th { background: #edf3f6; font-weight: 700; }
    .detail-body tr:nth-child(even) td { background: #f8fbfc; }
    .detail-body code:not(.syntax) { color: #7b376c; background: #f8f0f7; padding: 1px 4px; border-radius: 3px; font-size: .92em; }
    .detail-body a { color: #126679; text-decoration-thickness: 1px; text-underline-offset: 2px; }
    .code-block { margin: 14px 0 19px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #f7f9fb; }
    .code-label { padding: 5px 10px; color: var(--muted); background: #edf3f6; border-bottom: 1px solid var(--line);
      font: 700 11px/1.3 Inter, "Segoe UI", Arial, sans-serif; letter-spacing: .08em; }
    .code-block pre { margin: 0; padding: 12px 14px; overflow-x: auto; line-height: 1.5; tab-size: 2; }
    .code-block code { font: 12px/1.5 Consolas, "Cascadia Code", monospace; white-space: pre; }
    __PYGMENTS_CSS__
    @media (max-width: 1100px) {
      .workspace { grid-template-columns: 1fr; grid-template-rows: minmax(260px, 56vh) 12px minmax(0, 1fr); }
      .splitter { cursor: row-resize; }
      .splitter::after { left: calc(50% - 25px); top: 4px; width: 50px; height: 4px; }
      .pane-hint { display: none; }
      .detail-scroll { padding: 18px 18px 40px; }
    }
  </style>
</head>
<body>
  <main class="workspace" id="workspace">
    <section class="diagram-pane" aria-label="Control-flow diagram">
      <div class="pane-bar">
        <div><h1 class="pane-title">Expense analysis control flow</h1>
          <div class="pane-hint">Hover or focus a box to inspect its data and decisions</div></div>
        <div class="zoom-controls" aria-label="Diagram zoom">
          <button type="button" id="zoom-out" aria-label="Zoom out">&minus;</button>
          <span class="zoom-value" id="zoom-value">100%</span>
          <button type="button" id="zoom-in" aria-label="Zoom in">+</button>
          <button type="button" id="zoom-fit">Fit</button>
        </div>
      </div>
      <div class="diagram-viewport" id="diagram-viewport">
        <div class="diagram-surface" id="diagram-surface">
__SVG__
        </div>
      </div>
    </section>
    <div class="splitter" id="splitter" role="separator" aria-label="Resize diagram and details" aria-orientation="vertical"
      tabindex="0" aria-valuemin="320" aria-valuemax="1100" aria-valuenow="560"></div>
    <aside class="detail-pane" aria-label="Selected step details">
      <div class="detail-header"><strong>Step details</strong>
        <button type="button" class="overview-button" id="overview-button">Overview</button></div>
      <div class="detail-scroll" id="detail-scroll" aria-live="polite">
__DETAILS__
      </div>
    </aside>
  </main>
  <script>
    (() => {
      const workspace = document.getElementById('workspace');
      const splitter = document.getElementById('splitter');
      const viewport = document.getElementById('diagram-viewport');
      const surface = document.getElementById('diagram-surface');
      const detailScroll = document.getElementById('detail-scroll');
      const entries = [...document.querySelectorAll('.detail-entry')];
      const nodes = [...document.querySelectorAll('.flow-node')];
      const zoomValue = document.getElementById('zoom-value');
      let currentKey = 'overview';
      let zoomFactor = 1;
      let mobileHeight = null;

      function showDetail(key) {
        if (key === currentKey) return;
        currentKey = key;
        for (const entry of entries) entry.hidden = entry.dataset.key !== key;
        for (const node of nodes) node.classList.toggle('is-active', node.dataset.key === key);
        detailScroll.scrollTop = 0;
      }
      for (const node of nodes) {
        node.addEventListener('pointerenter', () => showDetail(node.dataset.key));
        node.addEventListener('focus', () => showDetail(node.dataset.key));
        node.addEventListener('click', () => showDetail(node.dataset.key));
        node.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            showDetail(node.dataset.key);
          }
        });
      }
      document.getElementById('overview-button').addEventListener('click', () => showDetail('overview'));

      function baseWidth() { return Math.max(viewport.clientWidth, 1152); }
      function updateZoom() {
        const actualWidth = baseWidth() * zoomFactor;
        surface.style.width = `${actualWidth}px`;
        zoomValue.textContent = `${Math.round(actualWidth / 1600 * 100)}%`;
      }
      function setZoom(next) {
        zoomFactor = Math.max(.35, Math.min(3.5, next));
        updateZoom();
      }
      document.getElementById('zoom-in').addEventListener('click', () => setZoom(zoomFactor * 1.2));
      document.getElementById('zoom-out').addEventListener('click', () => setZoom(zoomFactor / 1.2));
      document.getElementById('zoom-fit').addEventListener('click', () => setZoom(viewport.clientWidth / baseWidth()));
      viewport.addEventListener('wheel', event => {
        if (!event.ctrlKey) return;
        event.preventDefault();
        setZoom(zoomFactor * (event.deltaY < 0 ? 1.12 : 1 / 1.12));
      }, { passive: false });
      new ResizeObserver(updateZoom).observe(viewport);

      function mobile() { return window.matchMedia('(max-width: 1100px)').matches; }
      function setDivider(clientX, clientY) {
        const bounds = workspace.getBoundingClientRect();
        if (mobile()) {
          const height = Math.max(260, Math.min(bounds.height - 190, clientY - bounds.top));
          workspace.style.gridTemplateRows = `${height}px 12px minmax(0, 1fr)`;
          mobileHeight = height;
          splitter.setAttribute('aria-orientation', 'horizontal');
          splitter.setAttribute('aria-valuenow', String(Math.round(height)));
        } else {
          const width = Math.max(320, Math.min(1100, bounds.width - 360, bounds.right - clientX));
          workspace.style.setProperty('--detail-width', `${width}px`);
          splitter.setAttribute('aria-orientation', 'vertical');
          splitter.setAttribute('aria-valuenow', String(Math.round(width)));
        }
      }
      splitter.addEventListener('pointerdown', event => {
        splitter.setPointerCapture(event.pointerId);
        splitter.classList.add('dragging');
        setDivider(event.clientX, event.clientY);
      });
      splitter.addEventListener('pointermove', event => {
        if (splitter.hasPointerCapture(event.pointerId)) setDivider(event.clientX, event.clientY);
      });
      function endDrag(event) {
        if (splitter.hasPointerCapture(event.pointerId)) splitter.releasePointerCapture(event.pointerId);
        splitter.classList.remove('dragging');
      }
      splitter.addEventListener('pointerup', endDrag);
      splitter.addEventListener('pointercancel', endDrag);
      splitter.addEventListener('keydown', event => {
        const delta = event.shiftKey ? 60 : 20;
        const size = Number(splitter.getAttribute('aria-valuenow'));
        if (mobile() && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
          event.preventDefault();
          const bounds = workspace.getBoundingClientRect();
          setDivider(bounds.left, bounds.top + size + (event.key === 'ArrowDown' ? delta : -delta));
        } else if (!mobile() && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
          event.preventDefault();
          const bounds = workspace.getBoundingClientRect();
          setDivider(bounds.right - size + (event.key === 'ArrowLeft' ? delta : -delta), bounds.top);
        }
      });
      window.addEventListener('resize', () => {
        if (mobile()) {
          splitter.setAttribute('aria-orientation', 'horizontal');
          if (mobileHeight !== null) workspace.style.gridTemplateRows = `${mobileHeight}px 12px minmax(0, 1fr)`;
        } else {
          splitter.setAttribute('aria-orientation', 'vertical');
          workspace.style.removeProperty('grid-template-rows');
        }
      });
      updateZoom();
    })();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
