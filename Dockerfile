FROM python:3.12-alpine
LABEL org.opencontainers.image.authors="Salvatore Dello Iacono"

# Flush stdout/stderr immediately and avoid writing bytecode at runtime.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Override with docker run -e or --env-file.
ENV MODBUS_HOST=192.168.0.10
ENV MODBUS_PORT=502
ENV INFLUXDB_HOST=172.16.1.10
ENV INFLUXDB_PORT=8086
ENV INFLUXDB_USER=influx_user
ENV INFLUXDB_PASSWORD=influx_pass
ENV INFLUXDB_DATABASE=influxdb
ENV SLEEP_READOUT=5
ENV SLEEP_RETRY=120
ENV CLIENT_TIMEOUT=10
ENV REGISTERS_FILE=/config/registers.json

WORKDIR /app
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

# The gateway only needs outbound connections and read access to its config.
RUN addgroup -S -g 10001 gateway && adduser -S -D -H -u 10001 -G gateway gateway

COPY gateway /app/
COPY examples/example2.json /config/registers.json

USER 10001:10001

# Use the application's KeyboardInterrupt handler to close both clients.
STOPSIGNAL SIGINT
CMD ["python", "app.py"]
