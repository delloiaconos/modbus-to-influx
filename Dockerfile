FROM python:3.12.1-alpine3.18
MAINTAINER Salvatore Dello Iacono

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY gateway /app/

CMD ["python", "app.py"]