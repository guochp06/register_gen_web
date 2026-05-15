# Register Description Tool

A web-based tool for generating register documentation and code from Excel files. Supports recursive addr_map parsing and generates HTML, RDL, RALF, C Header, SV Header, UVM, and RTL outputs.

## Features

- **Excel Parsing**: Support `.xls` and `.xlsx` files
- **Hierarchical Structure**: Recursive parsing from `soc_addr_map` down to registers
- **Array Support**: Module and register arrays (e.g., `CPD*N(N=4)`)
- **Output Formats**:
  - HTML: Interactive register browser with search
  - RDL: SystemRDL 2.0 format
  - RALF: UVM Register Abstraction Layer format
  - C Header: Register defines for firmware
  - SV Header: SystemVerilog defines
  - UVM: UVM register model (via PeakRDL)
  - RTL: Verilog register block (via PeakRDL)
- **Cross-Platform**: Works on Windows and Linux

## Quick Start

### Windows

```batch
start.bat
```

### Linux

```bash
chmod +x start.sh
./start.sh
```

Then open http://localhost:5173 in your browser.

## Manual Setup

### Backend (Python)

```bash
cd backend

# Create virtual environment
python3 -m venv venv  # Linux
python -m venv venv   # Windows

# Activate
source venv/bin/activate  # Linux
venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt

# Start server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (Node.js)

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

## Optional: PeakRDL for UVM/RTL

To generate UVM and RTL outputs, install PeakRDL:

```bash
cd backend
source venv/bin/activate  # or venv\Scripts\activate on Windows

pip install systemrdl-compiler peakrdl-uvm peakrdl-regblock
```

## Excel Format

### addr_map Sheet

| MODULE_NAME | start_addr | end_addr | size |
|-------------|------------|----------|------|
| PE | 0x1000_0000 | 0x100F_FFFF | 1M |
| PEC | 0x1010_0000 | 0x101F_FFFF | 1M |
| C2C0 | 0x1020_0000 | 0x1020_FFFF | 64K |

### register Sheet

| OffsetAddress | RegName | Width | Bits | FieldName | Access | ResetValue | Description |
|---------------|---------|-------|------|-----------|--------|------------|-------------|
| 0x00 | CTRL | 32 | [31:0] | ctrl | RW | 32'h0 | Control register |
| 0x04 | STATUS | 32 | [31:0] | status | RO | 32'h0 | Status register |

## Project Structure

```
regtool/
├── backend/               # FastAPI backend
│   ├── app/
│   │   ├── api/v1/endpoints/   # API routes
│   │   ├── core/               # Configuration
│   │   ├── db/                 # Database models
│   │   ├── models/             # SQLAlchemy models
│   │   └── services/           # Business logic
│   │       ├── hierarchy_parser.py      # Excel parsing
│   │       ├── code_generator.py        # RDL/RALF generation
│   │       ├── hierarchy_html_generator.py  # HTML generation
│   │       └── peakrdl_wrapper.py       # UVM/RTL generation
│   ├── output/            # Generated files
│   └── requirements.txt
├── frontend/              # React frontend
│   ├── src/
│   │   └── App.tsx
│   └── package.json
├── start.bat             # Windows start script
├── start.sh              # Linux start script
└── README.md
```

## API Endpoints

- `GET /api/v1/versions` - List all versions
- `POST /api/v1/versions` - Create new version
- `POST /api/v1/versions/{id}/upload/batch` - Upload Excel files
- `GET /api/v1/versions/{id}/download/{format}` - Download code
- `GET /api/v1/versions/{id}/html` - Get HTML URL

## License

MIT License
