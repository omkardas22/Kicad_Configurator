"""
KiCad Constraint Configurator
Author: KiCad Constraint Configurator Team
Version: 2.1.1

Main application file. Provides a CustomTkinter GUI for:
  - Selecting an AI provider (Google Gemini, OpenAI, Anthropic, OpenRouter)
  - Entering a per-provider API key (stored in %APPDATA%/KiCadConfigurator/config.json)
  - Fetching available models from the provider with smart recommendations
  - Specifying a vendor URL (PCBWay, JLCPCB, etc.)
  - Auto-generating project name from selected vendor (updates on every URL change)
  - Scraping vendor capability pages with requests + BeautifulSoup
  - Extracting PCB constraints via AI structured output / Pydantic
  - Annular ring–aware via configuration generation
  - Column-based preset configuration (Signal/Power/Differential/Vias) with 10 tiers
  - Custom track / via sizes with manufacturer compatibility checking
  - Injecting extracted constraints into .kicad_pro (JSON) and .kicad_pcb (S-expression)
  - KiCad 9/10 compatible output (version 20260206)
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
from urllib.parse import urlparse

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
APP_VERSION = "2.1.1"
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

# Category colour accents for column headers
CLR_SIGNAL_COL = "#64b5f6"   # blue for signal traces
CLR_POWER_COL  = "#ef5350"   # red for power traces
CLR_DIFF_COL   = "#ab47bc"   # purple for differential pairs
CLR_VIA_COL    = "#66bb6a"   # green for vias

# Net-class defaults
SIGNAL_MULTIPLIER = 1.0
SIGNAL_COLOR     = "rgba(100, 181, 246, 0.800)"
POWER_MULTIPLIER = 2.0
POWER_COLOR      = "rgba(228, 26, 28, 0.800)"
DIFF_PAIR_COLOR  = "rgba(55, 126, 184, 0.800)"

NETCLASS_PATTERNS = [
    {"netclass": "Signal",            "pattern": "SIG_*"},
    {"netclass": "Signal",            "pattern": "UART_*"},
    {"netclass": "Signal",            "pattern": "I2C_*"},
    {"netclass": "Signal",            "pattern": "SPI_*"},
    {"netclass": "Power",             "pattern": "+*"},
    {"netclass": "Power",             "pattern": "GND*"},
    {"netclass": "Power",             "pattern": "VCC*"},
    {"netclass": "Power",             "pattern": "VDD*"},
    {"netclass": "Power",             "pattern": "VBUS*"},
    {"netclass": "Differential_Pair", "pattern": "DIFF_*"},
    {"netclass": "Differential_Pair", "pattern": "DP_*"},
    {"netclass": "Differential_Pair", "pattern": "CAN_*"},
    {"netclass": "Differential_Pair", "pattern": "USB_*"},
    {"netclass": "Differential_Pair", "pattern": "ETH_*"},
]

# Vendor quick-fill definitions
# Each entry: (button_label, url, project_name_prefix)
VENDOR_QUICK_FILLS = [
    ("JLCPCB",     "https://jlcpcb.com/capabilities/pcb",                       "JLCPCB"),
    ("PCBWay",     "https://www.pcbway.com/capabilities.html",                  "PCBWay"),
    ("OSHPark",    "https://docs.oshpark.com/submitting-designs/drill-specs/",  "OSHPark"),
    ("AllPCB",     "https://www.allpcb.com/pcb_capability.html",                "AllPCB"),
    ("NextPCB",    "https://www.nextpcb.com/pcb-capabilities",                  "NextPCB"),
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
        max_trace_width_mm:  float = Field(default=6.0,  description="Maximum copper trace width in mm")
        max_via_diameter_mm: float = Field(default=6.0,  description="Maximum via outer diameter in mm")
        max_via_drill_mm:    float = Field(default=6.0,  description="Maximum via drill hole diameter in mm")
        max_hole_diameter_mm:float = Field(default=6.35, description="Maximum mechanical drill hole diameter in mm")
        vendor_name:         str   = Field(default="Unknown Vendor", description="Name of the PCB manufacturer")
        source_url:          str   = Field(default="",   description="URL where constraints were scraped from")
        notes:               str   = Field(default="",   description="Any extra relevant notes from the vendor page")
else:
    class PCBConstraints:  # type: ignore[no-redef]
        """Fallback when Pydantic is unavailable."""
        def __init__(self, **kwargs: float | str):
            # Apply defaults for any missing fields
            defaults = {
                "min_trace_width_mm": 0.1, "min_clearance_mm": 0.1,
                "min_via_diameter_mm": 0.6, "min_via_drill_mm": 0.3,
                "min_hole_diameter_mm": 0.3, "min_annular_ring_mm": 0.1,
                "max_trace_width_mm": 6.0, "max_via_diameter_mm": 6.0,
                "max_via_drill_mm": 6.0, "max_hole_diameter_mm": 6.35,
                "vendor_name": "Unknown Vendor", "source_url": "", "notes": "",
            }
            defaults.update(kwargs)
            self.__dict__.update(defaults)


def _sanitize_constraints(c: PCBConstraints) -> PCBConstraints:
    """Ensure all constraint values are positive and reasonable."""
    for attr in ("min_trace_width_mm", "min_clearance_mm", "min_via_diameter_mm",
                 "min_via_drill_mm", "min_hole_diameter_mm", "min_annular_ring_mm"):
        val = getattr(c, attr, 0.0)
        if not isinstance(val, (int, float)) or val <= 0:
            setattr(c, attr, 0.1)
    for attr in ("max_trace_width_mm", "max_via_diameter_mm", "max_via_drill_mm",
                 "max_hole_diameter_mm"):
        val = getattr(c, attr, 0.0)
        if not isinstance(val, (int, float)) or val <= 0:
            setattr(c, attr, 6.0)
    # Ensure max > min
    if c.max_trace_width_mm <= c.min_trace_width_mm:
        c.max_trace_width_mm = max(c.min_trace_width_mm * 20, 6.0)
    if c.max_via_diameter_mm <= c.min_via_diameter_mm:
        c.max_via_diameter_mm = max(c.min_via_diameter_mm * 10, 6.0)
    if c.max_via_drill_mm <= c.min_via_drill_mm:
        c.max_via_drill_mm = max(c.min_via_drill_mm * 10, 6.0)
    return c


# ---------------------------------------------------------------------------
# URL / vendor name helpers
# ---------------------------------------------------------------------------
def _derive_vendor_name(url: str) -> str:
    """Extract a readable vendor name from a URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        # Remove www. and common TLDs
        domain = re.sub(r"^www\.", "", domain)
        # Take the main domain part
        parts = domain.split(".")
        if len(parts) >= 2:
            name = parts[-2]  # e.g., "jlcpcb" from "jlcpcb.com"
        else:
            name = parts[0] if parts else "Custom"
        # Capitalise
        return name.upper() if len(name) <= 6 else name.capitalize()
    except Exception:
        return "Custom"


def _derive_project_name(url: str) -> str:
    """Generate a project name from a vendor URL."""
    vendor = _derive_vendor_name(url)
    return f"{vendor}_Project"


def _validate_url(url: str) -> bool:
    """Validate that a URL is well-formed with http/https scheme."""
    try:
        result = urlparse(url)
        return result.scheme in ("http", "https") and bool(result.hostname)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Custom size compatibility checking
# ---------------------------------------------------------------------------
def check_track_compatibility(width_mm: float, c: PCBConstraints) -> tuple[bool, str]:
    """Check if a custom track width is compatible with vendor constraints.

    Returns (is_compatible, reason_string).
    """
    if c is None:
        return False, "No constraints loaded"
    if width_mm <= 0:
        return False, "Width must be > 0"
    if width_mm < c.min_trace_width_mm:
        return False, f"Below min ({c.min_trace_width_mm:.3f} mm)"
    if width_mm > c.max_trace_width_mm:
        return False, f"Above max ({c.max_trace_width_mm:.3f} mm)"
    return True, "Compatible"


def check_via_compatibility(dia_mm: float, drill_mm: float,
                            c: PCBConstraints) -> tuple[bool, str]:
    """Check if a custom via size is compatible with vendor constraints.

    Returns (is_compatible, reason_string).
    """
    if c is None:
        return False, "No constraints loaded"
    if dia_mm <= 0 or drill_mm <= 0:
        return False, "Values must be > 0"
    if drill_mm >= dia_mm:
        return False, "Drill must be < diameter"
    if dia_mm < c.min_via_diameter_mm:
        return False, f"Dia below min ({c.min_via_diameter_mm:.3f} mm)"
    if dia_mm > c.max_via_diameter_mm:
        return False, f"Dia above max ({c.max_via_diameter_mm:.3f} mm)"
    if drill_mm < c.min_via_drill_mm:
        return False, f"Drill below min ({c.min_via_drill_mm:.3f} mm)"
    if drill_mm > c.max_via_drill_mm:
        return False, f"Drill above max ({c.max_via_drill_mm:.3f} mm)"
    annular_ring = (dia_mm - drill_mm) / 2
    if annular_ring < c.min_annular_ring_mm:
        return False, f"AR {annular_ring:.3f} < min {c.min_annular_ring_mm:.3f} mm"
    return True, f"Compatible (AR: {annular_ring:.3f} mm)"


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

def _raise_for_status_with_detail(resp: requests.Response) -> None:
    """Raise error with API's detailed error message if present."""
    if not resp.ok:
        try:
            err_data = resp.json()
            err_obj = err_data.get("error", {})
            msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
            if msg:
                raise RuntimeError(f"[{resp.status_code}] {msg}")
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        resp.raise_for_status()


