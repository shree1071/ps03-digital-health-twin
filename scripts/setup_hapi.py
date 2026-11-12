"""Download and prepare a standalone HAPI FHIR R4 server (no Docker needed).

The published hapi-fhir-jpaserver-starter 5.4.0 WAR is not self-contained:
it omits snakeyaml and any embedded servlet container, and its bundled
application.yaml has an indentation bug in the `hapi.fhir.cors` block that
makes snakeyaml fail at startup. This script fetches the missing jars and
writes a corrected external config.

Usage:  python scripts/setup_hapi.py
Then:   scripts/start_hapi.sh   (or start_hapi.bat on Windows)
"""

from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "hapi"
CONFIG = ROOT / "config"
WAR_NAME = "hapi-fhir-jpaserver-starter-5.4.0.war"
WAR_URL = (
    "https://repo1.maven.org/maven2/ca/uhn/hapi/fhir/"
    f"hapi-fhir-jpaserver-starter/5.4.0/{WAR_NAME}"
)

# Versions managed by Spring Boot 2.4.4, which this WAR is built against.
EXTRA_JARS = {
    "snakeyaml-1.27.jar": "org/yaml/snakeyaml/1.27/snakeyaml-1.27.jar",
    "tomcat-embed-core-9.0.44.jar": "org/apache/tomcat/embed/tomcat-embed-core/9.0.44/tomcat-embed-core-9.0.44.jar",
    "tomcat-embed-el-9.0.44.jar": "org/apache/tomcat/embed/tomcat-embed-el/9.0.44/tomcat-embed-el-9.0.44.jar",
    "tomcat-embed-websocket-9.0.44.jar": "org/apache/tomcat/embed/tomcat-embed-websocket/9.0.44/tomcat-embed-websocket-9.0.44.jar",
}
MAVEN = "https://repo1.maven.org/maven2/"


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have {dest.name}")
        return
    print(f"  downloading {dest.name} ...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, open(tmp, "wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    tmp.replace(dest)
    print(f"  saved {dest.name} ({dest.stat().st_size / 1048576:.1f} MB)")


def fix_cors_indentation(text: str) -> str:
    """Dedent the mis-indented `cors:` block so it nests under `hapi.fhir`.

    Upstream ships `     cors:` (5 spaces) where 4 are required, which makes
    the whole document unparseable.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_cors = False
    for line in lines:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if not in_cors and stripped.startswith("cors:") and indent == 5:
            in_cors = True
            out.append(line[1:])
            continue
        if in_cors:
            if stripped and indent >= 7:
                out.append(line[1:])
                continue
            if stripped and indent < 7:
                in_cors = False
        out.append(line)
    return "\n".join(out)


def write_config(war: Path) -> Path:
    with zipfile.ZipFile(war) as archive:
        internal = archive.read("WEB-INF/classes/application.yaml").decode("utf-8")
    fixed = fix_cors_indentation(internal)

    # Persist to a file-backed H2 database so observations survive restarts.
    fixed = fixed.replace(
        "url: 'jdbc:h2:file:./target/database/h2'",
        "url: 'jdbc:h2:file:./var/hapi/h2;DB_CLOSE_DELAY=-1'",
    )
    CONFIG.mkdir(parents=True, exist_ok=True)
    dest = CONFIG / "hapi.yaml"
    dest.write_text(fixed, encoding="utf-8")

    try:
        import yaml  # type: ignore

        yaml.safe_load(fixed)
        print("  config parses as valid YAML")
    except ImportError:
        print("  (PyYAML not installed; snakeyaml will validate at startup)")
    except Exception as exc:
        print(f"  WARNING: config still invalid: {exc}", file=sys.stderr)
        raise
    return dest


def unpack_war(war: Path) -> Path:
    """Extract WEB-INF/classes and WEB-INF/lib for a flat-classpath launch.

    Running the WAR through WarLauncher with external Tomcat jars splits the
    classloaders, and Tomcat then cannot see Spring Boot's
    TomcatEmbeddedWebappClassLoader. A flat classpath avoids the split.
    WEB-INF/lib-provided is deliberately excluded: its servlet-api 3.1 would
    conflict with the Servlet 4.0 API inside tomcat-embed-core.
    """
    dest = VENDOR / "unpacked"
    marker = dest / "WEB-INF" / "classes" / "application.yaml"
    if marker.exists():
        print("  WAR already unpacked")
        return dest
    print("  unpacking WAR ...")
    with zipfile.ZipFile(war) as archive:
        members = [
            n
            for n in archive.namelist()
            if n.startswith("WEB-INF/classes/") or n.startswith("WEB-INF/lib/")
        ]
        archive.extractall(dest, members=members)
    print(f"  unpacked {len(members)} entries")
    return dest


def main() -> int:
    VENDOR.mkdir(parents=True, exist_ok=True)
    print("HAPI FHIR R4 setup")
    fetch(WAR_URL, VENDOR / WAR_NAME)
    for name, path in EXTRA_JARS.items():
        fetch(MAVEN + path, VENDOR / name)
    unpack_war(VENDOR / WAR_NAME)
    config = write_config(VENDOR / WAR_NAME)
    print(f"\nReady. Config: {config}")
    print("Start with: scripts/start_hapi.sh  (Windows: scripts\\start_hapi.bat)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())