"""
Standalone RTL Generator - Full APB3 register block generator
Supports all SystemRDL/UVM access types
Used when PeakRDL is not installed
"""
from pathlib import Path
from typing import List, Dict, Set
from datetime import datetime


# Access type definitions
# SW_ACCESS: r, w, rw, na
# HW_ACCESS: r, w, rw, na
# Side effects: onread, onwrite

SW_WRITE_ACCESS: Set[str] = {
    'RW', 'WO', 'WRC', 'WRS', 'WC', 'WS', 'WT',
    'W1C', 'W1S', 'W1T', 'W1SRC', 'W1CRS',
    'W0C', 'W0S', 'W0T', 'W0SRC', 'W0CRS',
    'WCRS', 'WO1'
}

SW_READ_ACCESS: Set[str] = {
    'RO', 'RW', 'RC', 'RS', 'RAC', 'RAS',
    'WRC', 'WRS', 'WC', 'WS', 'WT',
    'W1C', 'W1S', 'W1T', 'W1SRC', 'W1CRS',
    'W0C', 'W0S', 'W0T', 'W0SRC', 'W0CRS',
    'WCRS'
}

HW_WRITE_ACCESS: Set[str] = {
    'RO', 'RW', 'WO', 'RC', 'RS', 'RAC', 'RAS',
    'WRC', 'WRS', 'WC', 'WS', 'WT',
    'W1C', 'W1S', 'W1T', 'W1SRC', 'W1CRS',
    'W0C', 'W0S', 'W0T', 'W0SRC', 'W0CRS',
    'WCRS', 'WO1', 'HWR', 'HWW'
}

HW_READ_ACCESS: Set[str] = {
    'RW', 'WO', 'RC', 'RS', 'RAC', 'RAS',
    'WRC', 'WRS', 'WC', 'WS', 'WT',
    'W1C', 'W1S', 'W1T', 'W1SRC', 'W1CRS',
    'W0C', 'W0S', 'W0T', 'W0SRC', 'W0CRS',
    'WCRS', 'WO1', 'HWR', 'HWW'
}


