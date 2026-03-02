"""
Contract Validator — Autonomous Factory
════════════════════════════════════════
Validates worker-produced code against locked project contracts.
Checks: API endpoints vs api_contract.json, types vs types.json, DB vs db_schema.sql.
════════════════════════════════════════
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("factory.contract_validator")


class ContractValidator:
    """
    Validates code output against locked project contracts.

    Contracts:
    - api_contract.json: API endpoint definitions (method, path, request/response schemas)
    - types.json: Shared type definitions (used across backend/frontend)
    - db_schema.sql: Database schema (tables, columns, constraints)
    """

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.contracts_dir = self.project_path / "contracts"
        self._api_contract: Optional[dict] = None
        self._types_contract: Optional[dict] = None
        self._db_schema: Optional[str] = None

    def load_contracts(self) -> bool:
        """Load all contract files. Returns True if at least one loaded."""
        loaded = False

        api_path = self.contracts_dir / "api_contract.json"
        if api_path.exists():
            try:
                self._api_contract = json.loads(api_path.read_text())
                loaded = True
                logger.info(f"Loaded API contract: {len(self._api_contract.get('endpoints', []))} endpoints")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load api_contract.json: {e}")

        types_path = self.contracts_dir / "types.json"
        if types_path.exists():
            try:
                self._types_contract = json.loads(types_path.read_text())
                loaded = True
                logger.info(f"Loaded types contract: {len(self._types_contract.get('types', {}))} types")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load types.json: {e}")

        db_path = self.contracts_dir / "db_schema.sql"
        if db_path.exists():
            try:
                self._db_schema = db_path.read_text()
                loaded = True
                logger.info("Loaded DB schema contract")
            except IOError as e:
                logger.error(f"Failed to load db_schema.sql: {e}")

        return loaded

    def validate(self, code_files: List[dict]) -> dict:
        """
        Validate code files against all loaded contracts.

        Args:
            code_files: List of {"path": str, "content": str}

        Returns:
            {"valid": bool, "mismatches": [{"type": str, "detail": str, "file": str, "severity": str}]}
        """
        mismatches = []

        if self._api_contract:
            mismatches.extend(self._validate_api(code_files))

        if self._types_contract:
            mismatches.extend(self._validate_types(code_files))

        if self._db_schema:
            mismatches.extend(self._validate_db(code_files))

        # Check for undeclared endpoints
        mismatches.extend(self._check_undeclared_endpoints(code_files))

        valid = not any(m["severity"] == "error" for m in mismatches)

        if mismatches:
            logger.warning(f"Contract validation: {len(mismatches)} issue(s), valid={valid}")
        else:
            logger.info("Contract validation: all checks passed")

        return {"valid": valid, "mismatches": mismatches}

    def _validate_api(self, code_files: List[dict]) -> List[dict]:
        """Check code files reference only declared API endpoints."""
        mismatches = []
        endpoints = self._api_contract.get("endpoints", [])

        # Build set of declared paths
        declared_paths = set()
        for ep in endpoints:
            path = ep.get("path", "")
            method = ep.get("method", "GET").upper()
            declared_paths.add(f"{method} {path}")

        # Scan code files for route definitions
        route_patterns = [
            # FastAPI/Flask patterns
            r'@(?:app|router|bp)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
            # Express patterns
            r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        ]

        for f in code_files:
            content = f.get("content", "")
            for pattern in route_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for m in matches:
                    method = m.group(1).upper()
                    path = m.group(2)
                    route_key = f"{method} {path}"

                    # FER-CLI-005 FIX: Stricter path param normalization.
                    # Only match valid identifier names inside braces (e.g. {id}, {user_id})
                    # not arbitrary content like regex constraints or template expressions.
                    param_re = r'\{[a-zA-Z_][a-zA-Z0-9_]*\}'
                    normalized = re.sub(param_re, '{param}', path)
                    declared_normalized = {
                        re.sub(param_re, '{param}', p) for p in declared_paths
                    }
                    method_normalized = f"{method} {normalized}"

                    if method_normalized not in declared_normalized and route_key not in declared_paths:
                        mismatches.append({
                            "type": "api_undeclared",
                            "detail": f"Endpoint {route_key} not in api_contract.json",
                            "file": f["path"],
                            "severity": "error",
                        })

        return mismatches

    def _validate_types(self, code_files: List[dict]) -> List[dict]:
        """Check that code uses types consistent with types.json."""
        mismatches = []
        types_def = self._types_contract.get("types", {})

        for f in code_files:
            content = f.get("content", "")

            # Check TypeScript interface/type definitions
            ts_type_matches = re.finditer(
                r'(?:interface|type)\s+(\w+)\s*(?:=\s*)?{([^}]*)}',
                content, re.MULTILINE
            )
            for m in ts_type_matches:
                type_name = m.group(1)
                if type_name in types_def:
                    # Type exists in contract — check fields
                    contract_fields = set(types_def[type_name].get("fields", {}).keys())
                    code_fields = set(re.findall(r'(\w+)\s*[?]?\s*:', m.group(2)))

                    missing = contract_fields - code_fields
                    extra = code_fields - contract_fields

                    if missing:
                        mismatches.append({
                            "type": "type_missing_fields",
                            "detail": f"Type {type_name} missing fields from contract: {missing}",
                            "file": f["path"],
                            "severity": "warning",
                        })
                    if extra:
                        mismatches.append({
                            "type": "type_extra_fields",
                            "detail": f"Type {type_name} has fields not in contract: {extra}",
                            "file": f["path"],
                            "severity": "warning",
                        })

            # Check Python dataclass/Pydantic model definitions
            py_model_matches = re.finditer(
                r'class\s+(\w+)\s*\([^)]*(?:BaseModel|Base)\)',
                content
            )
            for m in py_model_matches:
                model_name = m.group(1)
                if model_name in types_def:
                    contract_fields = set(types_def[model_name].get("fields", {}).keys())
                    # Find fields in the class body (approximate)
                    class_body_match = re.search(
                        rf'class\s+{model_name}\s*\([^)]*\)\s*:(.*?)(?=\nclass\s|\Z)',
                        content, re.DOTALL
                    )
                    if class_body_match:
                        code_fields = set(re.findall(
                            r'^\s+(\w+)\s*[:=]',
                            class_body_match.group(1), re.MULTILINE
                        ))
                        missing = contract_fields - code_fields
                        if missing:
                            mismatches.append({
                                "type": "model_missing_fields",
                                "detail": f"Model {model_name} missing fields from contract: {missing}",
                                "file": f["path"],
                                "severity": "warning",
                            })

        return mismatches

    def _validate_db(self, code_files: List[dict]) -> List[dict]:
        """Check that migration/model files are consistent with db_schema.sql."""
        mismatches = []

        # Extract table definitions from schema
        schema_tables = {}
        table_matches = re.finditer(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(([^;]+)\)',
            self._db_schema, re.IGNORECASE | re.DOTALL
        )
        for m in table_matches:
            table_name = m.group(1).lower()
            body = m.group(2)
            # Extract column names (simplified)
            columns = set()
            for line in body.split('\n'):
                line = line.strip().strip(',')
                if line and not line.upper().startswith(('PRIMARY', 'FOREIGN', 'UNIQUE', 'CHECK', 'CONSTRAINT', 'INDEX', 'CREATE')):
                    col_match = re.match(r'(\w+)\s+\w+', line)
                    if col_match:
                        columns.add(col_match.group(1).lower())
            schema_tables[table_name] = columns

        # Check code files for table references
        for f in code_files:
            content = f.get("content", "")

            # Look for CREATE TABLE in migration files
            code_tables = re.finditer(
                r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)',
                content, re.IGNORECASE
            )
            for ct in code_tables:
                table = ct.group(1).lower()
                if table not in schema_tables and table != 'schema_version':
                    mismatches.append({
                        "type": "db_undeclared_table",
                        "detail": f"Table '{table}' not in db_schema.sql contract",
                        "file": f["path"],
                        "severity": "error",
                    })

        return mismatches

    def _check_undeclared_endpoints(self, code_files: List[dict]) -> List[dict]:
        """Check for fetch/axios calls to endpoints not in contract."""
        mismatches = []
        if not self._api_contract:
            return mismatches

        endpoints = self._api_contract.get("endpoints", [])
        declared_paths = set()
        for ep in endpoints:
            path = ep.get("path", "")
            # Normalize: strip param placeholders
            normalized = re.sub(r'\{[^}]+\}', '[^/]+', path)
            declared_paths.add(normalized)

        # Check frontend files for API calls
        api_call_patterns = [
            r'fetch\s*\(\s*[`"\']([^`"\']+)[`"\']',
            r'axios\.\w+\s*\(\s*[`"\']([^`"\']+)[`"\']',
            r'\.(?:get|post|put|delete|patch)\s*\(\s*[`"\']([^`"\']+)[`"\']',
        ]

        for f in code_files:
            if not any(f["path"].endswith(ext) for ext in ['.ts', '.tsx', '.js', '.jsx', '.vue', '.svelte']):
                continue
            content = f.get("content", "")
            for pattern in api_call_patterns:
                matches = re.finditer(pattern, content)
                for m in matches:
                    url = m.group(1)
                    if not url.startswith('/api'):
                        continue
                    # Check against declared paths
                    matched = False
                    for dp in declared_paths:
                        if re.match(dp + '$', url):
                            matched = True
                            break
                    if not matched:
                        mismatches.append({
                            "type": "frontend_undeclared_api",
                            "detail": f"Frontend calls undeclared API: {url}",
                            "file": f["path"],
                            "severity": "warning",
                        })

        return mismatches

    def get_relevant_contract_section(self, task_module: str) -> dict:
        """Get contract sections relevant to a specific module (for prompt injection)."""
        result = {}

        if self._api_contract and task_module in ("backend", "frontend"):
            endpoints = self._api_contract.get("endpoints", [])
            result["api_endpoints"] = endpoints

        if self._types_contract:
            result["types"] = self._types_contract.get("types", {})

        if self._db_schema and task_module in ("backend", "database"):
            result["db_schema"] = self._db_schema

        return result
