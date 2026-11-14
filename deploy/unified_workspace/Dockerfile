ARG BASE_IMAGE=enterprise-knowledge-manufacturing:main-zero-model-20261110-r1
FROM ${BASE_IMAGE}
USER root
COPY requirements.txt /tmp/release-requirements.txt
RUN cmp /tmp/release-requirements.txt /app/requirements.txt && rm /tmp/release-requirements.txt
RUN python -c "import shutil; [shutil.rmtree('/app/'+p, ignore_errors=True) for p in ('backend','scripts','services','migrations','deployment')]"
COPY --chown=app:app backend /app/backend
COPY --chown=app:app scripts /app/scripts
COPY --chown=app:app services /app/services
COPY --chown=app:app migrations /app/migrations
COPY --chown=app:app alembic.ini /app/alembic.ini
ENV PYTHONPATH=/app
USER app
CMD ["python","-m","uvicorn","backend.app.main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
