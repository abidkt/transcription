#!/bin/bash

# Start the docker-compose services
echo "Starting services with docker-compose..."
docker-compose up --detach

# If running ts-gpt ollama container, enable this
# Get the model installed on ts-gpt (requires curl)
echo "Downloading ollama model"
curl -X POST http://localhost:11435/api/pull -d '{"name": "llama3"}'
curl -X POST http://localhost:11435/api/pull -d '{"name": "gemma2"}'

# Re-attach to compose logs
echo "Re-attaching to console logs"
docker-compose logs -f
