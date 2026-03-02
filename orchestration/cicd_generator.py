"""
CI/CD Generator — Autonomous Factory
═════════════════════════════════════
Generates deployment configs based on project type.
Produces: Dockerfile, docker-compose.yml, GitHub Actions workflows.
═════════════════════════════════════
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("factory.cicd_generator")

# CI/CD templates per project type
GITHUB_ACTIONS_WEB = """name: Deploy Web App
on:
  push:
    branches: [main]
  pull_request:
    branches: [main, develop]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Lint
        run: |
          pip install ruff
          ruff check .
      - name: Test
        run: pytest tests/ -v --tb=short
      - name: Build frontend
        run: |
          cd frontend && npm ci && npm run build
        if: hashFiles('frontend/package.json') != ''

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Build and push Docker image
        run: |
          docker build -t ${{ github.repository }}:latest .
          docker push ${{ github.repository }}:latest
"""

GITHUB_ACTIONS_API = """name: Deploy API
on:
  push:
    branches: [main]
  pull_request:
    branches: [main, develop]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        ports: ['5432:5432']
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install
        run: pip install -r requirements.txt
      - name: Migrate
        run: python -m database.migrate
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/test_db
      - name: Test
        run: pytest tests/ -v
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/test_db

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Deploy
        run: |
          docker build -t ${{ github.repository }}:latest .
          docker push ${{ github.repository }}:latest
"""

GITHUB_ACTIONS_IOT = """name: Deploy IoT
on:
  push:
    branches: [main]
  pull_request:
    branches: [main, develop]

jobs:
  test-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install
        run: pip install -r requirements.txt
      - name: Test backend
        run: pytest tests/ -v -k "not hardware"

  test-firmware:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build firmware (simulation)
        run: |
          cd firmware && make sim-build
        if: hashFiles('firmware/Makefile') != ''
      - name: Test firmware
        run: pytest tests/hardware_sim/ -v
        if: hashFiles('tests/hardware_sim/') != ''

  deploy:
    needs: [test-backend, test-firmware]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Deploy backend
        run: docker build -t ${{ github.repository }}-backend:latest .
      - name: Prepare OTA
        run: |
          cd firmware && make release
        if: hashFiles('firmware/Makefile') != ''
"""

DOCKERFILE_TEMPLATE = """FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

DOCKER_COMPOSE_TEMPLATE = """version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/app
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: app
      POSTGRES_PASSWORD: postgres
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready"]
      interval: 5s
      timeout: 5s
      retries: 5
    ports:
      - "5432:5432"

volumes:
  pgdata:
"""

ENV_EXAMPLE = """# Application
APP_ENV=development
APP_PORT=8000
APP_SECRET=change-me-in-production

# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/app

# Auth
JWT_SECRET=change-me-in-production
JWT_EXPIRY_HOURS=24
"""


class CICDGenerator:
    """Generates CI/CD and deployment configuration files."""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)

    def generate(self, project_type: str = "web") -> dict:
        """Generate all deployment files for a project type."""
        files_created = []

        # GitHub Actions workflow
        workflow = self._get_workflow(project_type)
        wf_path = self.project_path / ".github" / "workflows" / "deploy.yml"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(workflow)
        files_created.append(str(wf_path))

        # Dockerfile
        df_path = self.project_path / "Dockerfile"
        if not df_path.exists():
            df_path.write_text(DOCKERFILE_TEMPLATE)
            files_created.append(str(df_path))

        # docker-compose.yml
        dc_path = self.project_path / "docker-compose.yml"
        if not dc_path.exists():
            dc_path.write_text(DOCKER_COMPOSE_TEMPLATE)
            files_created.append(str(dc_path))

        # .env.example
        env_path = self.project_path / ".env.example"
        if not env_path.exists():
            env_path.write_text(ENV_EXAMPLE)
            files_created.append(str(env_path))

        logger.info(f"Generated {len(files_created)} CI/CD files for {project_type}")
        return {"files_created": files_created, "project_type": project_type}

    def _get_workflow(self, project_type: str) -> str:
        workflows = {
            "web": GITHUB_ACTIONS_WEB,
            "api": GITHUB_ACTIONS_API,
            "iot": GITHUB_ACTIONS_IOT,
            "plm": GITHUB_ACTIONS_API,  # PLM uses API template
            "mobile": GITHUB_ACTIONS_WEB,  # Mobile uses web template base
        }
        return workflows.get(project_type, GITHUB_ACTIONS_WEB)
