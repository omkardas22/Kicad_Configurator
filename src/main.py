"""
KiCad Constraint Configurator
Author: KiCad Constraint Configurator Team
Version: 1.1.0

Main application file. Provides a CustomTkinter GUI for:
  - Selecting an AI provider (Google Gemini, OpenAI, Anthropic, OpenRouter)
  - Entering a per-provider API key (stored in %APPDATA%/KiCadConfigurator/config.json)
  - Fetching available models from the provider with smart recommendations
  - Specifying a vendor URL (PCBWay, JLCPCB, etc.)
  - Scraping vendor capability pages with requests + BeautifulSoup
  - Extracting PCB constraints via AI structured output / Pydantic
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
APP_NAME    = "KiCad Constraint Configurator"
APP_VERSION = "1.1.0"
APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "KiCadConfigurator"
CONFIG_FILE = APPDATA_DIR / "config.json"

# Colour palette — synced with Stitch "Precision Engineering Interface" design system
CLR_BG      = "#0b1326"   # surface
CLR_PANEL   = "#131b2e"   # surface-container-low
CLR_ACCENT  = "#4d8eff"   # primary-container
CLR_ACCENT2 = "#adc6ff"   # primary
CLR_SUCCESS = "#4edea3"   # secondary
CLR_WARNING = "#f9bd22"   # tertiary
CLR_ERROR   = "#ffb4ab"   # error
CLR_TEXT    = "#dae2fd"   # on-surface
CLR_SUBTEXT = "#c2c6d6"   # on-surface-variant
CLR_BORDER  = "#424754"   # outline-variant
CLR_CARD    = "#171f33"   # surface-container

# Net-class defaults
POWER_MULTIPLIER = 2.0
POWER_COLOR      = "rgba(228, 26, 28, 0.800)"
DIFF_PAIR_COLOR  = "rgba(55, 126, 184, 0.800)"

NETCLASS_PATTERNS = [
    {"netclass": "Power",             "pattern": "+*"},
    {"netclass": "Power",             "pattern": "GND*"},
    {"netclass": "Power",             "pattern": "VCC*"},
    {"netclass": "Differential_Pair", "pattern": "DIFF_*"},
    {"netclass": "Differential_Pair", "pattern": "DP_*"},
    {"netclass": "Differential_Pair", "pattern": "CAN_*"},
    {"netclass": "Differential_Pair", "pattern": "USB_*"},
]

# ---------------------------------------------------------------------------
# AI Provider definitions
# ---------------------------------------------------------------------------

# Each provider entry:
#   id          : internal key
#   label       : display name
#   placeholder : API key hint
#   recommended : preferred model IDs in priority order
#   static_models : fallback list when no API is available (Anthropic, etc.)
AI_PROVIDERS = [
    {
        "id":          "google",
        "label":       "Google Gemini",
        "placeholder": "AIza…",
        "recommended": [
            "gemini-2.5-flash",
            "gemini-2.5-flash-preview-05-20",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ],
        "static_models": [],
    },
    {
        "id":          "openai",
        "label":       "OpenAI",
        "placeholder": "sk-…",
        "recommended": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
        ],
        "static_models": [],
    },
    {
        "id":          "anthropic",
        "label":       "Anthropic Claude",
        "placeholder": "sk-ant-…",
        "recommended": [
            "claude-3-5-haiku-20241022",
            "claude-3-5-sonnet-20241022",
            "claude-3-haiku-20240307",
            "claude-opus-4-5",
        ],
        "static_models": [
            "claude-3-5-haiku-20241022",
            "claude-3-5-sonnet-20241022",
            "claude-3-haiku-20240307",
            "claude-3-sonnet-20240229",
            "claude-opus-4-5",
        ],
    },
    {
        "id":          "openrouter",
        "label":       "OpenRouter",
        "placeholder": "sk-or-…",
        "recommended": [
            "google/gemini-2.0-flash-exp:free",
            "openai/gpt-4o-mini",
            "anthropic/claude-3-haiku",
            "google/gemini-flash-1.5",
        ],
        "static_models": [],
    },
]

PROVIDER_MAP = {p["id"]: p for p in AI_PROVIDERS}


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
# Pydantic schema for structured AI output
# ---------------------------------------------------------------------------
if _PYDANTIC_OK:
    class PCBConstraints(BaseModel):
        """Structured PCB manufacturing constraint data extracted from a vendor page."""

        min_trace_width_mm:  float = Field(default=0.1,  description="Minimum copper trace width in mm")
        min_clearance_mm:    float = Field(default=0.1,  description="Minimum copper-to-copper clearance in mm")
        min_via_diameter_mm: float = Field(default=0.6,  description="Minimum via outer diameter in mm")
        min_via_drill_mm:    float = Field(default=0.3,  description="Minimum via drill hole diameter in mm")
        min_hole_diameter_mm:float = Field(default=0.3,  description="Minimum mechanical drill hole diameter in mm")
        min_annular_ring_mm: float = Field(default=0.1,  description="Minimum pad annular ring width in mm")
        vendor_name:         str   = Field(default="Unknown Vendor", description="Name of the PCB manufacturer")
        source_url:          str   = Field(default="",   description="URL where constraints were scraped from")
        notes:               str   = Field(default="",   description="Any extra relevant notes from the vendor page")
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
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body
    text = main.get_text(separator="\n") if main else soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:12000]


# ---------------------------------------------------------------------------
# Model fetcher — provider-specific
# ---------------------------------------------------------------------------

def fetch_models_google(api_key: str) -> list[str]:
    """Fetch available Gemini models via the REST API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}&pageSize=100"
    resp = requests.get(url, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    models = []
    for m in data.get("models", []):
        name = m.get("name", "")
        # Strip the "models/" prefix
        if name.startswith("models/"):
            name = name[len("models/"):]
        # Only include models that support generateContent
        supported = [a.get("name") for a in m.get("supportedGenerationMethods", [])]
        if "generateContent" in supported:
            models.append(name)
    return sorted(models)


def fetch_models_openai(api_key: str) -> list[str]:
    """Fetch available OpenAI models."""
    resp = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=12,
    )
    resp.raise_for_status()
    data = resp.json()
    ids = [m["id"] for m in data.get("data", [])]
    # Filter to useful chat/text completion models
    useful = [m for m in ids if any(k in m for k in ("gpt-4", "gpt-3.5", "o1", "o3"))]
    return sorted(useful)


