# Single container: the backend serves the API and the frontend. Keys come from the environment, never the image.
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY index.html .
COPY public public
ENV HOST=0.0.0.0 PORT=8000 DATABASE_PATH=/data/vyra.db MEDIA_DIR=/data/media
VOLUME /data
EXPOSE 8000
WORKDIR /app/backend
CMD ["python", "run.py"]