def fetch_models_google(api_key: str) -> list[str]:
    """Fetch available Gemini models via the REST API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}&pageSize=100"
    resp = requests.get(url, timeout=12)
    _raise_for_status_with_detail(resp)
    data = resp.json()
    models = []
    for m in data.get("models", []):
        name = m.get("name", "")
        # Strip the "models/" prefix
        if name.startswith("models/"):
            name = name[len("models/"):]
        # Only include models that support generateContent
        raw_supported = m.get("supportedGenerationMethods", [])
        supported = []
        for item in raw_supported:
            if isinstance(item, str):
                supported.append(item)
            elif isinstance(item, dict):
                supported.append(item.get("name", ""))
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
    _raise_for_status_with_detail(resp)
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
    _raise_for_status_with_detail(resp)
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

    Extract the PCB design constraints as numeric values in millimeters.
    Include BOTH minimum AND maximum capabilities where available.
    If a value is given in mils or inches, convert to mm (1 mil = 0.0254 mm, 1 inch = 25.4 mm).
    Return ONLY valid JSON matching this exact schema:
    {{
      "min_trace_width_mm":   <float>,
      "min_clearance_mm":     <float>,
      "min_via_diameter_mm":  <float>,
      "min_via_drill_mm":     <float>,
      "min_hole_diameter_mm": <float>,
      "min_annular_ring_mm":  <float>,
      "max_trace_width_mm":   <float>,
      "max_via_diameter_mm":  <float>,
      "max_via_drill_mm":     <float>,
      "max_hole_diameter_mm": <float>,
      "vendor_name":          "<string>",
      "source_url":           "<string>",
      "notes":                "<string>"
    }}
    Use conservative (larger) defaults when data is ambiguous or missing.
    For maximum values, use the largest values the vendor supports.
    If max values are not stated, use reasonable industry defaults
    (e.g., max trace width 6mm, max via diameter 6mm, max drill 6.35mm).

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
    data = _parse_json_response(response.text)
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

APP_NAME = "KiCad Constraint Configurator"
APP_VERSION = "2.1.1"


# ---------------------------------------------------------------------------
# Preset Generation (10 tiers per category)
# ---------------------------------------------------------------------------

def _linspace(start: float, end: float, n: int) -> list[float]:
    """Generate n evenly-spaced floats from start to end (inclusive)."""
    if n <= 1:
        return [start]
    step = (end - start) / (n - 1)
    return [round(start + i * step, 4) for i in range(n)]


def generate_signal_trace_presets(c: PCBConstraints) -> list[dict]:
    """Generate 10 signal trace width presets from min to practical max."""
    mn = max(c.min_trace_width_mm, 0.05)
    mx = max(min(max(c.max_trace_width_mm, mn * 5), 1.0), mn * 2)
    widths = _linspace(mn, mx, 10)
    names = [
        "Ultra Fine", "Extra Fine", "Fine", "Narrow", "Standard",
        "Medium", "Wide", "Heavy", "Extra Heavy", "Maximum",
    ]
    return [
        {"name": f"Signal {names[i]}", "track_width": w, "category": "signal"}
        for i, w in enumerate(widths)
    ]


def generate_power_trace_presets(c: PCBConstraints) -> list[dict]:
    """Generate 10 power trace width presets from 2× min to max."""
    mn = max(c.min_trace_width_mm * 2, 0.2)
    mx = max(min(c.max_trace_width_mm, 6.0), mn * 2)
    widths = _linspace(mn, mx, 10)
    names = [
        "Minimum", "Low", "Light", "Medium", "Standard",
        "High", "Heavy", "Extra Heavy", "Ultra Heavy", "Maximum",
    ]
    return [
        {"name": f"Power {names[i]}", "track_width": w, "category": "power"}
        for i, w in enumerate(widths)
    ]


def generate_diff_pair_presets(c: PCBConstraints) -> list[dict]:
    """Generate 10 differential pair presets (width + gap) from min to practical max."""
    mn_w = max(c.min_trace_width_mm, 0.05)
    mx_w = max(min(max(c.max_trace_width_mm, mn_w * 5), 0.8), mn_w * 2)
    widths = _linspace(mn_w, mx_w, 10)
    mn_gap = max(c.min_clearance_mm, 0.05)
    mx_gap = max(min(mn_gap * 5, 0.5), mn_gap * 1.5)
    gaps = _linspace(mn_gap, mx_gap, 10)
    names = [
        "Ultra Fine", "Extra Fine", "Fine", "Narrow", "Standard",
        "Medium", "Wide", "Heavy", "Extra Heavy", "Maximum",
    ]
    return [
        {
            "name": f"Diff {names[i]}",
            "diff_width": w,
            "diff_gap": g,
            "category": "diff_pair",
        }
        for i, (w, g) in enumerate(zip(widths, gaps))
    ]


def generate_via_presets(c: PCBConstraints) -> list[dict]:
    """Generate 10 via size presets ensuring annular ring compliance.
    First 3 configurations match min AR closely.
    Matches against standard via sizes (diameter, drill).
    """
    min_ar = max(c.min_annular_ring_mm, 0.05)
    mn_dr = max(c.min_via_drill_mm, 0.1)
    mx_dr = max(min(c.max_via_drill_mm, 6.0), mn_dr * 2)
    mx_d = max(min(c.max_via_diameter_mm, 6.0), min_ar * 2 + mn_dr * 2)

    STANDARD_VIAS = [
        (0.4, 0.2), (0.45, 0.2), (0.5, 0.2), (0.5, 0.25), (0.6, 0.25), (0.6, 0.3),
        (0.7, 0.3), (0.8, 0.4), (0.9, 0.4), (1.0, 0.5), (1.2, 0.6), (1.4, 0.7),
        (1.6, 0.8), (2.0, 1.0), (2.5, 1.2), (3.0, 1.5)
    ]

    names = [
        "Micro", "Ultra Fine", "Fine", "Small", "Standard",
        "Medium", "Large", "Heavy", "Extra Heavy", "Maximum",
    ]

    presets = []
    
    for i in range(10):
        if i < 3:
            target_dr = mn_dr + i * 0.05
        else:
            target_dr = mn_dr + 0.15 + (i - 2) * ((mx_dr - mn_dr) / 8)
        
        target_dr = min(max(target_dr, mn_dr), mx_dr)
        target_ar = min_ar if i < 3 else min_ar + (i - 2) * 0.05
        target_dia = min(target_dr + 2 * target_ar, mx_d)
        
        best_std = None
        best_score = float('inf')
        for (sd, sdr) in STANDARD_VIAS:
            ar = (sd - sdr) / 2
            if ar >= min_ar and sdr >= mn_dr and sd <= mx_d and sdr <= mx_dr:
                score = abs(sdr - target_dr) * 2 + abs(sd - target_dia)
                if score < best_score:
                    best_score = score
                    best_std = (sd, sdr)
        
        if best_std and best_score < 0.3:
            d, dr = best_std
        else:
            d = round(target_dia, 4)
            dr = round(target_dr, 4)
            if (d - dr) / 2 < min_ar:
                d = round(dr + 2 * min_ar, 4)

        presets.append({
            "name": f"Via {names[i]}",
            "via_dia": d,
            "via_drill": dr,
            "annular_ring": round((d - dr) / 2, 4),
            "category": "via",
        })
    
    return presets


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
                     selected_signals: list[dict] | None = None,
                     selected_power: list[dict] | None = None,
                     selected_diff: list[dict] | None = None,
                     selected_vias: list[dict] | None = None) -> None:
    """Patch a .kicad_pro file (JSON) with extracted constraints, net classes,
    user-selected track/via presets, and schematic sheet links."""
    with open(pro_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    project_name = pro_path.stem

    design = data.setdefault("board", {}).setdefault("design_settings", {})
    rules = design.setdefault("rules", {})
    rules["min_clearance"]          = constraints.min_clearance_mm
    rules["min_track_width"]        = constraints.min_trace_width_mm
    rules["min_via_diameter"]       = constraints.min_via_diameter_mm
    rules["min_via_annular_width"]  = constraints.min_annular_ring_mm
    rules["min_through_hole_diameter"] = constraints.min_hole_diameter_mm
    rules["min_hole_clearance"]     = constraints.min_clearance_mm
    rules["min_hole_to_hole"]       = constraints.min_hole_diameter_mm
    rules["min_copper_edge_clearance"] = max(constraints.min_clearance_mm, 0.3048)

    # Build net classes
    default_nc   = _build_net_class("Default",           constraints, "rgba(0, 0, 0, 0.000)", multiplier=1.0)
    signal_nc    = _build_net_class("Signal",            constraints, SIGNAL_COLOR,            multiplier=SIGNAL_MULTIPLIER)
    power_nc     = _build_net_class("Power",             constraints, POWER_COLOR,             multiplier=POWER_MULTIPLIER)
    diff_pair_nc = _build_net_class("Differential_Pair", constraints, DIFF_PAIR_COLOR,         multiplier=1.0, diff_pair=True)

    net_settings = data.setdefault("net_settings", {})
    net_settings["classes"]          = [default_nc, signal_nc, power_nc, diff_pair_nc]
    net_settings["netclass_patterns"] = copy.deepcopy(NETCLASS_PATTERNS)

    # ── Inject selected track widths ──────────────────────────────────
    track_widths_set = set()
    if selected_signals:
        for p in selected_signals:
            track_widths_set.add(p["track_width"])
    if selected_power:
        for p in selected_power:
            track_widths_set.add(p["track_width"])

    if track_widths_set:
        design["track_widths"] = [0.0] + sorted(track_widths_set)
    else:
        # Use sensible defaults from constraints
        design["track_widths"] = [0.0, constraints.min_trace_width_mm]

    # ── Inject selected via dimensions ────────────────────────────────
    if selected_vias:
        via_dims = sorted(
            {(p["via_dia"], p["via_drill"]) for p in selected_vias},
            key=lambda t: t[0],
        )
        design["via_dimensions"] = [
            {"diameter": 0.0, "drill": 0.0},  # sentinel entry
        ] + [
            {"diameter": d, "drill": dr} for d, dr in via_dims
        ]
    else:
        design["via_dimensions"] = [
            {"diameter": 0.0, "drill": 0.0},
            {"diameter": constraints.min_via_diameter_mm, "drill": constraints.min_via_drill_mm},
        ]

    # ── Inject diff pair dimensions ───────────────────────────────────
    if selected_diff:
        diff_dims = sorted(
            {(p["diff_width"], p["diff_gap"]) for p in selected_diff},
            key=lambda t: t[0],
        )
        design["diff_pair_dimensions"] = [
            {"gap": 0.0, "via_gap": 0.0, "width": 0.0},  # sentinel
        ] + [
            {"gap": g, "via_gap": g, "width": w} for w, g in diff_dims
        ]
    else:
        design["diff_pair_dimensions"] = [
            {"gap": 0.0, "via_gap": 0.0, "width": 0.0},
        ]

    # ── Update schematic sheet linking ───────────────────────────────
    schematic = data.setdefault("schematic", {})
    root_uuid = "00000000-0000-0000-0000-000000000001"
    schematic["top_level_sheets"] = [
        {
            "filename": f"{project_name}.kicad_sch",
            "name": project_name,
            "uuid": root_uuid
        }
    ]
    data["sheets"] = [
        [
            root_uuid,
            project_name
        ]
    ]

    # ── Update filename in meta ───────────────────────────────────────
    data["meta"]["filename"] = pro_path.name

    with open(pro_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def inject_kicad_pcb(pcb_path: Path, constraints: PCBConstraints) -> None:
    """Patch a .kicad_pcb file (S-expression) with extracted constraints via regex.

    The KiCad 9/10 template has these values inside the (setup ...) block.
    We do NOT touch copper_finish (which is correctly inside the stackup block only).
    """
    with open(pcb_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Only replace fields that exist in the KiCad 9/10 setup block
    # Note: In KiCad 9+, many of these fields are in the .kicad_pro, not .kicad_pcb
    # The PCB file is more of a board representation than a settings store

    # Validate: ensure no duplicate copper_finish outside stackup
    # Count occurrences — should be exactly 1 (inside stackup)
    cf_count = len(re.findall(r'\(copper_finish\s', content))
    if cf_count > 1:
        # Remove any copper_finish that appears outside the stackup block
        # The correct one is inside (stackup ... (copper_finish ...) ...)
        # Find the stackup block end
        lines = content.split('\n')
        in_stackup = False
        stackup_depth = 0
        fixed_lines = []
        copper_finish_seen_in_stackup = False

        for line in lines:
            stripped = line.strip()
            if '(stackup' in stripped:
                in_stackup = True
                stackup_depth = 0
            if in_stackup:
                stackup_depth += stripped.count('(') - stripped.count(')')
                if '(copper_finish' in stripped:
                    copper_finish_seen_in_stackup = True
                if stackup_depth <= 0:
                    in_stackup = False
                fixed_lines.append(line)
            elif '(copper_finish' in stripped and copper_finish_seen_in_stackup:
                # Skip duplicate copper_finish outside stackup
                continue
            else:
                fixed_lines.append(line)
        content = '\n'.join(fixed_lines)

    with open(pcb_path, "w", encoding="utf-8") as f:
        f.write(content)


def run_injection(
    constraints: PCBConstraints,
    output_dir: Path,
    template_dir: Path,
    project_name: str,
    log_callback,
    selected_signals: list[dict] | None = None,
    selected_power: list[dict] | None = None,
    selected_diff: list[dict] | None = None,
    selected_vias: list[dict] | None = None,
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
    inject_kicad_pro(pro_path, constraints, selected_signals, selected_power,
                     selected_diff, selected_vias)

    preset_count = sum(len(s) for s in [selected_signals or [], selected_power or [],
                                         selected_diff or [], selected_vias or []])
    log_callback(f"  ✅ .kicad_pro updated (design rules + net classes + {preset_count} presets)")

    pcb_path = dest / f"{project_name}.kicad_pcb"
    log_callback("⚙️  Validating .kicad_pcb …")
    inject_kicad_pcb(pcb_path, constraints)
    log_callback("  ✅ .kicad_pcb validated (no duplicate fields)")

    # ── Post-injection verification ───────────────────────────────────
    log_callback("🔍  Running post-injection verification …")
    _verify_output(pro_path, pcb_path, log_callback)

    return dest


def _verify_output(pro_path: Path, pcb_path: Path, log_callback) -> None:
    """Verify the generated files are valid."""
    # Verify .kicad_pro is valid JSON
    try:
        with open(pro_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Check essential keys exist
        assert "board" in data, "Missing 'board' key"
        assert "net_settings" in data, "Missing 'net_settings' key"
        assert "meta" in data, "Missing 'meta' key"
        design = data["board"]["design_settings"]
        assert "rules" in design, "Missing 'rules' in design_settings"
        assert "track_widths" in design, "Missing 'track_widths'"
        assert "via_dimensions" in design, "Missing 'via_dimensions'"

        # Verify annular ring compliance in via dimensions
        min_ar = data["board"]["design_settings"]["rules"].get("min_via_annular_width", 0)
        via_dims = design.get("via_dimensions", [])
        for vd in via_dims:
            d = vd.get("diameter", 0)
            dr = vd.get("drill", 0)
            if d > 0 and dr > 0:
                ar = (d - dr) / 2
                if ar < min_ar - 0.001:  # small tolerance for float rounding
                    log_callback(
                        f"  ⚠️ .kicad_pro: Via D={d:.3f} H={dr:.3f} "
                        f"has AR={ar:.3f} < min {min_ar:.3f}"
                    )

        log_callback("  ✅ .kicad_pro: Valid JSON, all required keys present")
    except json.JSONDecodeError as e:
        log_callback(f"  ❌ .kicad_pro: Invalid JSON — {e}")
    except AssertionError as e:
        log_callback(f"  ⚠️ .kicad_pro: Missing key — {e}")

    # Verify .kicad_pcb has balanced parentheses and no duplicate copper_finish
    try:
        with open(pcb_path, "r", encoding="utf-8") as f:
            pcb_content = f.read()
        depth = 0
        for ch in pcb_content:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced parentheses (too many closing)")
        if depth != 0:
            raise ValueError(f"Unbalanced parentheses (depth={depth} at end)")

        cf_count = len(re.findall(r'\(copper_finish\s', pcb_content))
        if cf_count > 1:
            log_callback(f"  ⚠️ .kicad_pcb: Found {cf_count} copper_finish entries (expected 1)")
        else:
            log_callback("  ✅ .kicad_pcb: Balanced parentheses, no duplicate fields")
    except Exception as e:
        log_callback(f"  ❌ .kicad_pcb: Validation error — {e}")


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------

class KiCadConfiguratorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1100x820")
        self.minsize(960, 720)
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
        self._is_alive:    bool                     = True  # Track if window exists

        # Preset data storage (by category)
        self._signal_presets: list[dict] = []
        self._power_presets:  list[dict] = []
        self._diff_presets:   list[dict] = []
        self._via_presets:    list[dict] = []

        # Checkbox vars (by category)
        self._signal_vars: list[ctk.BooleanVar] = []
        self._power_vars:  list[ctk.BooleanVar] = []
        self._diff_vars:   list[ctk.BooleanVar] = []
        self._via_vars:    list[ctk.BooleanVar] = []

        # URL-to-project-name tracking (for dynamic updates)
        self._last_url_for_name: str = ""
        self._url_trace_active: bool = False  # Prevent recursion

        # Custom sizes storage
        self._custom_tracks: list[float] = self._config.get("custom_tracks", [])
        self._custom_vias: list[list[float]] = self._config.get("custom_vias", [])
        # Widget references for custom sizes (rebuilt on changes)
        self._custom_track_widgets: list = []
        self._custom_via_widgets: list = []

        # Starred AI models
        self._starred_models: list[str] = self._config.get("starred_models", [])

        self._build_ui()
        self._restore_config()
        self._bind_shortcuts()

        # Handle window close gracefully
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        """Handle graceful window close."""
        self._is_alive = False
        self.destroy()

    def _safe_after(self, ms: int, func) -> None:
        """Thread-safe self.after() that checks if the window is still alive."""
        if self._is_alive:
            try:
                self.after(ms, func)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Keyboard Shortcuts
    # ------------------------------------------------------------------

    def _bind_shortcuts(self) -> None:
        """Bind keyboard shortcuts for common actions."""
        self.bind("<Control-s>", lambda e: self._save_api_key())
        self.bind("<Control-e>", lambda e: self._start_scrape())
        self.bind("<Control-i>", lambda e: self._inject_constraints())
        self.bind("<Control-l>", lambda e: self._export_log())

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

        # Shortcut hints on the right
        ctk.CTkLabel(
            title_frame,
            text="Ctrl+E Extract  |  Ctrl+I Inject  |  Ctrl+L Export Log",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=CLR_BORDER,
        ).pack(side="right", padx=24, pady=12)

        # ── Main content area (grid layout for responsive resizing) ───
        content = ctk.CTkFrame(self, fg_color=CLR_BG)
        content.pack(fill="both", expand=True, padx=16, pady=12)
        content.columnconfigure(0, weight=1, minsize=360)
        content.columnconfigure(1, weight=2, minsize=480)
        content.rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            content, fg_color=CLR_PANEL, corner_radius=12
        )
        left.grid(row=0, column=0, padx=(0, 8), sticky="nsew")

        right = ctk.CTkFrame(content, fg_color=CLR_PANEL, corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew")

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

        # Set up trace for dynamic project name updates
        self._url_var.trace_add("write", self._on_url_change)

        quick_frame = ctk.CTkFrame(parent, fg_color="transparent")
        quick_frame.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            quick_frame, text="Quick fill:",
            font=ctk.CTkFont(family="Segoe UI", size=11), text_color=CLR_SUBTEXT,
        ).pack(side="left", padx=(0, 6))
        for label, url, vendor_name in VENDOR_QUICK_FILLS:
            ctk.CTkButton(
                quick_frame, text=label, width=60,
                fg_color=CLR_BORDER, hover_color=CLR_BORDER,
                border_width=1, border_color=CLR_ACCENT,
                text_color=CLR_ACCENT2,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                command=lambda u=url, v=vendor_name: self._quick_fill_vendor(u, v),
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
            command=self._on_main_tab_changed,
        )
        self._tabs.pack(fill="both", expand=True, padx=12, pady=12)

        self._tabs.add("📊 Results")
        self._tabs.add("📐 Presets")
        self._tabs.add("⚙️ Custom")
        self._tabs.add("📋 Log")
        self._tabs.add("ℹ️ About")

        self._build_results_tab(self._tabs.tab("📊 Results"))
        self._build_presets_tab(self._tabs.tab("📐 Presets"))
        self._build_custom_sizes_tab(self._tabs.tab("⚙️ Custom"))
        self._build_log_tab(self._tabs.tab("📋 Log"))
        self._build_about_tab(self._tabs.tab("ℹ️ About"))

    def _on_main_tab_changed(self) -> None:
        """When the user clicks another tab, refresh the model list if needed so starred models rise to top."""
        if self._models_list:
            self._clear_model_list()
            self._populate_model_list(self._models_list)

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
        """Build the '📐 Presets' tab with column-based layout."""
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
            text="⚡  Trace, Via & Net Class Configuration",
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

        # ── Tabview container ──────────────────────────────────────────
        self._presets_tabview = ctk.CTkTabview(self._presets_content, fg_color="transparent")
        self._presets_tabview.pack(fill="both", expand=True, padx=4, pady=4)

        # Add tabs
        self._presets_tabview.add("Signal Traces")
        self._presets_tabview.add("Power Traces")
        self._presets_tabview.add("Diff Pairs")
        self._presets_tabview.add("Vias")

        # Build four columns
        self._signal_col_frame = self._build_preset_column(
            self._presets_tabview.tab("Signal Traces"), "Signal Traces", "📏", CLR_SIGNAL_COL,
            "Track widths for signal routing"
        )
        self._power_col_frame = self._build_preset_column(
            self._presets_tabview.tab("Power Traces"), "Power Traces", "⚡", CLR_POWER_COL,
            "Track widths for power delivery"
        )
        self._diff_col_frame = self._build_preset_column(
            self._presets_tabview.tab("Diff Pairs"), "Diff Pairs", "↔️", CLR_DIFF_COL,
            "Differential pair width / gap"
        )
        self._via_col_frame = self._build_preset_column(
            self._presets_tabview.tab("Vias"), "Vias", "⭕", CLR_VIA_COL,
            "Via diameter / drill / AR"
        )

        # ── Constraint summary footer ──────────────────────────────────
        self._constraint_summary_frame = ctk.CTkFrame(
            self._presets_content, fg_color=CLR_BG, corner_radius=8,
            border_width=1, border_color=CLR_BORDER,
        )
        self._constraint_summary_frame.pack(fill="x", padx=8, pady=(8, 4))

        self._constraint_summary_label = ctk.CTkLabel(
            self._constraint_summary_frame,
            text="📋  Constraint limits will appear here after extraction.",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=CLR_SUBTEXT, anchor="w", justify="left",
        )
        self._constraint_summary_label.pack(padx=12, pady=8, anchor="w")

        # ── Info footer ────────────────────────────────────────────────
        info_frame = ctk.CTkFrame(
            self._presets_content, fg_color=CLR_PANEL, corner_radius=8,
            border_width=1, border_color=CLR_BORDER,
        )
        info_frame.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(
            info_frame,
            text="ℹ️  Each column has 10 configurations from minimum to maximum vendor capability.\n"
                 "     Via configs are annular ring–verified. Selected items are injected into your KiCad project.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT, anchor="w", justify="left",
        ).pack(padx=12, pady=8, anchor="w")

    def _build_preset_column(self, parent, title: str, icon: str,
                              accent_color: str, subtitle: str) -> ctk.CTkFrame:
        """Build a single column for the preset tab. Returns the scrollable inner frame."""
        outer = ctk.CTkFrame(
            parent, fg_color=CLR_CARD, corner_radius=10,
            border_width=1, border_color=CLR_BORDER,
        )
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        # Column header
        header = ctk.CTkFrame(outer, fg_color=accent_color, corner_radius=8, height=60)
        header.pack(fill="x", padx=4, pady=4)
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text=f"{icon}  {title}",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#ffffff",
        ).pack(padx=12, pady=(8, 0), anchor="w")

        ctk.CTkLabel(
            header,
            text=subtitle,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=CLR_SUBTEXT,
        ).pack(padx=12, pady=(0, 4), anchor="w")

        # Select All / Deselect All buttons row
        sel_row = ctk.CTkFrame(outer, fg_color="transparent")
        sel_row.pack(fill="x", padx=6, pady=(4, 0))

        ctk.CTkButton(
            sel_row, text="All", width=40, height=22,
            fg_color=CLR_BORDER, hover_color=CLR_ACCENT,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=9),
            command=lambda o=outer: self._select_all_in_column(o, True),
        ).pack(side="left", padx=(0, 2))

        ctk.CTkButton(
            sel_row, text="None", width=40, height=22,
            fg_color=CLR_BORDER, hover_color=CLR_ACCENT,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=9),
            command=lambda o=outer: self._select_all_in_column(o, False),
        ).pack(side="left", padx=(0, 2))

        # Range label
        range_label = ctk.CTkLabel(
            outer, text="—",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=CLR_SUBTEXT,
        )
        range_label.pack(padx=8, pady=(4, 2), anchor="w")
        # Store as an attribute so we can update it later
        outer._range_label = range_label  # type: ignore[attr-defined]

        # Scrollable items area
        items_frame = ctk.CTkScrollableFrame(
            outer, fg_color="transparent", height=380,
        )
        items_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        return items_frame

    def _select_all_in_column(self, outer_frame: ctk.CTkFrame, select: bool) -> None:
        """Select or deselect all checkboxes in a preset column."""
        # Determine which vars list belongs to this column
        items_frame = None
        for child in outer_frame.winfo_children():
            if isinstance(child, ctk.CTkScrollableFrame):
                items_frame = child
                break
        if items_frame is None:
            return

        # Match column frame to vars list
        var_lists = [
            (self._signal_col_frame, self._signal_vars),
            (self._power_col_frame,  self._power_vars),
            (self._diff_col_frame,   self._diff_vars),
            (self._via_col_frame,    self._via_vars),
        ]
        for frame, vars_list in var_lists:
            if frame is items_frame:
                for var in vars_list:
                    var.set(select)
                return

    # ------------------------------------------------------------------
    # Custom Sizes Tab
    # ------------------------------------------------------------------

    def _build_custom_sizes_tab(self, parent) -> None:
        """Build the '⚙️ Custom' tab for entering custom track/via sizes."""
        outer = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        ctk.CTkLabel(
            outer,
            text="⚙️  Custom Track & Via Sizes",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=CLR_TEXT,
        ).pack(padx=16, pady=(16, 4), anchor="w")

        ctk.CTkLabel(
            outer,
            text="Add custom sizes and check compatibility with the loaded manufacturer constraints.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT,
        ).pack(padx=16, pady=(0, 12), anchor="w")

        # ── Custom Track Width Section ──────────────────────────────────
        track_section = ctk.CTkFrame(outer, fg_color=CLR_CARD, corner_radius=10,
                                     border_width=1, border_color=CLR_BORDER)
        track_section.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkLabel(
            track_section,
            text="📏  Custom Track Widths",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=CLR_SIGNAL_COL,
        ).pack(padx=16, pady=(12, 4), anchor="w")

        track_input_row = ctk.CTkFrame(track_section, fg_color="transparent")
        track_input_row.pack(fill="x", padx=16, pady=(4, 4))

        ctk.CTkLabel(
            track_input_row, text="Width (mm):",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_TEXT,
        ).pack(side="left", padx=(0, 6))

        self._custom_track_entry = ctk.CTkEntry(
            track_input_row, width=100,
            placeholder_text="e.g. 0.25",
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        )
        self._custom_track_entry.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            track_input_row, text="+ Add Track", width=100,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._add_custom_track,
        ).pack(side="left", padx=(0, 8))

        self._custom_track_status = ctk.CTkLabel(
            track_input_row, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT,
        )
        self._custom_track_status.pack(side="left")

        # Track list container
        self._custom_track_list_frame = ctk.CTkFrame(
            track_section, fg_color="transparent"
        )
        self._custom_track_list_frame.pack(fill="x", padx=16, pady=(4, 12))

        # ── Custom Via Size Section ─────────────────────────────────────
        via_section = ctk.CTkFrame(outer, fg_color=CLR_CARD, corner_radius=10,
                                   border_width=1, border_color=CLR_BORDER)
        via_section.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkLabel(
            via_section,
            text="⭕  Custom Via Sizes",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=CLR_VIA_COL,
        ).pack(padx=16, pady=(12, 4), anchor="w")

        via_input_row1 = ctk.CTkFrame(via_section, fg_color="transparent")
        via_input_row1.pack(fill="x", padx=16, pady=(4, 2))

        ctk.CTkLabel(
            via_input_row1, text="Diameter (mm):",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_TEXT,
        ).pack(side="left", padx=(0, 6))

        self._custom_via_dia_entry = ctk.CTkEntry(
            via_input_row1, width=90,
            placeholder_text="e.g. 0.6",
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        )
        self._custom_via_dia_entry.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(
            via_input_row1, text="Drill (mm):",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_TEXT,
        ).pack(side="left", padx=(0, 6))

        self._custom_via_drill_entry = ctk.CTkEntry(
            via_input_row1, width=90,
            placeholder_text="e.g. 0.3",
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=CLR_BG, border_color=CLR_BORDER, text_color=CLR_TEXT,
        )
        self._custom_via_drill_entry.pack(side="left")

        via_input_row2 = ctk.CTkFrame(via_section, fg_color="transparent")
        via_input_row2.pack(fill="x", padx=16, pady=(2, 4))

        self._custom_via_ar_label = ctk.CTkLabel(
            via_input_row2, text="Annular Ring: —",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=CLR_SUBTEXT,
        )
        self._custom_via_ar_label.pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            via_input_row2, text="+ Add Via", width=100,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._add_custom_via,
        ).pack(side="left", padx=(0, 8))

        self._custom_via_status = ctk.CTkLabel(
            via_input_row2, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT,
        )
        self._custom_via_status.pack(side="left")

        # Live annular ring calculation as user types
        self._custom_via_dia_entry.bind("<KeyRelease>", self._update_via_ar_preview)
        self._custom_via_drill_entry.bind("<KeyRelease>", self._update_via_ar_preview)

        # Via list container
        self._custom_via_list_frame = ctk.CTkFrame(
            via_section, fg_color="transparent"
        )
        self._custom_via_list_frame.pack(fill="x", padx=16, pady=(4, 12))

        # ── Info ────────────────────────────────────────────────────────
        info_frame = ctk.CTkFrame(outer, fg_color=CLR_PANEL, corner_radius=8,
                                  border_width=1, border_color=CLR_BORDER)
        info_frame.pack(fill="x", padx=12, pady=(4, 12))
        ctk.CTkLabel(
            info_frame,
            text="ℹ️  Compatible custom sizes are automatically included during injection.\n"
                 "     Incompatible entries are shown with ❌ and will be skipped.\n"
                 "     Custom sizes are saved and persist across sessions.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=CLR_SUBTEXT, anchor="w", justify="left",
        ).pack(padx=12, pady=8, anchor="w")

        # Render any previously saved custom sizes
        self._render_custom_tracks()
        self._render_custom_vias()

    def _update_via_ar_preview(self, event=None) -> None:
        """Live-update the annular ring display as user types diameter/drill."""
        try:
            dia = float(self._custom_via_dia_entry.get().strip())
            drill = float(self._custom_via_drill_entry.get().strip())
            if drill >= dia or dia <= 0 or drill <= 0:
                self._custom_via_ar_label.configure(
                    text="Annular Ring: ⚠️ Invalid", text_color=CLR_ERROR
                )
                return
            ar = (dia - drill) / 2
            # Check against constraints if available
            with self._constraints_lock:
                c = self._constraints
            if c and ar < c.min_annular_ring_mm:
                self._custom_via_ar_label.configure(
                    text=f"Annular Ring: {ar:.3f} mm ❌ (min: {c.min_annular_ring_mm:.3f})",
                    text_color=CLR_ERROR,
                )
            elif c:
                self._custom_via_ar_label.configure(
                    text=f"Annular Ring: {ar:.3f} mm ✅",
                    text_color=CLR_SUCCESS,
                )
            else:
                self._custom_via_ar_label.configure(
                    text=f"Annular Ring: {ar:.3f} mm (no constraints loaded)",
                    text_color=CLR_WARNING,
                )
        except (ValueError, TypeError):
            self._custom_via_ar_label.configure(
                text="Annular Ring: —", text_color=CLR_SUBTEXT
            )

    def _add_custom_track(self) -> None:
        """Add a custom track width from the entry field."""
        try:
            width = float(self._custom_track_entry.get().strip())
        except (ValueError, TypeError):
            self._custom_track_status.configure(
                text="⚠️ Enter a valid number", text_color=CLR_WARNING
            )
            return
        if width <= 0:
            self._custom_track_status.configure(
                text="⚠️ Width must be > 0", text_color=CLR_WARNING
            )
            return
        # Check for duplicates
        if width in self._custom_tracks:
            self._custom_track_status.configure(
                text="⚠️ Already added", text_color=CLR_WARNING
            )
            return
        self._custom_tracks.append(width)
        self._save_custom_sizes()
        self._render_custom_tracks()
        
        # Inject into preset tabs if constraints are loaded
        if self._constraints:
            self._signal_presets.append({"name": f"Custom {width}mm", "track_width": width, "category": "signal", "is_manual": True})
            self._power_presets.append({"name": f"Custom {width}mm", "track_width": width, "category": "power", "is_manual": True})
            
            # Re-render keeping existing selections
            sig_sel = {i for i, var in enumerate(self._signal_vars) if var.get()} | {len(self._signal_presets)-1}
            pwr_sel = {i for i, var in enumerate(self._power_vars) if var.get()} | {len(self._power_presets)-1}
            
            self._signal_vars = self._render_column(
                self._signal_col_frame, self._signal_presets,
                value_fmt=lambda p: f"{p['track_width']:.3f} mm",
                default_indices=sig_sel,
            )
            self._power_vars = self._render_column(
                self._power_col_frame, self._power_presets,
                value_fmt=lambda p: f"{p['track_width']:.3f} mm",
                default_indices=pwr_sel,
            )
            
        self._custom_track_entry.delete(0, "end")
        self._custom_track_status.configure(
            text=f"✅ Added {width:.3f} mm", text_color=CLR_SUCCESS
        )

    def _remove_custom_track(self, width: float) -> None:
        """Remove a custom track width."""
        if width in self._custom_tracks:
            self._custom_tracks.remove(width)
            self._save_custom_sizes()
            self._render_custom_tracks()

    def _render_custom_tracks(self) -> None:
        """Render the list of custom track widths with compatibility status."""
        for w in self._custom_track_list_frame.winfo_children():
            w.destroy()

        if not self._custom_tracks:
            ctk.CTkLabel(
                self._custom_track_list_frame,
                text="No custom track widths added.",
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=CLR_SUBTEXT,
            ).pack(padx=4, pady=4)
            return

        with self._constraints_lock:
            c = self._constraints

        for width in sorted(self._custom_tracks):
            row = ctk.CTkFrame(self._custom_track_list_frame, fg_color=CLR_BG,
                               corner_radius=6)
            row.pack(fill="x", pady=2)

            # Value
            ctk.CTkLabel(
                row, text=f"{width:.4f} mm",
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=CLR_ACCENT2,
            ).pack(side="left", padx=8, pady=4)

            # Compatibility status
            if c:
                ok, reason = check_track_compatibility(width, c)
                status_text = f"✅ {reason}" if ok else f"❌ {reason}"
                status_color = CLR_SUCCESS if ok else CLR_ERROR
            else:
                status_text = "— No constraints"
                status_color = CLR_SUBTEXT

            ctk.CTkLabel(
                row, text=status_text,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=status_color,
            ).pack(side="left", padx=4, pady=4)

            # Delete button
            ctk.CTkButton(
                row, text="✕", width=28, height=24,
                fg_color=CLR_BORDER, hover_color=CLR_ERROR,
                text_color=CLR_TEXT,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                command=lambda w=width: self._remove_custom_track(w),
            ).pack(side="right", padx=4, pady=2)

    def _add_custom_via(self) -> None:
        """Add a custom via size from the entry fields."""
        try:
            dia = float(self._custom_via_dia_entry.get().strip())
            drill = float(self._custom_via_drill_entry.get().strip())
        except (ValueError, TypeError):
            self._custom_via_status.configure(
                text="⚠️ Enter valid numbers", text_color=CLR_WARNING
            )
            return
        if dia <= 0 or drill <= 0:
            self._custom_via_status.configure(
                text="⚠️ Values must be > 0", text_color=CLR_WARNING
            )
            return
        if drill >= dia:
            self._custom_via_status.configure(
                text="⚠️ Drill must be < diameter", text_color=CLR_WARNING
            )
            return
        # Check duplicates
        for existing in self._custom_vias:
            if abs(existing[0] - dia) < 0.0001 and abs(existing[1] - drill) < 0.0001:
                self._custom_via_status.configure(
                    text="⚠️ Already added", text_color=CLR_WARNING
                )
                return

        self._custom_vias.append([dia, drill])
        self._save_custom_sizes()
        self._render_custom_vias()
        self._custom_via_dia_entry.delete(0, "end")
        self._custom_via_drill_entry.delete(0, "end")
        ar = (dia - drill) / 2
        self._custom_via_status.configure(
            text=f"✅ Added D:{dia:.3f} H:{drill:.3f} AR:{ar:.3f}",
            text_color=CLR_SUCCESS,
        )
        self._custom_via_ar_label.configure(text="Annular Ring: —", text_color=CLR_SUBTEXT)

    def _remove_custom_via(self, dia: float, drill: float) -> None:
        """Remove a custom via size."""
        self._custom_vias = [
            v for v in self._custom_vias
            if not (abs(v[0] - dia) < 0.0001 and abs(v[1] - drill) < 0.0001)
        ]
        self._save_custom_sizes()
        self._render_custom_vias()

    def _render_custom_vias(self) -> None:
        """Render the list of custom via sizes with compatibility status."""
        for w in self._custom_via_list_frame.winfo_children():
            w.destroy()

        if not self._custom_vias:
            ctk.CTkLabel(
                self._custom_via_list_frame,
                text="No custom via sizes added.",
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=CLR_SUBTEXT,
            ).pack(padx=4, pady=4)
            return

        with self._constraints_lock:
            c = self._constraints

        for via in sorted(self._custom_vias, key=lambda v: v[0]):
            dia, drill = via[0], via[1]
            ar = (dia - drill) / 2
            row = ctk.CTkFrame(self._custom_via_list_frame, fg_color=CLR_BG,
                               corner_radius=6)
            row.pack(fill="x", pady=2)

            # Value
            ctk.CTkLabel(
                row,
                text=f"D:{dia:.3f}  H:{drill:.3f}  AR:{ar:.3f} mm",
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=CLR_ACCENT2,
            ).pack(side="left", padx=8, pady=4)

            # Compatibility status
            if c:
                ok, reason = check_via_compatibility(dia, drill, c)
                status_text = f"✅ {reason}" if ok else f"❌ {reason}"
                status_color = CLR_SUCCESS if ok else CLR_ERROR
            else:
                status_text = "— No constraints"
                status_color = CLR_SUBTEXT

            ctk.CTkLabel(
                row, text=status_text,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=status_color,
            ).pack(side="left", padx=4, pady=4)

            # Delete button
            ctk.CTkButton(
                row, text="✕", width=28, height=24,
                fg_color=CLR_BORDER, hover_color=CLR_ERROR,
                text_color=CLR_TEXT,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                command=lambda d=dia, dr=drill: self._remove_custom_via(d, dr),
            ).pack(side="right", padx=4, pady=2)

    def _save_custom_sizes(self) -> None:
        """Persist custom track/via sizes to config."""
        self._config["custom_tracks"] = self._custom_tracks[:]
        self._config["custom_vias"] = [v[:] for v in self._custom_vias]
        save_config(self._config)

    def _get_compatible_custom_tracks(self) -> list[float]:
        """Return custom track widths that pass compatibility check."""
        with self._constraints_lock:
            c = self._constraints
        if c is None:
            return []
        return [w for w in self._custom_tracks if check_track_compatibility(w, c)[0]]

    def _get_compatible_custom_vias(self) -> list[dict]:
        """Return custom via sizes that pass compatibility check, as preset dicts."""
        with self._constraints_lock:
            c = self._constraints
        if c is None:
            return []
        result = []
        for via in self._custom_vias:
            dia, drill = via[0], via[1]
            ok, _ = check_via_compatibility(dia, drill, c)
            if ok:
                result.append({
                    "name": f"Custom D{dia:.2f}",
                    "via_dia": dia,
                    "via_drill": drill,
                    "annular_ring": round((dia - drill) / 2, 4),
                    "category": "via",
                })
        return result

    # ------------------------------------------------------------------
    # Log Tab
    # ------------------------------------------------------------------

    def _build_log_tab(self, parent) -> None:
        self._log_text = ctk.CTkTextbox(
            parent,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=CLR_BG, text_color=CLR_TEXT,
            wrap="word",
        )
        self._log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self._log_text.configure(state="disabled")

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=(0, 8))

        ctk.CTkButton(
            btn_row, text="📥 Export Log", width=110,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._export_log,
        ).pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            btn_row, text="Clear Log", width=100,
            fg_color="transparent", border_width=1, border_color=CLR_BORDER,
            hover_color=CLR_BORDER, text_color=CLR_SUBTEXT,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._clear_log,
        ).pack(side="right", padx=(0, 4))

    def _build_about_tab(self, parent) -> None:
        about_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        about_frame.pack(fill="both", expand=True)

        about_text = (
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "Automatically extracts PCB manufacturing constraints from vendor\n"
            "capability pages using AI, then injects them directly into your\n"
            "KiCad project files (KiCad 9/10 compatible).\n\n"
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
            "  • AI-powered constraint extraction (min & max)\n"
            "  • Annular ring–aware via configuration ← NEW\n"
            "  • Custom track / via sizes with compatibility check ← NEW\n"
            "  • Dynamic project name from vendor URL ← NEW\n"
            "  • Column-based preset configuration (10 tiers each)\n"
            "  • Signal / Power / Differential / Via categories\n"
            "  • Select All / Deselect All per category ← NEW\n"
            "  • Auto net-class config (Default / Power / Diff_Pair)\n"
            "  • .kicad_pro JSON patching (design rules + net classes)\n"
            "  • .kicad_pcb validation (no duplicate fields)\n"
            "  • KiCad 9/10 format output (version 20260206)\n"
            "  • Post-injection verification\n"
            "  • API keys stored securely in %APPDATA%\n"
            "  • Keyboard shortcuts (Ctrl+E/I/S/L)\n"
            "  • Log export\n\n"
            "Supported PCB Vendors:\n"
            "  • JLCPCB  •  PCBWay  •  OSH Park  •  AllPCB  •  NextPCB\n"
            "  • Any vendor with a capability page\n\n"
            "──────────────────────────────────────\n"
            "GitHub: https://github.com/omkardas22/Kicad_Configurator\n"
        )
        ctk.CTkLabel(
            about_frame, text=about_text,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=CLR_TEXT, justify="left", anchor="nw",
            wraplength=520,
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

        # Constraint grid — minimums
        ctk.CTkLabel(
            self._cards_frame,
            text="Minimum Capabilities",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=CLR_ACCENT2,
        ).pack(padx=8, pady=(8, 4), anchor="w")

        metrics_min = [
            ("Min Trace Width",  f"{c.min_trace_width_mm:.4f} mm",  "📏"),
            ("Min Clearance",    f"{c.min_clearance_mm:.4f} mm",    "↔️"),
            ("Min Via Diameter", f"{c.min_via_diameter_mm:.4f} mm", "⭕"),
            ("Min Via Drill",    f"{c.min_via_drill_mm:.4f} mm",    "🔩"),
            ("Min Hole Dia",     f"{c.min_hole_diameter_mm:.4f} mm","🕳️"),
            ("Min Annular Ring", f"{c.min_annular_ring_mm:.4f} mm", "🔘"),
        ]

        grid_min = ctk.CTkFrame(self._cards_frame, fg_color="transparent")
        grid_min.pack(fill="x")
        grid_min.columnconfigure((0, 1, 2), weight=1)

        for i, (label, value, icon) in enumerate(metrics_min):
            card = ctk.CTkFrame(grid_min, fg_color=CLR_BG, corner_radius=8)
            card.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
            ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=18)).pack(pady=(8, 2))
            ctk.CTkLabel(
                card, text=value,
                font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                text_color=CLR_ACCENT2,
            ).pack()
            ctk.CTkLabel(
                card, text=label,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=CLR_SUBTEXT,
            ).pack(pady=(0, 8))

        # Maximum capabilities
        ctk.CTkLabel(
            self._cards_frame,
            text="Maximum Capabilities",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=CLR_SUCCESS,
        ).pack(padx=8, pady=(12, 4), anchor="w")

        metrics_max = [
            ("Max Trace Width",  f"{c.max_trace_width_mm:.4f} mm",  "📏"),
            ("Max Via Diameter", f"{c.max_via_diameter_mm:.4f} mm", "⭕"),
            ("Max Via Drill",    f"{c.max_via_drill_mm:.4f} mm",    "🔩"),
            ("Max Hole Dia",     f"{c.max_hole_diameter_mm:.4f} mm","🕳️"),
        ]

        grid_max = ctk.CTkFrame(self._cards_frame, fg_color="transparent")
        grid_max.pack(fill="x")
        grid_max.columnconfigure((0, 1, 2, 3), weight=1)

        for i, (label, value, icon) in enumerate(metrics_max):
            card = ctk.CTkFrame(grid_max, fg_color=CLR_BG, corner_radius=8)
            card.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=18)).pack(pady=(8, 2))
            ctk.CTkLabel(
                card, text=value,
                font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                text_color=CLR_SUCCESS,
            ).pack()
            ctk.CTkLabel(
                card, text=label,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color=CLR_SUBTEXT,
            ).pack(pady=(0, 8))

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
                text_color=CLR_SUBTEXT, wraplength=400, justify="left", anchor="nw",
            ).pack(padx=12, pady=(0, 10), anchor="w")

        self._tabs.set("📊 Results")

        # Also populate the presets tab
        self._generate_all_presets(c)
        self._render_all_presets(c)

        # Refresh custom sizes compatibility display
        self._render_custom_tracks()
        self._render_custom_vias()

    # ------------------------------------------------------------------
    # Preset Generation & Rendering (Column-based)
    # ------------------------------------------------------------------

    def _generate_all_presets(self, c: PCBConstraints) -> None:
        """Generate 10 presets for each category and append custom sizes."""
        self._signal_presets = generate_signal_trace_presets(c)
        self._power_presets  = generate_power_trace_presets(c)
        self._diff_presets   = generate_diff_pair_presets(c)
        self._via_presets    = generate_via_presets(c)

        for w in self._custom_tracks:
            self._signal_presets.append({"name": f"Custom {w}mm", "track_width": w, "category": "signal", "is_manual": True})
            self._power_presets.append({"name": f"Custom {w}mm", "track_width": w, "category": "power", "is_manual": True})

        for v in self._custom_vias:
            self._via_presets.append({
                "name": f"Custom {v[0]}/{v[1]}mm",
                "via_dia": v[0],
                "via_drill": v[1],
                "annular_ring": round((v[0] - v[1]) / 2, 4),
                "category": "via",
                "is_manual": True
            })

    def _render_all_presets(self, c: PCBConstraints) -> None:
        """Render all four columns of presets."""
        # Hide placeholder, show content
        self._presets_placeholder.pack_forget()
        self._presets_content.pack(fill="both", expand=True)

        # Update vendor badge
        self._vendor_compat_badge.configure(text=f"  ✔ {c.vendor_name} Compatible  ")

        # Update constraint summary footer
        self._constraint_summary_label.configure(
            text=(
                f"📋  Vendor: {c.vendor_name}  |  "
                f"Trace: {c.min_trace_width_mm:.3f}–{c.max_trace_width_mm:.3f} mm  |  "
                f"Via Ø: {c.min_via_diameter_mm:.3f}–{c.max_via_diameter_mm:.3f} mm  |  "
                f"Drill: {c.min_via_drill_mm:.3f}–{c.max_via_drill_mm:.3f} mm  |  "
                f"Min AR: {c.min_annular_ring_mm:.3f} mm  |  "
                f"Clearance: {c.min_clearance_mm:.3f} mm"
            ),
        )

        # Render each column
        self._signal_vars = self._render_column(
            self._signal_col_frame, self._signal_presets,
            value_fmt=lambda p: f"{p['track_width']:.3f} mm",
            default_indices={0, 2, 4, 7},  # Select some useful defaults
        )
        self._update_range_label(
            self._signal_col_frame,
            f"{self._signal_presets[0]['track_width']:.3f} — {self._signal_presets[-1]['track_width']:.3f} mm"
        )

        self._power_vars = self._render_column(
            self._power_col_frame, self._power_presets,
            value_fmt=lambda p: f"{p['track_width']:.3f} mm",
            default_indices={0, 2, 4, 6, 9},
        )
        self._update_range_label(
            self._power_col_frame,
            f"{self._power_presets[0]['track_width']:.3f} — {self._power_presets[-1]['track_width']:.3f} mm"
        )

        self._diff_vars = self._render_column(
            self._diff_col_frame, self._diff_presets,
            value_fmt=lambda p: f"W:{p['diff_width']:.3f} G:{p['diff_gap']:.3f}",
            default_indices={0, 2, 4, 7},
        )
        self._update_range_label(
            self._diff_col_frame,
            f"{self._diff_presets[0]['diff_width']:.3f} — {self._diff_presets[-1]['diff_width']:.3f} mm"
        )

        # Vias now show annular ring
        self._via_vars = self._render_column(
            self._via_col_frame, self._via_presets,
            value_fmt=lambda p: f"D:{p['via_dia']:.3f} H:{p['via_drill']:.3f}",
            default_indices={0, 2, 4, 6, 9},
            extra_fmt=lambda p: f"AR:{p.get('annular_ring', 0):.3f}",
            ar_constraints=c,
        )
        self._update_range_label(
            self._via_col_frame,
            f"{self._via_presets[0]['via_dia']:.3f} — {self._via_presets[-1]['via_dia']:.3f} mm"
        )

    def _render_column(self, col_frame: ctk.CTkScrollableFrame, presets: list[dict],
                        value_fmt, default_indices: set[int],
                        extra_fmt=None,
                        ar_constraints: PCBConstraints | None = None) -> list[ctk.BooleanVar]:
        """Render 10 preset rows in a column frame. Returns the checkbox BooleanVars.

        extra_fmt: optional callable to produce an extra label (used for annular ring).
        ar_constraints: if provided, color-code the extra label based on annular ring.
        """
        # Clear existing widgets
        for w in col_frame.winfo_children():
            w.destroy()

        vars_list: list[ctk.BooleanVar] = []

        for idx, preset in enumerate(presets):
            var = ctk.BooleanVar(value=(idx in default_indices))
            vars_list.append(var)

            row_frame = ctk.CTkFrame(col_frame, fg_color="transparent")
            row_frame.pack(fill="x", padx=2, pady=1)

            # Tier number badge
            tier_num = idx + 1
            ctk.CTkLabel(
                row_frame,
                text=f"{tier_num:02d}",
                font=ctk.CTkFont(family="Consolas", size=9, weight="bold"),
                text_color=CLR_BORDER, width=20,
            ).pack(side="left", padx=(0, 4))

            # Checkbox
            cb = ctk.CTkCheckBox(
                row_frame, text="", variable=var, width=20,
                fg_color=CLR_ACCENT, hover_color=CLR_ACCENT2,
                border_color=CLR_BORDER, checkmark_color="#ffffff",
                checkbox_width=18, checkbox_height=18,
            )
            cb.pack(side="left", padx=(0, 4))

            # Value label
            ctk.CTkLabel(
                row_frame,
                text=value_fmt(preset),
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=CLR_ACCENT2, anchor="w",
            ).pack(side="left", padx=(0, 2))

            # Extra label (annular ring for vias)
            if extra_fmt is not None:
                ar_text = extra_fmt(preset)
                # Color-code annular ring
                ar_color = CLR_SUBTEXT
                if ar_constraints is not None:
                    ar_val = preset.get("annular_ring", 0)
                    min_ar = ar_constraints.min_annular_ring_mm
                    if ar_val >= min_ar * 2:
                        ar_color = CLR_SUCCESS   # >= 2× min: green
                    elif ar_val >= min_ar:
                        ar_color = CLR_WARNING   # >= min: yellow
                    else:
                        ar_color = CLR_ERROR     # < min: red (shouldn't happen)

                ctk.CTkLabel(
                    row_frame,
                    text=ar_text,
                    font=ctk.CTkFont(family="Consolas", size=9),
                    text_color=ar_color, anchor="w",
                ).pack(side="left", padx=(2, 0))

            # Preset name (smaller)
            name_text = preset["name"].split(" ", 1)[-1] if " " in preset["name"] else preset["name"]
            is_manual = preset.get("is_manual", False)
            if is_manual:
                name_text = f"🟡 {name_text}"
            
            ctk.CTkLabel(
                row_frame,
                text=name_text,
                font=ctk.CTkFont(family="Segoe UI", size=9),
                text_color=CLR_WARNING if is_manual else CLR_SUBTEXT, anchor="e",
            ).pack(side="right", padx=(4, 2))

        return vars_list

    def _update_range_label(self, col_frame: ctk.CTkScrollableFrame, text: str) -> None:
        """Update the range label for a column."""
        parent = col_frame.master  # The outer CTkFrame
        if hasattr(parent, '_range_label'):
            parent._range_label.configure(text=f"Range: {text}")

    # ------------------------------------------------------------------
    # URL change tracking (dynamic project name)
    # ------------------------------------------------------------------

    def _on_url_change(self, *args) -> None:
        """Callback triggered whenever the URL entry changes.

        Updates the project name dynamically if the current name was
        auto-generated from the previous URL (or is the default).
        """
        if self._url_trace_active:
            return  # Prevent recursion
        self._url_trace_active = True
        try:
            url = self._url_var.get().strip()
            if not url or not _validate_url(url):
                return

            current_name = self._project_name_var.get().strip()
            old_auto_name = (
                _derive_project_name(self._last_url_for_name)
                if self._last_url_for_name else ""
            )

            # Update if: name is default, empty, or matches the old auto-generated name
            if (not current_name
                    or current_name == "MyPCBProject"
                    or current_name == old_auto_name):
                new_name = _derive_project_name(url)
                self._project_name_var.set(new_name)

            self._last_url_for_name = url
        finally:
            self._url_trace_active = False

    # ------------------------------------------------------------------
    # Quick-fill vendor
    # ------------------------------------------------------------------

    def _quick_fill_vendor(self, url: str, vendor_name: str) -> None:
        """Set the vendor URL and auto-generate a project name."""
        # Setting url_var triggers the _on_url_change callback which
        # handles project name updates, so we just set the URL.
        # But we also need to force the project name for quick-fill.
        self._last_url_for_name = self._url_var.get().strip()  # save current before change
        self._url_var.set(url)
        # Force project name to match vendor (the trace callback handles this,
        # but we explicitly set it for quick-fill to be predictable)
        self._project_name_var.set(f"{vendor_name}_Project")
        self._last_url_for_name = url

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
        for child in self._model_scroll.winfo_children():
            if child is not self._model_placeholder:
                child.destroy()
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
        
        # Check if the user previously saved a model for this provider
        saved_model = self._config.get("ai_model", "")

        # Determine best recommendation that exists in the fetched list
        star_model = next((m for m in recommended if m in models), None)
        if star_model is None and models:
            star_model = models[0]

        self._models_list = models
        
        # Sort models: starred first, then recommended, then the rest
        def model_sort_key(m: str):
            is_starred = m in self._starred_models
            is_rec = m == star_model
            # Priority: starred=0, recommended=1, others=2
            priority = 0 if is_starred else (1 if is_rec else 2)
            return (priority, m)
            
        sorted_models = sorted(models, key=model_sort_key)

        for m in sorted_models:
            is_starred = m in self._starred_models
            is_rec = (m == star_model)
            
            if is_starred:
                label = f"{m}"
                color = CLR_WARNING  # Gold/yellow for starred
                font_weight = "bold"
            elif is_rec:
                label = f"{m}  ← Recommended"
                color = CLR_SUCCESS
                font_weight = "bold"
            else:
                label = m
                color = CLR_TEXT
                font_weight = "normal"
                
            row_frame = ctk.CTkFrame(self._model_scroll, fg_color="transparent")
            row_frame.pack(fill="x", padx=4, pady=2)
            
            star_btn = ctk.CTkButton(
                row_frame,
                text="★" if is_starred else "☆",
                width=24,
                height=24,
                font=ctk.CTkFont(size=14),
                text_color=CLR_WARNING if is_starred else CLR_SUBTEXT,
                fg_color="transparent",
                hover_color=CLR_CARD,
            )
            star_btn.pack(side="left", padx=(0, 4))
            
            rb = ctk.CTkRadioButton(
                row_frame,
                text=label,
                variable=self._selected_model_var,
                value=m,
                font=ctk.CTkFont(
                    family="Segoe UI",
                    size=11,
                    weight=font_weight,
                ),
                text_color=color,
                fg_color=CLR_ACCENT,
                hover_color=CLR_ACCENT2,
                command=self._on_model_select,
            )
            rb.pack(side="left", anchor="w")
            self._model_radio_buttons.append(rb)
            
            # Bind the toggle action with widgets after both are created
            star_btn.configure(command=lambda mod=m, b=star_btn, r=rb: self._toggle_star_model(mod, b, r))

        # Auto-select the previously saved model if it exists in the fetched list,
        # otherwise fallback to the recommended model.
        if saved_model and saved_model in models:
            self._selected_model_var.set(saved_model)
            self._on_model_select()
        elif star_model:
            self._selected_model_var.set(star_model)
            self._on_model_select()

    def _toggle_star_model(self, model: str, star_btn: ctk.CTkButton, rb: ctk.CTkRadioButton) -> None:
        """Toggle star status and apply visual update in-place without refreshing the whole list immediately."""
        pid = self._current_provider_id()
        is_rec = (model == next((m for m in PROVIDER_MAP[pid]["recommended"] if m in self._models_list), None) or 
                  (self._models_list and model == self._models_list[0]))

        if model in self._starred_models:
            self._starred_models.remove(model)
            # Update visuals to unstarred
            star_btn.configure(text="☆", text_color=CLR_SUBTEXT)
            
            # Revert label and color based on whether it is recommended
            if is_rec:
                rb.configure(text=f"{model}  ← Recommended", text_color=CLR_SUCCESS, font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"))
            else:
                rb.configure(text=model, text_color=CLR_TEXT, font=ctk.CTkFont(family="Segoe UI", size=11, weight="normal"))
        else:
            self._starred_models.append(model)
            # Update visuals to starred
            star_btn.configure(text="★", text_color=CLR_WARNING)
            rb.configure(text=f"{model}", text_color=CLR_WARNING, font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"))
            
        # Save to config
        self._config["starred_models"] = self._starred_models
        save_config(self._config)

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
                self._safe_after(0, lambda: self._on_models_fetched(models))
            except Exception as exc:
                err_msg = str(exc).strip()
                if not err_msg or err_msg == "None":
                    err_msg = f"{type(exc).__name__}: {exc!r}"
                self._safe_after(0, lambda: self._on_models_error(err_msg))

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
            if not self._is_alive:
                return
            self._log_text.configure(state="normal")
            self._log_text.insert("end", f"{message}\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        self._safe_after(0, _do)

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _export_log(self) -> None:
        """Export the log content to a text file."""
        log_content = self._log_text.get("1.0", "end").strip()
        if not log_content:
            messagebox.showinfo("Export Log", "Log is empty — nothing to export.")
            return
        file_path = filedialog.asksaveasfilename(
            title="Export Log",
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            initialfile=f"kicad_config_log_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(log_content)
                messagebox.showinfo("Export Log", f"Log exported to:\n{file_path}")
            except Exception as e:
                messagebox.showerror("Export Failed", f"Could not export log:\n{e}")

    def _set_status(self, msg: str) -> None:
        self._safe_after(0, lambda: self._status_var.set(msg))

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

        if not _validate_url(url):
            messagebox.showwarning(
                "Invalid URL",
                "Please enter a valid URL starting with http:// or https://",
            )
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

        # Always update project name from URL (dynamic naming)
        new_name = _derive_project_name(url)
        current_name = self._project_name_var.get().strip()
        old_auto_name = (
            _derive_project_name(self._last_url_for_name)
            if self._last_url_for_name else ""
        )
        if not current_name or current_name == "MyPCBProject" or current_name == old_auto_name:
            self._project_name_var.set(new_name)
        self._last_url_for_name = url

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

            # Sanitize extracted values
            constraints = _sanitize_constraints(constraints)

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
            self._log(f"  📏 Max Trace:       {constraints.max_trace_width_mm} mm")
            self._log(f"  ⭕ Max Via Dia:     {constraints.max_via_diameter_mm} mm")
            self._log(f"  🔩 Max Via Drill:   {constraints.max_via_drill_mm} mm")
            if constraints.notes:
                self._log(f"  📝 Notes: {constraints.notes[:200]}")

            # Log annular ring verification for generated vias
            self._log("  🔘 Via annular ring verification:")
            via_presets = generate_via_presets(constraints)
            for vp in via_presets:
                ar = vp["annular_ring"]
                status = "✅" if ar >= constraints.min_annular_ring_mm else "❌"
                self._log(
                    f"     {status} {vp['name']}: D={vp['via_dia']:.3f} "
                    f"H={vp['via_drill']:.3f} AR={ar:.3f} mm"
                )

            # Save the used provider/model to config
            self._config["ai_provider"] = provider_id
            self._config["ai_model"]    = model
            save_config(self._config)

            self._safe_after(0, lambda: self._render_results(constraints))
            self._safe_after(0, lambda: self._inject_btn.configure(state="normal"))
            self._set_status(f"✅ Extracted constraints from {constraints.vendor_name}")

        except Exception as exc:
            self._log(f"❌ Error: {exc}")
            self._set_status(f"Error: {exc}")
            self._safe_after(0, lambda: messagebox.showerror("Extraction Failed", str(exc)))

        finally:
            self._scraping = False
            self._safe_after(0, self._reset_scrape_ui)

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

        # Sanitize project name (remove invalid filename chars)
        project_name = re.sub(r'[<>:"/\\|?*]', '_', project_name).strip()
        if not project_name:
            messagebox.showwarning("Invalid Name", "Project name contains only invalid characters.")
            return

        # ── Collect selected presets by category ──────────────────────
        selected_signals = [
            self._signal_presets[i] for i, var in enumerate(self._signal_vars) if var.get()
        ] if self._signal_vars else None

        selected_power = [
            self._power_presets[i] for i, var in enumerate(self._power_vars) if var.get()
        ] if self._power_vars else None

        selected_diff = [
            self._diff_presets[i] for i, var in enumerate(self._diff_vars) if var.get()
        ] if self._diff_vars else None

        selected_vias = [
            self._via_presets[i] for i, var in enumerate(self._via_vars) if var.get()
        ] if self._via_vars else None

        # ── Merge compatible custom sizes ─────────────────────────────
        custom_tracks = self._get_compatible_custom_tracks()
        if custom_tracks:
            if selected_signals is None:
                selected_signals = []
            for w in custom_tracks:
                # Add as signal-category track preset (avoids duplicates)
                if not any(abs(p["track_width"] - w) < 0.0001 for p in selected_signals):
                    selected_signals.append({
                        "name": f"Custom {w:.3f}",
                        "track_width": w,
                        "category": "signal",
                    })

        custom_vias = self._get_compatible_custom_vias()
        if custom_vias:
            if selected_vias is None:
                selected_vias = []
            for cv in custom_vias:
                # Avoid duplicates
                if not any(
                    abs(p["via_dia"] - cv["via_dia"]) < 0.0001
                    and abs(p["via_drill"] - cv["via_drill"]) < 0.0001
                    for p in selected_vias
                ):
                    selected_vias.append(cv)

        total_selected = sum(len(s) for s in [
            selected_signals or [], selected_power or [],
            selected_diff or [], selected_vias or []
        ])

        if total_selected == 0:
            messagebox.showwarning(
                "No Presets Selected",
                "Please select at least one preset in the\n"
                "'📐 Presets' tab before injecting, or add\n"
                "compatible custom sizes in the '⚙️ Custom' tab.",
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

        # Count custom sizes for logging
        n_custom_tracks = len(custom_tracks)
        n_custom_vias = len(custom_vias)

        self._inject_btn.configure(state="disabled", text="⏳  Injecting …")
        self._log(f"{'─'*50}")
        self._log(f"💉 Injection started at {time.strftime('%H:%M:%S')}")
        self._log(f"  📐 {total_selected} presets selected across all categories")
        if n_custom_tracks or n_custom_vias:
            self._log(
                f"  ⚙️  Including {n_custom_tracks} custom track(s) "
                f"and {n_custom_vias} custom via(s)"
            )

        def _worker():
            try:
                dest = run_injection(
                    constraints,
                    output_dir,
                    template_dir,
                    project_name,
                    self._log,
                    selected_signals,
                    selected_power,
                    selected_diff,
                    selected_vias,
                )
                self._log(f"✅ Project created at: {dest}")
                self._set_status(f"✅ Injected into {dest}")
                self._safe_after(0, lambda: messagebox.showinfo(
                    "Done!",
                    f"KiCad project created successfully:\n{dest}\n\n"
                    "Open the .kicad_pro file in KiCad 9+ to start designing!",
                ))
            except Exception as exc:
                self._log(f"❌ Injection failed: {exc}")
                self._set_status(f"Error: {exc}")
                self._safe_after(0, lambda: messagebox.showerror("Injection Failed", str(exc)))
            finally:
                self._safe_after(0, lambda: self._inject_btn.configure(
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
