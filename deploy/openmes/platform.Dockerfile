# Reuse an already-built knowledge image only when the dependency manifest matches.
# A fresh checkout can build the root Dockerfile instead.
ARG KNOWLEDGE_BASE_IMAGE=enterprise-knowledge-base:local
FROM ${KNOWLEDGE_BASE_IMAGE}
COPY requirements.txt /tmp/openmes-requirements.txt
RUN python -c 'from pathlib import Path; assert Path("/tmp/openmes-requirements.txt").read_bytes()==Path("/app/requirements.txt").read_bytes(), "Dependency manifest differs"'
COPY --chown=app:app backend /app/backend
COPY --chown=app:app migrations /app/migrations
COPY --chown=app:app scripts /app/scripts
COPY --chown=app:app services /app/services
COPY --chown=app:app alembic.ini /app/alembic.ini
