# Deployment image for the fdatrack.com API (Railway). Not needed for the
# graded local install, which is just: pip install -e .
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY core ./core
COPY servers ./servers
COPY cli ./cli
COPY monitor ./monitor
COPY evals ./evals
COPY fixtures ./fixtures
RUN pip install --no-cache-dir -e ".[api]"
EXPOSE 8000
CMD ["sh", "-c", "uvicorn servers.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
