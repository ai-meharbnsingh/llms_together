"""
Figma MCP Client + Frontend Design Pipeline — Autonomous Factory
================================================================
Connects to Figma MCP server for design token extraction.
Falls back to modern design principles when Figma unavailable.
Routes: frontend/components -> Claude + Figma, frontend/logic -> DeepSeek.
================================================================
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("factory.figma_mcp")


class FigmaMCPClient:
    """
    Client for Figma MCP (Model Context Protocol) server.
    Fetches design tokens, layouts, and component specs from Figma.
    Graceful fallback when Figma MCP is not available.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.mcp_endpoint = self.config.get("mcp_endpoint", "http://localhost:3845")
        self.file_key = self.config.get("file_key")
        self._available = None

    async def check_availability(self) -> bool:
        """Check if Figma MCP server is running."""
        if self._available is not None:
            return self._available

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.mcp_endpoint}/health", timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    self._available = resp.status == 200
        except Exception:
            self._available = False
            logger.info("Figma MCP not available — using fallback design principles")

        return self._available

    async def get_design_tokens(self) -> dict:
        """Fetch design tokens from Figma (colors, typography, spacing)."""
        if not await self.check_availability():
            return self._fallback_tokens()

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.mcp_endpoint}/tools/get_design_tokens",
                    json={"file_key": self.file_key}
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.warning(f"Figma token fetch failed: {e}")

        return self._fallback_tokens()

    async def get_component_spec(self, component_name: str) -> dict:
        """Fetch component specification from Figma."""
        if not await self.check_availability():
            return self._fallback_component(component_name)

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.mcp_endpoint}/tools/get_component",
                    json={"file_key": self.file_key, "name": component_name}
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.warning(f"Figma component fetch failed: {e}")

        return self._fallback_component(component_name)

    async def get_layout(self, page_name: str) -> dict:
        """Fetch page layout from Figma."""
        if not await self.check_availability():
            return {}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.mcp_endpoint}/tools/get_layout",
                    json={"file_key": self.file_key, "page": page_name}
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.warning(f"Figma layout fetch failed: {e}")

        return {}

    def _fallback_tokens(self) -> dict:
        """Modern design tokens when Figma is unavailable."""
        return {
            "source": "fallback",
            "colors": {
                "primary": "#3B82F6",
                "secondary": "#6366F1",
                "success": "#10B981",
                "warning": "#F59E0B",
                "error": "#EF4444",
                "background": "#FFFFFF",
                "surface": "#F9FAFB",
                "text": "#111827",
                "text_secondary": "#6B7280",
                "border": "#E5E7EB",
            },
            "typography": {
                "font_family": "Inter, system-ui, -apple-system, sans-serif",
                "font_size_base": "16px",
                "font_sizes": {"xs": "12px", "sm": "14px", "base": "16px",
                               "lg": "18px", "xl": "20px", "2xl": "24px",
                               "3xl": "30px", "4xl": "36px"},
                "font_weights": {"normal": 400, "medium": 500, "semibold": 600, "bold": 700},
                "line_heights": {"tight": 1.25, "normal": 1.5, "relaxed": 1.75},
            },
            "spacing": {
                "unit": "8px",
                "scale": {"0": "0", "1": "4px", "2": "8px", "3": "12px",
                          "4": "16px", "5": "20px", "6": "24px", "8": "32px",
                          "10": "40px", "12": "48px", "16": "64px"},
            },
            "borders": {
                "radius": {"sm": "4px", "md": "8px", "lg": "12px", "xl": "16px", "full": "9999px"},
                "width": {"thin": "1px", "medium": "2px"},
            },
            "shadows": {
                "sm": "0 1px 2px rgba(0,0,0,0.05)",
                "md": "0 4px 6px -1px rgba(0,0,0,0.1)",
                "lg": "0 10px 15px -3px rgba(0,0,0,0.1)",
                "xl": "0 20px 25px -5px rgba(0,0,0,0.1)",
            },
            "breakpoints": {"sm": "640px", "md": "768px", "lg": "1024px", "xl": "1280px"},
            "accessibility": {"min_contrast": "4.5:1", "focus_ring": "2px solid #3B82F6",
                             "min_touch_target": "44px"},
        }

    def _fallback_component(self, name: str) -> dict:
        """Fallback component spec using modern conventions."""
        return {
            "source": "fallback",
            "name": name,
            "guidelines": {
                "grid": "8px base grid",
                "contrast": "WCAG AA minimum (4.5:1)",
                "touch_targets": "44x44px minimum",
                "responsive": "mobile-first with breakpoints at 640/768/1024/1280px",
                "framework": "Tailwind CSS utility classes preferred",
            },
        }


