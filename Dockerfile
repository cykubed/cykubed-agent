FROM python:3.11-slim-buster as build

RUN apt-get update && apt-get install -y curl vim
WORKDIR /usr/app

RUN mkdir /cache
RUN useradd -m cykube --uid 10000 && chown cykube /usr/app
RUN chown cykube /cache && chmod -R a+r /cache

USER cykube
ENV PATH="/home/cykube/.local/bin:$PATH"

RUN pip install poetry==1.3.1
COPY pyproject.toml poetry.lock ./
RUN poetry config installer.max-workers 10
# when this goes to prod switch to --without=dev
RUN poetry install --with=dev

COPY src .

ENTRYPOINT ["poetry", "run", "python", "main.py"]

