"""Build script — inject data/*.json into template.html and write docs/index.html.

The template carries a <!-- DATA_INJECTION_POINT --> marker. The pipeline
replaces that marker with a single <script> tag that pre-populates
window.__DATA__ with all four JSON payloads inlined. When the marker is
not replaced (i.e. the template is opened directly via npx serve), the
JS in the template falls back to fetching data/*.json over HTTP.

Run:
    python scripts/pipeline.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template.html"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DOCS_OUT = DOCS_DIR / "index.html"

INJECTION_MARKER = "<!-- DATA_INJECTION_POINT -->"

PAYLOAD_FILES = {
    "equity_curve": "equity_curve.json",
    "holdings_timeline": "holdings_timeline.json",
    "performance_stats": "performance_stats.json",
    "current_positioning": "current_positioning.json",
    "mode_c_universes": "mode_c_universes.json",
}


def main() -> None:
    if not TEMPLATE.exists():
        raise SystemExit(f"template.html not found at {TEMPLATE}")
    template = TEMPLATE.read_text(encoding="utf-8")

    if INJECTION_MARKER not in template:
        raise SystemExit(
            f"Injection marker {INJECTION_MARKER!r} not found in template.html. "
            f"Did the marker get edited out?"
        )

    payload: dict = {}
    for key, fname in PAYLOAD_FILES.items():
        path = DATA_DIR / fname
        if not path.exists():
            raise SystemExit(
                f"Missing data file: {path}. Run scripts/backtest.py first."
            )
        payload[key] = json.loads(path.read_text(encoding="utf-8"))

    # Inline as a single script tag. Compact JSON (no whitespace) keeps the
    # output smaller. The HTML script context disallows the literal sequence
    # </script>, but JSON-encoded data cannot contain it; sanitise </ -> <\/
    # to be defensive against future schema changes that might embed it.
    inlined = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    inject_block = f'<script>window.__DATA__={inlined};</script>'

    output = template.replace(INJECTION_MARKER, inject_block)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_OUT.write_text(output, encoding="utf-8")

    template_bytes = len(template.encode("utf-8"))
    output_bytes = len(output.encode("utf-8"))
    payload_bytes = len(inlined.encode("utf-8"))

    print(f"[pipeline] template.html       : {template_bytes:>10,} bytes "
          f"({'OK' if template_bytes < 200_000 else 'OVER 200KB CAP'})")
    print(f"[pipeline] inlined data        : {payload_bytes:>10,} bytes")
    print(f"[pipeline] docs/index.html     : {output_bytes:>10,} bytes")
    print(f"[pipeline] wrote {DOCS_OUT}")


if __name__ == "__main__":
    main()
