# Operations & Containerization Documentation

## Overview
Provides complete Dockerization and CI/CD pipelines for local development, staging, and automated testing.

## Infrastructure Setup

### 1. Backend Dockerfile (`backend/Dockerfile`)
- Base image: `python:3.11-slim`
- Installs `libpq-dev` and build tools for PostgreSQL connectivity.
- Copies `requirements.txt` and executes `pip install`.
- Exposes port `8000` and runs Gunicorn WSGI server.

### 2. Frontend Dockerfile (`frontend/Dockerfile`)
- Multi-stage build: `node:20-alpine` builds production dist bundle via `vite build`, `nginx:alpine` serves static assets on port `80`.

### 3. Docker Compose (`docker-compose.yml`)
Orchestrates 3 services:
- `postgres`: PostgreSQL 16 Alpine database with healthcheck (`pg_isready`).
- `backend`: Runs Django migrations, `seed_defaults`, and Gunicorn API server after PostgreSQL becomes healthy.
- `frontend`: Serves React SPA web application.

### 4. CI Workflow (`.github/workflows/ci.yml`)
Automated GitHub Actions pipeline triggered on `push` and `pull_request`:
- `backend-test`: Checks Django setup (`manage.py check`), runs migrations, and executes `pytest` suite.
- `frontend-lint-build`: Runs `npm ci`, `npm run lint` (`tsc --noEmit`), and `npm run build`.
