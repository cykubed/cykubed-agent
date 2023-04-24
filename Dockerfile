FROM python:3.11-slim-buster

WORKDIR /usr/app

ENV PATH="/home/cykubed/.local/bin:/usr/app/.venv/bin:$PATH"
ENV POETRY_VIRTUALENVS_IN_PROJECT=1

RUN mkdir /cache
RUN useradd -m cykubed --uid 10000 && chown cykubed:cykubed /usr/app
RUN chown cykubed /cache && chmod -R a+r /cache

USER cykubed

RUN pip install poetry==1.3.1
COPY --chown=cykubed:cykubed pyproject.toml poetry.lock ./
RUN poetry config installer.max-workers 10
RUN poetry install --no-root --with=dev
#RUN poetry install --no-root --without=dev

COPY  --chown=cykubed:cykubed src .

ENTRYPOINT ["python", "main.py"]

