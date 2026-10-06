FROM rust:1-slim AS kernel
WORKDIR /build
COPY graphkern ./graphkern
RUN cd graphkern && cargo build --release && cp target/release/libgraphkern.so /

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=kernel /libgraphkern.so /app/app/services/libgraphkern.so

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
