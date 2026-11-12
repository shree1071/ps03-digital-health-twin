#!/usr/bin/env bash
# Start the HAPI FHIR R4 server on port 8080 with persistent H2 storage.
# Requires: python scripts/setup_hapi.py  (run once)
set -euo pipefail
cd "$(dirname "$0")/.."

CLASSES="vendor/hapi/unpacked/WEB-INF/classes"
if [ ! -d "$CLASSES" ]; then
  echo "HAPI not prepared. Run: python scripts/setup_hapi.py" >&2
  exit 1
fi

# Spring Boot 2.4.4 is happiest on JDK 17. Override with HAPI_JAVA if needed.
JAVA_CMD="${HAPI_JAVA:-}"
if [ -z "$JAVA_CMD" ]; then
  for candidate in \
    "/c/Program Files/Microsoft/jdk-17.0.19.10-hotspot/bin/java" \
    "/c/Program Files/Eclipse Adoptium/jdk-17.0.18.8-hotspot/bin/java" \
    "/c/Program Files/Eclipse Adoptium/jdk-17.0.19.10-hotspot/bin/java"; do
    if [ -x "$candidate" ]; then JAVA_CMD="$candidate"; break; fi
  done
fi
JAVA_CMD="${JAVA_CMD:-java}"

mkdir -p var/hapi

# Flat classpath: WAR classes + WAR libs + the jars the WAR omits.
CP="$CLASSES;vendor/hapi/unpacked/WEB-INF/lib/*"
for jar in vendor/hapi/*.jar; do CP="$CP;$jar"; done

echo "Starting HAPI FHIR R4 on http://localhost:8080/fhir"
exec "$JAVA_CMD" -Xmx1g -cp "$CP" ca.uhn.fhir.jpa.starter.Application \
  --spring.config.location=file:./config/hapi.yaml \
  --server.port=8080