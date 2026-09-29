# One image for the MCP server, the dashboard and the warehouse builder.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY negotiation_mcp ./negotiation_mcp
COPY scripts ./scripts
COPY data ./data
ENV NEGOTIATION_DATA_DIR=/app/data/sample
EXPOSE 8080
CMD ["python", "-m", "negotiation_mcp.dashboard", "--host", "0.0.0.0", "--port", "8080"]
