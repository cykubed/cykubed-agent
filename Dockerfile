FROM python:3.11-slim-buster as build

WORKDIR /usr/app

# FIXME switch to non-root https://elastisys.com/howto-stop-running-containers-as-root-in-kubernetes/
RUN mkdir /var/lib/cykubecache

ENV PATH="/home/cykube/.local/bin:$PATH"

RUN pip install poetry==1.3.1
COPY pyproject.toml poetry.lock ./
RUN poetry config installer.max-workers 10
# when this goes to prod switch to --without=dev
RUN poetry install --with=dev

COPY src .

ENTRYPOINT ["poetry", "run", "python", "main.py"]

