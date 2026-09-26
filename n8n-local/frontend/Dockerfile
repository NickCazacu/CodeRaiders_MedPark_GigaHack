FROM n8nio/n8n:latest

WORKDIR /opt/medpark-frontend
COPY server.js ./server.js
COPY public ./public

EXPOSE 8080
ENTRYPOINT ["node"]
CMD ["server.js"]
