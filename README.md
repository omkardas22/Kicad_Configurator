# ⚡ KiCad Constraint Configurator

<div align="center">

![Version](https://img.shields.io/badge/version-1.0.0-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![KiCad](https://img.shields.io/badge/KiCad-7%2B-314CB0?style=for-the-badge&logo=kicad&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)

**Automatically extract PCB manufacturing constraints from vendor pages using Google Gemini AI and inject them directly into your KiCad project files.**

[Download Offline Installer](https://github.com/omkardas22/Kicad_Configurator/raw/main/releases/v1.0.0/standalone_installer/KiCadConfigurator_FullSetup_v1.0.0.exe) · [Download Web Installer](https://github.com/omkardas22/Kicad_Configurator/raw/main/releases/v1.0.0/web_installer/KiCadConfigurator_WebSetup_v1.0.0.exe) · [Report Bug](https://github.com/omkardas22/Kicad_Configurator/issues) · [Request Feature](https://github.com/omkardas22/Kicad_Configurator/issues)

</div>

---

## 🎯 What It Does

KiCad Constraint Configurator eliminates the manual, error-prone process of reading a PCB manufacturer's capability page and copying values into KiCad's design rule editor. Instead:

1. **Paste a vendor URL** (JLCPCB, PCBWay, OSH Park, or any manufacturer)
2. **Click "Scrape & Extract"** — the app fetches the page and sends it to Gemini 2.5 Flash
3. **Review the extracted constraints** displayed in a clean card interface
4. **Click "Inject into KiCad Files"** — constraints are written into your `.kicad_pro` and `.kicad_pcb` files with proper net classes automatically configured

---

## ✨ Features

| Feature | Details |
|---|---|
| 🤖 AI Extraction | Google Gemini 2.5 Flash with Pydantic structured output |
| 🌐 Web Scraping | `requests` + `BeautifulSoup4` — works on any vendor page |
| 🎨 Modern Dark UI | CustomTkinter dark-mode GUI |
| 💾 Persistent Config | API key & output directory saved in `%APPDATA%` |
| ⚙️ Design Rules | Patches `min_clearance`, `min_track_width`, `min_via_diameter`, etc. |
| 🔌 Net Classes | Auto-creates **Default**, **Power** (2× width/clearance, red), **CAN_Bus** (diff-pair, blue) |
| 📋 Net Patterns | `+*`, `GND*`, `VCC*` → Power; `CAN_*` → CAN_Bus |
| 📁 JSON Patching | `.kicad_pro` design settings + net_settings updated |
| 📝 S-Expr Patching | `.kicad_pcb` setup block updated via regex |
| 📦 Installers | Both standalone (offline) and web-stub installers |

---

## 🚀 Installation

### Option 1: Offline Installer (Recommended)
Download [`KiCadConfigurator_FullSetup_v1.0.0.exe`](https://github.com/omkardas22/Kicad_Configurator/raw/main/releases/v1.0.0/standalone_installer/KiCadConfigurator_FullSetup_v1.0.0.exe) — a self-contained installer. No internet required after download.

### Option 2: Web Installer
Download [`KiCadConfigurator_WebSetup_v1.0.0.exe`](https://github.com/omkardas22/Kicad_Configurator/raw/main/releases/v1.0.0/web_installer/KiCadConfigurator_WebSetup_v1.0.0.exe) — a lightweight stub (~2 MB) that downloads the application from GitHub during installation.

### Option 3: Run from Source
```bash
git clone https://github.com/omkardas22/Kicad_Configurator.git
cd Kicad_Configurator
pip install -r src/requirements.txt
python src/main.py
```

---

## 🖥️ System Requirements

- **OS:** Windows 10/11 64-bit
- **Python:** 3.10+ (for source runs)
- **Internet:** Required for AI extraction and web installer download
- **Google Gemini API Key:** Free tier available at [ai.google.dev](https://ai.google.dev)

---

## 📋 Comprehensive Usage Guide

The application is split into several main tabs to keep your workflow organized:

### 🏠 Home Tab
The primary control center for extracting constraints and injecting them into your project.

*   **🔑 Gemini API Key**: Enter your Google Gemini API key. 
    *   **Save Key**: Encrypts and saves your key to `%APPDATA%`.
    *   **Show / Hide Key**: Toggles the visibility of your API key.
*   **🧠 AI Model**:
    *   **Dropdown**: Select the specific Gemini model to use for extraction (defaults to `gemini-2.5-flash`).
    *   **🔄 Fetch Models**: Queries Google's servers to fetch the latest available AI models.
    *   **⭐ Star Button**: Pins the currently selected model to the top of the list so it survives restarts.
*   **🌐 Vendor Capability URL**: Paste the URL of the manufacturer's capability page here.
    *   **JLCPCB / PCBWay Quick Fill**: Instantly pastes the default URL for these common manufacturers.
*   **📂 Output Directory & Project Name**:
    *   **Browse**: Opens a folder selection dialog to choose where your project will be created.
    *   **Project Name**: The name of the `.kicad_pro` and `.kicad_pcb` files to generate.
*   **Action Buttons**:
    *   **🔍 Scrape & Extract Constraints**: Downloads the URL and uses AI to extract physical constraints (minimum track, drill size, etc.).
    *   **💉 Inject into KiCad Files**: Generates a new KiCad project in the output directory, heavily configuring it with the extracted constraints and all selected presets.

### 📐 Presets Tab
This tab lets you select which pre-configured trace widths and via sizes will be injected into your KiCad project.

*   **The Columns**: 
    *   **Signal Traces**: For general logic and routing.
    *   **Power Traces**: Thicker traces designed for power delivery.
    *   **Diff Pairs**: Paired traces for high-speed signals (like USB or CAN_Bus).
    *   **Vias**: Drill and diameter configurations that are strictly verified against the manufacturer's Minimum Annular Ring constraint.
*   **Column Buttons**:
    *   **All**: Instantly checks every preset box in that column.
    *   **None**: Instantly clears all checked boxes in that column.
*   **🧠 AI Generate Presets**: A powerful button that asks the AI to intelligently calculate 10 unique, scaled values for every column based on the manufacturer's absolute minimum capabilities. 
    *   AI-generated presets appear with **Green** labels.
    *   Custom-made presets appear with **Pink** labels.

### ⚙️ Custom Tab
If the AI-generated presets aren't exactly what you want, you can create your own here.

*   **Custom Tracks**:
    *   **+ Add Track**: Adds the track width to the local list below.
    *   **Add track preset**: Pushes the current track width *directly* into the **Presets Tab**, tagging it as a Custom preset (Pink text).
    *   **Clear All**: Deletes all custom tracks from the list.
*   **Custom Vias**:
    *   **Freeform Checkbox**: By default, entering a hole size auto-calculates the diameter to maintain the minimum annular ring. Checking "Freeform" disables auto-calculation, letting you manually input both sizes (though it will still warn you if you violate manufacturer constraints!).
    *   **+ Add Via**: Adds the via configuration to the local list below.
    *   **Add via preset**: Pushes the via configuration *directly* into the **Presets Tab**, tagging it as a Custom preset (Pink text).
    *   **Clear All**: Deletes all custom vias.

### 📜 Log Tab
View real-time backend operations, errors, and JSON responses from the AI.
*   **Export Log**: Saves the entire console history to a `.txt` file.
*   **Clear Log**: Wipes the console clean.

---

## 🏗️ Project Structure

```
Kicad_Configurator/
├── src/
│   ├── main.py               # Main application
│   └── requirements.txt      # Python dependencies
├── kicad_template/           # KiCad blank templates
│   ├── template.kicad_pro    # Project file (JSON)
│   ├── template.kicad_pcb    # PCB layout file (S-expression)
│   └── template.kicad_sch    # Schematic file
├── build_scripts/
│   ├── setup_offline.iss     # Offline installer (Inno Setup)
│   └── setup_web.iss         # Web stub installer (Inno Setup)
├── build.py                  # One-click build orchestrator
├── README.md
└── releases/
    └── v1.0.0/
        ├── app_payload.zip
        ├── standalone_installer/
        │   └── KiCadConfigurator_FullSetup_v1.0.0.exe
        └── web_installer/
            └── KiCadConfigurator_WebSetup_v1.0.0.exe
```

---

## 🛠️ Building from Source

### Prerequisites
- Python 3.10+
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (for installer compilation)
- Windows OS

### One-Click Build
```bash
# Full build (requires Inno Setup installed)
python build.py

# Dry run — validate paths without compiling
python build.py --dry-run

# Skip Inno Setup (PyInstaller only)
python build.py --skip-inno

# Custom version
python build.py --version 1.2.0
```

The build script will:
1. Install/update all pip dependencies
2. Run PyInstaller (`--onedir --noconsole`)
3. Zip the output to `releases/v1.0.0/app_payload.zip`
4. Compile both `.iss` scripts to `releases/v1.0.0/`

---

## ⚙️ Net Class Configuration

The injected net classes follow this scheme:

| Net Class | Track Width | Clearance | Via Dia | Color | Assigned Nets |
|---|---|---|---|---|---|
| **Default** | Vendor minimum | Vendor minimum | Vendor minimum | None | All unmatched |
| **Power** | 2× minimum | 2× minimum | Vendor minimum | 🔴 Red | `+*`, `GND*`, `VCC*` |
| **CAN_Bus** | Vendor minimum | Vendor minimum | Vendor minimum | 🔵 Blue | `CAN_*` |

CAN_Bus class additionally enables **differential pair** parameters (`diff_pair_gap`, `diff_pair_width`) for proper CAN bus routing.

---

## 🔐 API Key Security

Your Gemini API key is stored in plaintext JSON at:
```
%APPDATA%\KiCadConfigurator\config.json
```
This directory is user-profile scoped and not accessible to other Windows users. The key is **never** transmitted anywhere except to Google's Gemini API.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit changes: `git commit -m 'feat: add my feature'`
4. Push: `git push origin feature/my-feature`
5. Open a Pull Request

---

<div align="center">
Made with ❤️ for the KiCad community
</div>
