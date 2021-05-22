FROM python:3.8-slim

RUN apt-get update && apt-get install -y wget gnupg2 curl pipenv default-libmysqlclient-dev git-core

WORKDIR /app
COPY Pipfile* ./
RUN pipenv install --system --ignore-pipfile

RUN \
  echo "deb https://deb.nodesource.com/node_15.x buster main" > /etc/apt/sources.list.d/nodesource.list && \
  wget -qO- https://deb.nodesource.com/gpgkey/nodesource.gpg.key | apt-key add - && \
  echo "deb https://dl.yarnpkg.com/debian/ stable main" > /etc/apt/sources.list.d/yarn.list && \
  wget -qO- https://dl.yarnpkg.com/debian/pubkey.gpg | apt-key add - && \
  apt-get update && \
  apt-get install -yqq nodejs yarn && \
  npm i -g npm@^6 && \
  rm -rf /var/lib/apt/lists/*

COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile

RUN curl -LO "https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x ./kubectl && mv kubectl /usr/local/bin/kubectl

COPY src /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/

ENV PYTHONPATH app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000"]
