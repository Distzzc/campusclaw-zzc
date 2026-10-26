FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 'torch>=2.6,<3.0' --index-url https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 -r requirements.txt
COPY app.py .
COPY knowledge ./knowledge
COPY integrations ./integrations
COPY config ./config
COPY templates ./templates
COPY static ./static
RUN mkdir -p /app/data/materials
ENV FLASK_APP=app.py
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "app:app"]
