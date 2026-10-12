# Build the frontend, then serve it from the API.
#   docker build -t reach .
#   docker run --rm -p 8000:8000 reach            # add -e ANTHROPIC_API_KEY=... for the LLM helpers

FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY data ./data
RUN uv sync --frozen --no-dev --extra llm
COPY --from=web /web/dist ./frontend/dist
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "reach", "serve", "--host", "0.0.0.0", "--port", "8000"]
