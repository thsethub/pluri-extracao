# Estágio de Build
FROM node:20-alpine AS builder
WORKDIR /app

# Instalar dependências
COPY package*.json ./
RUN npm install

# Copiar código e buildar
COPY . .
# Ignorar lints no build para evitar quebras por avisos
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Estágio de Produção
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Copiar apenas o necessário
COPY --from=builder /app/next.config.ts ./
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

EXPOSE 3000

CMD ["npm", "start"]
