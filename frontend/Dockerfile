ARG NODE_IMAGE=node:20-alpine
ARG NGINX_IMAGE=nginx:1.25-alpine
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS builder

ARG NODE_IMAGE
ARG NGINX_IMAGE
ARG TARGETPLATFORM
ARG BUILDPLATFORM

WORKDIR /app

COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .
RUN npm run build

FROM ${NGINX_IMAGE} AS runtime

ARG NGINX_IMAGE

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
