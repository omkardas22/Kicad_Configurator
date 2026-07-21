"""
KiCad Constraint Configurator
Author: KiCad Constraint Configurator Team
Version: 1.0.0

Main application file. Provides a CustomTkinter GUI for:
  - Entering a Gemini API key (stored in %APPDATA%/KiCadConfigurator/config.json)
  - Specifying a vendor URL (PCBWay, JLCPCB, etc.)
  - Scraping vendor capability pages with requests + BeautifulSoup
  - Extracting PCB constraints via Gemini 2.5 Flash (structured output / Pydantic)
  - Injecting extracted constraints into .kicad_pro (JSON) and .kicad_pcb (S-expression)
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Optional heavy imports — degrade gracefully if missing (CI / unit tests)
# ---------------------------------------------------------------------------
try:
    import requests
    from bs4 import BeautifulSoup
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from google import genai
    from google.genai import types as genai_types
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

try:
    from pydantic import BaseModel, Field
    _PYDANTIC_OK = True
except ImportError:
    _PYDANTIC_OK = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME = "KiCad Constraint Configurator"
APP_VERSION = "1.0.0"
APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "KiCadConfigurator"
CONFIG_FILE = APPDATA_DIR / "config.json"
GEMINI_MODEL = "gemini-2.5-flash"

# Colour palette
CLR_BG = "#0f1117"
CLR_PANEL = "#1a1d27"
CLR_ACCENT = "#5865f2"
CLR_ACCENT2 = "#7289da"
CLR_SUCCESS = "#43b581"
CLR_WARNING = "#faa61a"
CLR_ERROR = "#ed4245"
CLR_TEXT = "#dcddde"
CLR_SUBTEXT = "#72767d"
CLR_BORDER = "#2f3136"

# Net-class defaults (applied on top of extracted minimums)
POWER_MULTIPLIER = 2.0
POWER_COLOR = "rgba(228,26,28,0.8)"
CANBUS_COLOR = "rgba(55,126,184,0.8)"

NETCLASS_PATTERNS = [
    {"netclass": "Power", "pattern": "+*"},
    {"netclass": "Power", "pattern": "GND*"},
    {"netclass": "Power", "pattern": "VCC*"},
    {"netclass": "CAN_Bus", "pattern": "CAN_*"},
]


# ---------------------------------------------------------------------------
# Path resolver (PyInstaller compatible)
# ---------------------------------------------------------------------------
def get_resource_path(relative_path: str) -> Path:
    """Return absolute path to bundled resource, compatible with PyInstaller."""
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent.parent
    return base / relative_path


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------
def load_config() -> dict:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Pydantic schema for Gemini structured output
# ---------------------------------------------------------------------------
if _PYDANTIC_OK:
    class PCBConstraints(BaseModel):
        """Structured PCB manufacturing constraint data extracted from a vendor page."""

        min_trace_width_mm: float = Field(
            default=0.1,
            description="Minimum copper trace width in mm",
        )
        min_clearance_mm: float = Field(
            default=0.1,
            description="Minimum copper-to-copper clearance in mm",
        )
        min_via_diameter_mm: float = Field(
            default=0.6,
            description="Minimum via outer diameter in mm",
        )
        min_via_drill_mm: float = Field(
            default=0.3,
            description="Minimum via drill hole diameter in mm",
        )
        min_hole_diameter_mm: float = Field(
            default=0.3,
            description="Minimum mechanical drill hole diameter in mm",
        )
        min_annular_ring_mm: float = Field(
            default=0.1,
            description="Minimum pad annular ring width in mm",
        )
        vendor_name: str = Field(
            default="Unknown Vendor",
            description="Name of the PCB manufacturer",
        )
        source_url: str = Field(
            default="",
            description="URL where constraints were scraped from",
        )
        notes: str = Field(
            default="",
            description="Any extra relevant notes from the vendor page",
        )
else:
    class PCBConstraints:  # type: ignore[no-redef]
        """Fallback when Pydantic is unavailable."""
        def __init__(self, **kwargs: float | str):
            self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# Web scraper
# ---------------------------------------------------------------------------
def scrape_vendor_page(url: str, timeout: int = 15) -> str:
    """Fetch and extract plain text from a vendor capability/spec page."""
    if not _REQUESTS_OK:
        raise RuntimeError("requests/beautifulsoup4 not installed.")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove nav / script / style noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    # Grab main content if possible, otherwise full body
    main = soup.find("main") or soup.find("article") or soup.body
    text = main.get_text(separator="\n") if main else soup.get_text(separator="\n")
    # Collapse excessive whitespace
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:12000]  # cap at 12k chars for Gemini context


# ---------------------------------------------------------------------------
# Gemini extractor
# ---------------------------------------------------------------------------
def extract_constraints_gemini(api_key: str, raw_text: str, source_url: str) -> PCBConstraints:
    """Call Gemini 2.5 Flash with structured output to extract PCB constraints."""
    if not _GENAI_OK:
        raise RuntimeError("google-genai not installed.")
    if not _PYDANTIC_OK:
        raise RuntimeError("pydantic not installed.")

    client = genai.Client(api_key=api_key)

    prompt = textwrap.dedent(f"""\
        You are an expert PCB manufacturing engineer.
        Below is raw text scraped from a PCB vendor capability page at: {source_url}

        Extract the minimum PCB design constraints as numeric values in millimeters.
        If a value is given in mils or inches, convert to mm (1 mil = 0.0254 mm, 1 inch = 25.4 mm).
        Return ONLY the structured JSON matching the schema.
        Use conservative (larger) defaults when data is ambiguous or missing.

        --- BEGIN VENDOR TEXT ---
        {raw_text}
        --- END VENDOR TEXT ---
    """)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PCBConstraints,
            temperature=0.1,
        ),
    )
    # Parse the structured response
    data = json.loads(response.text)
    data["source_url"] = source_url
    return PCBConstraints(**data)


# ---------------------------------------------------------------------------
# KiCad injection engine
# ---------------------------------------------------------------------------

def _build_net_class(name: str, constraints: PCBConstraints, color: str,
                     multiplier: float = 1.0, diff_pair: bool = False) -> dict:
    track = round(constraints.min_trace_width_mm * multiplier, 4)
    clr = round(constraints.min_clearance_mm * multiplier, 4)
    via_d = round(constraints.min_via_diameter_mm, 4)
    via_dr = round(constraints.min_via_drill_mm, 4)

    nc: dict = {
        "bus_width": 12,
        "clearance": clr,
        "diff_pair_gap": round(constraints.min_clearance_mm, 4) if diff_pair else 0.25,
        "diff_pair_via_gap": round(constraints.min_clearance_mm, 4) if diff_pair else 0.25,
        "diff_pair_width": track if diff_pair else 0.2,
        "line_style": 0,
        "microvia_diameter": 0.3,
        "microvia_drill": 0.1,
        "name": name,
        "pcb_color": color,
        "schematic_color": color,
        "track_width": track,
        "via_diameter": via_d,
        "via_drill": via_dr,
        "wire_width": 6,
    }
    return nc


def inject_kicad_pro(pro_path: Path, constraints: PCBConstraints) -> None:
    """Patch a .kicad_pro file (JSON) with extracted constraints and net classes."""
    with open(pro_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Update design rule minimums
    rules = data.setdefault("board", {}).setdefault(
        "design_settings", {}
    ).setdefault("rules", {})
    rules["min_clearance"] = constraints.min_clearance_mm
    rules["min_track_width"] = constraints.min_trace_width_mm
    rules["min_via_diameter"] = constraints.min_via_diameter_mm
    rules["min_via_annular_width"] = constraints.min_annular_ring_mm
    rules["min_through_hole_diameter"] = constraints.min_hole_diameter_mm
    rules["min_hole_clearance"] = constraints.min_clearance_mm
    rules["min_hole_to_hole"] = constraints.min_hole_diameter_mm

    # 2. Build net classes
    default_nc = _build_net_class(
        "Default", constraints, "rgba(0,0,0,0)", multiplier=1.0
    )
    power_nc = _build_net_class(
        "Power", constraints, POWER_COLOR, multiplier=POWER_MULTIPLIER
    )
    canbus_nc = _build_net_class(
        "CAN_Bus", constraints, CANBUS_COLOR, multiplier=1.0, diff_pair=True
    )

    net_settings = data.setdefault("net_settings", {})
    net_settings["classes"] = [default_nc, power_nc, canbus_nc]
    net_settings["netclass_patterns"] = copy.deepcopy(NETCLASS_PATTERNS)

    with open(pro_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def inject_kicad_pcb(pcb_path: Path, constraints: PCBConstraints) -> None:
    """Patch a .kicad_pcb file (S-expression) with extracted constraints via regex."""
    with open(pcb_path, "r", encoding="utf-8") as f:
        content = f.read()

    replacements = {
        r"\(clearance\s+[\d.]+\)": f"(clearance {constraints.min_clearance_mm})",
        r"\(track_width\s+[\d.]+\)": f"(track_width {constraints.min_trace_width_mm})",
        r"\(via_size\s+[\d.]+\)": f"(via_size {constraints.min_via_diameter_mm})",
        r"\(via_drill\s+[\d.]+\)": f"(via_drill {constraints.min_via_drill_mm})",
        r"\(via_min_size\s+[\d.]+\)": f"(via_min_size {constraints.min_via_diameter_mm})",
        r"\(via_min_drill\s+[\d.]+\)": f"(via_min_drill {constraints.min_via_drill_mm})",
        r"\(hole_to_hole_min\s+[\d.]+\)": f"(hole_to_hole_min {constraints.min_hole_diameter_mm})",
    }

    for pattern, replacement in replacements.items():
        content = re.sub(pattern, replacement, content)

    with open(pcb_path, "w", encoding="utf-8") as f:
        f.write(content)


def run_injection(
    constraints: PCBConstraints,
    output_dir: Path,
    template_dir: Path,
    project_name: str,
    log_callback,
) -> Path:
    """
    Copy templates to output_dir/<project_name>/ and inject constraints.
    Returns the path to the created project directory.
    """
    dest = output_dir / project_name
    dest.mkdir(parents=True, exist_ok=True)

    log_callback(f"📁 Creating project folder: {dest}")

    # Copy and rename templates
    files = {
        "template.kicad_pro": f"{project_name}.kicad_pro",
        "template.kicad_pcb": f"{project_name}.kicad_pcb",
        "template.kicad_sch": f"{project_name}.kicad_sch",
    }

    for src_name, dst_name in files.items():
        src = template_dir / src_name
        dst = dest / dst_name
        shutil.copy2(src, dst)
        log_callback(f"  ✅ Copied {dst_name}")

    # Inject into .kicad_pro
    pro_path = dest / f"{project_name}.kicad_pro"
    log_callback("⚙️  Injecting constraints into .kicad_pro …")
    inject_kicad_pro(pro_path, constraints)
    log_callback("  ✅ .kicad_pro updated (design rules + net classes + patterns)")

    # Inject into .kicad_pcb
    pcb_path = dest / f"{project_name}.kicad_pcb"
    log_callback("⚙️  Injecting constraints into .kicad_pcb …")
    inject_kicad_pcb(pcb_path, constraints)
    log_callback("  ✅ .kicad_pcb updated (setup block)")

    return dest


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------

class KiCadConfiguratorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("920x720")
        self.minsize(800, 620)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=CLR_BG)

        self._config: dict = load_config()
        self._constraints: Optional[PCBConstraints] = None
        self._scraping = False

        self._build_ui()
        self._restore_config()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Title bar ──────────────────────────────────────────────────
        title_frame = ctk.CTkFrame(self, fg_color=CLR_PANEL, corner_radius=0, height=64)
        title_frame.pack(fill="x", side="top")
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text="⚡  KiCad Constraint Configurator",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=CLR_TEXT,
        ).pack(side="left", padx=24, pady=12)

        ctk.CTkLabel(
            title_frame,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=CLR_SUBTEXT,
        ).pack(side="left", pady=12)

        # ── Main content area ──────────────────────────────────────────
        content = ctk.CTkFrame(self, fg_color=CLR_BG)
        content.pack(fill="both", expand=True, padx=16, pady=12)

        # Left panel (inputs)
        left = ctk.CTkScrollableFrame(
            content, fg_color=CLR_PANEL, corner_radius=12, width=380
        )
        left.pack(side="left", fill="y", padx=(0, 8))

        # Right panel (log + results)
        right = ctk.CTkFrame(content, fg_color=CLR_PANEL, corner_radius=12)
        right.pack(side="right", fill="both", expand=True)

        self._build_left_panel(left)
        self._build_right_panel(right)

        # ── Status bar ─────────────────────────────────────────────────
        self._status_var = ctk.StringVar(value="Ready")
        status_bar = ctk.CTkFrame(self, fg_color=CLR_BORDER, height=28, corner_radius=0)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        ctk.CTkLabel(
            status_bar, textvariable=self._status_var,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT, anchor="w",
        ).pack(side="left", padx=12, pady=4)

    def _section_label(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=CLR_ACCENT2, anchor="w",
        ).pack(fill="x", padx=16, pady=(16, 4))

    def _build_left_panel(self, parent) -> None:
        ctk.CTkLabel(
            parent, text="Configuration",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=CLR_TEXT,
        ).pack(padx=16, pady=(16, 8), anchor="w")

        # ── API Key ────────────────────────────────────────────────────
        self._section_label(parent, "🔑  Gemini API Key")
        self._api_key_var = ctk.StringVar()
        api_row = ctk.CTkFrame(parent, fg_color="transparent")
        api_row.pack(fill="x", padx=16, pady=(0, 4))
        self._api_entry = ctk.CTkEntry(
            api_row, textvariable=self._api_key_var,
            placeholder_text="AIza…",
            show="•", width=220,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        )
        self._api_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            api_row, text="Save Key", width=80,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._save_api_key,
        ).pack(side="right")

        self._key_status_label = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUCCESS, anchor="w",
        )
        self._key_status_label.pack(fill="x", padx=16)

        # Toggle visibility
        ctk.CTkButton(
            parent, text="Show / Hide Key", width=130,
            fg_color="transparent", border_width=1, border_color=CLR_BORDER,
            hover_color=CLR_BORDER, text_color=CLR_SUBTEXT,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._toggle_key_visibility,
        ).pack(padx=16, pady=(2, 8), anchor="w")

        # ── Vendor URL ─────────────────────────────────────────────────
        self._section_label(parent, "🌐  Vendor Capability URL")
        self._url_var = ctk.StringVar()
        ctk.CTkEntry(
            parent, textvariable=self._url_var,
            placeholder_text="https://www.jlcpcb.com/capabilities/pcb",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        ).pack(fill="x", padx=16, pady=(0, 4))

        # Quick-fill buttons
        quick_frame = ctk.CTkFrame(parent, fg_color="transparent")
        quick_frame.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            quick_frame, text="Quick fill:",
            font=ctk.CTkFont(family="Segoe UI", size=11), text_color=CLR_SUBTEXT,
        ).pack(side="left", padx=(0, 6))
        for label, url in [
            ("JLCPCB", "https://jlcpcb.com/capabilities/pcb"),
            ("PCBWay", "https://www.pcbway.com/capabilities.html"),
        ]:
            ctk.CTkButton(
                quick_frame, text=label, width=68,
                fg_color=CLR_BORDER, hover_color=CLR_BORDER,
                border_width=1, border_color=CLR_ACCENT,
                text_color=CLR_ACCENT2,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                command=lambda u=url: self._url_var.set(u),
            ).pack(side="left", padx=2)

        # ── Output Directory ───────────────────────────────────────────
        self._section_label(parent, "📂  Output Directory")
        dir_row = ctk.CTkFrame(parent, fg_color="transparent")
        dir_row.pack(fill="x", padx=16, pady=(0, 8))
        self._output_dir_var = ctk.StringVar()
        ctk.CTkEntry(
            dir_row, textvariable=self._output_dir_var,
            placeholder_text="Select output folder …",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            dir_row, text="Browse", width=70,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._browse_output,
        ).pack(side="right")

        # ── Project Name ───────────────────────────────────────────────
        self._section_label(parent, "📝  Project Name")
        self._project_name_var = ctk.StringVar(value="MyPCBProject")
        ctk.CTkEntry(
            parent, textvariable=self._project_name_var,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        ).pack(fill="x", padx=16, pady=(0, 12))

        # ── Action Buttons ─────────────────────────────────────────────
        ctk.CTkFrame(parent, fg_color=CLR_BORDER, height=1).pack(
            fill="x", padx=16, pady=8
        )

        self._scrape_btn = ctk.CTkButton(
            parent, text="🔍  Scrape & Extract Constraints",
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=42, corner_radius=8,
            command=self._start_scrape,
        )
        self._scrape_btn.pack(fill="x", padx=16, pady=4)

        self._inject_btn = ctk.CTkButton(
            parent, text="💉  Inject into KiCad Files",
            fg_color="#2d7a45", hover_color="#3a9e58",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=42, corner_radius=8,
            command=self._inject_constraints,
            state="disabled",
        )
        self._inject_btn.pack(fill="x", padx=16, pady=(0, 4))

        self._progress = ctk.CTkProgressBar(
            parent, mode="indeterminate",
            fg_color=CLR_BG, progress_color=CLR_ACCENT,
        )
        self._progress.pack(fill="x", padx=16, pady=(4, 16))
        self._progress.set(0)

    def _build_right_panel(self, parent) -> None:
        # Tabs
        self._tabs = ctk.CTkTabview(
            parent,
            fg_color=CLR_PANEL,
            segmented_button_fg_color=CLR_BG,
            segmented_button_selected_color=CLR_ACCENT,
            segmented_button_selected_hover_color=CLR_ACCENT2,
            segmented_button_unselected_color=CLR_BG,
            segmented_button_unselected_hover_color=CLR_BORDER,
            text_color=CLR_TEXT,
        )
        self._tabs.pack(fill="both", expand=True, padx=12, pady=12)

        self._tabs.add("📊 Results")
        self._tabs.add("📋 Log")
        self._tabs.add("ℹ️ About")

        self._build_results_tab(self._tabs.tab("📊 Results"))
        self._build_log_tab(self._tabs.tab("📋 Log"))
        self._build_about_tab(self._tabs.tab("ℹ️ About"))

    def _build_results_tab(self, parent) -> None:
        self._results_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self._results_frame.pack(fill="both", expand=True)

        # Placeholder label
        self._results_placeholder = ctk.CTkLabel(
            self._results_frame,
            text="No constraints extracted yet.\nRun 'Scrape & Extract' to begin.",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color=CLR_SUBTEXT,
        )
        self._results_placeholder.pack(expand=True, pady=60)

        # Results cards container (hidden until data arrives)
        self._cards_frame = ctk.CTkFrame(self._results_frame, fg_color="transparent")

    def _build_log_tab(self, parent) -> None:
        self._log_text = ctk.CTkTextbox(
            parent,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=CLR_BG, text_color=CLR_TEXT,
            wrap="word",
        )
        self._log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self._log_text.configure(state="disabled")

        ctk.CTkButton(
            parent, text="Clear Log", width=100,
            fg_color="transparent", border_width=1, border_color=CLR_BORDER,
            hover_color=CLR_BORDER, text_color=CLR_SUBTEXT,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._clear_log,
        ).pack(side="right", padx=8, pady=(0, 8))

    def _build_about_tab(self, parent) -> None:
        about_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        about_frame.pack(fill="both", expand=True)

        about_text = (
            f"**{APP_NAME}** v{APP_VERSION}\n\n"
            "Automatically extracts PCB manufacturing constraints from vendor\n"
            "capability pages using AI (Google Gemini 2.5 Flash), then injects\n"
            "them directly into your KiCad project files.\n\n"
            "──────────────────────────────────────\n"
            "Features:\n"
            "  • AI-powered constraint extraction\n"
            "  • Auto net-class configuration (Default / Power / CAN_Bus)\n"
            "  • .kicad_pro JSON patching (design rules + net classes)\n"
            "  • .kicad_pcb S-expression patching (setup block)\n"
            "  • API key stored securely in %APPDATA%\n\n"
            "Supported Vendors:\n"
            "  • JLCPCB  •  PCBWay  •  OSH Park  •  Any vendor with a cap page\n\n"
            "──────────────────────────────────────\n"
            "GitHub: https://github.com/omkardas22/Kicad_Configurator\n"
        )
        ctk.CTkLabel(
            about_frame, text=about_text,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=CLR_TEXT, justify="left", anchor="nw",
            wraplength=480,
        ).pack(padx=20, pady=20, anchor="nw")

    # ------------------------------------------------------------------
    # Results Cards
    # ------------------------------------------------------------------

    def _render_results(self, c: PCBConstraints) -> None:
        """Render extracted constraint data as visual cards."""
        # Clear previous
        for widget in self._cards_frame.winfo_children():
            widget.destroy()
        self._results_placeholder.pack_forget()
        self._cards_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # Vendor badge
        vendor_card = ctk.CTkFrame(self._cards_frame, fg_color=CLR_ACCENT, corner_radius=10)
        vendor_card.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            vendor_card,
            text=f"🏭  {c.vendor_name}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="white",
        ).pack(padx=16, pady=10)

        # Constraint grid
        metrics = [
            ("Min Trace Width", f"{c.min_trace_width_mm:.4f} mm", "📏"),
            ("Min Clearance",   f"{c.min_clearance_mm:.4f} mm",   "↔️"),
            ("Min Via Diameter",f"{c.min_via_diameter_mm:.4f} mm", "⭕"),
            ("Min Via Drill",   f"{c.min_via_drill_mm:.4f} mm",    "🔩"),
            ("Min Hole Dia",    f"{c.min_hole_diameter_mm:.4f} mm","🕳️"),
            ("Min Annular Ring",f"{c.min_annular_ring_mm:.4f} mm", "🔘"),
        ]

        grid = ctk.CTkFrame(self._cards_frame, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1)

        for i, (label, value, icon) in enumerate(metrics):
            card = ctk.CTkFrame(grid, fg_color=CLR_BG, corner_radius=8)
            card.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="nsew")
            ctk.CTkLabel(
                card, text=icon,
                font=ctk.CTkFont(size=20),
            ).pack(pady=(10, 2))
            ctk.CTkLabel(
                card, text=value,
                font=ctk.CTkFont(family="Consolas", size=16, weight="bold"),
                text_color=CLR_ACCENT2,
            ).pack()
            ctk.CTkLabel(
                card, text=label,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color=CLR_SUBTEXT,
            ).pack(pady=(0, 10))

        # Net class preview
        if c.notes:
            notes_card = ctk.CTkFrame(self._cards_frame, fg_color=CLR_BG, corner_radius=8)
            notes_card.pack(fill="x", pady=(8, 4))
            ctk.CTkLabel(
                notes_card, text="📝  Vendor Notes",
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                text_color=CLR_TEXT, anchor="w",
            ).pack(padx=12, pady=(10, 4), anchor="w")
            ctk.CTkLabel(
                notes_card, text=c.notes,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color=CLR_SUBTEXT, wraplength=360, justify="left", anchor="nw",
            ).pack(padx=12, pady=(0, 10), anchor="w")

        # Switch to results tab
        self._tabs.set("📊 Results")

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _restore_config(self) -> None:
        if "api_key" in self._config:
            self._api_key_var.set(self._config["api_key"])
            self._key_status_label.configure(text="✔ Key loaded from config", text_color=CLR_SUCCESS)
        if "output_dir" in self._config:
            self._output_dir_var.set(self._config["output_dir"])

    def _save_api_key(self) -> None:
        key = self._api_key_var.get().strip()
        if not key:
            self._key_status_label.configure(text="⚠ Enter a key first", text_color=CLR_WARNING)
            return
        self._config["api_key"] = key
        save_config(self._config)
        self._key_status_label.configure(text="✔ Key saved", text_color=CLR_SUCCESS)

    def _toggle_key_visibility(self) -> None:
        current = self._api_entry.cget("show")
        self._api_entry.configure(show="" if current == "•" else "•")

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self._output_dir_var.set(path)
            self._config["output_dir"] = path
            save_config(self._config)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        """Append message to log textbox (thread-safe)."""
        def _do():
            self._log_text.configure(state="normal")
            self._log_text.insert("end", f"{message}\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        self.after(0, _do)

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _set_status(self, msg: str) -> None:
        self.after(0, lambda: self._status_var.set(msg))

    # ------------------------------------------------------------------
    # Scrape & Extract (background thread)
    # ------------------------------------------------------------------

    def _start_scrape(self) -> None:
        if self._scraping:
            return
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a vendor capability URL.")
            return
        api_key = self._api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("Missing API Key", "Please enter your Gemini API key.")
            return

        self._scraping = True
        self._scrape_btn.configure(state="disabled", text="⏳  Working …")
        self._inject_btn.configure(state="disabled")
        self._progress.start()
        self._tabs.set("📋 Log")
        self._log(f"{'─'*50}")
        self._log(f"🚀 Starting extraction at {time.strftime('%H:%M:%S')}")
        self._log(f"🌐 URL: {url}")

        thread = threading.Thread(
            target=self._scrape_worker,
            args=(url, api_key),
            daemon=True,
        )
        thread.start()

    def _scrape_worker(self, url: str, api_key: str) -> None:
        try:
            # Step 1: Scrape
            self._log("📡 Fetching vendor page …")
            self._set_status("Scraping vendor page …")
            raw_text = scrape_vendor_page(url)
            self._log(f"  ✅ Fetched {len(raw_text):,} chars of content")

            # Step 2: Gemini extraction
            self._log("🤖 Sending to Gemini 2.5 Flash for extraction …")
            self._set_status("Calling Gemini API …")
            constraints = extract_constraints_gemini(api_key, raw_text, url)
            self._constraints = constraints

            self._log(f"  ✅ Extraction complete!")
            self._log(f"  🏭 Vendor: {constraints.vendor_name}")
            self._log(f"  📏 Min Trace:       {constraints.min_trace_width_mm} mm")
            self._log(f"  ↔️  Min Clearance:   {constraints.min_clearance_mm} mm")
            self._log(f"  ⭕ Min Via Dia:     {constraints.min_via_diameter_mm} mm")
            self._log(f"  🔩 Min Via Drill:   {constraints.min_via_drill_mm} mm")
            self._log(f"  🕳️  Min Hole Dia:    {constraints.min_hole_diameter_mm} mm")
            self._log(f"  🔘 Min Annular:     {constraints.min_annular_ring_mm} mm")
            if constraints.notes:
                self._log(f"  📝 Notes: {constraints.notes[:200]}")

            # Update UI
            self.after(0, lambda: self._render_results(constraints))
            self.after(0, lambda: self._inject_btn.configure(state="normal"))
            self._set_status(f"✅ Extracted constraints from {constraints.vendor_name}")

        except Exception as exc:
            self._log(f"❌ Error: {exc}")
            self._set_status(f"Error: {exc}")
            self.after(0, lambda: messagebox.showerror("Extraction Failed", str(exc)))

        finally:
            self._scraping = False
            self.after(0, self._reset_scrape_ui)

    def _reset_scrape_ui(self) -> None:
        self._scrape_btn.configure(state="normal", text="🔍  Scrape & Extract Constraints")
        self._progress.stop()
        self._progress.set(0)

    # ------------------------------------------------------------------
    # Inject into KiCad files
    # ------------------------------------------------------------------

    def _inject_constraints(self) -> None:
        if self._constraints is None:
            messagebox.showwarning("No Data", "Please extract constraints first.")
            return

        output_dir_str = self._output_dir_var.get().strip()
        if not output_dir_str:
            messagebox.showwarning("No Output Dir", "Please select an output directory.")
            return

        project_name = self._project_name_var.get().strip()
        if not project_name:
            messagebox.showwarning("No Project Name", "Please enter a project name.")
            return

        output_dir = Path(output_dir_str)
        template_dir = get_resource_path("kicad_template")

        if not template_dir.exists():
            messagebox.showerror(
                "Missing Templates",
                f"Template directory not found:\n{template_dir}\n\n"
                "Ensure kicad_template/ is present in the app folder.",
            )
            return

        self._inject_btn.configure(state="disabled", text="⏳  Injecting …")
        self._log(f"{'─'*50}")
        self._log(f"💉 Injection started at {time.strftime('%H:%M:%S')}")

        def _worker():
            try:
                dest = run_injection(
                    self._constraints,
                    output_dir,
                    template_dir,
                    project_name,
                    self._log,
                )
                self._log(f"✅ Project created at: {dest}")
                self._set_status(f"✅ Injected into {dest}")
                self.after(0, lambda: messagebox.showinfo(
                    "Done!",
                    f"KiCad project created successfully:\n{dest}\n\n"
                    "Open the .kicad_pro file in KiCad to start designing!",
                ))
            except Exception as exc:
                self._log(f"❌ Injection failed: {exc}")
                self._set_status(f"Error: {exc}")
                self.after(0, lambda: messagebox.showerror("Injection Failed", str(exc)))
            finally:
                self.after(0, lambda: self._inject_btn.configure(
                    state="normal", text="💉  Inject into KiCad Files"
                ))

        threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = KiCadConfiguratorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
