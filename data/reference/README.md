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
4. **The number as published.** No unit conversion, no rounding, no averaging across sources. If two
   publishers disagree, record both rows.
5. **Never delete a row because its source went offline.** Leave it with the date it was recorded;
   the page shows what was published at the time.

## Shape

```jsonc
{
  "track": "optical",            // or "ssb"
  "updated": "2026-09-16",
  "segment_revenue": [           // lets a diversified company enter the computed share table
    {
      "ticker": "AVGO",
      "value": 1234000000, "currency": "USD",
      "period": "FY2025",
      "basis": "10-K segment note",
      "source_name": "...", "source_url": "https://...", "publish_date": "2026-01-01",
      "notes": ""
    }
  ],
  "cited_share": [               // published share or ranking, quoted unchanged
    {
      "entity": "Innolight", "metric": "global optical transceiver vendor rank",
      "value": "1", "unit": "rank", "period": "2025",
      "source_name": "LightCounting", "source_url": "https://...", "publish_date": "2026-03-01",
      "original_publisher": "", "confidence": "high", "notes": ""
    }
  ],
  "shipments": [],               // solid-state only: reported semi-solid / solid-state shipments
  "capacity": []                 // solid-state only: announced capacity, with `start_year`
}
```

A row without `source_url` is dropped by `pipelines/valuation/share.py` and logged, so an
unsourced number cannot reach a page by accident.
