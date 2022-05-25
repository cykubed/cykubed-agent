FROM python:3.8-slim

RUN apt-get update && apt-get install -y wget gnupg2 curl pipenv default-libmysqlclient-dev git-core
# Using Debian, as root
RUN curl -fsSL https://deb.nodesource.com/setup_14.x | bash -
RUN apt-get install -y nodejs

WORKDIR /app
COPY Pipfile* ./
RUN pipenv install --system --ignore-pipfile

RUN curl -LO "https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x ./kubectl && mv kubectl /usr/local/bin/kubectl

COPY src /app/app

ENV PYTHONPATH app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000"]
