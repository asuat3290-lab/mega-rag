#!/usr/bin/env python3
"""Launch the MEGA retrieval UI with research and evidence workbenches."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from claim_audit_ui import attach_claim_audit
from evidence_library_ui import attach_evidence_library
from research_orchestrator_ui import attach_research_orchestrator
from webui import CONFIG, build_ui, resolve_api_key


def build_workbench():
    app = build_ui()
    app = attach_research_orchestrator(app)
    app = attach_claim_audit(app)
    return attach_evidence_library(app)


def main() -> int:
    if not resolve_api_key(CONFIG["models"]["flash"]):
        print("DEEPSEEK_API_KEY is not set; local retrieval and evidence review remain available.")
    app = build_workbench()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
