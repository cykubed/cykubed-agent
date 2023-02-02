FROM python:3.10-slim-buster as build
RUN apt-get update
RUN apt-get install -y --no-install-recommends build-essential gcc

WORKDIR /src/app
RUN python -m venv /usr/app/venv
ENV PATH="/usr/app/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.10-slim-buster@sha256:b0f095dee13b2b4552d545be4f0f1c257f26810c079720c0902dc5e7f3e6b514

WORKDIR /usr/app
ENV PATH="/usr/app/venv/bin:$PATH"
RUN useradd -m cykube && chown cykube /usr/app
USER cykube

COPY --from=build /usr/app/venv ./venv
COPY src/ .

ENTRYPOINT ["python", "./main.py"]

