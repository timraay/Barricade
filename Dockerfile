FROM python:3.13-trixie

# psycopg2 builds from source and requires pg_config and a compiler toolchain
RUN apt-get update \
 && apt-get install --yes --no-install-recommends \
    build-essential \
    libpq-dev \
    pkg-config \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY ./ .

CMD [ "python", "app.py" ]
