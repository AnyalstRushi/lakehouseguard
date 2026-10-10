FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY contracts ./contracts
RUN pip install --no-cache-dir .
# Default: run the full demo and write the dashboard to /app/site
CMD ["lakehouseguard", "demo", "--root", "/app/lakehouse_data", "--out", "/app/site"]
