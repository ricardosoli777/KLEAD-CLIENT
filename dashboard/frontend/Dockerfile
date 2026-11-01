FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

RUN npm run build

EXPOSE 3000

# API_KEY é lido em runtime pelo proxy server-side (injetado via env do stack) — nunca no browser nem na imagem
ENV BACKEND_URL="http://backend:8000"

CMD ["npm", "start"]
