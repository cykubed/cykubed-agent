FROM python:3.10-slim-buster as build

WORKDIR /usr/app

RUN useradd -m cykube && chown cykube /usr/app
USER cykube

RUN mkdir -p /tmp/cykube/build
ENV PATH="/home/cykube/.local/bin:$PATH"

RUN pip install poetry==1.3.1
COPY pyproject.toml poetry.lock ./
RUN poetry config installer.max-workers 10
RUN poetry install --no-root --without=dev

COPY src .

ENTRYPOINT ["poetry", "run", "python", "main.py"]

