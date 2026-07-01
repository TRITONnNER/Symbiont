# Веб-портал Симбионта. Контекст сборки — КОРЕНЬ репозитория:
#   docker build -f platform/web-portal/Dockerfile -t symbiont-portal .
#   docker run -p 8080:80 --add-host=host.docker.internal:host-gateway symbiont-portal
# Бэкенд ожидается на 127.0.0.1:8600 (при необходимости поправьте upstream в nginx.conf).
FROM nginx:alpine

# Вшитая оболочка (React/Babel/шрифты/флаги/иконки — всё локально, без CDN).
COPY webapp/ /usr/share/nginx/html/

# Служебные файлы разработки в образ не тащим.
RUN rm -f /usr/share/nginx/html/serve.py \
          /usr/share/nginx/html/vendor_assets.py \
          /usr/share/nginx/html/README.md

COPY platform/web-portal/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
