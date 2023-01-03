FROM python:3.10-slim-buster as build
RUN apt-get update
RUN apt-get install -y --no-install-recommends build-essential gcc

WORKDIR /src/app
RUN python -m venv /usr/app/venv
ENV PATH="/usr/app/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.10-slim-buster@sha256:b0f095dee13b2b4552d545be4f0f1c257f26810c079720c0902dc5e7f3e6b514

RUN groupadd -g 999 python && \
    useradd -r -u 999 -g python python

RUN mkdir /usr/app && chown python:python /usr/app
WORKDIR /usr/app

COPY --chown=python:python --from=build /usr/app/venv ./venv
COPY --chown=python:python . .

USER 999

COPY src/ .

ENV PATH="/usr/app/venv/bin:$PATH"

ENTRYPOINT ["python", "./main.py"]
