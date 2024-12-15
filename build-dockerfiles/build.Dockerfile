FROM node:alpine

RUN apk update && apk add curl gpg helm httpie bash
RUN npm i -g wrangler
