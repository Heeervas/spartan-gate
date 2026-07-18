FROM python:3.12-slim
WORKDIR /app
COPY server.py .
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin reader
USER 10001:10001
EXPOSE 3000
CMD ["python3", "-u", "server.py"]
