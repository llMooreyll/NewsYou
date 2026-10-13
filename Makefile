# Newsletter pipeline — common commands.
#
# The venv keeps the system Python untouched. `make setup` is the only step that
# needs the network for packages; everything else just talks to the sources.

PY      ?= ./.venv/bin/python
PY_SYS  ?= python3
FULLTEXT ?= today

.PHONY: help setup check collect ai render all ab clean

help:
	@echo "setup     create .venv and install requirements.txt"
	@echo "check     verify the pipeline imports and both engines resolve"
	@echo "collect   fetch every source into data/collected"
	@echo "ai        build the stage-2 decisions and apply them"
	@echo "render    build site/digest.html"
	@echo "all       collect + ai + render"
	@echo "ab        run both engines and write reports/engine-comparison.md"
	@echo "clean     remove generated data (keeps site/digest.html)"

setup:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

check:
	$(PY) -c "import trafilatura, feedparser; print('modern engine: ok')"
	$(PY_SYS) -c "import sys; sys.path.insert(0,'tools'); \
from collectors import extract as ex; print('legacy engine: ok')"

collect:
	$(PY) tools/collect.py --all --fulltext $(FULLTEXT) \
		--outdir data/collected --aidir data/ai

ai:
	$(PY) tools/demo_decisions.py --collected data/collected --ai data/ai
	$(PY) tools/ai_apply.py --collected data/collected --ai data/ai --out data/processed

render:
	$(PY) tools/render_digest.py --indir data/processed \
		--fallback data/collected --output site/digest.html

all: collect ai render

ab:
	$(PY) tools/run_ab.py

clean:
	rm -rf data/collected data/ai data/processed \
		data/collected-legacy data/ai-legacy data/processed-legacy \
		data/collected-modern data/ai-modern data/processed-modern \
		reports
