FROM python:3.12-slim

# FFmpeg required for audio conversion
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.stt.txt ./
RUN pip install --no-cache-dir -r requirements.stt.txt

COPY . .

EXPOSE 7625
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7625"]
