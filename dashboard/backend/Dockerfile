FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Usuário sem privilégios — limita o impacto de uma eventual exploração
RUN useradd --create-home --uid 1001 appuser \
    && mkdir -p /app/data /app/media/temp \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
