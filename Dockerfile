FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10001

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY docs ./docs

EXPOSE 10001

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10001}"]
