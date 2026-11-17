FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# база лежит на volume, если он подключён
ENV DATABASE_PATH=/data/titlebot.sqlite3
RUN mkdir -p /data

EXPOSE 8080
CMD ["python", "main.py"]
