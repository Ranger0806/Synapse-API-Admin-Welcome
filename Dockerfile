FROM python:3.12.13-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "main", "-n", "/app/notice/notice.sh"]