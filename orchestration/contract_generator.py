"""
Contract Generator — Autonomous Factory
════════════════════════════════════════
Generates locked project contracts from approved blueprints.
Produces: api_contract.json, db_schema.sql, types.json
Kimi validates contracts for completeness before locking.
════════════════════════════════════════
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("factory.contract_generator")


class ContractGenerator:
    """
    Generates contract files from blueprint markdown.

    Flow:
    1. Parse blueprint for API endpoints, DB tables, types
    2. Generate contract files (JSON/SQL)
    3. Kimi validates completeness
    4. Lock contracts after human approval
    """

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.contracts_dir = self.project_path / "contracts"

    async def generate_from_blueprint(self, blueprint_content: str,
                                       worker_adapter=None) -> dict:
        """
        Generate all contracts from blueprint using an LLM worker.

        Args:
            blueprint_content: The approved blueprint markdown
            worker_adapter: Worker to use for generation (typically Claude)

        Returns:
            {"api_contract": dict, "db_schema": str, "types": dict, "generated_files": []}
        """
        if worker_adapter:
            return await self._generate_with_llm(blueprint_content, worker_adapter)
        else:
            return self._generate_from_parse(blueprint_content)

    async def _generate_with_llm(self, blueprint: str, worker) -> dict:
        """Use an LLM worker to generate contracts from blueprint."""

        # Generate API contract
        api_prompt = f"""Analyze this blueprint and generate an API contract as JSON.

BLUEPRINT:
{blueprint}

Return ONLY valid JSON with this exact structure:
{{
  "version": 1,
  "base_url": "/api",
  "endpoints": [
    {{
      "path": "/api/resource",
      "method": "GET|POST|PUT|DELETE",
      "description": "what it does",
      "request_body": {{}},
      "response": {{}},
      "auth_required": true|false
    }}
  ]
}}"""

        api_result = await self._call_worker(worker, api_prompt, "Extract API endpoints from blueprint")
        api_contract = self._parse_json_response(api_result, {"version": 1, "endpoints": []})

        # Generate DB schema
        db_prompt = f"""Analyze this blueprint and generate a SQL schema.

BLUEPRINT:
{blueprint}

Return ONLY valid SQL (CREATE TABLE statements). Include:
- All tables with columns, types, constraints
- Primary keys, foreign keys, indexes
- created_at/updated_at on all tables
- Use SQLite-compatible syntax"""

        db_result = await self._call_worker(worker, db_prompt, "Extract DB schema from blueprint")
        db_schema = self._extract_sql(db_result)

        # Generate types
        types_prompt = f"""Analyze this blueprint and generate shared type definitions as JSON.

BLUEPRINT:
{blueprint}

