#!/bin/sh
# Re-render every diagram into docs/img. Needs Java and Graphviz; PLANTUML_JAR defaults to ./plantuml.jar
# (download it from https://github.com/plantuml/plantuml/releases).
set -e
cd "$(dirname "$0")"
JAR="${PLANTUML_JAR:-plantuml.jar}"
java -jar "$JAR" -tsvg -o ../img ./*.puml
python3 make_charts.py
