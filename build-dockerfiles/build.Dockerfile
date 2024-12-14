FROM node:alpine

RUN apk update && apk add curl gpg helm httpie
RUN npm i -g wrangler
