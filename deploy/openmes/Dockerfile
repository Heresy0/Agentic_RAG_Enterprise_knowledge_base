ARG OPENMES_BASE_IMAGE=ghcr.io/mes-open/openmes@sha256:0b64934615eaa60697777ed9d18423eb91bc38b27ea1e0cbba3e326f74cc94fe
FROM ${OPENMES_BASE_IMAGE}
COPY ReadOnlyIntegration.php /var/www/html/app/Http/Middleware/ReadOnlyIntegration.php
# Keep the upstream auth, policies and line scoping. Add a capability restriction
# for the dedicated knowledge:read-only Sanctum token, including direct API use.
RUN php -r '$p="/var/www/html/bootstrap/app.php"; $s=file_get_contents($p); $a="->withMiddleware(function (Middleware \$middleware): void {"; if(substr_count($s,$a)!==1){exit(1);} file_put_contents($p,str_replace($a,$a."\n        \$middleware->api(prepend: [\\App\\Http\\Middleware\\ReadOnlyIntegration::class]);",$s));' \
    && php -l /var/www/html/app/Http/Middleware/ReadOnlyIntegration.php \
    && php -l /var/www/html/bootstrap/app.php
