ARG MAVEN_IMAGE=maven:3.9.11-eclipse-temurin-17
FROM ${MAVEN_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/tests
COPY demo/python/requirements.lock /tmp/requirements.lock
RUN /opt/tests/bin/pip install --no-cache-dir -r /tmp/requirements.lock
ENV PATH="/opt/tests/bin:${PATH}"
WORKDIR /builds
