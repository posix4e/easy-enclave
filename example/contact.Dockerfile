FROM python:3.11-slim

WORKDIR /app
COPY contact_server.py /app/contact_server.py

ENV CONTACT_PORT=8080

CMD ["python", "/app/contact_server.py"]