class FrontendDesignPipeline:
    """
    Pipeline for frontend component generation.

    Flow:
    1. Check Figma MCP for design context
    2. Build design prompt with tokens + layout
    3. Send to Claude (frontend_design role) for component generation
    4. Return structured output
    """

    def __init__(self, figma_client: FigmaMCPClient, worker_adapter=None):
        self.figma = figma_client
        self.worker = worker_adapter

    async def generate_component(self, component_name: str,
                                  requirements: str,
                                  framework: str = "react") -> dict:
        """Generate a frontend component with design context."""

        # 1. Get design context
        tokens = await self.figma.get_design_tokens()
        component_spec = await self.figma.get_component_spec(component_name)

        # 2. Build prompt
        prompt = self._build_design_prompt(
            component_name, requirements, tokens, component_spec, framework
        )

        # 3. Send to worker (Claude)
        if self.worker:
            result = await self.worker.send_message(
                prompt,
                system_prompt="Generate production frontend component with design tokens"
            )
            if result.get("success"):
                return {
                    "success": True,
                    "component": result["response"],
                    "design_source": tokens.get("source", "unknown"),
                    "framework": framework,
                }

        return {"success": False, "error": "No worker available"}

    def _build_design_prompt(self, name: str, requirements: str,
                              tokens: dict, spec: dict,
                              framework: str) -> str:
        """Build a rich design prompt with Figma context."""
        sections = [
            f"# Generate Component: {name}",
            f"\n## Requirements\n{requirements}",
            f"\n## Framework: {framework}",
        ]

        # Design tokens
        if tokens:
            source = tokens.get("source", "figma")
            sections.append(f"\n## Design Tokens ({source})")
            sections.append(f"Colors: {json.dumps(tokens.get('colors', {}), indent=2)}")
            sections.append(f"Typography: {json.dumps(tokens.get('typography', {}), indent=2)}")
            sections.append(f"Spacing: {json.dumps(tokens.get('spacing', {}), indent=2)}")

        # Component spec
        if spec and spec.get("guidelines"):
            sections.append(f"\n## Component Guidelines\n{json.dumps(spec['guidelines'], indent=2)}")

        sections.append("""
## Output Requirements
Return structured JSON:
{
  "files": [
    {"path": "frontend/components/ComponentName.tsx", "content": "...", "action": "create"},
    {"path": "frontend/components/ComponentName.css", "content": "...", "action": "create"}
  ],
  "decisions": [],
  "notes": ["Design decisions made"],
  "tests_needed": ["Component render test", "Interaction test"]
}

Use Tailwind CSS for styling. Ensure WCAG AA accessibility.
""")
        return "\n".join(sections)

    @staticmethod
    def should_use_figma(task_module: str) -> bool:
        """Check if a task should use the Figma pipeline."""
        return task_module.startswith("frontend/components")

    @staticmethod
    def get_logic_role() -> str:
        """Role for frontend logic tasks (state, API calls)."""
        return "code_generation_complex"

    @staticmethod
    def get_design_role() -> str:
        """Role for frontend design tasks (components, layout)."""
        return "frontend_design"
