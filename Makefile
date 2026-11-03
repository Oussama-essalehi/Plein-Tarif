# Plein Tarif — pipeline targets. Each target delegates to run.py so that
# Windows users can run exactly the same steps with `python run.py <target>`.
PYTHON ?= python

.PHONY: download parse clean enrich model analyze maps export test all

download:
	$(PYTHON) run.py download
parse:
	$(PYTHON) run.py parse
clean:
	$(PYTHON) run.py clean
enrich:
	$(PYTHON) run.py enrich
model:
	$(PYTHON) run.py model
analyze:
	$(PYTHON) run.py analyze
maps:
	$(PYTHON) run.py maps
export:
	$(PYTHON) run.py export
test:
	$(PYTHON) run.py test
all:
	$(PYTHON) run.py all