def fetch_models_anthropic(_api_key: str) -> list[str]:
    """Return Anthropic's static model list (no public list endpoint)."""
    return PROVIDER_MAP["anthropic"]["static_models"][:]


def fetch_models_openrouter(api_key: str) -> list[str]:
    """Fetch available OpenRouter models."""
    resp = requests.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return sorted([m["id"] for m in data.get("data", [])])


FETCH_MODELS_FN = {
    "google":     fetch_models_google,
    "openai":     fetch_models_openai,
    "anthropic":  fetch_models_anthropic,
    "openrouter": fetch_models_openrouter,
}


# ---------------------------------------------------------------------------
# AI extraction adapters — one per provider
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT_TEMPLATE = textwrap.dedent("""\
    You are an expert PCB manufacturing engineer.
    Below is raw text scraped from a PCB vendor capability page at: {source_url}

    Extract the minimum PCB design constraints as numeric values in millimeters.
    If a value is given in mils or inches, convert to mm (1 mil = 0.0254 mm, 1 inch = 25.4 mm).
    Return ONLY valid JSON matching this exact schema:
    {{
      "min_trace_width_mm":   <float>,
      "min_clearance_mm":     <float>,
      "min_via_diameter_mm":  <float>,
      "min_via_drill_mm":     <float>,
      "min_hole_diameter_mm": <float>,
      "min_annular_ring_mm":  <float>,
      "vendor_name":          "<string>",
      "source_url":           "<string>",
      "notes":                "<string>"
    }}
    Use conservative (larger) defaults when data is ambiguous or missing.

    --- BEGIN VENDOR TEXT ---
    {raw_text}
    --- END VENDOR TEXT ---
""")


def _parse_json_response(text: str) -> dict:
    """Extract JSON from a possibly markdown-fenced response."""
    # Strip ```json ... ``` fences
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def extract_constraints_google(api_key: str, model: str, raw_text: str, source_url: str) -> PCBConstraints:
    """Call Google Gemini with structured output to extract PCB constraints."""
    if not _GENAI_OK:
        raise RuntimeError(
            "google-genai package not installed. Run: pip install google-genai"
        )
    if not _PYDANTIC_OK:
        raise RuntimeError("pydantic not installed.")

    client = genai.Client(api_key=api_key)
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(raw_text=raw_text, source_url=source_url)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PCBConstraints,
            temperature=0.1,
        ),
    )
    data = json.loads(response.text)
    data["source_url"] = source_url
    return PCBConstraints(**data)


def extract_constraints_openai(api_key: str, model: str, raw_text: str, source_url: str) -> PCBConstraints:
    """Call OpenAI chat completions API (pure requests) to extract PCB constraints."""
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(raw_text=raw_text, source_url=source_url)
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are an expert PCB manufacturing engineer. Return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
    }
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    data = _parse_json_response(content)
    data["source_url"] = source_url
    return PCBConstraints(**data)


def extract_constraints_anthropic(api_key: str, model: str, raw_text: str, source_url: str) -> PCBConstraints:
    """Call Anthropic Messages API (pure requests) to extract PCB constraints."""
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(raw_text=raw_text, source_url=source_url)
    payload = {
        "model":      model,
        "max_tokens": 1024,
        "messages":   [{"role": "user", "content": prompt}],
    }
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["content"][0]["text"]
    data = _parse_json_response(content)
    data["source_url"] = source_url
    return PCBConstraints(**data)


def extract_constraints_openrouter(api_key: str, model: str, raw_text: str, source_url: str) -> PCBConstraints:
    """Call OpenRouter's OpenAI-compatible endpoint to extract PCB constraints."""
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(raw_text=raw_text, source_url=source_url)
    payload = {
        "model":      model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": "You are an expert PCB manufacturing engineer. Return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
    }
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://github.com/omkardas22/Kicad_Configurator",
            "X-Title":       "KiCad Constraint Configurator",
        },
        json=payload,
        timeout=90,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    data = _parse_json_response(content)
    data["source_url"] = source_url
    return PCBConstraints(**data)


EXTRACT_FN = {
    "google":     extract_constraints_google,
    "openai":     extract_constraints_openai,
    "anthropic":  extract_constraints_anthropic,
    "openrouter": extract_constraints_openrouter,
}


# ---------------------------------------------------------------------------
# KiCad injection engine
# ---------------------------------------------------------------------------

