FROM nginx:1.29-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/*.html web/*.js web/*.css web/field.jpg /usr/share/nginx/html/
COPY web/fonts/ /usr/share/nginx/html/fonts/
# Cache-bust: every JS/CSS URL in index.html gets ?v=<hash of the shipped files>, so after a deploy no browser or
# CDN edge (e.g. a Cloudflare tunnel) can mix an old cached script with new ones.
RUN cd /usr/share/nginx/html && v=$(cat *.js *.css | md5sum | cut -c1-10) \
 && sed -i -E "s#(src|href)=\"([A-Za-z0-9_.-]+\.(js|css))\"#\1=\"\2?v=${v}\"#g" index.html \
 && grep -c "?v=${v}" index.html
