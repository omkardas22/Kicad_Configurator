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

## 📋 Usage Guide

### Step 1 — Enter API Key
Enter your Google Gemini API key in the **🔑 Gemini API Key** field and click **Save Key**. The key is stored encrypted in `%APPDATA%\KiCadConfigurator\config.json`.

### Step 2 — Enter Vendor URL
Paste the manufacturer's PCB capability page URL. Use the quick-fill buttons for JLCPCB or PCBWay.

**Example URLs:**
- JLCPCB: `https://jlcpcb.com/capabilities/pcb`
- PCBWay: `https://www.pcbway.com/capabilities.html`

### Step 3 — Scrape & Extract
Click **🔍 Scrape & Extract Constraints**. The app will:
- Fetch and parse the vendor page
- Send the text to Gemini AI for structured extraction
- Display the results in the **📊 Results** tab

### Step 4 — Set Output & Inject
1. Set your **Output Directory** (where the KiCad project folder will be created)
2. Set your **Project Name**
3. Click **💉 Inject into KiCad Files**

The app copies the KiCad templates, renames them to your project name, and injects all constraints.

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