def _build_net_class(name: str, constraints: PCBConstraints, color: str,
                     multiplier: float = 1.0, diff_pair: bool = False) -> dict:
    track   = round(constraints.min_trace_width_mm * multiplier, 4)
    clr     = round(constraints.min_clearance_mm * multiplier, 4)
    via_d   = round(constraints.min_via_diameter_mm, 4)
    via_dr  = round(constraints.min_via_drill_mm, 4)

    nc: dict = {
        "bus_width":          12,
        "clearance":          clr,
        "diff_pair_gap":      round(constraints.min_clearance_mm, 4) if diff_pair else 0.25,
        "diff_pair_via_gap":  round(constraints.min_clearance_mm, 4) if diff_pair else 0.25,
        "diff_pair_width":    track if diff_pair else 0.2,
        "line_style":         0,
        "microvia_diameter":  0.3,
        "microvia_drill":     0.1,
        "name":               name,
        "pcb_color":          color,
        "priority":           2147483647,
        "schematic_color":    color,
        "track_width":        track,
        "tuning_profile":     "",
        "via_diameter":       via_d,
        "via_drill":          via_dr,
        "wire_width":         6,
    }
    return nc


def inject_kicad_pro(pro_path: Path, constraints: PCBConstraints,
                     selected_presets: list[dict] | None = None) -> None:
    """Patch a .kicad_pro file (JSON) with extracted constraints, net classes,
    and user-selected track/via presets."""
    with open(pro_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    design = data.setdefault("board", {}).setdefault("design_settings", {})
    rules = design.setdefault("rules", {})
    rules["min_clearance"]          = constraints.min_clearance_mm
    rules["min_track_width"]        = constraints.min_trace_width_mm
    rules["min_via_diameter"]       = constraints.min_via_diameter_mm
    rules["min_via_annular_width"]  = constraints.min_annular_ring_mm
    rules["min_through_hole_diameter"] = constraints.min_hole_diameter_mm
    rules["min_hole_clearance"]     = constraints.min_clearance_mm
    rules["min_hole_to_hole"]       = constraints.min_hole_diameter_mm

    default_nc   = _build_net_class("Default",           constraints, "rgba(0, 0, 0, 0.000)", multiplier=1.0)
    power_nc     = _build_net_class("Power",             constraints, POWER_COLOR,             multiplier=POWER_MULTIPLIER)
    diff_pair_nc = _build_net_class("Differential_Pair", constraints, DIFF_PAIR_COLOR,         multiplier=1.0, diff_pair=True)

    net_settings = data.setdefault("net_settings", {})
    net_settings["classes"]          = [default_nc, power_nc, diff_pair_nc]
    net_settings["netclass_patterns"] = copy.deepcopy(NETCLASS_PATTERNS)

    # ── Inject selected track/via presets ──────────────────────────────
    if selected_presets:
        # KiCad expects a 0.0 sentinel as the first entry in both lists
        track_widths = [0.0] + sorted({p["track_width"] for p in selected_presets})
        via_dims = sorted(
            {(p["via_dia"], p["via_drill"]) for p in selected_presets},
            key=lambda t: t[0],
        )
        design["track_widths"] = track_widths
        design["via_dimensions"] = [
            {"diameter": 0.0, "drill": 0.0},  # sentinel entry
        ] + [
            {"diameter": d, "drill": dr} for d, dr in via_dims
        ]

    with open(pro_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def inject_kicad_pcb(pcb_path: Path, constraints: PCBConstraints) -> None:
    """Patch a .kicad_pcb file (S-expression) with extracted constraints via regex."""
    with open(pcb_path, "r", encoding="utf-8") as f:
        content = f.read()

    replacements = {
        r"\(clearance\s+[\d.]+\)":       f"(clearance {constraints.min_clearance_mm})",
        r"\(track_width\s+[\d.]+\)":     f"(track_width {constraints.min_trace_width_mm})",
        r"\(via_size\s+[\d.]+\)":        f"(via_size {constraints.min_via_diameter_mm})",
        r"\(via_drill\s+[\d.]+\)":       f"(via_drill {constraints.min_via_drill_mm})",
        r"\(via_min_size\s+[\d.]+\)":    f"(via_min_size {constraints.min_via_diameter_mm})",
        r"\(via_min_drill\s+[\d.]+\)":   f"(via_min_drill {constraints.min_via_drill_mm})",
        r"\(hole_to_hole_min\s+[\d.]+\)":f"(hole_to_hole_min {constraints.min_hole_diameter_mm})",
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
    selected_presets: list[dict] | None = None,
) -> Path:
    """Copy templates to output_dir/<project_name>/ and inject constraints."""
    dest = output_dir / project_name
    dest.mkdir(parents=True, exist_ok=True)
    log_callback(f"📁 Creating project folder: {dest}")

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

    pro_path = dest / f"{project_name}.kicad_pro"
    log_callback("⚙️  Injecting constraints into .kicad_pro …")
    inject_kicad_pro(pro_path, constraints, selected_presets)
    if selected_presets:
        log_callback(f"  ✅ .kicad_pro updated (design rules + net classes + {len(selected_presets)} presets)")
    else:
        log_callback("  ✅ .kicad_pro updated (design rules + net classes + patterns)")

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
        self.geometry("980x760")
        self.minsize(860, 660)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=CLR_BG)

        try:
            self.iconbitmap(get_resource_path("app_icon.ico"))
        except Exception:
            pass

        self._config:      dict                    = load_config()
        self._constraints: Optional[PCBConstraints] = None
        self._constraints_lock                      = threading.Lock()
        self._scraping:    bool                     = False
        self._models_list: list[str]                = []

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

        left = ctk.CTkScrollableFrame(
            content, fg_color=CLR_PANEL, corner_radius=12, width=400
        )
        left.pack(side="left", fill="y", padx=(0, 8))

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

        # ── AI Provider ────────────────────────────────────────────────
        self._section_label(parent, "🤖  AI Provider")
        provider_labels = [p["label"] for p in AI_PROVIDERS]
        self._provider_var = ctk.StringVar(value=provider_labels[0])

        self._provider_menu = ctk.CTkOptionMenu(
            parent,
            variable=self._provider_var,
            values=provider_labels,
            fg_color=CLR_BG,
            button_color=CLR_ACCENT,
            button_hover_color=CLR_ACCENT2,
            dropdown_fg_color=CLR_CARD,
            dropdown_hover_color=CLR_BORDER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_provider_change,
        )
        self._provider_menu.pack(fill="x", padx=16, pady=(0, 8))

        # ── API Key ────────────────────────────────────────────────────
        self._section_label(parent, "🔑  API Key")
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

        ctk.CTkButton(
            parent, text="Show / Hide Key", width=130,
            fg_color="transparent", border_width=1, border_color=CLR_BORDER,
            hover_color=CLR_BORDER, text_color=CLR_SUBTEXT,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._toggle_key_visibility,
        ).pack(padx=16, pady=(2, 4), anchor="w")

        # ── Model Selection ────────────────────────────────────────────
        self._section_label(parent, "🧠  AI Model")

        # Fetch button + connection indicator row
        fetch_row = ctk.CTkFrame(parent, fg_color="transparent")
        fetch_row.pack(fill="x", padx=16, pady=(0, 4))

        self._fetch_btn = ctk.CTkButton(
            fetch_row, text="🔄 Fetch Models", width=130,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._fetch_models,
        )
        self._fetch_btn.pack(side="left", padx=(0, 8))

        self._conn_status_label = ctk.CTkLabel(
            fetch_row, text="○ Not connected",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT,
        )
        self._conn_status_label.pack(side="left")

        # Model list frame
        model_frame = ctk.CTkFrame(parent, fg_color=CLR_BG, corner_radius=8)
        model_frame.pack(fill="x", padx=16, pady=(0, 4))

        # Scrollable model list implemented as a CTkScrollableFrame with radio-style buttons
        self._model_scroll = ctk.CTkScrollableFrame(
            model_frame, fg_color="transparent", height=140
        )
        self._model_scroll.pack(fill="x", padx=4, pady=4)

        self._selected_model_var = ctk.StringVar(value="")
        self._model_radio_buttons: list[ctk.CTkRadioButton] = []

        # Placeholder text
        self._model_placeholder = ctk.CTkLabel(
            self._model_scroll,
            text="← Enter API key and click 'Fetch Models'",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT,
        )
        self._model_placeholder.pack(padx=8, pady=16)

        # Selected model indicator
        self._selected_model_label = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=CLR_ACCENT2, anchor="w",
        )
        self._selected_model_label.pack(fill="x", padx=16, pady=(0, 4))

        # ── Vendor URL ─────────────────────────────────────────────────
        self._section_label(parent, "🌐  Vendor Capability URL")
        self._url_var = ctk.StringVar()
        ctk.CTkEntry(
            parent, textvariable=self._url_var,
            placeholder_text="https://www.jlcpcb.com/capabilities/pcb",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        ).pack(fill="x", padx=16, pady=(0, 4))

        quick_frame = ctk.CTkFrame(parent, fg_color="transparent")
        quick_frame.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            quick_frame, text="Quick fill:",
            font=ctk.CTkFont(family="Segoe UI", size=11), text_color=CLR_SUBTEXT,
        ).pack(side="left", padx=(0, 6))
        for label, url in [
            ("JLCPCB",  "https://jlcpcb.com/capabilities/pcb"),
            ("PCBWay",  "https://www.pcbway.com/capabilities.html"),
            ("OSHPark", "https://docs.oshpark.com/submitting-designs/drill-specs/"),
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
        self._tabs.add("📐 Rules & Presets")
        self._tabs.add("📋 Log")
        self._tabs.add("ℹ️ About")

        self._build_results_tab(self._tabs.tab("📊 Results"))
        self._build_presets_tab(self._tabs.tab("📐 Rules & Presets"))
        self._build_log_tab(self._tabs.tab("📋 Log"))
        self._build_about_tab(self._tabs.tab("ℹ️ About"))

    def _build_results_tab(self, parent) -> None:
        self._results_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self._results_frame.pack(fill="both", expand=True)

        self._results_placeholder = ctk.CTkLabel(
            self._results_frame,
            text="No constraints extracted yet.\nRun 'Scrape & Extract' to begin.",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color=CLR_SUBTEXT,
        )
        self._results_placeholder.pack(expand=True, pady=60)

        self._cards_frame = ctk.CTkFrame(self._results_frame, fg_color="transparent")

    def _build_presets_tab(self, parent) -> None:
        """Build the '📐 Rules & Presets' tab content."""
        self._presets_outer = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self._presets_outer.pack(fill="both", expand=True)

        # Placeholder — shown before any scrape
        self._presets_placeholder = ctk.CTkLabel(
            self._presets_outer,
            text="No presets generated yet.\nRun 'Scrape & Extract' to load compatibility options.",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color=CLR_SUBTEXT,
        )
        self._presets_placeholder.pack(expand=True, pady=60)

        # Container for live content (hidden until populated)
        self._presets_content = ctk.CTkFrame(self._presets_outer, fg_color="transparent")

        # ── Header row ─────────────────────────────────────────────────
        header_row = ctk.CTkFrame(self._presets_content, fg_color="transparent")
        header_row.pack(fill="x", padx=8, pady=(8, 12))

        ctk.CTkLabel(
            header_row,
            text="⚡  Compatible Trace & Via Presets",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=CLR_TEXT,
        ).pack(side="left")

        self._vendor_compat_badge = ctk.CTkLabel(
            header_row,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=CLR_ACCENT,
            fg_color=CLR_CARD,
            corner_radius=6,
        )
        self._vendor_compat_badge.pack(side="right", padx=4)

        # ── Range display frame ────────────────────────────────────────
        range_frame = ctk.CTkFrame(
            self._presets_content, fg_color=CLR_CARD, corner_radius=10,
            border_width=1, border_color=CLR_BORDER,
        )
        range_frame.pack(fill="x", padx=8, pady=(0, 12))
        range_frame.columnconfigure((0, 1), weight=1)

        # Trace width range
        tw_frame = ctk.CTkFrame(range_frame, fg_color="transparent")
        tw_frame.grid(row=0, column=0, padx=16, pady=12, sticky="ew")
        ctk.CTkLabel(
            tw_frame, text="TRACE WIDTH RANGE",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color=CLR_SUBTEXT,
        ).pack(anchor="w")
        self._tw_range_label = ctk.CTkLabel(
            tw_frame, text="— mm  to  — mm",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=CLR_ACCENT,
        )
        self._tw_range_label.pack(anchor="w", pady=(4, 0))

        # Via size range
        vs_frame = ctk.CTkFrame(range_frame, fg_color="transparent")
        vs_frame.grid(row=0, column=1, padx=16, pady=12, sticky="ew")
        ctk.CTkLabel(
            vs_frame, text="VIA SIZE RANGE",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color=CLR_SUBTEXT,
        ).pack(anchor="w")
        self._vs_range_label = ctk.CTkLabel(
            vs_frame, text="— mm  to  — mm",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=CLR_ACCENT,
        )
        self._vs_range_label.pack(anchor="w", pady=(4, 0))

        # ── Table header ───────────────────────────────────────────────
        table_container = ctk.CTkFrame(
            self._presets_content, fg_color=CLR_CARD, corner_radius=10,
            border_width=1, border_color=CLR_BORDER,
        )
        table_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        col_headers = ["", "Preset Name", "Track Width", "Via Outer Dia", "Via Drill Dia", "Status"]
        col_widths  = [40, 180, 110, 110, 110, 90]

        hdr_frame = ctk.CTkFrame(table_container, fg_color=CLR_BG, corner_radius=0)
        hdr_frame.pack(fill="x")
        for i, (h, w) in enumerate(zip(col_headers, col_widths)):
            ctk.CTkLabel(
                hdr_frame, text=h, width=w,
                font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
                text_color=CLR_SUBTEXT, anchor="w",
            ).grid(row=0, column=i, padx=8, pady=8, sticky="w")

        # Scrollable rows
        self._presets_table_frame = ctk.CTkScrollableFrame(
            table_container, fg_color="transparent", height=320,
        )
        self._presets_table_frame.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Info footer ────────────────────────────────────────────────
        info_frame = ctk.CTkFrame(
            self._presets_content, fg_color=CLR_PANEL, corner_radius=8,
            border_width=1, border_color=CLR_BORDER,
        )
        info_frame.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(
            info_frame,
            text="ℹ️  Selected presets will be injected directly as track/via size lists in KiCad.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT, anchor="w",
        ).pack(padx=12, pady=8, anchor="w")

        # Data storage
        self._preset_vars: list[ctk.BooleanVar] = []
        self._preset_data: list[dict] = []
        self._preset_row_widgets: list[list] = []

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
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "Automatically extracts PCB manufacturing constraints from vendor\n"
            "capability pages using AI, then injects them directly into your\n"
            "KiCad project files.\n\n"
            "──────────────────────────────────────\n"
            "Supported AI Providers:\n"
            "  • Google Gemini   (gemini-2.x-flash recommended)\n"
            "  • OpenAI          (gpt-4o-mini recommended)\n"
            "  • Anthropic Claude(claude-3-5-haiku recommended)\n"
            "  • OpenRouter      (many models, free tiers available)\n\n"
            "Features:\n"
            "  • Multi-provider AI with dynamic model listing\n"
            "  • Smart model recommendations per provider\n"
            "  • Per-provider API key storage\n"
            "  • AI-powered constraint extraction\n"
            "  • Auto net-class config (Default / Power / CAN_Bus)\n"
            "  • .kicad_pro JSON patching (design rules + net classes)\n"
            "  • .kicad_pcb S-expression patching (setup block)\n"
            "  • API keys stored securely in %APPDATA%\n\n"
            "Supported PCB Vendors:\n"
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
            ("Min Trace Width",  f"{c.min_trace_width_mm:.4f} mm",  "📏"),
            ("Min Clearance",    f"{c.min_clearance_mm:.4f} mm",    "↔️"),
            ("Min Via Diameter", f"{c.min_via_diameter_mm:.4f} mm", "⭕"),
            ("Min Via Drill",    f"{c.min_via_drill_mm:.4f} mm",    "🔩"),
            ("Min Hole Dia",     f"{c.min_hole_diameter_mm:.4f} mm","🕳️"),
            ("Min Annular Ring", f"{c.min_annular_ring_mm:.4f} mm", "🔘"),
        ]

        grid = ctk.CTkFrame(self._cards_frame, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1)

        for i, (label, value, icon) in enumerate(metrics):
            card = ctk.CTkFrame(grid, fg_color=CLR_BG, corner_radius=8)
            card.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="nsew")
            ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=20)).pack(pady=(10, 2))
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

        self._tabs.set("📊 Results")

        # Also populate the presets tab
        self._generate_presets(c)
        self._render_presets(c)

    # ------------------------------------------------------------------
    # Preset Generation & Rendering
    # ------------------------------------------------------------------

    def _generate_presets(self, c: PCBConstraints) -> None:
        """Compute 10 trace/via presets from manufacturer min to practical max."""
        mn_tw = c.min_trace_width_mm
        mn_vd = c.min_via_diameter_mm
        mn_vr = c.min_via_drill_mm

        def clamp_tw(v: float) -> float:
            return round(max(v, mn_tw), 4)

        def clamp_vd(v: float) -> float:
            return round(max(v, mn_vd), 4)

        def clamp_vr(v: float) -> float:
            return round(max(v, mn_vr), 4)

        self._preset_data = [
            {
                "name": "Signal (Absolute Min)",
                "track_width": clamp_tw(mn_tw),
                "via_dia":     clamp_vd(mn_vd),
                "via_drill":   clamp_vr(mn_vr),
                "default_on":  True,
            },
            {
                "name": "Signal (Fine)",
                "track_width": clamp_tw(mn_tw * 1.27),
                "via_dia":     clamp_vd(mn_vd),
                "via_drill":   clamp_vr(mn_vr),
                "default_on":  True,
            },
            {
                "name": "Signal (Standard)",
                "track_width": clamp_tw(0.200),
                "via_dia":     clamp_vd(mn_vd + 0.1),
                "via_drill":   clamp_vr(mn_vr + 0.1),
                "default_on":  True,
            },
            {
                "name": "Signal (Robust)",
                "track_width": clamp_tw(0.254),
                "via_dia":     clamp_vd(0.800),
                "via_drill":   clamp_vr(0.450),
                "default_on":  False,
            },
            {
                "name": "Power (Low Current)",
                "track_width": clamp_tw(0.400),
                "via_dia":     clamp_vd(0.900),
                "via_drill":   clamp_vr(0.500),
                "default_on":  True,
            },
            {
                "name": "Power (Medium Current)",
                "track_width": clamp_tw(0.800),
                "via_dia":     clamp_vd(1.000),
                "via_drill":   clamp_vr(0.600),
                "default_on":  True,
            },
            {
                "name": "Power (High Current)",
                "track_width": clamp_tw(1.200),
                "via_dia":     clamp_vd(1.200),
                "via_drill":   clamp_vr(0.700),
                "default_on":  False,
            },
            {
                "name": "Power (Max Current)",
                "track_width": clamp_tw(2.000),
                "via_dia":     clamp_vd(1.500),
                "via_drill":   clamp_vr(0.900),
                "default_on":  True,
            },
            {
                "name": "Via (Standard Spec)",
                "track_width": clamp_tw(0.254),
                "via_dia":     clamp_vd(0.800),
                "via_drill":   clamp_vr(0.400),
                "default_on":  False,
            },
            {
                "name": "Via (High Current)",
                "track_width": clamp_tw(0.500),
                "via_dia":     clamp_vd(1.200),
                "via_drill":   clamp_vr(0.600),
                "default_on":  False,
            },
        ]

    def _render_presets(self, c: PCBConstraints) -> None:
        """Render the preset table rows with checkboxes."""
        # Hide placeholder, show content
        self._presets_placeholder.pack_forget()
        self._presets_content.pack(fill="both", expand=True)

        # Update vendor badge
        self._vendor_compat_badge.configure(text=f"  ✔ {c.vendor_name} Compatible  ")

        # Update range labels
        min_tw = min(p["track_width"] for p in self._preset_data)
        max_tw = max(p["track_width"] for p in self._preset_data)
        self._tw_range_label.configure(text=f"{min_tw:.3f} mm  to  {max_tw:.3f} mm")

        min_vd = min(p["via_drill"] for p in self._preset_data)
        max_vd = max(p["via_dia"] for p in self._preset_data)
        self._vs_range_label.configure(text=f"{min_vd:.3f} mm  to  {max_vd:.3f} mm")

        # Clear old rows
        for widgets in self._preset_row_widgets:
            for w in widgets:
                w.destroy()
        self._preset_row_widgets.clear()
        self._preset_vars.clear()

        col_widths = [40, 180, 110, 110, 110, 90]

        for idx, preset in enumerate(self._preset_data):
            var = ctk.BooleanVar(value=preset["default_on"])
            self._preset_vars.append(var)

            row_widgets = []
            bg = "transparent"

            # Checkbox
            cb = ctk.CTkCheckBox(
                self._presets_table_frame, text="", variable=var, width=col_widths[0],
                fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
                border_color=CLR_BORDER, checkmark_color="#ffffff",
            )
            cb.grid(row=idx, column=0, padx=8, pady=6, sticky="w")
            row_widgets.append(cb)

            # Preset name
            name_lbl = ctk.CTkLabel(
                self._presets_table_frame, text=preset["name"], width=col_widths[1],
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=CLR_TEXT, anchor="w",
            )
            name_lbl.grid(row=idx, column=1, padx=8, pady=6, sticky="w")
            row_widgets.append(name_lbl)

            # Track width
            tw_lbl = ctk.CTkLabel(
                self._presets_table_frame, text=f"{preset['track_width']:.3f} mm",
                width=col_widths[2],
                font=ctk.CTkFont(family="Consolas", size=12),
                text_color=CLR_ACCENT2, anchor="w",
            )
            tw_lbl.grid(row=idx, column=2, padx=8, pady=6, sticky="w")
            row_widgets.append(tw_lbl)

            # Via diameter
            vd_lbl = ctk.CTkLabel(
                self._presets_table_frame, text=f"{preset['via_dia']:.3f} mm",
                width=col_widths[3],
                font=ctk.CTkFont(family="Consolas", size=12),
                text_color=CLR_ACCENT2, anchor="w",
            )
            vd_lbl.grid(row=idx, column=3, padx=8, pady=6, sticky="w")
            row_widgets.append(vd_lbl)

            # Via drill
            vr_lbl = ctk.CTkLabel(
                self._presets_table_frame, text=f"{preset['via_drill']:.3f} mm",
                width=col_widths[4],
                font=ctk.CTkFont(family="Consolas", size=12),
                text_color=CLR_ACCENT2, anchor="w",
            )
            vr_lbl.grid(row=idx, column=4, padx=8, pady=6, sticky="w")
            row_widgets.append(vr_lbl)

            # Status badge
            status_lbl = ctk.CTkLabel(
                self._presets_table_frame, text="Compatible",
                width=col_widths[5],
                font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
                text_color=CLR_SUCCESS, fg_color=CLR_CARD, corner_radius=4,
            )
            status_lbl.grid(row=idx, column=5, padx=8, pady=6, sticky="w")
            row_widgets.append(status_lbl)

            self._preset_row_widgets.append(row_widgets)


    # ------------------------------------------------------------------
    # Provider / Model helpers
    # ------------------------------------------------------------------

    def _current_provider_id(self) -> str:
        label = self._provider_var.get()
        for p in AI_PROVIDERS:
            if p["label"] == label:
                return p["id"]
        return "google"

    def _on_provider_change(self, _value: str) -> None:
        """Update API key placeholder and clear the model list when provider changes."""
        pid = self._current_provider_id()
        provider = PROVIDER_MAP[pid]
        self._api_entry.configure(placeholder_text=provider["placeholder"])

        # Restore saved key for this provider
        saved_keys = self._config.get("api_keys", {})
        self._api_key_var.set(saved_keys.get(pid, ""))

        # Update key status
        if saved_keys.get(pid):
            self._key_status_label.configure(text="✔ Key loaded from config", text_color=CLR_SUCCESS)
        else:
            self._key_status_label.configure(text="", text_color=CLR_SUCCESS)

        # Clear model list
        self._clear_model_list()
        self._conn_status_label.configure(text="○ Not connected", text_color=CLR_SUBTEXT)

        # If provider has static models, populate immediately
        if provider["static_models"]:
            self._populate_model_list(provider["static_models"])
            self._conn_status_label.configure(
                text="● Static list loaded", text_color=CLR_WARNING
            )

    def _clear_model_list(self) -> None:
        for rb in self._model_radio_buttons:
            rb.destroy()
        self._model_radio_buttons.clear()
        self._model_placeholder.pack(padx=8, pady=16)
        self._selected_model_label.configure(text="")
        self._selected_model_var.set("")
        self._models_list = []

    def _populate_model_list(self, models: list[str]) -> None:
        """Fill the model scroll frame with radio buttons, marking the recommended one."""
        self._model_placeholder.pack_forget()

        pid = self._current_provider_id()
        recommended = PROVIDER_MAP[pid]["recommended"]

        # Determine best recommendation that exists in the fetched list
        star_model = next((m for m in recommended if m in models), None)
        if star_model is None and models:
            star_model = models[0]

        self._models_list = models

        for m in models:
            is_rec = (m == star_model)
            label  = f"⭐ {m}  ← Recommended" if is_rec else m
            color  = CLR_SUCCESS if is_rec else CLR_TEXT
            rb = ctk.CTkRadioButton(
                self._model_scroll,
                text=label,
                variable=self._selected_model_var,
                value=m,
                font=ctk.CTkFont(
                    family="Segoe UI",
                    size=11,
                    weight="bold" if is_rec else "normal",
                ),
                text_color=color,
                fg_color=CLR_ACCENT,
                hover_color=CLR_ACCENT2,
                command=self._on_model_select,
            )
            rb.pack(anchor="w", padx=8, pady=2)
            self._model_radio_buttons.append(rb)

        # Auto-select the recommended model
        if star_model:
            self._selected_model_var.set(star_model)
            self._on_model_select()

    def _on_model_select(self) -> None:
        model = self._selected_model_var.get()
        if model:
            self._selected_model_label.configure(
                text=f"Selected: {model}", text_color=CLR_ACCENT2
            )

    def _fetch_models(self) -> None:
        """Background thread: fetch models from the selected provider."""
        if not _REQUESTS_OK:
            messagebox.showerror("Missing Dependency", "requests package is not installed.")
            return

        api_key = self._api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("No API Key", "Please enter an API key before fetching models.")
            return

        pid = self._current_provider_id()
        self._fetch_btn.configure(state="disabled", text="⏳ Fetching…")
        self._conn_status_label.configure(text="⏳ Connecting…", text_color=CLR_WARNING)

        def _worker():
            try:
                models = FETCH_MODELS_FN[pid](api_key)
                self.after(0, lambda: self._on_models_fetched(models))
            except Exception as exc:
                self.after(0, lambda: self._on_models_error(str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_models_fetched(self, models: list[str]) -> None:
        self._fetch_btn.configure(state="normal", text="🔄 Fetch Models")
        if not models:
            self._conn_status_label.configure(text="⚠ No models found", text_color=CLR_WARNING)
            return
        self._conn_status_label.configure(
            text=f"✅ {len(models)} models available", text_color=CLR_SUCCESS
        )
        self._clear_model_list()
        self._populate_model_list(models)

    def _on_models_error(self, err: str) -> None:
        self._fetch_btn.configure(state="normal", text="🔄 Fetch Models")
        self._conn_status_label.configure(
            text="❌ Connection failed", text_color=CLR_ERROR
        )
        messagebox.showerror(
            "Fetch Models Failed",
            f"Could not retrieve model list:\n\n{err}\n\n"
            "Check your API key and internet connection.",
        )

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _restore_config(self) -> None:
        # Provider
        saved_provider = self._config.get("ai_provider", "google")
        for p in AI_PROVIDERS:
            if p["id"] == saved_provider:
                self._provider_var.set(p["label"])
                break

        pid = self._current_provider_id()
        provider = PROVIDER_MAP[pid]
        self._api_entry.configure(placeholder_text=provider["placeholder"])

        # API key for current provider
        saved_keys = self._config.get("api_keys", {})
        # Backward-compat: migrate legacy "api_key" field
        if "api_key" in self._config and "google" not in saved_keys:
            saved_keys["google"] = self._config.pop("api_key")
            self._config["api_keys"] = saved_keys

        key = saved_keys.get(pid, "")
        if key:
            self._api_key_var.set(key)
            self._key_status_label.configure(text="✔ Key loaded from config", text_color=CLR_SUCCESS)

        # Output dir
        if "output_dir" in self._config:
            self._output_dir_var.set(self._config["output_dir"])

        # If static-models provider, populate immediately
        if provider["static_models"]:
            self._populate_model_list(provider["static_models"])
            self._conn_status_label.configure(
                text="● Static list loaded", text_color=CLR_WARNING
            )

        # Restore previously selected model
        saved_model = self._config.get("ai_model", "")
        if saved_model and saved_model in self._models_list:
            self._selected_model_var.set(saved_model)
            self._on_model_select()

    def _save_api_key(self) -> None:
        key = self._api_key_var.get().strip()
        if not key:
            self._key_status_label.configure(text="⚠ Enter a key first", text_color=CLR_WARNING)
            return
        pid = self._current_provider_id()
        api_keys = self._config.setdefault("api_keys", {})
        api_keys[pid] = key
        self._config["ai_provider"] = pid
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

        if not _REQUESTS_OK:
            messagebox.showerror(
                "Missing Dependency",
                "The 'requests' and 'beautifulsoup4' packages are required.\n"
                "Run: pip install requests beautifulsoup4",
            )
            return

        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a vendor capability URL.")
            return

        api_key = self._api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("Missing API Key", "Please enter your API key.")
            return

        model = self._selected_model_var.get().strip()
        if not model:
            messagebox.showwarning(
                "No Model Selected",
                "Please select an AI model.\n"
                "Click 'Fetch Models' to load the available models.",
            )
            return

        pid = self._current_provider_id()

        self._scraping = True
        self._scrape_btn.configure(state="disabled", text="⏳  Working …")
        self._inject_btn.configure(state="disabled")
        self._progress.start()
        self._tabs.set("📋 Log")
        self._log(f"{'─'*50}")
        self._log(f"🚀 Starting extraction at {time.strftime('%H:%M:%S')}")
        self._log(f"🤖 Provider: {PROVIDER_MAP[pid]['label']}  |  Model: {model}")
        self._log(f"🌐 URL: {url}")

        thread = threading.Thread(
            target=self._scrape_worker,
            args=(url, api_key, pid, model),
            daemon=True,
        )
        thread.start()

    def _scrape_worker(self, url: str, api_key: str, provider_id: str, model: str) -> None:
        try:
            self._log("📡 Fetching vendor page …")
            self._set_status("Scraping vendor page …")
            raw_text = scrape_vendor_page(url)
            self._log(f"  ✅ Fetched {len(raw_text):,} chars of content")

            self._log(f"🤖 Sending to {PROVIDER_MAP[provider_id]['label']} ({model}) …")
            self._set_status(f"Calling {PROVIDER_MAP[provider_id]['label']} API …")

            extract_fn = EXTRACT_FN[provider_id]
            constraints = extract_fn(api_key, model, raw_text, url)

            with self._constraints_lock:
                self._constraints = constraints

            self._log("  ✅ Extraction complete!")
            self._log(f"  🏭 Vendor: {constraints.vendor_name}")
            self._log(f"  📏 Min Trace:       {constraints.min_trace_width_mm} mm")
            self._log(f"  ↔️  Min Clearance:   {constraints.min_clearance_mm} mm")
            self._log(f"  ⭕ Min Via Dia:     {constraints.min_via_diameter_mm} mm")
            self._log(f"  🔩 Min Via Drill:   {constraints.min_via_drill_mm} mm")
            self._log(f"  🕳️  Min Hole Dia:    {constraints.min_hole_diameter_mm} mm")
            self._log(f"  🔘 Min Annular:     {constraints.min_annular_ring_mm} mm")
            if constraints.notes:
                self._log(f"  📝 Notes: {constraints.notes[:200]}")

            # Save the used provider/model to config
            self._config["ai_provider"] = provider_id
            self._config["ai_model"]    = model
            save_config(self._config)

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
        with self._constraints_lock:
            constraints = self._constraints

        if constraints is None:
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

        # ── Collect selected presets ───────────────────────────────────
        selected_presets: list[dict] | None = None
        if self._preset_data and self._preset_vars:
            selected_presets = [
                {
                    "track_width": p["track_width"],
                    "via_dia":     p["via_dia"],
                    "via_drill":   p["via_drill"],
                }
                for p, var in zip(self._preset_data, self._preset_vars)
                if var.get()
            ]
            if not selected_presets:
                messagebox.showwarning(
                    "No Presets Selected",
                    "Please select at least one trace/via preset in the\n"
                    "'📐 Rules & Presets' tab before injecting.",
                )
                return

        output_dir   = Path(output_dir_str)
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
        if selected_presets:
            self._log(f"  📐 {len(selected_presets)} presets selected for injection")

        def _worker():
            try:
                dest = run_injection(
                    constraints,
                    output_dir,
                    template_dir,
                    project_name,
                    self._log,
                    selected_presets,
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
