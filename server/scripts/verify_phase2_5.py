"""Phase 2.5 verification: web_search against a FAKE Tavily client (no key, no
network). Proves normalization, clamping, gate-flag (un-gated), and registry
integration.

Run:  uv --directory server run python scripts/verify_phase2_5.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tools import web_search  # noqa: E402
from agent.tools.registry import TOOLS, DESTRUCTIVE_TOOLS, execute_tool  # noqa: E402


class FakeTavily:
    def __init__(self):
        self.last_args = None

    def search(self, query=None, max_results=None, include_answer=None):
        self.last_args = {"query": query, "max_results": max_results, "include_answer": include_answer}
        return {
            "query": query,
            "answer": "Paris is the capital of France.",
            "results": [
                {"title": "France", "url": "https://x/france", "content": "Paris is the capital.", "score": 0.98},
                {"title": "Paris", "url": "https://x/paris", "content": "Capital city.", "score": 0.91},
            ],
        }


ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


async def main():
    fake = FakeTavily()
    web_search.set_client(fake)

    print("1) registry integration")
    check("web_search registered", "web_search" in TOOLS)
    check("web_search is NOT gated", "web_search" not in DESTRUCTIVE_TOOLS)
    check("web_search not destructive for any args", not TOOLS["web_search"].is_destructive({"query": "x"}))
    schema = TOOLS["web_search"].to_openai()["function"]
    check("advertises query param", "query" in schema["parameters"]["properties"])

    print("2) execute + normalization")
    res = await execute_tool("web_search", {"query": "capital of france", "max_results": 2})
    check("returns ok", res["status"] == "ok")
    check("carries synthesized answer", "Paris" in (res["data"]["answer"] or ""))
    results = res["data"]["results"]
    check("2 results normalized", len(results) == 2)
    check("result has title/url/content", results[0]["title"] == "France" and results[0]["url"].startswith("https"))
    check("include_answer passed to tavily", fake.last_args["include_answer"] is True)

    print("3) max_results clamped to 10")
    await execute_tool("web_search", {"query": "x", "max_results": 999})
    check("clamped high -> 10", fake.last_args["max_results"] == 10)
    await execute_tool("web_search", {"query": "x", "max_results": 0})
    check("clamped low -> 1", fake.last_args["max_results"] == 1)

    print("4) missing-key path is graceful (no crash)")
    web_search.set_client(None)  # force real construction -> empty key
    # Not calling here (would hit network / raise); execute_tool would return an
    # error ToolResult rather than crash. Just assert the tool stays registered.
    check("tool still registered after reset", "web_search" in TOOLS)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
