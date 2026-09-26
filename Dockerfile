FROM python:3.12.1-alpine3.18
MAINTAINER Salvatore Dello Iacono

# Defaults from gateway/app.py; override with docker run -e or --env-file.
ENV MODBUS_HOST=192.168.0.10 \
    MODBUS_PORT=502 \
    INFLUXDB_HOST=172.16.1.10 \
    INFLUXDB_PORT=8086 \
    INFLUXDB_USER=influx_user \
    INFLUXDB_PASSWORD=influx_pass \
    INFLUXDB_DATABASE=influxdb \
    SLEEP_READOUT=5 \
    SLEEP_RETRY=120 \
    REGISTERS_FILE=/app/registers.json

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY gateway /app/
COPY registers.json /app/registers.json

CMD ["python", "app.py"]
