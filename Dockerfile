FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional extras live in their own layer: adding a package here does not
# invalidate the (expensive) main dependency layer above.
COPY requirements-extra.txt .
RUN pip install --no-cache-dir -r requirements-extra.txt

# Copy application
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Run the bot + Mini App web server
CMD ["python", "-m", "app.main"]
