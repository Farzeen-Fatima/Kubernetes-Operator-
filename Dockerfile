# Use official Python 3.11 slim image for smaller footprint
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy operator code
COPY operator.py .

# Create non-root user for security
RUN useradd -m -u 1000 operator && \
    chown -R operator:operator /app

# Switch to non-root user
USER operator

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the operator
CMD ["kopf", "run", "--verbose", "operator.py"]