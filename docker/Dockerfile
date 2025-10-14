FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY docker/docker_entrypoint.sh /app/docker_entrypoint.sh

RUN chmod +x ./docker_entrypoint.sh

ENTRYPOINT ["./docker_entrypoint.sh"]