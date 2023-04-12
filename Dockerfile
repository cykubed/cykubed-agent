FROM python:3.11-slim-buster as build

WORKDIR /usr/app

ENV PATH="/home/cykube/.local/bin:/usr/app/.venv/.bin:$PATH"
ENV POETRY_VIRTUALENVS_IN_PROJECT=1

RUN mkdir /cache
RUN useradd -m cykube --uid 10000 && chown cykube /usr/app
RUN chown 10000 /cache && chmod -R a+r /cache

USER 10000
ENV PATH="/home/cykube/.local/bin:$PATH"

RUN pip install poetry==1.3.1
COPY pyproject.toml poetry.lock ./
RUN poetry config installer.max-workers 10
RUN poetry install --no-root --with=dev
#RUN poetry install --no-root --without=dev

FROM python:3.11-slim-buster

WORKDIR /usr/app
RUN useradd -m cykube --uid 10000 && chown cykube /usr/app
#USER 10000

COPY --from=build /usr/app/.venv/ .venv/
COPY src .
ENV PATH="/usr/app/.venv/bin:$PATH"

ENTRYPOINT ["python", "main.py"]

