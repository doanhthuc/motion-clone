FROM node:20-alpine
# ffmpeg → ffprobe đọc duration audio khi upload (audio library)
# yt-dlp → import video/audio từ URL Facebook/TikTok/YouTube trong Input node
# aria2 → tải model song song (-x16) cho tab "Models AI" (nhanh hơn wget nhiều với file chục GB)
RUN apk add --no-cache ffmpeg procps aria2 yt-dlp
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund
COPY src ./src
EXPOSE 8080
CMD ["node", "src/server.js"]
