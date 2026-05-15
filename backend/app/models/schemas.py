"""
Pydantic schemas for API
"""
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class RegisterFieldBase(BaseModel):
    name: str
    bit_range: str
    msb: int
    lsb: int
    access: str = "RW"
    reset_value: str = "0"
    description: Optional[str] = None

class RegisterFieldCreate(RegisterFieldBase):
    pass

class RegisterFieldResponse(RegisterFieldBase):
    id: int
    register_id: int

    class Config:
        from_attributes = True

class RegisterBase(BaseModel):
    name: str
    address: int
    offset: int = 0
    width: int = 32
    description: Optional[str] = None
    is_array: int = 0
    array_count: int = 1

class RegisterCreate(RegisterBase):
    fields: List[RegisterFieldCreate] = []

class RegisterResponse(RegisterBase):
    id: int
    module_id: int
    fields: List[RegisterFieldResponse] = []

    class Config:
        from_attributes = True

class RegisterModuleBase(BaseModel):
    name: str
    base_address: int = 0
    end_address: int = 0
    size: int = 0
    is_array: int = 0
    array_count: int = 1

class RegisterModuleCreate(RegisterModuleBase):
    registers: List[RegisterCreate] = []
    submodules: List['RegisterModuleCreate'] = []

class RegisterModuleResponse(RegisterModuleBase):
    id: int
    version_id: int
    registers: List[RegisterResponse] = []
    submodules: List['RegisterModuleResponse'] = []

    class Config:
        from_attributes = True

class VersionBase(BaseModel):
    name: str
    description: Optional[str] = None
    user_id: Optional[str] = 'default'
    is_published: Optional[bool] = False

class VersionCreate(VersionBase):
    pass

class VersionResponse(VersionBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    modules: List[RegisterModuleResponse] = []
    warnings: Optional[List[str]] = None  # 生成过程中的warnings
    top_addrmap_name: Optional[str] = None  # 顶层addr_map名字

    class Config:
        from_attributes = True

class CodeGenerationRequest(BaseModel):
    formats: List[str]
    cpu_if: Optional[str] = "axilite"  # apb3, apb4, axilite

class RTLGenerationRequest(BaseModel):
    """RTL generation configuration request"""
    module: Optional[str] = None  # Module name, None for combined/all modules
    cpu_if: str = "axilite"  # CPU interface: apb3, apb4, axilite
    address_width: int = 32  # Address bus width: 16, 32, 64
    reset_type: str = "active_low"  # Reset type: active_low, active_high
    pipeline: bool = False  # Enable pipeline stage

class RTLGenerationResponse(BaseModel):
    """RTL generation response"""
    success: bool
    version_id: int
    module: Optional[str] = None
    message: str
    files: List[dict] = []
    rtl_path: Optional[str] = None
    download_url: Optional[str] = None

class RTLConfigOptions(BaseModel):
    """Available RTL generation options"""
    cpu_interfaces: List[dict] = [
        {"value": "axilite", "label": "AXI4-Lite", "default": True},
        {"value": "apb3", "label": "APB3", "default": False},
        {"value": "apb4", "label": "APB4", "default": False},
    ]
    address_widths: List[dict] = [
        {"value": 16, "label": "16-bit", "default": False},
        {"value": 32, "label": "32-bit", "default": True},
        {"value": 64, "label": "64-bit", "default": False},
    ]
    reset_types: List[dict] = [
        {"value": "active_low", "label": "Active Low", "default": True},
        {"value": "active_high", "label": "Active High", "default": False},
    ]

class RTLFileInfo(BaseModel):
    """RTL file information"""
    filename: str
    path: str
    size: int
    generated_at: Optional[datetime] = None

class UploadResponse(BaseModel):
    success: bool
    filename: Optional[str] = None
    total_files: Optional[int] = None
    modules_count: int = 0
    registers_count: int = 0
    warnings: List[str] = []
    html_url: Optional[str] = None
    code_results: Optional[dict] = None

# Resolve forward reference
RegisterModuleCreate.model_rebuild()
RegisterModuleResponse.model_rebuild()
