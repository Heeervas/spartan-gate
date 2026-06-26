FROM python:3.12-slim AS runtime

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spartan_route ./spartan_route
COPY web ./web

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV CLAWROUTE_HOST=0.0.0.0
ENV CLAWROUTE_PORT=18790
ENV SPARTAN_ROUTE_CONFIG_DIR=/app/config
ENV SPARTAN_ROUTE_WEB_DIR=/app/web

EXPOSE 18790

CMD ["python3", "-m", "spartan_route"]
