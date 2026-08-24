FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDISUPPLY_INTELLIGENCE_REPORT=/app/data/dashboard/current.json \
    MEDISUPPLY_KNOWLEDGE_GRAPH=/app/data/dashboard/knowledge_graph.sqlite

WORKDIR /app

RUN addgroup --system medisupply \
    && adduser --system --ingroup medisupply --home /app medisupply

COPY requirements-phase14.txt ./requirements-phase14.txt
RUN python -m pip install --no-cache-dir --requirement requirements-phase14.txt

COPY --chown=medisupply:medisupply src/api ./src/api
COPY --chown=medisupply:medisupply src/intelligence ./src/intelligence
COPY --chown=medisupply:medisupply src/cleaning/normalizers.py ./src/cleaning/normalizers.py
COPY --chown=medisupply:medisupply data/dashboard/current.json ./data/dashboard/current.json
COPY --chown=medisupply:medisupply data/dashboard/knowledge_graph.sqlite.gz ./data/dashboard/knowledge_graph.sqlite.gz

RUN python -c "import gzip, pathlib, shutil; source=pathlib.Path('/app/data/dashboard/knowledge_graph.sqlite.gz'); target=source.with_suffix(''); source_handle=gzip.open(source, 'rb'); target_handle=target.open('wb'); shutil.copyfileobj(source_handle, target_handle); source_handle.close(); target_handle.close(); source.unlink()" \
    && chown medisupply:medisupply /app/data/dashboard/knowledge_graph.sqlite \
    && chmod 0444 /app/data/dashboard/current.json /app/data/dashboard/knowledge_graph.sqlite

USER medisupply

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; payload=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)); assert payload['status'] == 'ok'"]

CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
