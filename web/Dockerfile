FROM nginx:1.29-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/*.html web/*.js web/*.css web/field.jpg /usr/share/nginx/html/
COPY web/fonts/ /usr/share/nginx/html/fonts/
COPY web/manifest.webmanifest /usr/share/nginx/html/
COPY web/icons/ /usr/share/nginx/html/icons/
# Cache-bust: every JS/CSS URL in index.html gets ?v=<hash of the shipped files>, so after a deploy no browser or
# CDN edge (e.g. a Cloudflare tunnel) can mix an old cached script with new ones. nginx.conf serves these versioned
# URLs as immutable (1 year) and index.html as no-cache, so a deploy is picked up on the next page load.
# Then precompress the text files once (gzip -9, served by gzip_static) instead of on the fly per request.
RUN cd /usr/share/nginx/html && v=$(cat *.js *.css | md5sum | cut -c1-10) \
 && sed -i -E "s#(src|href)=\"([A-Za-z0-9_.-]+\.(js|css))\"#\1=\"\2?v=${v}\"#g" index.html \
 && grep -c "?v=${v}" index.html \
 && for f in *.html *.js *.css; do gzip -9 -n -c "$f" > "$f.gz" && touch -r "$f" "$f.gz"; done
