#!/bin/bash

# Script to run the ai_core Django application

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Run Django migrations
echo "Running Django migrations..."
python manage.py migrate

# Collect static files (if needed)
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Start the Django development server
echo "Starting Django server on 0.0.0.0:8000..."
python manage.py runserver 0.0.0.0:8000