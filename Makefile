# Newsletter pipeline — common commands.
#
# The venv keeps the system Python untouched. `make setup` is the only step that
# needs the network for packages; everything else just talks to the sources.
#
# All behaviour comes from config/config.toml. These variables are just the
# common overrides; anything not set here is taken from the file.
#
#   make all                          # collect + AI + render
#   make all AI=none                  # no model call
#   make all AI_MODEL=deepseek-flash  # a different model
#   make config                       # show the effective configuration
#   make archive
#
# See the file itself for every option, or run `make config`.

PY       ?= ./.venv/bin/python
PY_SYS   ?= python3
CONFIG   ?= config/config.toml
FULLTEXT ?=
AI       ?=
AI_MODEL ?=

# Only pass a flag when the variable is set, so config.toml stays in charge.
CONFIG_ARG   = $(if $(CONFIG),--config $(CONFIG),)
FULLTEXT_ARG = $(if $(FULLTEXT),--fulltext $(FULLTEXT),)
AI_ARG       = $(if $(AI),--ai-backend $(AI),)
AI_MODEL_ARG = $(if $(AI_MODEL),--ai-model $(AI_MODEL),)
AI_ARGS      = $(AI_ARG) $(AI_MODEL_ARG)

.PHONY: help setup check test collect ai demo render all daily ab archive clean config

help:
	@echo "setup     create .venv and install requirements.txt"
	@echo "check     verify the pipeline imports and both engines resolve"
	@echo "test      run the offline regression tests"
	@echo "config    print the effective configuration and where each value came from"
	@echo "collect   fetch every source into data/collected"
	@echo "ai        apply stage-2 decisions (AI=none to skip the model call)"
	@echo "demo      seed example decisions without a model, then apply them"
	@echo "render    build site/digest.html"
	@echo "all       collect + ai + render"
	@echo "daily     all, then archive a copy of the day (this is the one to run)"
	@echo "ab        run both engines and write reports/engine-comparison.md"
	@echo "archive   archive the current outputs without re-running anything"
	@echo "clean     remove generated data (keeps site/digest.html and archive/)"
	@echo ""
	@echo "overrides: CONFIG=$(CONFIG) FULLTEXT= AI= AI_MODEL="

setup:
	@python3 -m venv .venv
	@$(PY) -m pip install --upgrade pip
	@$(PY) -m pip install -r requirements.txt

check:
	@$(PY) -c "import trafilatura, feedparser; print('modern engine: ok')"
	@$(PY_SYS) -c "import sys; sys.path.insert(0,'tools'); \
from collectors import extract as ex; print('legacy engine: ok')"

test:
	@$(PY) -m unittest discover -s tests -v

config:
	@$(PY) tools/collect.py --show-config $(CONFIG_ARG)

# Every recipe below is silent. The tools print their own progress and summary,
# so echoing the command line as well only buries the output that matters.
collect:
	@$(PY) tools/collect.py --all $(CONFIG_ARG) $(FULLTEXT_ARG) \
		--outdir data/collected --aidir data/ai

ai:
	@$(PY) tools/ai_apply.py --collected data/collected --ai data/ai \
		--out data/processed $(CONFIG_ARG) $(AI_ARGS)

demo:
	@$(PY) tools/demo_decisions.py --collected data/collected --ai data/ai
	@$(PY) tools/ai_apply.py --collected data/collected --ai data/ai \
		--out data/processed $(CONFIG_ARG) --ai-backend none

render:
	@$(PY) tools/render_digest.py --indir data/processed \
		--fallback data/collected --output site/digest.html $(CONFIG_ARG)

all: collect ai render

# The everyday command: everything `all` does, then a snapshot of the day.
# The digest at the root is copied into the archive, never moved, so
# site/digest.html is always the current report and the next run may overwrite it.
daily: all archive

ab:
	@$(PY) tools/run_ab.py

archive:
	@$(PY) tools/archive.py $(CONFIG_ARG) $(if $(DAY),--day $(DAY),) \
		$(if $(FORCE),--force,)

clean:
	@rm -rf data/collected data/ai data/processed \
		data/collected-legacy data/ai-legacy data/processed-legacy \
		data/collected-modern data/ai-modern data/processed-modern \
		reports
