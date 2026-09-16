# Curated reference data

Numbers that cannot be computed from an API and have to be read off a public page, recorded by hand
with the publisher, the URL and the date attached. Nothing in here is recomputed by the pipeline; it
is quoted, and the pages label it as quoted.

Rules for adding a row, in order of importance:

1. **Only publicly readable pages.** A press release, a research house's own free summary, a trade
   association release, a filing, an IR page, or a major-media article quoting one of those. Never
   the body of a paid or subscription report, never anything behind a login, a phone number or a
   WeChat scan, never an employer's internal research.
2. **The original publisher, where possible.** If a figure is only available via an aggregator,
   record the aggregator in `source_name` and name the original in `original_publisher`.
3. **A date confirmed from the page itself.** Search engines report the re-index date for many
   Chinese media pages, which is not the publication date. If it cannot be confirmed from the page
   body, write `"unconfirmed"` rather than guessing.
4. **The number as published.** No unit conversion, no rounding, no averaging across sources, and no
   summing of quarters into a year. If two publishers disagree — or one publisher disagrees with
   itself — record both rows and say so in `caveat`.
5. **Never delete a row because its source went offline.** Leave it with the date it was recorded;
   the page shows what was published at the time.

## The two note fields

Every row carries two note fields and they are **not** interchangeable.

- **`caveat`** — one or two sentences **written for the reader**, in the voice of the page. It says
  what the figure is not: the basis, the scope, the layer of the stack, the staleness, the thing a
  reader would otherwise infer and get wrong. The page renders it beside the figure, so it must read
  as published prose. No instructions, no internal vocabulary, no "MUST NOT", no addressee.
  Every row in a list the page renders (`cited_share`, `shipments`, `capacity`, `segment_revenue`)
  needs one.
- **`curator_notes`** — the working record for whoever maintains this file. Provenance, what was
  checked verbatim and when, superseded figures, dead URLs and mirrors, traps for the next person.
  **The page never prints it**, and `pipelines/valuation/publish.py` must not copy it into
  `data/facts/` (the facts files are served at `site/public/data/facts/` and are world-readable).
  Write it as if it will be read anyway: a note is a record, not a message to the publisher.

`confidence` is `"high"` only when the figure has been checked verbatim against the source page
body, and the check belongs in `curator_notes` with its date. Anything still `"low"` must not be in
a list the page renders.

## Shape

```jsonc
{
  "track": "optical",            // or "ssb"
  "updated": "2026-09-16",
  "note": "…",                   // what this file is; not rendered
  "segment_revenue": [           // lets a diversified company enter the computed share table
    {
      "ticker": "COHR",
      "value": 5274600000, "currency": "USD",
      "period": "FY2026",
      "basis": "Segment Revenues, Datacenter & Communications segment, fiscal year ended 2026-06-30",
      "source_name": "…", "source_url": "https://…", "publish_date": "2026-08-12",
      "confidence": "high",
      "caveat": "…", "curator_notes": "…"
    }
  ],
  "cited_share": [               // published share or ranking, quoted unchanged
    {
      "entity": "Innolight", "metric": "global optical transceiver vendor rank",
      "value": "1", "unit": "rank", "period": "2025",
      "source_name": "LightCounting", "source_url": "https://…", "publish_date": "2026-03-01",
      "original_publisher": "", "confidence": "high",
      "caveat": "…", "curator_notes": "…"
    }
  ],
  "shipments": [],               // solid-state only (rendered): units or cells actually shipped
  "capacity": [],                // solid-state only (rendered): announced *capacity*, with `start_year`
  "readiness": [],               // solid-state only: target years, licence ceilings, maturity statements
  "segment_disclosure": [],      // evidence of what a company does and does not break out; not rendered
  "financials": [],              // supporting company figures; not rendered
  "pool_verdicts": [],           // per-company verdict on whether a track revenue line exists; not rendered
  "open_gaps": [],               // what is missing and why, including "nobody publishes this"
  "citability_verdict": ""       // which source types are safe to cite on a public page
}
```

`capacity` holds **capacity and nothing else** — a number of GWh, MWh, cells or tonnes a named site
can make in a year, with `start_year` set to the year the company stated. A target date with no
capacity attached, a licence ceiling, a technology-readiness statement and a floor area are all
*not* capacity: they go in `readiness`, which the page renders separately or not at all. `start_year`
is rendered in a narrow "Stated start" column, so keep it to a year or a short phrase.

A row without `source_url` is dropped by `pipelines/valuation/share.py` and logged, so an
unsourced number cannot reach a page by accident.

## Who enters the computed share pool

Recording a `segment_revenue` row is a statement that the company **discloses** that line. Whether
the company then enters basis A is decided by `share_basis` in `pipelines/valuation/config.py`, not
here: `total` uses group revenue, `segment` requires a row in this file, `none` keeps the company out
of the pool entirely because it sells into the pool rather than competing with it. A company whose
`share_basis` is `segment` and which has no row here is published as excluded, with a reason taken
from its `segment_line` — so `segment_line` must say which of "publishes it", "reports it bundled"
and "does not publish it" is true, on evidence, before the exclusion reason is fit to show a reader.
