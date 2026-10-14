FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
ARG INSTALL_LOCAL_EMBEDDINGS=0
COPY requirements-local-embeddings.lock .
RUN if [ "$INSTALL_LOCAL_EMBEDDINGS" = "1" ]; then pip install --no-cache-dir -r requirements-local-embeddings.lock; fi

# Copy project files
COPY . .

EXPOSE 5000

CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--threads=8", "--call", "app:create_app"]
