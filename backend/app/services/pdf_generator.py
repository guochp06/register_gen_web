"""
PDF Generator service: Playwright + pypdf

Builds a single clean PDF document from peakrdl-html content fragments.
Concatenates all module content pages into one HTML, renders once with
Playwright, and adds a bookmark outline from the RAL hierarchy.

Key design:
  - Content HTML fragments are pure <div> with register tables (no sidebar/JS)
  - All fragments are concatenated into one HTML string
  - Cross-module links (href="#ModuleName") are preserved as PDF anchors
  - Print CSS controls page breaks between modules
  - A single Playwright render produces the final PDF
  - Browser is ALWAYS closed in finally block
  - Global semaphore limits concurrent instances (max 3)
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import socket
import tempfile
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# Global semaphore: at most 3 concurrent PDF generation tasks
_pdf_semaphore = asyncio.Semaphore(3)

GENERATION_TIMEOUT = 300
PAGE_LOAD_TIMEOUT = 60000     # ms for combined HTML
RENDER_BUFFER_MS = 2000


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _QuietHTTPHandler(SimpleHTTPRequestHandler):
    serve_dir: str = ""
    def __init__(self, *args, **kwargs):
        self.directory = self.serve_dir
        super().__init__(*args, **kwargs, directory=self.directory)
    def log_message(self, format, *args):
        pass


class _TempHTTPServer:
    def __init__(self, serve_dir: Path):
        self.serve_dir = str(serve_dir)
        self.port = _find_free_port()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        _QuietHTTPHandler.serve_dir = self.serve_dir
        self._server = HTTPServer(("127.0.0.1", self.port), _QuietHTTPHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("HTTP server port %d serving %s", self.port, self.serve_dir)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
            self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class PDFGenerationError(Exception):
    """Raised when PDF generation fails irrecoverably."""


# ── HTML builder ────────────────────────────────────────────────────────────

_PRINT_CSS = """
<style>
  /* Reset */
  body { font-family: 'Lato', 'Roboto Slab', sans-serif; margin: 0; padding: 0; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0; page-break-inside: avoid; }
  th, td { border: 1px solid #666; padding: 4px 8px; text-align: left; font-size: 11px; }
  th { background: #f0f0f0; font-weight: bold; }
  h1 { font-size: 18px; border-bottom: 2px solid #333; padding-bottom: 4px; margin-top: 8px; }
  h2 { font-size: 15px; margin-top: 12px; }
  dl.node-info { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; font-size: 12px; }
  .address { font-family: monospace; font-size: 11px; }
  a { color: #2980b9; }
  /* Each module starts on a new page */
  .pdf-module { page-break-before: always; padding: 0 8px; }
  .pdf-module:first-child { page-break-before: avoid; }
  /* Inline tables should not break mid-table */
  .pdf-module table { page-break-inside: avoid; }
  /* Hide empty elements */
  #_AbsAddrDetails:empty, #_AbsAddrDetails:has(> :empty) { display: none; }
  @page { margin: 8mm; size: A4 landscape; }
  @media print {
    .pdf-module { page-break-before: always; }
    .pdf-module:first-child { page-break-before: avoid; }
  }
</style>
"""

_HTML_WRAPPER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
{style}
</head>
<body>
{body}
</body>
</html>"""


def _sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def _load_ral_data(html_dir: Path) -> list[dict]:
    """Load RAL JSON data from peakrdl-html output."""
    ral_files = sorted(html_dir.glob("data/ral-data-*.json"))
    entries = []
    for rf in ral_files:
        with open(rf) as f:
            data = json.load(f)
            entries.extend(data)
    return entries


def _build_ral_path(ral: list[dict], node_idx: int) -> str:
    """Build dot-separated path for a RAL node (matching peakrdl-html's ral.js)."""
    parts = []
    idx = node_idx
    while idx is not None and idx >= 0 and idx < len(ral):
        parts.append(ral[idx]["name"])
        parent = ral[idx].get("parent")
        idx = parent if parent is not None and parent >= 0 else None
    return ".".join(reversed(parts))


def _traverse_tree(ral: list[dict], node_idx: int, visitor):
    """Depth-first traversal of the RAL tree."""
    entry = ral[node_idx]
    visitor(node_idx, entry)
    for child_idx in entry.get("children", []):
        _traverse_tree(ral, child_idx, visitor)


def build_combined_html(html_dir: Path) -> str:
    """
    Build a single HTML document by concatenating all peakrdl-html content
    fragments with proper id anchors and print CSS.
    """
    ral = _load_ral_data(html_dir)
    content_dir = html_dir / "content"

    if not ral:
        raise PDFGenerationError("No RAL data found — HTML not generated yet")

    fragments = []
    total = [0]  # mutable counter

    def _collect(node_idx, entry):
        name = entry["name"]
        path = _build_ral_path(ral, node_idx)
        uid = _sha1_hex(path)
        content_file = content_dir / f"{uid}.html"

        if content_file.exists():
            html = content_file.read_text(encoding="utf-8")
            html = re.sub(r'<!--[^>]*-->', '', html, count=1)

            # Compute absolute address by summing offsets along the path
            abs_addr = 0
            idx = node_idx
            while idx is not None and idx >= 0:
                off = ral[idx].get("offset", "0")
                try:
                    abs_addr += int(off, 16) if isinstance(off, str) else off
                except (ValueError, TypeError):
                    pass
                parent = ral[idx].get("parent")
                idx = parent if (parent is not None and parent >= 0) else None
            # Inject into the empty _AbsAddr element
            html = html.replace(
                '<dd id="_AbsAddr" class="address"></dd>',
                f'<dd id="_AbsAddr" class="address">0x{abs_addr:X}</dd>'
            )

            # 1. Rewrite ?p=FULL_PATH links → #FULL_PATH anchors
            #    href="?p=top.inst.C2C0.Tx_FIFO" → href="#top.inst.C2C0.Tx_FIFO"
            html = re.sub(r'href="\?p=([^"]+)"', r'href="#\1"', html)

            # 2. Keep original #name links as-is (same-page field refs).
            #    Only ?p= links need conversion for cross-module jumps.

            # 3. Use full path as the unique id
            html = re.sub(
                r'<h1\b([^>]*)>',
                rf'<h1 id="{path}"\1>',
                html,
                count=1
            )
            fragments.append(f'<div class="pdf-module" id="{path}">\n{html}\n</div>')
            total[0] += 1
        else:
            logger.warning("Content file not found for %s (sha1=%s)", path, uid[:12])

    # Traverse from root nodes (parent=None)
    root_ids = [i for i, e in enumerate(ral) if e.get("parent") is None]
    for root_id in root_ids:
        _traverse_tree(ral, root_id, _collect)

    body = "\n".join(fragments)
    combined = _HTML_WRAPPER.format(style=_PRINT_CSS, body=body)
    logger.info("Combined HTML: %d modules, %.1f KB", total[0], len(combined) / 1024)
    return combined


def _ral_tree_to_bookmarks(ral: list[dict]) -> list[dict]:
    """Build a bookmark tree from RAL hierarchy with full paths for page lookup."""
    root_ids = [i for i, e in enumerate(ral) if e.get("parent") is None]
    bookmarks = []
    children_map = {i: e.get("children", []) for i, e in enumerate(ral)}

    def _build(node_idx):
        entry = ral[node_idx]
        node = {
            "title": entry["name"],
            "path": _build_ral_path(ral, node_idx),  # full unique path
            "children": []
        }
        for child_idx in children_map.get(node_idx, []):
            node["children"].append(_build(child_idx))
        return node

    for root_id in root_ids:
        bookmarks.append(_build(root_id))
    return bookmarks


# ── PDF Generator ───────────────────────────────────────────────────────────

class PDFGenerator:
    """Generate a clean PDF from peakrdl-html content fragments."""

    def __init__(self, html_dir: Path, output_dir: Path, title: str):
        self.html_dir = html_dir
        self.output_dir = output_dir
        self.title = title
        self._browser = None
        self._http_server: Optional[_TempHTTPServer] = None

    async def generate(self) -> Path:
        """
        Build combined HTML from content fragments, render with Playwright
        to a single PDF, and add bookmark outline from RAL tree.

        Returns path to the generated PDF.
        """
        async with _pdf_semaphore:
            logger.info("PDF generation start: %s", self.title)

            # Phase 1: build combined HTML
            combined_html = build_combined_html(self.html_dir)

            # Phase 2: write to a temp file inside html_dir so relative
            # font/CSS references work
            temp_html = self.html_dir / ".pdf_combined.html"
            temp_html.write_text(combined_html, encoding="utf-8")

            # Phase 3: serve via HTTP (fonts need proper MIME types)
            self._http_server = _TempHTTPServer(self.html_dir)
            self._http_server.start()

            try:
                async with async_playwright() as p:
                    self._browser = await p.chromium.launch()

                    try:
                        page = await self._browser.new_page()
                        # Block external CDNs
                        await page.route("**/mathjax*", lambda r: r.abort())
                        await page.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())

                        url = f"{self._http_server.base_url}/.pdf_combined.html"
                        logger.info("Loading combined HTML: %s", url)

                        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
                        await page.wait_for_timeout(RENDER_BUFFER_MS)

                        # Capture element positions AND viewport dimensions for
                        # converting pixel coords to PDF point coords later
                        self._elem_positions = await page.evaluate("""
                            () => {
                                const pos = {};
                                document.querySelectorAll('[id]').forEach(el => {
                                    if (!el.id || el.id.startsWith('_')) return;
                                    const r = el.getBoundingClientRect();
                                    pos[el.id] = {top: r.top, left: r.left};
                                });
                                return pos;
                            }
                        """)
                        self._viewport = await page.evaluate("""
                            () => ({width: window.innerWidth, height: window.innerHeight})
                        """)
                        logger.info("Captured %d element positions", len(self._elem_positions))

                        self.output_dir.mkdir(parents=True, exist_ok=True)
                        pdf_path = self.output_dir / f"{self.title}.pdf"

                        await page.pdf(
                            path=str(pdf_path),
                            format="A4",
                            landscape=True,
                            print_background=True,
                            margin={"top": "8mm", "bottom": "8mm", "left": "8mm", "right": "8mm"}
                        )
                        logger.info("PDF raw rendered: %s (%.1f KB)",
                                    pdf_path, pdf_path.stat().st_size / 1024)

                    finally:
                        if self._browser and self._browser.is_connected():
                            await self._browser.close()
                        self._browser = None
            finally:
                self._http_server.stop()

            # Phase 4: scan pages → name_to_page mapping
            name_to_page = self._scan_pages_for_names(pdf_path)

            # Phase 5: directly rewrite each link's Dest=/name → [page, /Fit]
            self._fix_link_destinations(pdf_path, name_to_page)

            # Phase 6: add bookmarks
            self._add_bookmarks(pdf_path, name_to_page)

            # Clean up temp file (AFTER post-processing)
            if temp_html.exists():
                temp_html.unlink()

            logger.info("PDF completed: %s (%.1f KB)", pdf_path,
                        pdf_path.stat().st_size / 1024)
            return pdf_path

    def _scan_pages_for_names(self, pdf_path: Path) -> dict:
        """
        Build name→page mapping by sequential module-to-page matching.

        Modules appear in depth-first order with page-break-before on each.
        The first heading on each page identifies the module.
        Sequential matching disambiguates duplicate names (e.g. multiple Tx_FIFO).
        """
        from pypdf import PdfReader

        ral = _load_ral_data(self.html_dir)
        if not ral:
            return {}

        # Ordered list: (full_path, short_name)
        ordered = []
        root_ids = [i for i, e in enumerate(ral) if e.get("parent") is None]
        def _collect(ni):
            e = ral[ni]
            ordered.append((_build_ral_path(ral, ni), e["name"]))
            for ci in e.get("children", []):
                _collect(ci)
        for rid in root_ids:
            _collect(rid)

        reader = PdfReader(str(pdf_path))
        name_to_page = {}

        mod_idx = 0
        for page_num in range(len(reader.pages)):
            text = reader.pages[page_num].extract_text() or ""
            if not text.strip():
                continue
            first_line = text.split("\n")[0].strip()
            head = text[:200].replace("\n", " ")

            # Try to match page to next module: heading first, then full text
            matched = False
            for offset in range(5):
                ci = mod_idx + offset
                if ci >= len(ordered):
                    break
                path, name = ordered[ci]
                if name and first_line:
                    # Normalize: heading "soc_addr_map Top Level" → "soc_addr_map_top_level"
                    fn = first_line.lower().replace(" ", "_")
                    n  = name.lower()
                    # Exact containment after normalization
                    if n in fn or fn in n or name in first_line:
                        name_to_page[path] = page_num
                        mod_idx = ci + 1
                        matched = True
                        break
                if name and name in head:
                    name_to_page[path] = page_num
                    mod_idx = ci + 1
                    matched = True
                    break
            # if not matched, this page is a continuation of the previous module
            # if not matched, this page might be a continuation (overflow) of the previous module

        # Unmatched modules get interpolated page numbers
        last_page = 0
        for path, name in ordered:
            if path not in name_to_page:
                name_to_page[path] = last_page
            else:
                last_page = name_to_page[path]

        # Field-level IDs: collect ALL element IDs from the combined HTML
        # and map them to the same page as their parent module.
        # This enables same-page anchors (#field_name) and address-map links
        # (#soc_addr_map_inst) to resolve correctly.
        combined = self.html_dir / ".pdf_combined.html"
        if combined.exists():
            import re as _re
            html_text = combined.read_text(encoding="utf-8")
            # Find all id="xxx" attributes
            all_ids = _re.findall(r'\bid="([^"]+)"', html_text)
            # For each id, find which pdf-module div contains it
            # Modules are sequential in the HTML; find the last module before each id
            module_positions = []
            for m in _re.finditer(r'<div class="pdf-module" id="([^"]+)">', html_text):
                module_positions.append((m.start(), m.group(1)))
            module_positions.append((len(html_text), None))  # sentinel

            import re as _re
            for eid in set(all_ids):
                if eid.startswith("_") or eid in name_to_page:
                    continue
                # Find the first occurrence of this id
                m = _re.search(rf'\bid="{_re.escape(eid)}"', html_text)
                if not m:
                    name_to_page[eid] = 0
                    continue
                pos = m.start()
                # Find which pdf-module contains this position
                for j in range(len(module_positions) - 1):
                    if module_positions[j][0] <= pos < module_positions[j+1][0]:
                        module_path = module_positions[j][1]
                        page = name_to_page.get(module_path, 0)
                        name_to_page[eid] = page
                        break
                else:
                    name_to_page[eid] = 0

        logger.info("Page scan: %d modules + %d field IDs mapped",
                    len(ordered), len(name_to_page) - len(ordered))
        return name_to_page

    def _resolve_deep_names(self, pdf_path: Path, name_to_page: dict, unresolved: set):
        """
        Resolve remaining link targets by scanning PDF page text.
        Handles both full-path names and field-level short names.
        """
        if not unresolved:
            return name_to_page

        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))

        for page_num, page in enumerate(reader.pages):
            if not unresolved:
                break
            text = page.extract_text() or ""
            found = set()
            for name in unresolved:
                if name in text:
                    found.add(name)
                elif "." in name:
                    short = name.rsplit(".", 1)[-1]
                    if short in text:
                        found.add(name)
            for name in found:
                name_to_page[name] = page_num
            unresolved -= found

        for name in unresolved:
            name_to_page[name] = 0
        return name_to_page

    def _fix_link_destinations(self, pdf_path: Path, name_to_page: dict):
        """
        Replace every link annotation's named Dest=/xxx with a direct
        [page, /Fit] destination. No /Names dictionary needed.
        """
        from pypdf import PdfWriter, PdfReader
        from pypdf.generic import ArrayObject, NameObject

        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer_pages = list(writer.pages)

        fixed = 0
        for page in writer_pages:
            for annot in page.get("/Annots", []):
                obj = annot.get_object()
                if obj.get("/Subtype") != "/Link":
                    continue
                dest = obj.get("/Dest")
                if not isinstance(dest, str):  # pypdf returns str for NameObject
                    continue
                name = dest.lstrip("/")
                target_page = name_to_page.get(name)
                if target_page is not None and 0 <= target_page < len(writer_pages):
                    obj[NameObject("/Dest")] = ArrayObject([
                        writer_pages[target_page].indirect_reference,
                        NameObject("/Fit"),
                    ])
                    fixed += 1

        with open(pdf_path, "wb") as f:
            writer.write(f)

        logger.info("Fixed %d link destinations to direct [page, /Fit]", fixed)

    def _add_bookmarks_and_names(self, pdf_path: Path, name_to_page: dict):
        """
        Single-pass PDF rewrite: add both bookmark outline AND /Names dictionary.

        Must be a single pass because PdfWriter doesn't preserve the other
        modification when writing.
        """
        from pypdf import PdfWriter, PdfReader
        from pypdf.generic import (
            ArrayObject, DictionaryObject, NameObject, NumberObject,
            create_string_object
        )

        ral = _load_ral_data(self.html_dir)
        if not ral:
            return

        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()

        for page in reader.pages:
            writer.add_page(page)
        writer_pages = list(writer.pages)

        # -- Part A: Bookmarks --
        bookmarks = _ral_tree_to_bookmarks(ral)

        def _add_bm_nodes(parent, nodes):
            for node in nodes:
                title = node["title"]
                path = node.get("path", title)
                page_num = name_to_page.get(path, name_to_page.get(title, 0))
                if page_num >= len(writer_pages):
                    page_num = 0
                bm = writer.add_outline_item(title, page_num, parent=parent)
                if node.get("children"):
                    _add_bm_nodes(bm, node["children"])

        for bm in bookmarks:
            root_path = bm.get("path", bm["title"])
            root_page = name_to_page.get(root_path, name_to_page.get(bm["title"], 0))
            root_bm = writer.add_outline_item(bm["title"], root_page)
            if bm.get("children"):
                _add_bm_nodes(root_bm, bm["children"])

        logger.info("Bookmarks: %d top-level entries", len(bookmarks))

        # -- Part B: /Names dictionary with accurate Y positions --
        # Convert pixel coords to PDF point coords
        vp_h = getattr(self, '_viewport', {}).get('height', 734) or 734
        # A4 landscape with 8mm margins: content ≈ 550pt high
        PDF_CONTENT_H_PT = 550.0
        px_to_pt = PDF_CONTENT_H_PT / vp_h if vp_h > 0 else 0.75

        elem_pos = getattr(self, '_elem_positions', {})

        dests_list = ArrayObject()
        dest_count = 0
        for name, page_num in name_to_page.items():
            if page_num < 0 or page_num >= len(writer_pages):
                continue
            left_pt = NumberObject(0)
            top_pt = NumberObject(PDF_CONTENT_H_PT)
            # If we have pixel positions, compute accurate Y WITHIN page
            if name in elem_pos:
                ep = elem_pos[name]
                y_px = ep['top']
                left_px = ep.get('left', 0)
                # Use the page_num from name_to_page (text-scan, reliable).
                # Pixel positions give us the Y-within-page offset.
                y_in_page_px = y_px - (page_num * vp_h)
                # Clamp Y offset to valid range
                y_in_page_px = max(0, min(y_in_page_px, vp_h))
                # PDF coords: bottom-up, invert Y
                y_pt = max(0, PDF_CONTENT_H_PT - (y_in_page_px * px_to_pt))
                x_pt = left_px * px_to_pt
                top_pt = NumberObject(y_pt)
                left_pt = NumberObject(x_pt)

            dests_list.append(NameObject(f"/{name}"))
            dests_list.append(ArrayObject([
                writer_pages[page_num].indirect_reference,
                NameObject("/XYZ"),
                left_pt,
                top_pt,
                NumberObject(0),
            ]))
            dest_count += 1

        names_dict = DictionaryObject({
            NameObject("/Dests"): DictionaryObject({
                NameObject("/Names"): dests_list,
            })
        })
        writer._root_object[NameObject("/Names")] = names_dict
        logger.info("Names dict: %d destinations", len(name_to_page))

        with open(pdf_path, "wb") as f:
            writer.write(f)

    def _add_names_dictionary(self, pdf_path: Path, name_to_page: dict):
        """
        Add a /Names dictionary to the PDF catalog so that named destinations
        (string Dest from Playwright) resolve to the correct pages.

        Playwright renders <a href='#foo'> as Dest=/foo but does NOT create
        the required /Names←/Dests mapping in the PDF. This function adds it.
        """
        from pypdf import PdfWriter, PdfReader
        from pypdf.generic import (
            ArrayObject, DictionaryObject, NameObject, NumberObject,
            create_string_object
        )

        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()

        # Build the /Names←/Dests name tree
        # Format: << /Names [ (name1) [page1 /XYZ 0 0 0] (name2) [page2 /XYZ 0 0 0] ... ] >>
        dests_list = ArrayObject()
        for name, page_num in name_to_page.items():
            if page_num < 0 or page_num >= len(reader.pages):
                continue
            dests_list.append(NameObject(f"/{name}"))
            dests_list.append(ArrayObject([
                writer_pages[page_num].indirect_reference,
                NameObject("/XYZ"),
                NumberObject(0),
                NumberObject(0),
                NumberObject(0),
            ]))

        names_dict = DictionaryObject({
            NameObject("/Dests"): DictionaryObject({
                NameObject("/Names"): dests_list,
            })
        })

        # Add /Names to the catalog
        for page in reader.pages:
            writer.add_page(page)

        writer._root_object[NameObject("/Names")] = names_dict

        with open(pdf_path, "wb") as f:
            writer.write(f)

        logger.info("Added /Names dictionary: %d destinations", len(name_to_page))

    def _fix_named_destinations(self, pdf_path: Path, name_to_page: dict):
        """
        Fix Playwright-generated link annotations.

        Playwright renders <a href='#foo'> as Dest=/foo (named string),
        but does NOT register those names in the PDF's /Names dictionary.
        This makes links unclickable in most viewers.

        Fix: convert string Dest to array Dest [page, /Fit].
        """
        from pypdf import PdfWriter, PdfReader
        from pypdf.generic import ArrayObject, NameObject, NumberObject

        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()

        fixed_count = 0
        unresolved = set()
        for page in reader.pages:
            annots = page.get("/Annots", [])
            for annot in annots:
                obj = annot.get_object()
                if obj.get("/Subtype") != "/Link":
                    continue
                dest = obj.get("/Dest")
                if not isinstance(dest, str):
                    continue
                name = dest.lstrip("/")
                target_page = name_to_page.get(name)
                if target_page is None:
                    unresolved.add(name)
                    continue
                if 0 <= target_page < len(reader.pages):
                    obj[NameObject("/Dest")] = ArrayObject([
                        reader.pages[target_page].indirect_reference,
                        NameObject("/XYZ"),
                        NumberObject(0),
                        NumberObject(0),
                        NumberObject(0),
                    ])
                    fixed_count += 1
            writer.add_page(page)

        logger.info("Link fix: %d resolved, %d unresolved string-Dest remaining",
                    fixed_count, len(unresolved))

        with open(pdf_path, "wb") as f:
            writer.write(f)

        logger.info("Fixed %d link destinations, %d names unresolved", fixed_count,
                    sum(1 for n in name_to_page.values()))

    def _add_bookmarks(self, pdf_path: Path, name_to_page: dict):
        """
        Add PDF bookmark outline from RAL hierarchy.

        Args:
            pdf_path: path to the rendered PDF
            name_to_page: dict mapping module name → page number (0-indexed)
        """
        from pypdf import PdfWriter, PdfReader

        ral = _load_ral_data(self.html_dir)
        if not ral:
            return

        bookmarks = _ral_tree_to_bookmarks(ral)
        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        def _add_bm_nodes(parent, nodes):
            for node in nodes:
                title = node["title"]
                path = node.get("path", title)
                # Look up by full path first, fall back to short name
                page_num = name_to_page.get(path, name_to_page.get(title, 0))
                bm = writer.add_outline_item(title, page_num, parent=parent)
                if node.get("children"):
                    _add_bm_nodes(bm, node["children"])

        for bm in bookmarks:
            root_path = bm.get("path", bm["title"])
            root_page = name_to_page.get(root_path, name_to_page.get(bm["title"], 0))
            root_bm = writer.add_outline_item(bm["title"], root_page)
            if bm.get("children"):
                _add_bm_nodes(root_bm, bm["children"])

        with open(pdf_path, "wb") as f:
            writer.write(f)

        logger.info("Bookmarks added: %d top-level entries", len(bookmarks))


async def generate_pdf_safe(
    html_dir: Path,
    output_dir: Path,
    title: str,
    timeout: int = GENERATION_TIMEOUT
) -> Path:
    """
    Generate PDF with timeout and guaranteed cleanup.
    """
    gen = PDFGenerator(html_dir, output_dir, title)
    try:
        return await asyncio.wait_for(gen.generate(), timeout=timeout)
    except asyncio.TimeoutError:
        if gen._browser and gen._browser.is_connected():
            await gen._browser.close()
        if gen._http_server:
            gen._http_server.stop()
        raise PDFGenerationError(f"PDF generation timed out after {timeout}s")
    except Exception as e:
        if gen._browser and gen._browser.is_connected():
            await gen._browser.close()
        if gen._http_server:
            gen._http_server.stop()
        raise PDFGenerationError(str(e))
