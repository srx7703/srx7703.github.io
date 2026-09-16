.PHONY: setup test lint snapshot snapshot-midterms snapshot-fomc

setup:            ## install python deps (uv) 
	uv sync

test:             ## unit tests
	uv run pytest

lint:             ## ruff
	uv run ruff check .

snapshot:         ## snapshot all prediction-market sets
	uv run python -m pipelines.predmarkets.snapshot --set all

snapshot-midterms:
	uv run python -m pipelines.predmarkets.snapshot --set midterms

snapshot-fomc:
	uv run python -m pipelines.predmarkets.snapshot --set fomc
