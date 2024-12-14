FROM python:3.12-slim-bullseye

WORKDIR /usr/app

ENV PATH="/home/cykubed/.local/bin:/usr/app/.venv/bin:$PATH"
ENV POETRY_VIRTUALENVS_IN_PROJECT=1

RUN apt-get update && apt-get install -y curl git vim

RUN useradd -m cykubed --uid 10000 && chown cykubed:cykubed /usr/app

USER cykubed

RUN pip install poetry==1.8
COPY --chown=cykubed:cykubed pyproject.toml poetry.lock ./
RUN poetry config installer.max-workers 10
#RUN poetry install --no-root --with=dev
RUN poetry install --no-root --without=dev

ENV TZ=UTC
COPY  --chown=cykubed:cykubed src .

ENTRYPOINT ["python", "main.py"]

