FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for manim
RUN apt-get update && \
    apt-get install -y \
        ffmpeg \
        libcairo2-dev \
        libpango1.0-dev \
        libpangocairo-1.0-0 \
        libgdk-pixbuf2.0-dev \
        libffi-dev \
        shared-mime-info \
        pkg-config \
        build-essential \
        python3-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]