Return ONLY valid JSON:
{{
  "version": 1,
  "types": {{
    "TypeName": {{
      "description": "what this type represents",
      "fields": {{
        "field_name": {{"type": "string|number|boolean|array|object", "required": true|false, "description": "..."}}
      }}
    }}
  }}
}}"""

        types_result = await self._call_worker(worker, types_prompt, "Extract type definitions from blueprint")
        types_contract = self._parse_json_response(types_result, {"version": 1, "types": {}})

        # Write contract files
        generated_files = self._write_contracts(api_contract, db_schema, types_contract)

        return {
            "api_contract": api_contract,
            "db_schema": db_schema,
            "types": types_contract,
            "generated_files": generated_files,
        }

    def _generate_from_parse(self, blueprint: str) -> dict:
        """Fallback: parse blueprint directly without LLM (basic extraction)."""
        api_contract = {"version": 1, "base_url": "/api", "endpoints": []}
        types_contract = {"version": 1, "types": {}}
        db_schema = "-- Auto-generated from blueprint\n-- TODO: Review and complete\n\n"

        # Extract API endpoints from markdown tables/lists
        endpoint_pattern = r'(?:GET|POST|PUT|DELETE|PATCH)\s+(/\S+)'
        for match in re.finditer(endpoint_pattern, blueprint):
            method_path = match.group(0).split()
            if len(method_path) == 2:
                api_contract["endpoints"].append({
                    "path": method_path[1],
                    "method": method_path[0],
                    "description": "",
                    "auth_required": True,
                })

        # Extract table references
        table_pattern = r'(?:table|model|entity)[:\s]+(\w+)'
        for match in re.finditer(table_pattern, blueprint, re.IGNORECASE):
            table_name = match.group(1).lower()
            db_schema += f"CREATE TABLE IF NOT EXISTS {table_name} (\n"
            db_schema += f"    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            db_schema += f"    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,\n"
            db_schema += f"    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP\n"
            db_schema += f");\n\n"

            types_contract["types"][table_name.capitalize()] = {
                "description": f"Auto-extracted from blueprint",
                "fields": {
                    "id": {"type": "number", "required": True},
                    "created_at": {"type": "string", "required": True},
                    "updated_at": {"type": "string", "required": True},
                }
            }

        generated_files = self._write_contracts(api_contract, db_schema, types_contract)
        return {
            "api_contract": api_contract,
            "db_schema": db_schema,
            "types": types_contract,
            "generated_files": generated_files,
        }

    async def _call_worker(self, worker, message: str, system_prompt: str) -> str:
        """Call a worker and return the response text."""
        try:
            result = await worker.send_message(message, system_prompt=system_prompt)
            if result.get("success") and result.get("response"):
                return result["response"]
            logger.error(f"Worker call failed: {result}")
            return ""
        except Exception as e:
            logger.error(f"Worker call exception: {e}")
            return ""

    def _parse_json_response(self, response: str, default: dict) -> dict:
        """Extract JSON from worker response (handles markdown code blocks)."""
        if not response:
            return default

        # Try direct parse
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            pass

        # Try extracting from code blocks
        json_match = re.search(r'```(?:json)?\s*\n([\s\S]*?)\n```', response)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding JSON object
        brace_start = response.find('{')
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(response)):
                if response[i] == '{':
                    depth += 1
                elif response[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(response[brace_start:i + 1])
                        except json.JSONDecodeError:
                            break

        logger.warning("Could not parse JSON from worker response, using default")
        return default

    def _extract_sql(self, response: str) -> str:
        """Extract SQL from worker response."""
        if not response:
            return "-- No schema generated\n"

        # Try code block extraction
        sql_match = re.search(r'```(?:sql)?\s*\n([\s\S]*?)\n```', response)
        if sql_match:
            return sql_match.group(1).strip()

        # If response looks like SQL already (contains CREATE TABLE)
        if 'CREATE TABLE' in response.upper():
            # Strip non-SQL text before first CREATE
            idx = response.upper().find('CREATE')
            return response[idx:].strip()

        return response.strip()

    def _write_contracts(self, api_contract: dict, db_schema: str,
                          types_contract: dict) -> List[str]:
        """Write contract files to disk."""
        self.contracts_dir.mkdir(parents=True, exist_ok=True)
        files = []

        # API contract
        api_path = self.contracts_dir / "api_contract.json"
        api_path.write_text(json.dumps(api_contract, indent=2))
        files.append(str(api_path))
        logger.info(f"Generated: {api_path}")

        # DB schema
        db_path = self.contracts_dir / "db_schema.sql"
        db_path.write_text(db_schema)
        files.append(str(db_path))
        logger.info(f"Generated: {db_path}")

        # Types
        types_path = self.contracts_dir / "types.json"
        types_path.write_text(json.dumps(types_contract, indent=2))
        files.append(str(types_path))
        logger.info(f"Generated: {types_path}")

        return files

    async def validate_with_kimi(self, kimi_worker, blueprint: str) -> dict:
        """
        Have Kimi validate contracts for completeness against the blueprint.

        Returns: {"valid": bool, "issues": [], "suggestions": []}
        """
        contracts = {}
        for name in ["api_contract.json", "types.json"]:
            path = self.contracts_dir / name
            if path.exists():
                contracts[name] = path.read_text()

        db_path = self.contracts_dir / "db_schema.sql"
        if db_path.exists():
            contracts["db_schema.sql"] = db_path.read_text()

        # FER-CLI-004 FIX: Use chunked validation for large blueprints instead
        # of arbitrary truncation. Send full content up to model limits (~120k chars),
        # and only summarize if truly enormous.
        max_blueprint_chars = 60000
        max_contract_chars = 60000
        bp_text = blueprint
        if len(bp_text) > max_blueprint_chars:
            bp_text = (
                blueprint[:max_blueprint_chars // 2]
                + "\n\n[... MIDDLE SECTION OMITTED FOR LENGTH ...]\n\n"
                + blueprint[-(max_blueprint_chars // 2):]
            )
            logger.warning(
                f"Blueprint truncated for validation: {len(blueprint)} -> {len(bp_text)} chars "
                f"(head+tail preserved, middle omitted)"
            )

        contracts_text = json.dumps(contracts, indent=2)
        if len(contracts_text) > max_contract_chars:
            contracts_text = (
                contracts_text[:max_contract_chars // 2]
                + "\n\n[... MIDDLE SECTION OMITTED FOR LENGTH ...]\n\n"
                + contracts_text[-(max_contract_chars // 2):]
            )
            logger.warning(
                f"Contracts truncated for validation: original -> {len(contracts_text)} chars"
            )

        prompt = f"""Review these contracts for completeness against the blueprint.

BLUEPRINT:
{bp_text}

CONTRACTS:
{contracts_text}

Return JSON:
{{
  "valid": true|false,
  "completeness_score": 0.0-1.0,
  "issues": ["list of missing or incorrect items"],
  "suggestions": ["improvements"]
}}
Only mark valid if completeness_score > 0.9."""

        result = await self._call_worker(
            kimi_worker, prompt, "Validate contract completeness"
        )
        return self._parse_json_response(result, {
            "valid": False,
            "completeness_score": 0.0,
            "issues": ["Validation failed"],
            "suggestions": [],
        })

    def lock_contracts(self) -> bool:
        """
        Mark contracts as locked (read-only).
        After locking, contracts are immutable within the current phase.
        Any modification requires a new phase.
        """
        lock_file = self.contracts_dir / ".locked"
        if self.contracts_dir.exists():
            lock_file.write_text(json.dumps({
                "locked": True,
                "locked_at": __import__("datetime").datetime.now().isoformat(),
                "files": [f.name for f in self.contracts_dir.iterdir()
                          if f.suffix in ('.json', '.sql')],
            }, indent=2))
            logger.info("Contracts LOCKED — immutable until next phase")
            return True
        return False

    def is_locked(self) -> bool:
        """Check if contracts are locked."""
        lock_file = self.contracts_dir / ".locked"
        return lock_file.exists()

    def unlock_contracts(self) -> bool:
        """Unlock contracts (for new phase or revision)."""
        lock_file = self.contracts_dir / ".locked"
        if lock_file.exists():
            lock_file.unlink()
            logger.info("Contracts UNLOCKED")
            return True
        return False
