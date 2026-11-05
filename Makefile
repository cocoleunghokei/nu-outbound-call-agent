# Single entry point for every quality check, used the same way locally and in CI.
#
# Contract for subprojects: once backend/ or frontend/ has code, give it a
# Makefile with three targets, `lint`, `test` and `build`. The targets below
# call them automatically; a subproject without a Makefile is skipped.

SUBPROJECTS := backend frontend spikes/vapi

.DEFAULT_GOAL := help
.PHONY: help setup check hygiene secrets lint test build db-check

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install the git hooks (pre-commit and commit-msg)
	pre-commit install

check: hygiene secrets lint test build ## Run everything CI runs, except the database check

hygiene: ## Repo-wide checks: secrets, whitespace, YAML, workflows, file sizes
	pre-commit run --all-files

secrets: ## Scan the whole git history for leaked keys
	pre-commit run gitleaks-history --hook-stage manual --all-files

lint: ## Lint each subproject that has a Makefile
	@$(call each_subproject,lint)

test: ## Test each subproject that has a Makefile
	@$(call each_subproject,test)

build: ## Build each subproject that has a Makefile
	@$(call each_subproject,build)

db-check: ## Rebuild the local Supabase DB from migrations, lint it, check RLS (needs Docker)
	./scripts/check-db.sh

# $(call each_subproject,<target>) runs <target> in every subproject that has a Makefile.
define each_subproject
	set -e; for dir in $(SUBPROJECTS); do \
	  if [ -f "$$dir/Makefile" ]; then \
	    echo "==> $$dir: $(1)"; $(MAKE) --no-print-directory -C "$$dir" $(1); \
	  else \
	    echo "==> $$dir: no Makefile yet, skipping $(1)"; \
	  fi; \
	done
endef
