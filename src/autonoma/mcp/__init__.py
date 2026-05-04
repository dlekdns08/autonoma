"""Model Context Protocol (MCP) server — feature #8.

Exposes a minimal HTTP transport so MCP clients (Claude Code, Cursor,
etc.) can drive Autonoma programmatically. Implemented in pure stdlib +
FastAPI; no dependency on the upstream ``mcp`` Python SDK.
"""
