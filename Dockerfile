FROM python:3.10-slim-buster as build
RUN apt-get update
RUN apt-get install -y --no-install-recommends \
build-essential gcc curl git-core bash

WORKDIR /src/app
RUN python -m venv /usr/app/venv
ENV PATH="/usr/app/venv/bin:$PATH"

RUN curl -LO "https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x ./kubectl

COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.10-slim-buster@sha256:b0f095dee13b2b4552d545be4f0f1c257f26810c079720c0902dc5e7f3e6b514
WORKDIR /usr/app
COPY --from=build /usr/app/venv ./venv
COPY --from=build /src/app/kubectl /usr/bin
COPY --from=build /bin/bash /usr/bin

COPY src/ .

ENV PATH="/usr/app/venv/bin:$PATH"
