.PHONY: setup test lint snapshot snapshot-midterms snapshot-fomc backfill sec valuation valuation-daily publish site all

setup:            ## install python deps (uv) and site deps (npm)
	uv sync
	cd site && npm install

test:             ## unit tests + prose-numbers guardrail
	uv run pytest -q -p no:warnings

lint:             ## ruff
	uv run ruff check .

snapshot:         ## full snapshot of all prediction-market sets
	uv run python -m pipelines.predmarkets.snapshot --set all --scope full

snapshot-midterms:
	uv run python -m pipelines.predmarkets.snapshot --set midterms

snapshot-fomc:
	uv run python -m pipelines.predmarkets.snapshot --set fomc

backfill:         ## daily price history for the FOMC set
	uv run python -m pipelines.predmarkets.backfill --set fomc

sec:              ## SEC XBRL ingest + transform (needs SEC_USER_AGENT with a contact address)
	uv run python -m pipelines.sec.ingest
	uv run python -m pipelines.sec.transform

valuation:        ## prices, FX, analyst consensus and fundamentals for the valuation pages
	uv run python -m pipelines.valuation.fx
	uv run python -m pipelines.valuation.prices
	uv run python -m pipelines.valuation.estimates_yf
	uv run python -m pipelines.valuation.estimates_em
	uv run python -m pipelines.valuation.fundamentals

valuation-daily:  ## the weekday run: prices and FX only, then rebuild the pages
	uv run python -m pipelines.valuation.fx
	uv run python -m pipelines.valuation.prices
	uv run python -m pipelines.valuation.publish

publish:          ## rebuild every mart and facts file from the snapshots
	uv run python -m pipelines.predmarkets.publish
	uv run python -m pipelines.statarb.publish
	uv run python -m pipelines.finllm.publish
	uv run python -m pipelines.valuation.publish
	uv run python -m pipelines.valuation.evaluate

site:             ## build the static site (syncs data/ into site/public/data first)
	cd site && npm run build

all: setup test snapshot backfill sec valuation publish site   ## rebuild everything from scratch (sec and valuation need SEC_USER_AGENT)
