"""
Configuration module - Cross-platform path handling
"""
from pydantic_settings import BaseSettings
from typing import List
from pathlib import Path
import os

# Get project root directory (backend's parent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "Register Description Tool"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = ["*"]

    # Database - use project root
    DATABASE_URL: str = f"sqlite:///{PROJECT_ROOT / 'regtool.db'}"

    # Output directories - cross-platform paths
    # Directory structure: OUTPUT_DIR / {version_name} / {format}/
    # format: rdl, ralf, header, svh, uvm, rtl, html
    OUTPUT_DIR: Path = PROJECT_ROOT / "output"
    TEMP_DIR: Path = OUTPUT_DIR / "temp"

    # PeakRDL settings
    PEAKRDL_ENABLED: bool = True
    DEFAULT_CPU_IF: str = "apb3"  # apb3, apb4, axilite

    class Config:
        case_sensitive = True

    def ensure_directories(self):
        """Create base output directories if they don't exist"""
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        return self

settings = Settings().ensure_directories()