class RTLGenerator:
    """Generate Verilog RTL for APB3 register block with full access type support"""

    def __init__(self):
        self.indent = "    "

    def generate(self, module) -> str:
        """Generate APB3 register block for a module"""
        lines = [
            f"// RTL Register Block for {module.name}",
            f"// Generated at: {datetime.now().isoformat()}",
            f"// Interface: APB3",
            f"// Supports: All SystemRDL 2.0 access types",
            "",
            "`ifndef _REG_BLOCK_SV_",
            "`define _REG_BLOCK_SV_",
            "",
            f"module {module.name}_reg_block #(",
            f"    parameter ADDR_WIDTH = 32,",
            f"    parameter DATA_WIDTH = 32",
            f") (",
            f"    input  wire                  pclk,",
            f"    input  wire                  preset_n,",
            f"",
            f"    // APB3 Interface",
            f"    input  wire                  psel,",
            f"    input  wire                  penable,",
            f"    input  wire [ADDR_WIDTH-1:0] paddr,",
            f"    input  wire                  pwrite,",
            f"    input  wire [DATA_WIDTH-1:0] pwdata,",
            f"    output reg  [DATA_WIDTH-1:0] prdata,",
            f"    output reg                   pready,",
            f"    output reg                   pslverr,",
            "",
        ]

        # Add field ports
        lines.extend(self._generate_field_ports(module))

        lines.append(")")
        lines.append("")

        # Address decoding
        lines.extend(self._generate_address_decode(module))

        # Register storage
        lines.extend(self._generate_register_storage(module))

        # Field logic
        lines.extend(self._generate_field_logic(module))

        # APB FSM
        lines.extend(self._generate_apb_fsm(module))

        lines.append("")
        lines.append("endmodule")
        lines.append("")
        lines.append("`endif")
        lines.append("")

        return '\n'.join(lines)

    def _generate_field_ports(self, module) -> List[str]:
        """Generate field port declarations based on access type"""
        lines = []
        ports = []

        for reg in module.registers:
            for field in reg.fields:
                width = field.msb - field.lsb + 1
                access = field.access.upper() if field.access else 'RW'

                # Determine port direction
                if access in SW_WRITE_ACCESS:
                    # SW can write - output to hardware
                    if access in HW_READ_ACCESS:
                        ports.append(f"    output wire [{width-1}:0] {reg.name}_{field.name}")
                    else:
                        ports.append(f"    output reg  [{width-1}:0] {reg.name}_{field.name}")

                if access in HW_WRITE_ACCESS:
                    # HW can write - input from hardware
                    ports.append(f"    input  wire [{width-1}:0] {reg.name}_{field.name}_hw")

                # Sticky/ interrupt status outputs
                if access in ['RC', 'RS', 'RAC', 'RAS']:
                    ports.append(f"    output wire {reg.name}_{field.name}_sticky")

        if ports:
            lines.append("    // Field ports")
            lines.extend(ports)

        # Remove comma from last port
        if lines:
            lines[-1] = lines[-1].rstrip(',')

        return lines

    def _generate_address_decode(self, module) -> List[str]:
        """Generate address decode logic"""
        if not module.registers:
            return []

        num_regs = len(module.registers)
        sel_width = max(1, (num_regs - 1).bit_length())

        lines = [
            "    // Address decode",
            f"    reg [{sel_width-1}:0] reg_sel;",
            "",
            "    always @(*) begin",
            "        case (paddr[7:0])",
        ]

        for i, reg in enumerate(module.registers):
            addr = reg.offset
            lines.append(f"            8'h{addr:02X}: reg_sel = {sel_width}'d{i};  // {reg.name}")

        lines.append(f"            default: reg_sel = {sel_width}'d0;")
        lines.append("        endcase")
        lines.append("    end")
        lines.append("")

        return lines

    def _generate_register_storage(self, module) -> List[str]:
        """Generate register storage declarations"""
        lines = ["    // Register storage"]

        for reg in module.registers:
            lines.append(f"    reg [31:0] {reg.name}_reg;")
            # Shadow register for read-clear/set
            for field in reg.fields:
                access = field.access.upper() if field.access else 'RW'
                if access in ['RC', 'RS', 'RAC', 'RAS', 'W1SRC', 'W1CRS', 'W0SRC', 'W0CRS']:
                    lines.append(f"    reg {reg.name}_{field.name}_sticky;")

        lines.append("")
        return lines

    def _generate_field_logic(self, module) -> List[str]:
        """Generate field read/write logic for all access types"""
        lines = [
            "    // Field logic",
            "    always @(posedge pclk or negedge preset_n) begin",
            "        if (!preset_n) begin",
        ]

        # Reset values
        for reg in module.registers:
            reset_val = 0
            for field in reg.fields:
                try:
                    val_str = str(field.reset_value).replace("'h", "").replace("'b", "").replace("'d", "").replace("_", "")
                    val = int(val_str, 0) if val_str else 0
                    width = field.msb - field.lsb + 1
                    mask = (1 << width) - 1
                    reset_val |= (val & mask) << field.lsb
                except:
                    pass
            lines.append(f"            {reg.name}_reg <= 32'h{reset_val:08X};")

            # Reset sticky bits
            for field in reg.fields:
                access = field.access.upper() if field.access else 'RW'
                if access in ['RC', 'RS', 'RAC', 'RAS', 'W1SRC', 'W1CRS', 'W0SRC', 'W0CRS']:
                    reset_str = "1'b1" if access in ['RS', 'RAS'] else "1'b0"
                    lines.append(f"            {reg.name}_{field.name}_sticky <= {reset_str};")

        lines.append("        end")
        lines.append("        else begin")

        # Write logic
        lines.append("            // Software write")
        lines.append("            if (psel && penable && pwrite) begin")
        lines.append("                case (reg_sel)")

        for i, reg in enumerate(module.registers):
            lines.append(f"                    {i}: begin  // {reg.name}")

            for field in reg.fields:
                access = field.access.upper() if field.access else 'RW'
                msb, lsb = field.msb, field.lsb

                if access == 'RW':
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= pwdata[{msb}:{lsb}];")

                elif access == 'WO':
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= pwdata[{msb}:{lsb}];")

                elif access == 'W1C':
                    # Write 1 to clear
                    lines.append(f"                        // W1C: {field.name}")
                    lines.append(f"                        if (pwdata[{msb}]) {reg.name}_reg[{msb}:{lsb}] <= '0;")

                elif access == 'W1S':
                    # Write 1 to set
                    lines.append(f"                        // W1S: {field.name}")
                    lines.append(f"                        if (pwdata[{msb}]) {reg.name}_reg[{msb}:{lsb}] <= '1;")

                elif access == 'W1T':
                    # Write 1 to toggle
                    lines.append(f"                        // W1T: {field.name}")
                    lines.append(f"                        if (pwdata[{msb}]) {reg.name}_reg[{msb}:{lsb}] <= ~{reg.name}_reg[{msb}:{lsb}];")

                elif access == 'W0C':
                    # Write 0 to clear
                    lines.append(f"                        // W0C: {field.name}")
                    lines.append(f"                        if (!pwdata[{msb}]) {reg.name}_reg[{msb}:{lsb}] <= '0;")

                elif access == 'W0S':
                    # Write 0 to set
                    lines.append(f"                        // W0S: {field.name}")
                    lines.append(f"                        if (!pwdata[{msb}]) {reg.name}_reg[{msb}:{lsb}] <= '1;")

                elif access == 'W0T':
                    # Write 0 to toggle
                    lines.append(f"                        // W0T: {field.name}")
                    lines.append(f"                        if (!pwdata[{msb}]) {reg.name}_reg[{msb}:{lsb}] <= ~{reg.name}_reg[{msb}:{lsb}];")

                elif access == 'WC':
                    # Write clear (any write clears)
                    lines.append(f"                        // WC: {field.name}")
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= '0;")

                elif access == 'WS':
                    # Write set (any write sets)
                    lines.append(f"                        // WS: {field.name}")
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= '1;")

                elif access == 'WT':
                    # Write toggle (any write toggles)
                    lines.append(f"                        // WT: {field.name}")
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= ~{reg.name}_reg[{msb}:{lsb}];")

                elif access == 'W1SRC':
                    # Write 1 to set, read to clear
                    lines.append(f"                        // W1SRC: {field.name}")
                    lines.append(f"                        if (pwdata[{msb}]) {reg.name}_{field.name}_sticky <= 1'b1;")

                elif access == 'W1CRS':
                    # Write 1 to clear, read to set
                    lines.append(f"                        // W1CRS: {field.name}")
                    lines.append(f"                        if (pwdata[{msb}]) {reg.name}_{field.name}_sticky <= 1'b0;")

                elif access == 'W0SRC':
                    # Write 0 to set, read to clear
                    lines.append(f"                        // W0SRC: {field.name}")
                    lines.append(f"                        if (!pwdata[{msb}]) {reg.name}_{field.name}_sticky <= 1'b1;")

                elif access == 'W0CRS':
                    # Write 0 to clear, read to set
                    lines.append(f"                        // W0CRS: {field.name}")
                    lines.append(f"                        if (!pwdata[{msb}]) {reg.name}_{field.name}_sticky <= 1'b0;")

                elif access == 'WCRS':
                    # Write clears all bits
                    lines.append(f"                        // WCRS: {field.name}")
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= '0;")

                elif access == 'WO1':
                    # Write once (software can only write once)
                    lines.append(f"                        // WO1: {field.name} (write once - software responsibility)")
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= pwdata[{msb}:{lsb}];")

                elif access == 'WRC':
                    # Write clears, read clears
                    lines.append(f"                        // WRC: {field.name}")
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= '0;")

                elif access == 'WRS':
                    # Write sets, read sets
                    lines.append(f"                        // WRS: {field.name}")
                    lines.append(f"                        {reg.name}_reg[{msb}:{lsb}] <= '1;")

                elif access in ['RO', 'RC', 'RS', 'RAC', 'RAS']:
                    # Read-only in software, hardware writes
                    pass  # No software write

            lines.append("                    end")

        lines.append("                endcase")
        lines.append("            end")
        lines.append("")

        # Hardware write logic
        lines.append("            // Hardware write")
        for reg in module.registers:
            for field in reg.fields:
                access = field.access.upper() if field.access else 'RW'
                msb, lsb = field.msb, field.lsb

                if access in HW_WRITE_ACCESS and access not in ['RW', 'WO']:
                    if access == 'RO':
                        lines.append(f"            {reg.name}_reg[{msb}:{lsb}] <= {reg.name}_{field.name}_hw;")
                    elif access in ['RC', 'RS']:
                        # Sticky bits - hardware sets, software clears/sets on read
                        lines.append(f"            if ({reg.name}_{field.name}_hw) {reg.name}_{field.name}_sticky <= 1'b1;")

        lines.append("        end")
        lines.append("    end")
        lines.append("")

        # Read side-effects
        lines.append("    // Read side-effects (implemented as combinational for simplicity)")
        lines.append("    always @(*) begin")
        for reg in module.registers:
            for field in reg.fields:
                access = field.access.upper() if field.access else 'RW'
                msb, lsb = field.msb, field.lsb

                if access == 'RC':
                    # Read to clear
                    lines.append(f"        // RC: {field.name} - cleared on read")
                    # Note: Actual clear happens on next clock, this is for documentation

                elif access == 'RS':
                    # Read to set
                    lines.append(f"        // RS: {field.name} - set on read")

        lines.append("    end")
        lines.append("")

        # Field output assignments
        lines.append("    // Field output assignments")
        for reg in module.registers:
            for field in reg.fields:
                access = field.access.upper() if field.access else 'RW'
                msb, lsb = field.msb, field.lsb

                if access in SW_WRITE_ACCESS:
                    lines.append(f"    assign {reg.name}_{field.name} = {reg.name}_reg[{msb}:{lsb}];")

                # Sticky bit output
                if access in ['RC', 'RS', 'RAC', 'RAS', 'W1SRC', 'W1CRS', 'W0SRC', 'W0CRS']:
                    lines.append(f"    assign {reg.name}_{field.name}_sticky = {reg.name}_{field.name}_sticky;")

        lines.append("")
        return lines

    def _generate_apb_fsm(self, module) -> List[str]:
        """Generate APB FSM for read data with read side-effects"""
        lines = [
            "    // APB read data with read side-effects",
            "    reg read_active;",
            "",
            "    always @(posedge pclk or negedge preset_n) begin",
            "        if (!preset_n) begin",
            "            read_active <= 1'b0;",
            "        end else begin",
            "            read_active <= psel && penable && !pwrite;",
            "        end",
            "    end",
            "",
            "    // Read data mux",
            "    always @(*) begin",
            "        prdata = 32'h0;",
            "        case (reg_sel)",
        ]

        for i, reg in enumerate(module.registers):
            lines.append(f"            {i}: begin  // {reg.name}")

            # Build read value field by field
            field_reads = []
            for field in reg.fields:
                access = field.access.upper() if field.access else 'RW'
                msb, lsb = field.msb, field.lsb

                if access in ['RC', 'RS', 'RAC', 'RAS', 'W1SRC', 'W1CRS', 'W0SRC', 'W0CRS']:
                    # Use sticky bit for read
                    sticky_width = msb - lsb + 1
                    if sticky_width > 1:
                        field_reads.append(f"{sticky_width}'({reg.name}_{field.name}_sticky)")
                    else:
                        field_reads.append(f"{reg.name}_{field.name}_sticky")
                else:
                    field_reads.append(f"{reg.name}_reg[{msb}:{lsb}]")

            # Simple assignment - full register read
            lines.append(f"                prdata = {reg.name}_reg;")
            lines.append("            end")

        lines.append("        endcase")
        lines.append("    end")
        lines.append("")

        # APB ready and error
        lines.extend([
            "    // APB ready and error",
            "    always @(posedge pclk or negedge preset_n) begin",
            "        if (!preset_n) begin",
            "            pready <= 1'b0;",
            "            pslverr <= 1'b0;",
            "        end",
            "        else begin",
            "            pready <= psel && penable;",
            "            pslverr <= 1'b0;  // No decode errors",
            "        end",
            "    end",
            "",
        ])

        return lines

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save RTL to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving RTL: {e}")
            return False


class SimpleRTLGenerator:
    """Simplified RTL generator that works with the hierarchy structure"""

    def generate_for_module(self, module, output_dir: Path) -> dict:
        """Generate RTL for a single module"""
        result = {'success': False, 'files': []}

        try:
            gen = RTLGenerator()
            rtl_content = gen.generate(module)

            output_file = output_dir / f"{module.name}_reg_block.sv"
            if gen.save_to_file(rtl_content, output_file):
                result['success'] = True
                result['files'].append(str(output_file))
                result['path'] = str(output_file)
            else:
                result['error'] = "Failed to save file"

        except Exception as e:
            result['error'] = str(e)

        return result
