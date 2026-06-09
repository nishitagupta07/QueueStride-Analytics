# --- Stage 1: Build the React frontend ---
FROM node:18-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
ENV REACT_APP_API_URL=/
RUN npm run build

# --- Stage 2: Run the FastAPI backend ---
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy backend requirements and install
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend code
COPY backend/ ./backend/

# Copy built React frontend to static folder in backend
COPY --from=frontend-builder /app/frontend/build ./backend/frontend_build

# Set working directory to backend so Python imports resolve correctly
WORKDIR /app/backend

# Create necessary directories
RUN mkdir -p static logs

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
