FROM python:3.11-slim-buster

WORKDIR /usr/app

ENV PATH="/home/cykube/.local/bin:/usr/app/.venv/bin:$PATH"
ENV POETRY_VIRTUALENVS_IN_PROJECT=1

RUN mkdir /cache
RUN useradd -m cykube --uid 10000 && chown cykube:cykube /usr/app
RUN chown cykube /cache && chmod -R a+r /cache

USER cykube

RUN pip install poetry==1.3.1
COPY --chown=cykube:cykube pyproject.toml poetry.lock ./
RUN poetry config installer.max-workers 10
RUN poetry install --no-root --with=dev
#RUN poetry install --no-root --without=dev

COPY  --chown=cykube:cykube src .

ENTRYPOINT ["python", "main.py"]

