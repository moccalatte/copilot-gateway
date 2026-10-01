FROM python:3.12-slim

WORKDIR /app

# Prevent Python from writing pyc files to disk and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create persistent data directory
RUN mkdir -p /app/data

EXPOSE 8787

CMD ["python", "server.py"]
