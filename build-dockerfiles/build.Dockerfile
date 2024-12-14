FROM node:slim

RUN apt-get update && apt-get install -y curl gpg

RUN curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | tee /usr/share/keyrings/helm.gpg > /dev/null

RUN apt-get install apt-transport-https --yes && \
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" | tee /etc/apt/sources.list.d/helm-stable-debian.list && \
   apt-get update && \
   apt-get install helm

RUN npm i -g wrangler
