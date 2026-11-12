@echo off
REM Start the HAPI FHIR R4 server on port 8080 with persistent H2 storage.
REM Requires: python scripts\setup_hapi.py  (run once)
setlocal
cd /d "%~dp0.."

if not exist "vendor\hapi\unpacked\WEB-INF\classes" (
  echo HAPI not prepared. Run: python scripts\setup_hapi.py
  exit /b 1
)

REM Spring Boot 2.4.4 is happiest on JDK 17. Override with HAPI_JAVA.
set "JAVA_CMD=%HAPI_JAVA%"
if "%JAVA_CMD%"=="" (
  if exist "C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot\bin\java.exe" set "JAVA_CMD=C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot\bin\java.exe"
)
if "%JAVA_CMD%"=="" (
  if exist "C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot\bin\java.exe" set "JAVA_CMD=C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot\bin\java.exe"
)
if "%JAVA_CMD%"=="" set "JAVA_CMD=java"

if not exist "var\hapi" mkdir "var\hapi"

setlocal enabledelayedexpansion
set "CP=vendor\hapi\unpacked\WEB-INF\classes;vendor\hapi\unpacked\WEB-INF\lib\*"
for %%J in (vendor\hapi\*.jar) do set "CP=!CP!;%%J"

echo Starting HAPI FHIR R4 on http://localhost:8080/fhir
"%JAVA_CMD%" -Xmx1g -cp "!CP!" ca.uhn.fhir.jpa.starter.Application --spring.config.location=file:./config/hapi.yaml --server.port=8080
endlocal
endlocal