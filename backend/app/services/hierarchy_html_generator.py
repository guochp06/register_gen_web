"""
Hierarchy HTML Generator - Generate interactive web page for register browsing
Similar to peakrdl-html output but with custom styling
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from app.services.hierarchy_parser import RegisterHierarchy, Module, Register, RegisterField


class HierarchyHTMLGenerator:
    """Generate interactive HTML for register hierarchy"""

    def generate(self, hierarchy: RegisterHierarchy) -> str:
        """Generate complete HTML page"""
        # Build indices
        module_index = self._build_module_index(hierarchy)
        register_index = self._build_register_index(hierarchy)

        html_parts = [
            self._generate_header(hierarchy),
            self._generate_sidebar(hierarchy),
            self._generate_main_content(hierarchy),
            self._generate_footer(hierarchy, module_index, register_index),
        ]
        return '\n'.join(html_parts)

    def _build_module_index(self, hierarchy: RegisterHierarchy) -> Dict:
        """Build module index for JavaScript"""
        index = {}
        def add_module_to_index(module: Module):
            index[module.name] = {
                'name': module.name,
                'start_addr': module.start_addr,
                'end_addr': module.end_addr,
                'size': module.size,
                'registers': [
                    {'name': r.name, 'offset': r.offset, 'address': module.start_addr + r.offset}
                    for r in module.registers
                ]
            }
            for sub in module.submodules:
                add_module_to_index(sub)

        for mod in hierarchy.top_modules:
            add_module_to_index(mod)
        return index

    def _build_register_index(self, hierarchy: RegisterHierarchy) -> Dict:
        """Build register index for JavaScript"""
        index = {}
        def add_registers_from_module(module: Module):
            for reg in module.registers:
                full_name = f"{module.name}_{reg.name}"
                index[full_name] = {
                    'module': module.name,
                    'name': reg.name,
                    'address': module.start_addr + reg.offset,
                    'offset': reg.offset,
                    'width': reg.width,
                    'fields': [
                        {'name': f.name, 'msb': f.msb, 'lsb': f.lsb, 'access': f.access}
                        for f in reg.fields
                    ]
                }
            for sub in module.submodules:
                add_registers_from_module(sub)

        for mod in hierarchy.top_modules:
            add_registers_from_module(mod)
        return index

    def _generate_header(self, hierarchy: RegisterHierarchy) -> str:
        """Generate HTML head and header"""
        total_regs = sum(
            len(m.registers) for m in hierarchy.all_modules.values()
        ) if hierarchy.all_modules else 0

        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{hierarchy.version_name} - Register Map</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            min-height: 100vh;
            color: #333;
        }}

        .header {{
            background: #fff;
            border-bottom: 1px solid #ddd;
            padding: 20px 30px;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 100;
        }}
        .header h1 {{
            font-size: 1.8em;
            color: #333;
            margin-bottom: 10px;
        }}
        .header-info {{
            display: flex;
            gap: 30px;
            font-size: 0.9em;
            color: #666;
        }}
        .header-info span {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .header-info a {{
            color: #0066cc;
            text-decoration: none;
        }}
        .header-info a:hover {{
            text-decoration: underline;
        }}

        .search-bar {{
            background: #fff;
            border-bottom: 1px solid #ddd;
            padding: 15px 30px;
            position: fixed;
            top: 90px;
            left: 0;
            right: 0;
            z-index: 99;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .search-input {{
            flex: 1;
            max-width: 500px;
            padding: 10px 15px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }}
        .search-btn {{
            padding: 10px 20px;
            background: #28a745;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }}
        .search-btn:hover {{
            background: #218838;
        }}

        .main-container {{
            display: flex;
            margin-top: 160px;
            min-height: calc(100vh - 160px);
        }}

        .sidebar {{
            width: 320px;
            background: #2d2d2d;
            color: #fff;
            overflow-y: auto;
            position: fixed;
            top: 160px;
            bottom: 0;
            left: 0;
        }}
        .sidebar-header {{
            padding: 15px;
            background: #1a1a1a;
            font-weight: bold;
            border-bottom: 1px solid #444;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .tree {{
            padding: 10px;
        }}
        .tree-item {{
            margin: 2px 0;
        }}
        .tree-toggle {{
            display: inline-block;
            width: 16px;
            cursor: pointer;
            color: #888;
            font-size: 10px;
        }}
        .tree-link {{
            display: inline-block;
            padding: 5px 8px;
            color: #ccc;
            text-decoration: none;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
        }}
        .tree-link:hover {{
            background: #444;
            color: #fff;
        }}
        .tree-link.active {{
            background: #0066cc;
            color: #fff;
        }}
        .tree-link.module {{
            color: #fff;
            font-weight: 500;
        }}
        .tree-link.register {{
            color: #ff9999;
            font-size: 12px;
        }}
        .tree-children {{
            margin-left: 20px;
            display: none;
        }}
        .tree-children.expanded {{
            display: block;
        }}

        .content {{
            flex: 1;
            margin-left: 320px;
            padding: 20px 30px;
            background: #f5f5f5;
        }}

        .breadcrumb {{
            background: #fff;
            padding: 12px 20px;
            border-radius: 4px;
            margin-bottom: 20px;
            border: 1px solid #ddd;
        }}
        .breadcrumb a {{
            color: #0066cc;
            text-decoration: none;
        }}
        .breadcrumb a:hover {{
            text-decoration: underline;
        }}
        .breadcrumb-separator {{
            margin: 0 8px;
            color: #999;
        }}

        .module-card {{
            background: #fff;
            border-radius: 4px;
            border: 1px solid #ddd;
            margin-bottom: 20px;
            display: none;
        }}
        .module-card.active {{
            display: block;
        }}
        .module-header {{
            padding: 20px;
            border-bottom: 1px solid #eee;
            background: #fafafa;
        }}
        .module-header h2 {{
            font-size: 1.5em;
            color: #d32f2f;
            margin-bottom: 15px;
        }}
        .module-info {{
            display: grid;
            grid-template-columns: 150px 1fr;
            gap: 10px;
            font-size: 0.95em;
        }}
        .module-info-label {{
            color: #666;
        }}
        .module-info-value {{
            color: #333;
            font-family: 'SF Mono', Monaco, monospace;
        }}

        .register-section {{
            padding: 20px;
        }}
        .register-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .register-table th {{
            background: #f8f9fa;
            padding: 12px 15px;
            text-align: left;
            font-weight: 600;
            color: #333;
            border-bottom: 2px solid #ddd;
        }}
        .register-table td {{
            padding: 12px 15px;
            border-bottom: 1px solid #eee;
        }}
        .register-table tr:hover {{
            background: #f8f9fa;
        }}
        .register-name {{
            color: #0066cc;
            cursor: pointer;
            text-decoration: none;
            font-weight: 500;
        }}
        .register-name:hover {{
            text-decoration: underline;
        }}
        .offset-cell {{
            font-family: 'SF Mono', Monaco, monospace;
            color: #666;
        }}

        .register-detail {{
            background: #fff;
            border-radius: 4px;
            border: 1px solid #ddd;
            margin-bottom: 20px;
            display: none;
        }}
        .register-detail.active {{
            display: block;
        }}
        .register-detail-header {{
            padding: 20px;
            border-bottom: 1px solid #eee;
            background: #fafafa;
        }}
        .register-detail-header h3 {{
            font-size: 1.3em;
            color: #333;
            margin-bottom: 10px;
        }}
        .field-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .field-table th {{
            background: #f8f9fa;
            padding: 10px 15px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #ddd;
        }}
        .field-table td {{
            padding: 10px 15px;
            border-bottom: 1px solid #eee;
        }}
        .bit-range {{
            font-family: 'SF Mono', Monaco, monospace;
            background: #e3f2fd;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.9em;
        }}
        .access-rw {{ color: #28a745; font-weight: 500; }}
        .access-ro {{ color: #fd7e14; font-weight: 500; }}
        .access-wo {{ color: #dc3545; font-weight: 500; }}
        .access-w1c {{ color: #6f42c1; font-weight: 500; }}
        .access-w1s {{ color: #20c997; font-weight: 500; }}

        .search-results {{
            background: #fff;
            border-radius: 4px;
            border: 1px solid #ddd;
            padding: 20px;
            display: none;
        }}
        .search-results.active {{
            display: block;
        }}
        .search-result-item {{
            padding: 10px;
            border-bottom: 1px solid #eee;
            cursor: pointer;
        }}
        .search-result-item:hover {{
            background: #f8f9fa;
        }}
        .search-result-type {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.8em;
            margin-right: 10px;
        }}
        .search-result-type.module {{
            background: #0066cc;
            color: #fff;
        }}
        .search-result-type.register {{
            background: #28a745;
            color: #fff;
        }}

        .footer {{
            text-align: center;
            padding: 20px;
            color: #666;
            border-top: 1px solid #ddd;
            margin-top: 30px;
        }}

        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.75em;
            font-weight: 500;
        }}
        .badge-array {{
            background: #6c757d;
            color: #fff;
        }}
        .badge-instance {{
            background: #28a745;
            color: #fff;
            font-size: 0.65em;
        }}

        /* Pagination styles */
        .pagination {{
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 15px;
            gap: 15px;
            border-top: 1px solid #eee;
            margin-top: 10px;
        }}
        .page-btn {{
            padding: 8px 15px;
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            color: #333;
            transition: all 0.2s;
        }}
        .page-btn:hover {{
            background: #f0f0f0;
            border-color: #aaa;
        }}
        .page-btn:disabled {{
            color: #ccc;
            cursor: not-allowed;
            background: #f9f9f9;
        }}
        .page-info {{
            font-size: 14px;
            color: #333;
            min-width: 100px;
            text-align: center;
        }}
        .page-size {{
            font-size: 12px;
            color: #999;
            margin-left: 10px;
        }}
        .current-page {{
            font-weight: bold;
            color: #28a745;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{hierarchy.version_name}</h1>
        <div class="header-info">
            <span><strong>Modules:</strong> {len(hierarchy.all_modules)}</span>
            <span><strong>Registers:</strong> {total_regs}</span>
            <span><strong>Version:</strong> {hierarchy.version_name}</span>
        </div>
    </div>

    <div class="search-bar">
        <input type="text" class="search-input" id="globalSearch"
               placeholder="Search register or field name, or address (e.g., 0x1000)">
        <button class="search-btn" onclick="performSearch()">Search</button>
    </div>
'''

    def _generate_sidebar(self, hierarchy: RegisterHierarchy) -> str:
        """Generate left sidebar with tree navigation"""
        html = '''
    <div class="main-container">
        <div class="sidebar">
            <div class="sidebar-header">
                <span>Module Tree</span>
                <span style="font-size: 0.8em; color: #888;">Click to expand</span>
            </div>
            <div class="tree">
'''
        # Generate tree nodes - top level only shows submodules, not registers
        for module in hierarchy.top_modules:
            html += self._generate_tree_node(module, 0, show_registers=False)

        html += '''
            </div>
        </div>
'''
        return html

    def _generate_tree_node(self, module: Module, level: int, show_registers: bool = True) -> str:
        """Generate tree node for a module"""
        # For top-level modules (level 0 with show_registers=False), only consider submodules as children
        if show_registers:
            has_children = len(module.submodules) > 0 or len(module.registers) > 0
        else:
            has_children = len(module.submodules) > 0

        node_id = f"tree_{module.name}"
        indent = "&nbsp;&nbsp;" * level

        array_badge = ""
        if module.is_array and module.array_count > 1:
            array_badge = f' <span class="badge badge-array">x{module.array_count}</span>'

        # Show array instance badge
        instance_badge = ""
        if getattr(module, 'is_array_instance', False):
            instance_badge = f' <span class="badge badge-instance">inst</span>'

        html = f'''
                <div class="tree-item">
                    {indent}<span class="tree-toggle" onclick="toggleTree('{node_id}', event)">
                        {'▼' if has_children else '&nbsp;'}</span>
                    <a class="tree-link module" onclick="showModule('{module.name}', event)">
                        {module.name}{array_badge}{instance_badge}
                    </a>
'''

        if has_children:
            html += f'''
                    <div class="tree-children expanded" id="{node_id}">
'''
            # Sub-modules
            for sub in module.submodules:
                html += self._generate_tree_node(sub, level + 1, show_registers=True)

            # Registers - only show for leaf modules or when explicitly requested
            if show_registers:
                for reg in module.registers:
                    html += f'''
                        <div class="tree-item">
                            {indent}&nbsp;&nbsp;&nbsp;&nbsp;
                            <a class="tree-link register" onclick="showRegister('{module.name}', '{reg.name}')">
                                {reg.name}
                            </a>
                        </div>
'''

            html += '''
                    </div>
'''

        html += '''
                </div>
'''
        return html

    def _generate_main_content(self, hierarchy: RegisterHierarchy) -> str:
        """Generate main content area"""
        html = '''
        <div class="content">
            <div class="breadcrumb" id="breadcrumb">
                <a href="#" onclick="showHome()">Home</a>
            </div>

            <div class="search-results" id="searchResults">
                <h3>Search Results</h3>
                <div id="searchResultsList"></div>
            </div>
'''

        # Generate module cards
        def generate_module_cards(module: Module, parent: Module = None):
            nonlocal html
            html += self._generate_module_card(module, parent)
            for reg in module.registers:
                html += self._generate_register_detail(module, reg)
            for sub in module.submodules:
                generate_module_cards(sub, module)

        for mod in hierarchy.top_modules:
            generate_module_cards(mod)

        html += '''
        </div>
    </div>
'''
        return html

    def _generate_module_card(self, module: Module, parent_module: Module = None) -> str:
        """Generate module detail card"""
        array_info = f" [Array: {module.array_count}]" if module.is_array and module.array_count > 1 else ""

        # Build instance info for array instances
        instance_info = ""
        if getattr(module, 'is_array_instance', False):
            base_name = getattr(module, 'base_module_name', '')
            instance_info = f'''
                        <span class="module-info-label">Instance Of:</span>
                        <span class="module-info-value" style="color: #28a745;">{base_name}</span>'''

        # Calculate offset from parent if exists
        offset_from_parent = module.start_addr
        if parent_module:
            offset_from_parent = module.start_addr - parent_module.start_addr

        html = f'''
            <div class="module-card" id="module_{module.name}">
                <div class="module-header">
                    <h2>{module.name}{array_info}</h2>
                    <div class="module-info">
                        <span class="module-info-label">Absolute Address:</span>
                        <span class="module-info-value" style="color: #0066cc; font-weight: bold;">0x{module.start_addr:08X}</span>
                        <span class="module-info-label">Offset:</span>
                        <span class="module-info-value">+0x{offset_from_parent:X}</span>
                        <span class="module-info-label">End Address:</span>
                        <span class="module-info-value">0x{module.end_addr:08X}</span>{instance_info}
                        <span class="module-info-label">Size:</span>
                        <span class="module-info-value">0x{module.size:X} ({module.size // 1024 if module.size >= 1024 else 1} KB)</span>
                        <span class="module-info-label">Registers:</span>
                        <span class="module-info-value">{len(module.registers)}</span>
                        <span class="module-info-label">Submodules:</span>
                        <span class="module-info-value">{len(module.submodules)}</span>
                    </div>
                </div>
'''
        # Submodule address mapping table (for addr_map only modules like GCS, soc_addr_map)
        if module.submodules:
            html += '''
                <div class="register-section">
                    <h3 style="margin-bottom: 15px; color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 8px;">
                        Address Mapping
                    </h3>
                    <table class="register-table">
                        <thead>
                            <tr>
                                <th>Module Name</th>
                                <th>Absolute Address</th>
                                <th>Offset</th>
                                <th>Size</th>
                                <th>Type</th>
                            </tr>
                        </thead>
                        <tbody>
'''
            for sub in module.submodules:
                type_label = "Array Instance" if getattr(sub, 'is_array_instance', False) else "Module"
                offset_from_parent = sub.start_addr - module.start_addr
                html += f'''
                            <tr>
                                <td>
                                    <a class="register-name" onclick="showModule('{sub.name}')">
                                        {sub.name}
                                    </a>
                                </td>
                                <td class="offset-cell" style="color: #0066cc; font-weight: 500;">0x{sub.start_addr:08X}</td>
                                <td class="offset-cell">+0x{offset_from_parent:X}</td>
                                <td class="offset-cell">0x{sub.size:X} ({sub.size // 1024 if sub.size >= 1024 else sub.size} {'KB' if sub.size >= 1024 else 'B'})</td>
                                <td>{type_label}</td>
                            </tr>
'''
            html += '''
                        </tbody>
                    </table>
                </div>
'''

        # Register table (if module has registers)
        if module.registers:
            registers_per_page = 50
            total_registers = len(module.registers)
            total_pages = (total_registers + registers_per_page - 1) // registers_per_page

            html += f'''
                <div class="register-section">
                    <h3 style="margin-bottom: 15px; color: #333; border-bottom: 2px solid #28a745; padding-bottom: 8px;">
                        Registers
                        <span style="float: right; font-size: 0.8em; color: #666; font-weight: normal;">
                            Total: {total_registers}
                        </span>
                    </h3>
                    <table class="register-table" id="regtable_{module.name}">
                        <thead>
                            <tr>
                                <th>Absolute Address</th>
                                <th>Offset</th>
                                <th>Name</th>
                                <th>Width</th>
                                <th>Fields</th>
                            </tr>
                        </thead>
'''
            # Generate paginated register rows
            for page_idx in range(total_pages):
                start_idx = page_idx * registers_per_page
                end_idx = min(start_idx + registers_per_page, total_registers)
                display_style = "display: table-row-group;" if page_idx == 0 else "display: none;"

                html += f'''
                        <tbody class="reg-page-{module.name}" data-page="{page_idx}" style="{display_style}">
'''
                for reg in module.registers[start_idx:end_idx]:
                    field_count = len(reg.fields)
                    abs_addr = module.start_addr + reg.offset
                    html += f'''
                            <tr>
                                <td class="offset-cell" style="color: #0066cc; font-weight: 500;">0x{abs_addr:08X}</td>
                                <td class="offset-cell">+0x{reg.offset:04X}</td>
                                <td>
                                    <a class="register-name" onclick="showRegister('{module.name}', '{reg.name}')">
                                        {reg.name}
                                    </a>
                                </td>
                                <td>{reg.width} bits</td>
                                <td>{field_count}</td>
                            </tr>
'''
                html += '''
                        </tbody>
'''

            # Pagination controls
            if total_pages > 1:
                html += f'''
                    </table>
                    <div class="pagination" id="pagination_{module.name}">
                        <button class="page-btn" onclick="changePage('{module.name}', -1)" title="Previous page">◀</button>
                        <span class="page-info">
                            Page <span class="current-page" id="page-current-{module.name}">1</span> / <span id="page-total-{module.name}">{total_pages}</span>
                        </span>
                        <button class="page-btn" onclick="changePage('{module.name}', 1)" title="Next page">▶</button>
                        <span class="page-size">50 per page</span>
                    </div>
                </div>
'''
            else:
                html += '''
                    </table>
                </div>
'''

        # Show message if module is addr_map only (no registers, only submodules)
        if not module.registers and not module.submodules:
            html += '''
                <div class="register-section">
                    <p style="color: #666; font-style: italic;">
                        This module has no registers or submodules defined.
                    </p>
                </div>
'''

        html += '''
            </div>
'''
        return html

    def _generate_register_detail(self, module: Module, reg: Register) -> str:
        """Generate register detail view"""
        full_addr = module.start_addr + reg.offset

        html = f'''
            <div class="register-detail" id="register_{module.name}_{reg.name}">
                <div class="register-detail-header">
                    <h3>{module.name}.{reg.name}</h3>
                    <div class="module-info">
                        <span class="module-info-label">Absolute Address:</span>
                        <span class="module-info-value" style="color: #0066cc; font-weight: bold;">0x{full_addr:08X}</span>
                        <span class="module-info-label">Offset:</span>
                        <span class="module-info-value">+0x{reg.offset:04X}</span>
                        <span class="module-info-label">Width:</span>
                        <span class="module-info-value">{reg.width} bits</span>
                    </div>
                </div>
                <div class="register-section">
                    <table class="field-table">
                        <thead>
                            <tr>
                                <th>Field</th>
                                <th>Bits</th>
                                <th>Access</th>
                                <th>Reset Value</th>
                                <th>Description</th>
                            </tr>
                        </thead>
                        <tbody>
'''
        for field in reg.fields:
            access_class = f"access-{field.access.lower()}"
            html += f'''
                            <tr>
                                <td><strong>{field.name}</strong></td>
                                <td><span class="bit-range">[{field.msb}:{field.lsb}]</span></td>
                                <td class="{access_class}">{field.access}</td>
                                <td><code>{field.reset_value}</code></td>
                                <td>{field.description or '-'}</td>
                            </tr>
'''

        html += '''
                        </tbody>
                    </table>
                </div>
            </div>
'''
        return html

    def _generate_footer(self, hierarchy: RegisterHierarchy,
                         module_index: Dict, register_index: Dict) -> str:
        """Generate footer and JavaScript"""
        module_index_json = json.dumps(module_index)
        register_index_json = json.dumps(register_index)

        return f'''
    <div class="footer">
        <p>Register Description Tool | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>

    <script>
        const moduleIndex = {module_index_json};
        const registerIndex = {register_index_json};
        let currentModule = null;

        function toggleTree(nodeId, event) {{
            event.stopPropagation();
            const node = document.getElementById(nodeId);
            const toggle = event.target;
            if (node) {{
                node.classList.toggle('expanded');
                toggle.textContent = node.classList.contains('expanded') ? '▼' : '▶';
            }}
        }}

        function showModule(moduleName, evt) {{
            document.querySelectorAll('.module-card').forEach(card => card.classList.remove('active'));
            document.querySelectorAll('.register-detail').forEach(detail => detail.classList.remove('active'));
            document.getElementById('searchResults').classList.remove('active');

            const moduleCard = document.getElementById('module_' + moduleName);
            if (moduleCard) {{
                moduleCard.classList.add('active');
                currentModule = moduleName;
                updateBreadcrumb(moduleName);
            }}

            document.querySelectorAll('.tree-link').forEach(link => link.classList.remove('active'));
            if (evt) {{
                const target = evt.target.closest('.tree-link') || evt.target;
                target.classList.add('active');
            }}

            // Reset pagination to first page
            resetPagination(moduleName);
        }}

        function resetPagination(moduleName) {{
            // Reset page state
            pageState[moduleName] = 0;

            // Update display if pagination exists
            const currentPageEl = document.getElementById('page-current-' + moduleName);
            const pagination = document.getElementById('pagination_' + moduleName);
            if (!currentPageEl || !pagination) return;

            currentPageEl.textContent = '1';

            // Show first tbody, hide others
            const tbodies = document.querySelectorAll('.reg-page-' + moduleName);
            tbodies.forEach(tb => {{
                const pageIdx = parseInt(tb.dataset.page);
                tb.style.display = pageIdx === 0 ? 'table-row-group' : 'none';
            }});

            // Update button states
            const buttons = pagination.querySelectorAll('.page-btn');
            if (buttons.length >= 2) {{
                buttons[0].disabled = true;
                const totalPages = parseInt(document.getElementById('page-total-' + moduleName).textContent);
                buttons[1].disabled = totalPages <= 1;
            }}
        }}

        function showRegister(moduleName, regName) {{
            document.querySelectorAll('.module-card').forEach(card => card.classList.remove('active'));
            document.querySelectorAll('.register-detail').forEach(detail => detail.classList.remove('active'));
            document.getElementById('searchResults').classList.remove('active');

            const regDetail = document.getElementById('register_' + moduleName + '_' + regName);
            if (regDetail) {{
                regDetail.classList.add('active');
                updateBreadcrumb(moduleName, regName);
            }}
        }}

        function updateBreadcrumb(moduleName, regName = null) {{
            let html = '<a href="#" onclick="showHome(); return false;">Home</a>';
            if (moduleName) {{
                html += '<span class="breadcrumb-separator">/</span>';
                html += '<a href="#" onclick="showModule(\\'' + moduleName + '\\', null); return false;">' + moduleName + '</a>';
            }}
            if (regName) {{
                html += '<span class="breadcrumb-separator">/</span>';
                html += regName;
            }}
            document.getElementById('breadcrumb').innerHTML = html;
        }}

        function showHome() {{
            document.querySelectorAll('.module-card').forEach(card => card.classList.remove('active'));
            document.querySelectorAll('.register-detail').forEach(detail => detail.classList.remove('active'));
            document.getElementById('searchResults').classList.remove('active');
            document.getElementById('breadcrumb').innerHTML = '<a href="#" onclick="showHome(); return false;">Home</a>';

            // Show all modules and reset their pagination
            document.querySelectorAll('.module-card').forEach(card => {{
                card.classList.add('active');
                const moduleId = card.id.replace('module_', '');
                resetPagination(moduleId);
            }});
        }}

        // Pagination state
        const pageState = {{}};

        function changePage(moduleName, direction) {{
            const totalPages = parseInt(document.getElementById('page-total-' + moduleName).textContent);
            if (!pageState[moduleName]) {{
                pageState[moduleName] = 0;
            }}

            const newPage = pageState[moduleName] + direction;
            if (newPage < 0 || newPage >= totalPages) return;

            pageState[moduleName] = newPage;

            // Update page display
            document.getElementById('page-current-' + moduleName).textContent = newPage + 1;

            // Show/hide appropriate tbody
            const tbodies = document.querySelectorAll('.reg-page-' + moduleName);
            tbodies.forEach(tb => {{
                const pageIdx = parseInt(tb.dataset.page);
                tb.style.display = pageIdx === newPage ? 'table-row-group' : 'none';
            }});

            // Update button states
            const pagination = document.getElementById('pagination_' + moduleName);
            const buttons = pagination.querySelectorAll('.page-btn');
            buttons[0].disabled = newPage === 0;
            buttons[1].disabled = newPage === totalPages - 1;
        }}

        function performSearch() {{
            const term = document.getElementById('globalSearch').value.toLowerCase().trim();
            if (!term) return;

            const results = [];

            // Search modules
            for (const [name, mod] of Object.entries(moduleIndex)) {{
                if (name.toLowerCase().includes(term)) {{
                    results.push({{type: 'module', name: name, display: name}});
                }}
            }}

            // Search registers
            for (const [name, reg] of Object.entries(registerIndex)) {{
                if (reg.name.toLowerCase().includes(term) ||
                    name.toLowerCase().includes(term) ||
                    ('0x' + reg.address.toString(16)).includes(term)) {{
                    results.push({{
                        type: 'register',
                        name: reg.name,
                        module: reg.module,
                        display: reg.module + '.' + reg.name + ' (0x' + reg.address.toString(16).toUpperCase() + ')'
                    }});
                }}
            }}

            displaySearchResults(results);
        }}

        function displaySearchResults(results) {{
            const listDiv = document.getElementById('searchResultsList');

            if (results.length === 0) {{
                listDiv.innerHTML = '<p>No results found.</p>';
            }} else {{
                listDiv.innerHTML = results.map(r => {{
                    const typeClass = r.type === 'module' ? 'module' : 'register';
                    const typeLabel = r.type === 'module' ? 'MODULE' : 'REG';
                    const onclick = r.type === 'module'
                        ? `showModule('${{r.name}}')`
                        : `showRegister('${{r.module}}', '${{r.name}}')`;
                    return `<div class="search-result-item" onclick="${{onclick}}">
                        <span class="search-result-type ${{typeClass}}">${{typeLabel}}</span>
                        ${{r.display}}
                    </div>`;
                }}).join('');
            }}

            document.querySelectorAll('.module-card').forEach(card => card.classList.remove('active'));
            document.querySelectorAll('.register-detail').forEach(detail => detail.classList.remove('active'));
            document.getElementById('searchResults').classList.add('active');
        }}

        document.getElementById('globalSearch').addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') performSearch();
        }});

        // Show all modules by default
        showHome();
    </script>
</body>
</html>
'''

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save HTML to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving HTML: {e}")
            return False
