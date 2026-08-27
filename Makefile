# lhdtrack -- daily QoR regression for LiveHD.
#
# The cron entry point is `make run`. It is deliberately the only thing a
# scheduled job needs to know:
#
#     0 3 * * *  cd /path/to/lhdtrack && make run >> var/cron.log 2>&1
#
# Everything is scoped to this machine. `uname -n` is in every ledger row and
# names this machine's pages, because wall clock and peak RSS -- half of what
# lhdtrack measures -- do not travel between hosts.

ROOT    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
HOST    := $(shell uname -n)
# data/   committed: the append-only per-machine ledgers, the only source of truth
# target/ generated: every page, re-derived from data/ on each run
# (comments go ABOVE the assignment -- a trailing one leaks whitespace into the value)
DATA    := $(ROOT)/data
TARGET  := $(ROOT)/target

# Prefer an installed `lhdtrack`; fall back to running from the source tree so a
# fresh checkout works with no install step.
LHDTRACK := $(shell command -v lhdtrack 2>/dev/null)
ifeq ($(LHDTRACK),)
LHDTRACK := PYTHONPATH=$(ROOT)/src python3 -m lhdtrack.cli
endif

# Liberty for `make toolchain-local`. ciel's layout by default; override on the
# command line if the PDK lives elsewhere.
TECH_DIR ?= $(HAGENT_TECH_DIR)
SKY130   ?= $(TECH_DIR)/sky130_fd_sc_hd__tt_025C_1v80.lib
# ASAP7 ships gzipped in lambdapdk; local_toolchain decompresses into var/.
# All five cell families are needed -- a mapped netlist references cells from
# more than one, and OpenSTA cannot link what it has not read.
ASAP7    ?= $(HOME)/projs/lambdapdk/lambdapdk/asap7/libs/asap7sc7p5t_rvt/nldm/*RVT_TT*.lib.gz

RUN_ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help run check report show seed toolchain toolchain-local \
        cache clean-cache clean-work clean distclean lint

help: ## Show this help
	@echo "lhdtrack -- $(HOST)"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  cron:  0 3 * * *  cd $(ROOT) && make run >> var/cron.log 2>&1"

## ---------------------------------------------------------------- daily ----

# A failing test must still leave a readable report and a non-zero exit: cron
# needs the signal, and whoever reads the mail needs the page. So the status is
# captured, the paths are printed either way, and the status is re-raised last.
# The lint is ADVISORY here. A corpus problem -- a test missing a testbench, a
# stale manifest -- is worth printing, but it must not stop the regression: the
# other 170 tests still have numbers to produce, and a nightly that refuses to
# run because one test is malformed reports nothing at all.
run: ## THE CRON TARGET: lint the corpus, run the regression, render this machine's pages
	-@$(LHDTRACK) check
	@rc=0; $(LHDTRACK) run $(RUN_ARGS) || rc=$$?; \
	 echo; \
	 echo "report:  $(TARGET)/report-$(HOST).html"; \
	 echo "history: $(TARGET)/timeseries-$(HOST).html"; \
	 if [ $$rc -ne 0 ]; then echo "STATUS:  $$rc (a test failed -- see the report's failures section)"; fi; \
	 exit $$rc

check: ## Lint the corpus (every test has both languages, a testbench, an SDC, a licence)
	@$(LHDTRACK) check

report: ## Re-render target/ from data/ without re-measuring
	@$(LHDTRACK) report

show: ## Print this machine's last run in the terminal
	@$(LHDTRACK) show

## ------------------------------------------------------------- toolchain ----

toolchain: ## Build every tool from its pin and stage it (the hermetic path)
	bazel run //:sync-toolchain

toolchain-local: ## Stage tools already on this machine (fast, NOT pinned -- rows are marked "local")
	@python3 $(ROOT)/tools/local_toolchain.py \
	  --liberty "sky130=$(SKY130)" --liberty "asap7=$(ASAP7)"

## ------------------------------------------------------------------ corpus --

seed: ## Generate the harness, driver pair and SDC for every test (or TEST=<name>)
	@$(LHDTRACK) import seed $(TEST)

## ------------------------------------------------------------------ upkeep --

cache: ## Show what the baseline cache is holding
	@$(LHDTRACK) cache

clean-cache: ## Drop cached baselines, forcing yosys and verilator to re-measure
	@$(LHDTRACK) cache --clear all

clean-work: ## Drop scratch workdirs (netlists, vobj trees); keeps the cache and the ledger
	rm -rf $(ROOT)/var/work

clean: clean-work ## Remove scratch state, generated pages and __pycache__
	rm -rf $(TARGET)
	find $(ROOT) -name __pycache__ -type d -prune -exec rm -rf {} +

distclean: clean ## Also drop the toolchain and the cache. data/ IS NEVER TOUCHED.
	rm -rf $(ROOT)/var/toolchain $(ROOT)/var/cache

lint: ## Compile-check every Python file
	@python3 -m compileall -q $(ROOT)/src $(ROOT)/tools $(ROOT)/flows && echo "ok"
