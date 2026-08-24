"""Thin MCP stdio adapter over core.tools. No logic lives here.

Run:  python -m servers.mcp_stdio
Claude Desktop / Claude Code config: see README.

Deliberately NOT exposed: snapshot/diff (plain code in monitor/), and any
assess-everything mega-tool (the model above this boundary does the judging).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from core.env import load_env

load_env()

from core import tools as t
from core.fda_client import FDAClient

mcp = MCPServer(
    "fdatrack",
    instructions=(
        "FDA drug signal tools. Report what FDA publishes; zero records means "
        "untracked, not adequately supplied. Never suggest a different molecule, "
        "never compute a dose, never declare presentations equivalent."
    ),
)
_client = FDAClient()


@mcp.tool()
def resolve_drug(query: str) -> dict:
    """Resolve a free-text drug name to candidate molecules with the matched
    search path and confidence. Reports whether the product is marketed at all
    (distinguishes 'untracked' from 'does not exist'). Does not pick a winner."""
    return t.resolve_drug(_client, query)


@mcp.tool()
def get_shortage_picture(ingredient: str) -> dict:
    """All FDA shortage records for a molecule, grouped by presentation:
    availability distribution, companies, date ranges, update types, and
    related_info free text verbatim. Zero records returns cleanly and is a
    meaningful, common result - it means FDA does not track this, not that
    supply is adequate."""
    return t.get_shortage_picture(_client, ingredient)


@mcp.tool()
def get_discontinuations(ingredient: str) -> dict:
    """Records with status 'To Be Discontinued' for a molecule: permanent
    market withdrawal, a bigger planning event than temporary shortage."""
    return t.get_discontinuations(_client, ingredient)


@mcp.tool()
def find_alternate_sources(ingredient: str, route: str | None = None) -> dict:
    """Every marketed NDC product for the same molecule, clustered by route,
    dosage form and parsed concentration. API-grade bulk is filtered out and
    counted in provenance. Raw FDA strength strings are shown verbatim per
    cluster. Returns groups only: no equivalence verdicts, no substitutions."""
    return t.find_alternate_sources(_client, ingredient, route)


@mcp.tool()
def check_recalls(
    ingredient: str | None = None,
    ndc: str | None = None,
    firm: str | None = None,
) -> dict:
    """FDA enforcement actions (recalls), open and historical, searched by
    ingredient, NDC, or recalling firm. For many drugs this is the only
    endpoint with anything to say. Dates normalized to ISO; open vs
    terminated distinguished."""
    return t.check_recalls(_client, ingredient, ndc, firm)


@mcp.tool()
def get_label_facts(ndc: str) -> dict:
    """Route, strength, dosage form and packaging for one NDC product.
    Confirms a candidate product is the same route and form."""
    return t.get_label_facts(_client, ndc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
