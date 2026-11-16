FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FRONTEND_PATH=/app/frontend
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd -r youtube && useradd -r -g youtube -m youtube
COPY --chown=youtube:youtube backend/app /app/app
COPY --chown=youtube:youtube frontend /app/frontend
COPY --chown=youtube:youtube scripts /app/scripts
COPY --chown=youtube:youtube tests /app/tests
USER youtube
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--no-proxy-headers"]
