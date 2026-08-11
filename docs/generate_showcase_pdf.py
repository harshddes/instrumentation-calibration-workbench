"""
Build a clickable portfolio PDF for the Instrumentation Calibration Workbench.

Pseudocode
----------
1. Resolve repo root / asset paths and GitHub base URLs.
2. Create a letter-size PDF canvas with footer page numbers.
3. Emit cover, overview, architecture, GUI/chamber pages, plasma results,
   calibration, repository map, and quick-start sections.
4. Embed PNG screenshots and SVG review plots (svglib); attach URI link
   annotations for every documentation / example path.
5. Write docs/Instrumentation_Calibration_Workbench_Showcase.pdf.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from svglib.svglib import svg2rlg

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "docs" / "assets" / "readme"
OUT_PDF = REPO_ROOT / "docs" / "Instrumentation_Calibration_Workbench_Showcase.pdf"

GH = "https://github.com/harshddes/instrumentation-calibration-workbench"
GH_BLOB = f"{GH}/blob/main"
GH_TREE = f"{GH}/tree/main"
GH_WIKI = f"{GH}/wiki"

PAGE_W, PAGE_H = letter
MARGIN = 0.7 * inch
CONTENT_W = PAGE_W - 2 * MARGIN

NAVY = HexColor("#1f2937")
ACCENT = HexColor("#1f4e79")
MUTED = HexColor("#4b5563")
RULE = HexColor("#d1d5db")
SOFT = HexColor("#f3f4f6")
LINK = HexColor("#0b5fff")


class ShowcasePDF:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.c = canvas.Canvas(str(path), pagesize=letter)
        self.c.setTitle("Instrumentation Calibration Workbench - Portfolio Showcase")
        self.c.setAuthor("Harsh Desai")
        self.c.setSubject("Lab instrumentation, telemetry sync, calibration, LunarRego plasma diagnostics")
        self.page = 0
        self.y = PAGE_H - MARGIN

    # ----- page chrome -----
    def new_page(self, title: str | None = None) -> None:
        if self.page:
            self._footer()
            self.c.showPage()
        self.page += 1
        self.y = PAGE_H - MARGIN
        if title:
            self.h1(title)

    def _footer(self) -> None:
        self.c.setStrokeColor(RULE)
        self.c.setLineWidth(0.6)
        self.c.line(MARGIN, 0.45 * inch, PAGE_W - MARGIN, 0.45 * inch)
        self.c.setFont("Helvetica", 8)
        self.c.setFillColor(MUTED)
        self.c.drawString(MARGIN, 0.28 * inch, "Instrumentation Calibration Workbench")
        self.c.drawRightString(PAGE_W - MARGIN, 0.28 * inch, f"{self.page}")
        self._link_rect(
            MARGIN,
            0.22 * inch,
            2.6 * inch,
            0.18 * inch,
            GH,
        )

    def _need(self, h: float) -> None:
        if self.y - h < 0.75 * inch:
            self.new_page()

    # ----- typography -----
    def h1(self, text: str) -> None:
        self.c.setFillColor(NAVY)
        self.c.setFont("Helvetica-Bold", 18)
        self.c.drawString(MARGIN, self.y, text)
        self.y -= 10
        self.c.setStrokeColor(ACCENT)
        self.c.setLineWidth(1.8)
        self.c.line(MARGIN, self.y, MARGIN + 1.8 * inch, self.y)
        self.y -= 18

    def h2(self, text: str) -> None:
        self._need(28)
        self.c.setFillColor(ACCENT)
        self.c.setFont("Helvetica-Bold", 13)
        self.c.drawString(MARGIN, self.y, text)
        self.y -= 16

    def body(self, text: str, size: float = 10, leading: float = 13, color=MUTED) -> None:
        self.c.setFillColor(color)
        self.c.setFont("Helvetica", size)
        for line in self._wrap(text, CONTENT_W, "Helvetica", size):
            self._need(leading + 2)
            self.c.drawString(MARGIN, self.y, line)
            self.y -= leading
        self.y -= 4

    def bullet(self, text: str) -> None:
        self.c.setFillColor(MUTED)
        self.c.setFont("Helvetica", 10)
        bullet_w = 12
        lines = self._wrap(text, CONTENT_W - bullet_w, "Helvetica", 10)
        for i, line in enumerate(lines):
            self._need(14)
            if i == 0:
                self.c.drawString(MARGIN, self.y, "•")
            self.c.drawString(MARGIN + bullet_w, self.y, line)
            self.y -= 13
        self.y -= 2

    def link_line(self, label: str, url: str) -> None:
        self._need(14)
        self.c.setFont("Helvetica", 10)
        self.c.setFillColor(LINK)
        self.c.drawString(MARGIN, self.y, label)
        w = self.c.stringWidth(label, "Helvetica", 10)
        self._link_rect(MARGIN, self.y - 2, w, 12, url)
        self.y -= 14

    def spacer(self, h: float = 8) -> None:
        self.y -= h

    # ----- helpers -----
    def _wrap(self, text: str, width: float, font: str, size: float) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        cur = words[0]
        for w in words[1:]:
            trial = f"{cur} {w}"
            if self.c.stringWidth(trial, font, size) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    def _link_rect(self, x: float, y: float, w: float, h: float, url: str) -> None:
        self.c.linkURL(url, (x, y, x + w, y + h), relative=0)

    def draw_png(self, path: Path, max_w: float, max_h: float, caption: str = "") -> None:
        if not path.exists():
            self.body(f"[missing image: {path.name}]")
            return
        img = ImageReader(str(path))
        iw, ih = img.getSize()
        scale = min(max_w / iw, max_h / ih)
        w, h = iw * scale, ih * scale
        self._need(h + (18 if caption else 6))
        x = MARGIN + (CONTENT_W - w) / 2
        self.c.drawImage(img, x, self.y - h, width=w, height=h, preserveAspectRatio=True, mask="auto")
        self.y -= h + 4
        if caption:
            self.c.setFont("Helvetica-Oblique", 8)
            self.c.setFillColor(MUTED)
            for line in self._wrap(caption, CONTENT_W, "Helvetica-Oblique", 8):
                self._need(11)
                self.c.drawString(MARGIN, self.y, line)
                self.y -= 10
            self.y -= 4

    def draw_svg(self, path: Path, max_w: float, max_h: float, caption: str = "") -> None:
        if not path.exists():
            self.body(f"[missing plot: {path.name}]")
            return
        drawing = svg2rlg(str(path))
        if drawing is None:
            self.body(f"[could not parse SVG: {path.name}]")
            return
        scale = min(max_w / drawing.width, max_h / drawing.height)
        drawing.width *= scale
        drawing.height *= scale
        drawing.scale(scale, scale)
        h = drawing.height
        self._need(h + (18 if caption else 6))
        x = MARGIN + (CONTENT_W - drawing.width) / 2
        from reportlab.graphics import renderPDF

        renderPDF.draw(drawing, self.c, x, self.y - h)
        self.y -= h + 4
        if caption:
            self.c.setFont("Helvetica-Oblique", 8)
            self.c.setFillColor(MUTED)
            for line in self._wrap(caption, CONTENT_W, "Helvetica-Oblique", 8):
                self._need(11)
                self.c.drawString(MARGIN, self.y, line)
                self.y -= 10
            self.y -= 4

    def table(self, headers: list[str], rows: list[list[str]], col_fracs: list[float]) -> None:
        col_ws = [CONTENT_W * f for f in col_fracs]
        row_h = 16
        header_h = 18
        self._need(header_h + row_h * len(rows) + 8)
        x0 = MARGIN
        # header
        self.c.setFillColor(SOFT)
        self.c.rect(x0, self.y - header_h + 4, CONTENT_W, header_h, fill=1, stroke=0)
        self.c.setFillColor(NAVY)
        self.c.setFont("Helvetica-Bold", 8)
        x = x0 + 4
        for h, w in zip(headers, col_ws):
            self.c.drawString(x, self.y - 8, h)
            x += w
        self.y -= header_h
        self.c.setFont("Helvetica", 8)
        self.c.setFillColor(MUTED)
        for row in rows:
            self._need(row_h + 2)
            x = x0 + 4
            # wrap-ish: draw first line only; keep cells short
            for cell, w in zip(row, col_ws):
                self.c.drawString(x, self.y - 4, cell[: int(w / 4.2)])
                x += w
            self.y -= row_h
        self.y -= 6

    # ----- sections -----
    def cover(self) -> None:
        self.page = 1
        self.c.setFillColor(ACCENT)
        self.c.rect(0, PAGE_H - 2.4 * inch, PAGE_W, 2.4 * inch, fill=1, stroke=0)
        self.c.setFillColor(white)
        self.c.setFont("Helvetica-Bold", 22)
        self.c.drawString(MARGIN, PAGE_H - 1.0 * inch, "Instrumentation Calibration")
        self.c.drawString(MARGIN, PAGE_H - 1.35 * inch, "Workbench")
        self.c.setFont("Helvetica", 11)
        self.c.drawString(
            MARGIN,
            PAGE_H - 1.75 * inch,
            "Python lab instrumentation · telemetry sync · calibration lineage · LunarRego plasma diagnostics",
        )

        y = PAGE_H - 2.9 * inch
        self.c.setFillColor(NAVY)
        self.c.setFont("Helvetica-Bold", 12)
        self.c.drawString(MARGIN, y, "Portfolio showcase PDF")
        y -= 20
        self.c.setFont("Helvetica", 10)
        self.c.setFillColor(MUTED)
        for line in [
            "Author: Harsh Desai",
            "Public repository with clickable documentation links",
            "Includes DAQ/TDK logging architecture, calibration artifacts, and LP/EP/RPA analysis",
        ]:
            self.c.drawString(MARGIN, y, line)
            y -= 14

        y -= 10
        self.c.setFillColor(LINK)
        self.c.setFont("Helvetica-Bold", 11)
        label = GH
        self.c.drawString(MARGIN, y, label)
        self._link_rect(MARGIN, y - 2, self.c.stringWidth(label, "Helvetica-Bold", 11), 14, GH)
        y -= 28

        self.c.setFillColor(SOFT)
        self.c.roundRect(MARGIN, y - 2.1 * inch, CONTENT_W, 2.2 * inch, 6, fill=1, stroke=0)
        self.c.setFillColor(NAVY)
        self.c.setFont("Helvetica-Bold", 11)
        self.c.drawString(MARGIN + 12, y - 0.25 * inch, "Contents")
        self.c.setFont("Helvetica", 10)
        self.c.setFillColor(MUTED)
        contents = [
            "1. Project overview and capabilities",
            "2. Architecture: DAQ ↔ TDK snapshot bridge",
            "3. Operator GUIs (DAQ, TDK, LunarRego)",
            "4. Chamber experiment and bias electrode",
            "5. Plasma diagnostics: LP / EP / RPA + dI/dV results",
            "6. Calibration lineage demo",
            "7. Repository map and quick start (with links)",
        ]
        cy = y - 0.5 * inch
        for item in contents:
            self.c.drawString(MARGIN + 12, cy, item)
            cy -= 14

        self.c.setFillColor(MUTED)
        self.c.setFont("Helvetica-Oblique", 8)
        self.c.drawString(MARGIN, 0.7 * inch, "Click blue URLs throughout this PDF to open GitHub documentation.")
        self._footer()

    def overview(self) -> None:
        self.new_page("1. Project overview")
        self.body(
            "This repository is a curated public showcase of research-engineering software for "
            "laboratory instrumentation. It shows how Python can coordinate independent instruments, "
            "merge their telemetry with explicit freshness metadata, preserve calibration lineage in "
            "versioned artifacts, and operate plasma diagnostics for a lunar-regolith electric-field experiment."
        )
        self.h2("Core idea — DAQ + TDK")
        self.body(
            "A DAQ logger records measurement channels. A power-supply logger publishes the latest "
            "telemetry snapshot as JSON. Each DAQ row records whether that telemetry was fresh, stale, "
            "or missing. A calibration package then maps raw DAQ current readings onto reference-scale "
            "values using a versioned JSON artifact."
        )
        self.h2("Extended path — LunarRego")
        self.body(
            "Beyond DAQ/TDK, the project includes a LunarRego operator GUI for Langmuir Probe (LP), "
            "Emissive Probe (EP), and Retarding Potential Analyzer (RPA). Inside the vacuum chamber, "
            "a bias electrode is driven positive or negative to generate the desired electric field "
            "while lunar-regolith simulant lofting / charging is observed. LP and EP provide plasma-"
            "potential markers; early RPA collector sweeps are retained as rudimentary pipeline "
            "results only (not claimed as plasma potential)."
        )
        self.h2("Vacuum system status (current)")
        self.body(
            "The chamber vacuum train is a roughing pump combined with a CTI cryopump. There is "
            "currently a roughing-pump problem, so the team is troubleshooting the vacuum system "
            "while preparing for additional diagnostic runs. In parallel, work is underway to "
            "automate the automatic valve controller (AVC) that sequences the roughing / cryopump path."
        )
        self.h2("What this shows")
        for b in [
            "Multi-instrument logging with an explicit JSON snapshot bridge",
            "Keithley DAQ scan logic with CSV output and TDK telemetry columns",
            "TDK Lambda telemetry/control with GUI operation and safety-oriented state handling",
            "LunarRego LP / EP / RPA GUI with hardware-map role assignment and Keithley backends",
            "Public I–V review for approved LP / EP / RPA CSVs, including dI/dV markers",
            "Calibration generator with source hashes, cleaning rules, coefficients, and review plots",
            "Reusable calibration library for scripts, notebooks, and downstream analysis",
        ]:
            self.bullet(b)
        self.spacer(6)
        self.h2("Documentation links")
        links = [
            ("Repository README", f"{GH_BLOB}/README.md"),
            ("Documentation index", f"{GH_BLOB}/docs/README.md"),
            ("Architecture / data flow", f"{GH_BLOB}/docs/architecture/dataflow.md"),
            ("Calibration methodology", f"{GH_BLOB}/docs/calibration/methodology.md"),
            ("Plasma diagnostics methodology", f"{GH_BLOB}/docs/plasma_diagnostics/methodology.md"),
            ("Workflow runbooks", f"{GH_BLOB}/docs/workflows/runbooks.md"),
            ("Reproducibility boundary", f"{GH_BLOB}/docs/reproducibility.md"),
            ("Project Wiki", GH_WIKI),
            ("Wiki source: Plasma Diagnostics", f"{GH_BLOB}/wiki/PlasmaDiagnostics.md"),
        ]
        for label, url in links:
            self.link_line(f"{label}  →  {url}", url)

    def architecture(self) -> None:
        self.new_page("2. Architecture: DAQ ↔ TDK bridge")
        self.body(
            "Two instruments produce related data without a shared process clock. The implemented "
            "solution uses a small JSON snapshot as the bridge between TDK telemetry and DAQ rows. "
            "Timing uncertainty stays visible as freshness status instead of being hidden."
        )
        self.h2("Data flow")
        for b in [
            "TDK logger → TDK CSV and published snapshot JSON",
            "DAQ logger → channel readings",
            "Snapshot → freshness status (fresh / stale / missing / invalid)",
            "Merged DAQ CSV carries readings + TDK columns + freshness",
            "Merged CSV → calibration generator → versioned artifact → calibration library",
            "Merged CSV → dashboard / analysis",
        ]:
            self.bullet(b)
        self.h2("Snapshot contract (example fields)")
        self.body(
            "ps_1_voltage, ps_1_current, ps_1_output_state, ps_2_voltage, ps_2_current, "
            "ps_2_output_state, plus timestamp / sequence / published_at metadata.",
            size=9,
        )
        self.link_line(
            "Open snapshot helper source  →  instrumentation/snapshot.py",
            f"{GH_BLOB}/instrumentation/snapshot.py",
        )
        self.link_line(
            "Open architecture doc  →  docs/architecture/dataflow.md",
            f"{GH_BLOB}/docs/architecture/dataflow.md",
        )
        self.spacer(4)
        self.h2("LunarRego diagnostics path")
        for b in [
            "Hardware Map JSON assigns instrument / panel / GPIB / board per LP, EP, RPA plate role",
            "GUI acquires LP CSV and RPA combined collector CSV; EP sheet is reviewed from approved export",
            "analyze_iv_curves computes I–V and dI/dV; LP/EP markers are plasma-potential related",
            "RPA public review is rudimentary only — not labeled plasma potential",
            "Vacuum: roughing + CTI cryopump; roughing-pump troubleshooting and AVC automation in progress",
        ]:
            self.bullet(b)

    def guis(self) -> None:
        self.new_page("3. Operator GUIs")
        self.h2("Keithley DAQ 2700")
        self.body(
            "Tk operator surface for multi-channel DAQ logging with folder/CSV controls and "
            "merged TDK telemetry columns."
        )
        self.draw_png(
            ASSETS / "daq.png",
            CONTENT_W,
            2.6 * inch,
            "DAQ GUI — source: docs/assets/readme/daq.png",
        )
        self.link_line(
            "Run: python -m instrumentation.daq.GUI_DAQ2700",
            f"{GH_BLOB}/instrumentation/daq/GUI_DAQ2700.py",
        )

        self.h2("TDK Lambda")
        self.body(
            "Telemetry/control GUI for supply logging, snapshot publish, and operator-facing "
            "manual/auto controls with safety-oriented state handling."
        )
        self.draw_png(
            ASSETS / "tdk.png",
            CONTENT_W,
            2.6 * inch,
            "TDK GUI — source: docs/assets/readme/tdk.png",
        )
        self.link_line(
            "Run: python -m instrumentation.tdk.GUI_TDK",
            f"{GH_BLOB}/instrumentation/tdk/GUI_TDK.py",
        )

        self.new_page("3b. LunarRego diagnostics GUI")
        self.body(
            "LP / EP / RPA operator GUI with Hardware Map roles. On the RPA tab, P2 (Keithley 2410) "
            "owns the retarding-voltage sweep and NPLC master timing; P4 collector (Keithley 2400-LV) "
            "holds collector bias while a Keithley 6485 picoammeter records collector current in lockstep "
            "(set V → read pico). Combined CSV columns: Timestamp, Sweep_V, Picoammeter_I."
        )
        self.draw_png(
            ASSETS / "lunar-rego-gui.png",
            CONTENT_W,
            4.4 * inch,
            "LunarRego GUI (RPA tab) — source: docs/assets/readme/lunar-rego-gui.png",
        )
        self.link_line(
            "Run: python -m instrumentation.lunar_rego.GUI_LunarRego",
            f"{GH_BLOB}/instrumentation/lunar_rego/GUI_LunarRego.py",
        )

    def chamber(self) -> None:
        self.new_page("4. Chamber experiment and bias electrode")
        self.body(
            "LunarRego is a vacuum-chamber campaign that couples plasma diagnostics to lunar-regolith-"
            "simulant lofting under a controlled electric field."
        )
        self.h2("Bias electrode = field actuator")
        self.body(
            "The square metallic plate mounted on a vertical rod is the bias electrode. It is driven "
            "to a chosen positive or negative potential to generate the desired electric field. Dust / "
            "regolith simulant response (charging, lofting, transport) is then interpreted against the "
            "local plasma state measured by LP and EP — not against an assumed plasma potential. "
            "Fuller RPA campaigns are planned after vacuum recovery."
        )
        self.h2("Vacuum train and current status")
        self.body(
            "The chamber uses a roughing pump + CTI cryopump combination. A roughing-pump issue is "
            "under active troubleshooting, which pauses additional diagnostic runs for now. In parallel, "
            "the automatic valve controller (AVC) is being automated to sequence the roughing / cryopump "
            "path more reliably for future campaigns."
        )
        self.draw_png(
            ASSETS / "lunar-rego-chamber-probes.png",
            CONTENT_W,
            4.6 * inch,
            "Vacuum-chamber probe assembly and bias electrode — docs/assets/readme/lunar-rego-chamber-probes.png",
        )
        self.bullet("Horizontal probe / central stack: plasma-diagnostics path into the measurement volume")
        self.bullet("Left square plate: bias electrode for +/− electric-field control")
        self.bullet("Feedthrough wiring connects SMUs / picoammeter / supplies outside the chamber")

    def plasma(self) -> None:
        self.new_page("5. Plasma diagnostics — LP / EP / RPA")
        self.h2("Probe roles")
        self.table(
            ["Probe", "Measures", "Public marker"],
            [
                ["LP", "I vs probe bias", "V* = dI/dV peak; V_f at I≈0"],
                ["EP", "Floating V vs emission", "Vp ≈ high-emission asymptote"],
                ["RPA", "Collector I vs retarding V", "Rudimentary dI/dV only (not Vp)"],
            ],
            [0.15, 0.40, 0.45],
        )
        self.h2("RPA acquisition contract")
        for b in [
            "P2 = retarding / discriminator plate (software sweep, NPLC master)",
            "P4 = collector bias on 2400-LV; 6485 companion reads picoammeter current",
            "Lockstep: for each Sweep_V, set P2 then read 6485",
            "Other plates may hold fixed bias / own CSV; 6485 is not free-running on them",
        ]:
            self.bullet(b)

        self.h2("Approved public datasets only")
        files = [
            ("LP_07302026_140808.csv", f"{GH_BLOB}/examples/plasma_diagnostics/LP_07302026_140808.csv"),
            ("RPA_combined_07302026_170652.csv", f"{GH_BLOB}/examples/plasma_diagnostics/RPA_combined_07302026_170652.csv"),
            ("EP_PlasmaDiagnostics_exp.csv", f"{GH_BLOB}/examples/plasma_diagnostics/EP_PlasmaDiagnostics_exp.csv"),
        ]
        for name, url in files:
            self.link_line(name, url)

        self.h2("Regenerated review markers")
        self.table(
            ["Diagnostic", "Estimate", "Notes"],
            [
                ["LP", "V* ≈ 23.6 V; V_f ≈ 11.5 V", "Plasma-potential related (dI/dV)"],
                ["EP", "Vp ≈ 21.3 V", "High-emission floating asymptote"],
                ["RPA", "dI/dV feature ≈ 21.5 V", "Rudimentary only — NOT plasma potential"],
            ],
            [0.18, 0.40, 0.42],
        )
        self.body(
            "RPA is shown only as an early pipeline example while the vacuum system "
            "(roughing pump + CTI cryopump) is troubleshot and AVC automation continues. "
            "More RPA results are planned after vacuum recovery."
        )
        self.link_line(
            "Methodology  →  docs/plasma_diagnostics/methodology.md",
            f"{GH_BLOB}/docs/plasma_diagnostics/methodology.md",
        )
        self.link_line(
            "Regenerate plots  →  instrumentation/lunar_rego/analyze_iv_curves.py",
            f"{GH_BLOB}/instrumentation/lunar_rego/analyze_iv_curves.py",
        )

        self.new_page("5b. Review plots")
        self.draw_svg(
            ASSETS / "lp-iv-didv.svg",
            CONTENT_W,
            2.7 * inch,
            "Langmuir Probe I–V and dI/dV with V* and V_f markers",
        )
        self.draw_svg(
            ASSETS / "rpa-iv-didv.svg",
            CONTENT_W,
            2.7 * inch,
            "RPA collector I–V and dI/dV — rudimentary early result (not plasma potential)",
        )
        self.draw_svg(
            ASSETS / "ep-floating-potential.svg",
            CONTENT_W,
            2.5 * inch,
            "Emissive Probe floating potential vs emission → Vp asymptote",
        )

    def calibration(self) -> None:
        self.new_page("6. Calibration lineage demo")
        self.body(
            "The public calibration demo is generated from synthetic data so reviewers can inspect "
            "the full lineage without private experiment archives: source CSV, cleaned CSV, rejected "
            "rows, JSON metadata (hashes, coefficients, fit quality), and an SVG review plot."
        )
        self.h2("Demo model")
        self.body("ps_1_current = 0.4 * DAQ_KEEPER_I + 0.01", size=11, color=NAVY)
        self.draw_svg(
            ASSETS / "calibration-plot.svg",
            CONTENT_W,
            3.0 * inch,
            "Synthetic keeper-current calibration review plot",
        )
        self.link_line(
            "Artifact JSON  →  calibration_process/artifacts/demo_keeper_current_linear.json",
            f"{GH_BLOB}/calibration_process/artifacts/demo_keeper_current_linear.json",
        )
        self.link_line(
            "Generator  →  calibration_process/generate_keeper_current_calibration.py",
            f"{GH_BLOB}/calibration_process/generate_keeper_current_calibration.py",
        )
        self.link_line(
            "Runtime library  →  calibration_process/library.py",
            f"{GH_BLOB}/calibration_process/library.py",
        )
        self.link_line(
            "Methodology  →  docs/calibration/methodology.md",
            f"{GH_BLOB}/docs/calibration/methodology.md",
        )

    def repo_map(self) -> None:
        self.new_page("7. Repository map and quick start")
        self.h2("Key paths")
        rows = [
            ("instrumentation/daq/", "Keithley DAQ class + GUI"),
            ("instrumentation/tdk/", "TDK telemetry/control + GUI"),
            ("instrumentation/lunar_rego/", "LP/EP/RPA GUI, backends, IV review"),
            ("instrumentation/snapshot.py", "TDK↔DAQ snapshot contract"),
            ("calibration_process/", "Versioned calibration generator/library"),
            ("examples/plasma_diagnostics/", "Approved LP/EP/RPA CSVs only"),
            ("docs/", "Architecture, calibration, plasma, runbooks"),
            ("wiki/", "GitHub Wiki source pages"),
            ("SCD_3D_AI_Lab/", "Streamlit CSV exploration dashboard"),
            ("code_xray/", "Static/dynamic DAQ tracing experiment"),
        ]
        for rel, purpose in rows:
            self._need(26)
            self.c.setFont("Helvetica-Bold", 9)
            self.c.setFillColor(LINK)
            self.c.drawString(MARGIN, self.y, rel)
            w = self.c.stringWidth(rel, "Helvetica-Bold", 9)
            url = f"{GH_TREE}/{rel.rstrip('/')}" if rel.endswith("/") else f"{GH_BLOB}/{rel}"
            self._link_rect(MARGIN, self.y - 2, w, 11, url)
            self.y -= 11
            self.c.setFont("Helvetica", 9)
            self.c.setFillColor(MUTED)
            self.c.drawString(MARGIN + 8, self.y, purpose)
            self.y -= 14

        self.h2("Quick start")
        for line in [
            "python -m venv .venv",
            ".\\.venv\\Scripts\\Activate.ps1",
            "python -m pip install -r requirements.txt",
            "python -m calibration_process.generate_keeper_current_calibration",
            "python -m instrumentation.daq.GUI_DAQ2700",
            "python -m instrumentation.tdk.GUI_TDK",
            "python -m instrumentation.lunar_rego.GUI_LunarRego",
            "python -m instrumentation.lunar_rego.analyze_iv_curves",
        ]:
            self.bullet(line)
        self.link_line("Runbooks  →  docs/workflows/runbooks.md", f"{GH_BLOB}/docs/workflows/runbooks.md")
        self.link_line("Requirements  →  requirements.txt", f"{GH_BLOB}/requirements.txt")

        self.h2("Public-release boundary")
        self.body(
            "This repository is curated from a larger working lab tree. Large experiment archives, "
            "scratch folders, and private editor metadata are excluded. For LunarRego, only the three "
            "approved LP / EP / RPA CSVs are public."
        )
        self.link_line(
            "Reproducibility notes  →  docs/reproducibility.md",
            f"{GH_BLOB}/docs/reproducibility.md",
        )
        self.spacer(10)
        self.c.setFillColor(ACCENT)
        self.c.setFont("Helvetica-Bold", 11)
        self.c.drawString(MARGIN, self.y, "Repository home")
        self.y -= 16
        self.link_line(GH, GH)

    def build(self) -> Path:
        self.cover()
        self.overview()
        self.architecture()
        self.guis()
        self.chamber()
        self.plasma()
        self.calibration()
        self.repo_map()
        self._footer()
        self.c.save()
        return self.path


def main() -> None:
    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    path = ShowcasePDF(OUT_PDF).build()
    print(path)


if __name__ == "__main__":
    main()
