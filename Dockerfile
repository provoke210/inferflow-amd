FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY inferflow ./inferflow
RUN pip install --no-deps . && useradd -m app && mkdir /app/data && chown app:app /app/data
USER app
EXPOSE 8000
CMD ["uvicorn", "inferflow.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
