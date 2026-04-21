"""Project scaffold templates for bootstrapping common project structures."""

from __future__ import annotations

SCAFFOLD_TEMPLATES: dict[str, dict] = {
    "python_cli": {
        "name": "Python CLI App",
        "description": "A command-line application with argparse, logging, and tests",
        "files": [
            {"path": "main.py", "description": "Entry point with CLI argument parsing"},
            {"path": "src/__init__.py", "description": "Package init"},
            {"path": "src/core.py", "description": "Core business logic"},
            {"path": "tests/__init__.py", "description": "Test package init"},
            {"path": "tests/test_core.py", "description": "Unit tests"},
            {"path": "README.md", "description": "Usage documentation"},
            {"path": "requirements.txt", "description": "Dependencies"},
        ],
    },
    "fastapi_service": {
        "name": "FastAPI Service",
        "description": "A production-ready REST API with FastAPI, Pydantic models, and tests",
        "files": [
            {"path": "main.py", "description": "FastAPI application entry point"},
            {"path": "app/__init__.py", "description": "App package init"},
            {"path": "app/api.py", "description": "API route definitions"},
            {"path": "app/models.py", "description": "Pydantic request/response models"},
            {"path": "app/service.py", "description": "Business logic service layer"},
            {"path": "app/config.py", "description": "Configuration settings"},
            {"path": "tests/__init__.py", "description": "Test package init"},
            {"path": "tests/test_api.py", "description": "API endpoint tests"},
            {"path": "requirements.txt", "description": "Python dependencies"},
            {"path": "Dockerfile", "description": "Container build instructions"},
            {"path": "README.md", "description": "API documentation"},
        ],
    },
    "next_app": {
        "name": "Next.js App",
        "description": "A modern Next.js application with App Router, TypeScript, and Tailwind CSS",
        "files": [
            {"path": "src/app/layout.tsx", "description": "Root layout with providers"},
            {"path": "src/app/page.tsx", "description": "Home page component"},
            {"path": "src/app/globals.css", "description": "Global styles with Tailwind"},
            {"path": "src/components/Header.tsx", "description": "Site header component"},
            {"path": "src/components/Footer.tsx", "description": "Site footer component"},
            {"path": "src/lib/utils.ts", "description": "Shared utility functions"},
            {"path": "src/hooks/useData.ts", "description": "Custom data-fetching hook"},
            {"path": "tailwind.config.ts", "description": "Tailwind CSS configuration"},
            {"path": "next.config.ts", "description": "Next.js configuration"},
            {"path": "package.json", "description": "Project dependencies and scripts"},
            {"path": "README.md", "description": "Project documentation"},
        ],
    },
    "data_pipeline": {
        "name": "Data Pipeline",
        "description": "An ETL data pipeline with data validation, transformation, and reporting",
        "files": [
            {"path": "pipeline.py", "description": "Main pipeline orchestrator"},
            {"path": "src/__init__.py", "description": "Package init"},
            {"path": "src/extract.py", "description": "Data extraction from sources"},
            {"path": "src/transform.py", "description": "Data cleaning and transformation"},
            {"path": "src/load.py", "description": "Data loading to destination"},
            {"path": "src/validate.py", "description": "Data validation and schema checks"},
            {"path": "src/report.py", "description": "Summary report generation"},
            {"path": "config/pipeline.yaml", "description": "Pipeline configuration"},
            {"path": "tests/test_transform.py", "description": "Transformation unit tests"},
            {"path": "requirements.txt", "description": "Python dependencies"},
            {"path": "README.md", "description": "Pipeline documentation"},
        ],
    },
}
