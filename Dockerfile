# Use a lightweight Python base
FROM python:3.10-slim

# Install system dependencies (ffmpeg, aria2, nodejs, jq, curl)
RUN apt-get update && \
    apt-get install -y ffmpeg aria2 jq curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all other files
COPY . .

# Make the downloader script executable
RUN chmod +x animepahe-dl.sh

# Run the bot
CMD ["python", "bot.py"]
