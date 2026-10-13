# Newsletter pipeline — common commands.
#
# The venv keeps the system Python untouched. `make setup` is the only step that
# needs the network for packages; everything else just talks to the sources.
#
# The AI layer is off by default. Enable it with AI=pi:
#
#   make all AI=pi AI_MODEL=deepseek-flash
#   make archive AI=pi

PY       ?= ./.venv/bin/python
PY_SYS   ?= python3
FULLTEXT ?= today
AI       ?= none
AI_MODEL ?=

.PHONY: help setup check test collect ai demo render all ab archive clean

help:
	@echo "setup     create .venv and install requirements.txt"
	@echo "check     verify the pipeline imports and both engines resolve"
	@echo "test      run the offline regression tests"
	@echo "collect   fetch every source into data/collected"
	@echo "ai        apply stage-2 decisions (AI=pi to call the model, default none)"
	@echo "demo      seed example decisions without a model, then apply them"
	@echo "render    build site/digest.html"
	@echo "all       collect + ai + render"
	@echo "ab        run both engines and write reports/engine-comparison.md"
	@echo "archive   archive today's run into archive/<day>/ (AI=pi to use the model)"
	@echo "clean     remove generated data (keeps site/digest.html and archive/)"

setup:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

check:
	$(PY) -c "import trafilatura, feedparser; print('modern engine: ok')"
	$(PY_SYS) -c "import sys; sys.path.insert(0,'tools'); \
from collectors import extract as ex; print('legacy engine: ok')"

test:
	$(PY) -m unittest discover -s tests -v

collect:
	$(PY) tools/collect.py --all --fulltext $(FULLTEXT) \
		--outdir data/collected --aidir data/ai

ai:
	$(PY) tools/ai_apply.py --collected data/collected --ai data/ai \
		--out data/processed --ai-backend $(AI) \
		$(if $(AI_MODEL),--ai-model $(AI_MODEL),)

demo:
	$(PY) tools/demo_decisions.py --collected data/collected --ai data/ai
	$(PY) tools/ai_apply.py --collected data/collected --ai data/ai --out data/processed

render:
	$(PY) tools/render_digest.py --indir data/processed \
		--fallback data/collected --output site/digest.html

all: collect ai render

ab:
	$(PY) tools/run_ab.py

archive:
	$(PY) tools/archive.py --fulltext $(FULLTEXT) --ai-backend $(AI) \
		$(if $(AI_MODEL),--ai-model $(AI_MODEL),)

clean:
	rm -rf data/collected data/ai data/processed \
		data/collected-legacy data/ai-legacy data/processed-legacy \
		data/collected-modern data/ai-modern data/processed-modern \
		reports
