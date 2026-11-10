# Contracts targets. The root Makefile calls lint, test and build.
#
# `gen` writes three outputs from the JSON Schemas in this directory:
#   backend/app/contracts/generated.py   pydantic models
#   frontend/src/contracts/generated.ts  TypeScript types and parseCallEvent
#   backend/app/contracts/schemas/       a copy of every schema, shipped with the backend build
# `lint` checks the schemas and fails if the checked-in outputs differ from a fresh `gen`.
.PHONY: gen lint test build

PY_OUT ?= ../backend/app/contracts/generated.py
TS_OUT ?= ../frontend/src/contracts/generated.ts
SCHEMA_OUT ?= ../backend/app/contracts/schemas
UV := uv run --project ../backend

gen:
	$(UV) python gen_py.py $(PY_OUT)
	node gen-ts.mjs $(TS_OUT)
	rm -rf $(SCHEMA_OUT)
	mkdir -p $(SCHEMA_OUT)
	find . -name '*.v1.json' -not -path './examples/*' | while read -r f; do \
	  mkdir -p "$(SCHEMA_OUT)/$$(dirname "$$f")" && cp "$$f" "$(SCHEMA_OUT)/$$f"; \
	done

lint:
	$(UV) python check_schemas.py
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	  $(MAKE) --no-print-directory gen PY_OUT="$$tmp/generated.py" TS_OUT="$$tmp/generated.ts" SCHEMA_OUT="$$tmp/schemas" >/dev/null && \
	  diff -u ../backend/app/contracts/generated.py "$$tmp/generated.py" && \
	  diff -u ../frontend/src/contracts/generated.ts "$$tmp/generated.ts" && \
	  diff -ru ../backend/app/contracts/schemas "$$tmp/schemas" || \
	  { echo "contracts are out of date: run 'make -C contracts gen' and commit the result"; exit 1; }

test:
	node test-parse.mjs

build:
	@:
