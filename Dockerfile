FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FUND_ESTIMATOR_FORCE_MOCK=0
ENV FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK=0
ENV FUND_ESTIMATOR_DB=/app/data/fund_estimator.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "fund_estimator.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
