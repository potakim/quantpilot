# QuantPilot 화면 이미지 (P1-13·P1-14, ADR 0018·0019)
# API 주소는 실행 시 서버 환경변수 QP_API_URL·QP_WS_URL로만 받는다 — 빌드에 값이 들어가지 않는다.
FROM node:22-slim AS deps
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS build
ENV NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN npm run build && npm prune --omit=dev --no-audit --no-fund

FROM node:22-slim
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /web
COPY --from=build --chown=node:node /web/package.json /web/next.config.mjs ./
COPY --from=build --chown=node:node /web/node_modules ./node_modules
COPY --from=build --chown=node:node /web/.next ./.next
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
