FROM python:3.10-slim

# Install system dependencies for OpenCV and other C-bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip to ensure latest wheel support
RUN pip install --no-cache-dir --upgrade pip

# Install PyTorch first to match exact validated CUDA versions
RUN pip install --no-cache-dir torch==2.14.0+cu130 torchvision==0.29.0+cu130 --extra-index-url https://download.pytorch.org/whl/cu130

# Install remaining requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# EasyOCR isn't in requirements.txt directly, install it manually
RUN pip install --no-cache-dir easyocr

# Copy Sentinel project components
COPY backend/ ./backend/
COPY ai_engine/ ./ai_engine/
COPY streaming/ ./streaming/

# Set environment variables
ENV PYTHONPATH=/app:/app/backend
ENV PYTHONUNBUFFERED=1

# Run the entrypoint script
RUN chmod +x backend/scripts/entrypoint.sh && mkdir -p /app/scripts && ln -sf /app/backend/scripts/entrypoint.sh /app/scripts/entrypoint.sh
ENTRYPOINT ["backend/scripts/entrypoint.sh"]
