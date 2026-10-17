# Curating ring C (private cybersecurity companies): the rules

Ring C is the private-company layer of the cybersecurity page. It is hand-curated, so it gets the most checking.
The validator is `pipelines/cyber/reference.py` (generic rules in `pipelines/common/curation.py`); run
`uv run python -m pipelines.cyber.reference` to see every fault in every row at once. The files are
`data/reference/cyber/{private_directory,private_metrics,events}.json`, each `{"track", "kind", "updated", "note",
"rows": [...], "gaps": []}`.

## What goes in, and what does not

- **Public sources only.** A company announcement, a regulatory filing (SEC EDGAR: 8-K, 10-Q, 10-K, 20-F, S-1,
  424B4, Form D; the UK, EU, Israeli and Chinese equivalents), or a named public page of a news outlet that anyone can
  open without a subscription.
- **Never** PitchBook, Crunchbase, CB Insights, Similarweb, Tracxn, Dealroom, Caplight, Preqin, Bloomberg (terminal or
  site), Owler, ZoomInfo, 企查查, 天眼查, IT桔子, nor any employer or internal document. `source_url` on any of those hosts
  is rejected by the validator.
- **A company with no public source is not in the repository, not even its name.** There is no "name only" row.
- A fact you could only see in a search-engine snippet is tag `S`; it is not charted and not used in a KPI. Open the
  page. If you cannot open it, drop the row.
- Dates come from the page text (the dateline or the body), never from a search index, an RSS field or a page's
  metadata. 36Kr and 36氪 index dates are often the re-index date; read the dateline in the article.

## Tags

`V` read on the primary page today; `S` seen only in a search extract; `C` computed from `V` inputs (say how in
`curator_notes`); `U` unverified. Only `V` and `C` rows may have `chartable: true`.

## Row fields

Every row, every kind: `id`, `company`, `layer` (one of the twelve codes in `pipelines/cyber/config.py`), `market`
(`overseas` or `china`), `source_name`, `source_url`, `publish_date` (YYYY-MM-DD, the date on the page),
`as_of_date` (a YYYY-MM-DD date the fact describes; for a fact about a period, the period's last day: 2025 is 2025-12-31, 2024-05 is 2024-05-31), `last_checked` (the day you opened the page), `tag`,
`chartable`, `caveat`, `curator_notes`.

`caveat` is **one reader-facing sentence** that travels with the number and says what the number is not: "Post-money
valuation of the preferred round as the company states it; not a fair value of common stock." Never a note to
yourself. `curator_notes` is for you (what you read and where). It is working text, not a citation: it never enters a mart, a fact or a page, and it is not committed. Curators return it with each row and the maintainer keeps it in the git-ignored sidecar `data/reference/cyber/notes/<kind>.json` (keyed by row id). The words
"todo", "tbd", "re-verify", "placeholder", "researcher" and similar anywhere else reject the file.

### `private_directory` (one row per company)

`company`, `name_cn` (China only), `layer`, `l2` (one sub-sector code from the layer's list), `market`, `country`
(ISO-2), `status` (`independent`, `pe_owned`, `pending_acquisition`, `acquired`), `website`, `summary` (one factual
sentence in the company's own terms, no adjectives), `founded` (year, optional). The provenance fields describe the
page the summary comes from (usually the company's own site or an announcement).

### `private_metrics` (one public fact per row; a company has 2 to 4 rows)

`metric` (`post_money_valuation`, `round_amount`, `arr`, `acquisition_price`, `tender_valuation`, ...), `period`
(`2025-06-30` or `FY2025`), `value` (a positive number in whole units), `unit` (`USD`, `CNY`, `EUR`), `qualifier`
(`=` `>` `<` `~`), `metric_kind`, `basis`, `status` (`current`, `superseded`, `disputed`), `definition` (what exactly
the figure measures), `company_confirmed` (true if the company or a filing states it), `vintage_year` and `stale_mark`
(valuation rows: the year of the round; true if a newer round exists or none has been reported for two years).

`metric_kind` (closed): `primary_round_valuation` (the valuation a company states for a priced round in its own announcement; the caveat says whether the release labels it pre- or post-money, and almost none does), `round_amount`,
`tender_valuation`, `arr_company_stated`, `acquisition_price_announced`, `acquisition_price_sec` (the amount a filing
records; the URL must be on sec.gov), `take_private_price`, `secondary_mark`, `reported_talks`,
`third_party_estimate`, `derived`. The last three of `secondary_mark`, `reported_talks`, `third_party_estimate` are
never charted.

`basis`: `disclosed` (the company or a filing states the number), `reported` (a publication says so; the caveat names
the publication and date and uses the word "reported"), `undisclosed` (only the round amount is known, there is no
valuation, and there is no valuation row).

A valuation a publication reported is drawn on the page, hollow, only when the curator marked the row `chartable`
(today one company, BigID). Reported talks, third-party estimates and secondary marks are never charted, whoever
reported them, and neither is a reported figure the curator did not mark.

`qualifier`: store "more than $4.8 billion" as `value 4.8e9, qualifier ">"`. Store "hundreds of millions of yuan" as a
`~` or `>` figure with the lower bound, and say so in the caveat.

**One valuation row per company in the headline slot**: the latest primary round that carries a valuation. Older rounds
may be stored with `status: superseded`. Do not infer a valuation from a round amount, from an ownership stake, or
from a secondary-market price.

### `events`

`event_kind` (`ipo`, `acquisition_announced`, `acquisition_closed`, `take_private`, `delisting`, `round`),
`event_date` (announcement date), `closed_date` (optional), `counterparty` (buyer or lead investor), `buyer_type`
(`platform_security`, `non_security_software`, `pe`, `insurance`, `state`, `strategic`, `other`, `none`; required for
acquisitions and take-privates), `value`/`unit`/`qualifier`/`metric_kind` when a price is stated, and `sec_value` when a
filing records a different amount than the press release (the acquirer's 10-Q fair-value-of-consideration line), plus
`status_after` (`independent`, `pe_owned`, `pending_acquisition`, `acquired`).

Announcement and closing are two events with their own dates and sources. A deal that is only announced is
`acquisition_announced`; do not write a closing date you have not read.

### Buyer type: what `platform_security` means

`buyer_type` is the type of the party the source names as the acquirer, decided by one rule and checked on that party's
latest annual filing or its own about page, not on a news description of it.

- `platform_security`: **security is the acquirer's main revenue line or its stated core business.** A company that
  sells security next to something larger is not one. Cisco is `strategic`: its fiscal 2026 annual report puts
  security at about 13% of revenue. Veeam (data resilience), Cribl (a telemetry engine for IT and security), Tailscale
  (networking) and Infoblox (core DNS, DHCP and IP address management, in its own words) are `non_security_software`.
  Akamai stays: security is its largest revenue line in its fiscal 2025 annual report. A10 Networks stays: its own
  results release says security-led solutions have reached two thirds of revenue. F5 stays on its own description, a
  multicloud application delivery and security provider, and Kiteworks and Spin.AI stay on theirs.
- `pe`: a financial sponsor, or a buyer vehicle owned by one. **A take-private by a sponsor-owned buyer is `pe`**
  whatever the vehicle sells: Varonis, bought by Proofpoint, a Thoma Bravo portfolio company that provides committed
  equity, is `pe`; Proofpoint buying Hornetsecurity, a security company making a purchase, is `platform_security`.
- `non_security_software`: a software, cloud or data company for which security is not the core. `strategic`: any
  other operating company (networking and hardware, industrial, financial, services). `insurance` and `state` are what
  they say.
- `other`: **a merger whose sources name no acquirer** (NetSPI and Synack, ThreatModeler and IriusRisk). `none`: a
  round, a listing or a delisting.

A row carries the buyer its own source names. When the announcement names one buyer and the completion another
(runZero and NetRise: announced as bought by Accenture, completed by Dragos), the rows keep their own types and the
deal takes the type of the row that opens it.

### Scope of the events file

A deal is recorded when the **target sells a security product**, in its own description. A security vendor buying a
company outside security (observability, networking, enterprise operations) is not a deal in security and is not drawn
or counted: the row is kept with `chartable: false` and `not_charted_code: outside_scope`, so it stays visible in the
table of rows that are not charted (Console, Galileo and Kentik). The deals are what announcements and roundups
surfaced, not every deal in the market.

### Why a row is not charted

Every row with `chartable: false` carries a `not_charted_code` from this closed list, and the pipeline fails a check for
a row that has none: `round_size_only` (the size of a round, no valuation or price), `amount_not_exact` (a floor or a
range such as "several hundred million yuan"), `mixed_amount` (new money mixed with a tender, a credit line, a stock
swap, or an instrument not labelled as equity), `no_figure_stated` (the event states no amount), `ip_asset_purchase`
(assets or IP bought, not the company), `combined_value` (one value for several companies or a stake),
`price_not_comparable` (a maximum, a net or a partial amount, or a sponsor deal rather than a priced round),
`basis_unclear` (a valuation whose pre- or post-money basis is not settled), `superseded_figure` (a later figure
replaced it), `too_old` (too old to set beside the charted figures) and `outside_scope` (see above). A figure that is
`reported` by a publication, or of a never-charted kind, gets its reason from those fields and needs no code. A figure
that both `private_metrics` and `events` hold (the same company, day, kind of figure, value and source) is listed once.

## Verification

A curator writes the rows and a second, independent agent opens the sources again. The verifier re-opens at least 40%
of the rows, and every valuation, ARR and acquisition-price row, and returns for each: `confirmed`, `corrected` (with
the corrected field and the page text that shows it), `unsupported` (the page does not say this) or `unreachable`.
Anything not confirmed or corrected is dropped.

## Traps this table has fallen into

- Pre-money against post-money. Press often reports a pre-money figure as "valued at".
- ARR is not revenue, and it is a company's own definition. Store it as `arr_company_stated` with its date.
- A press-release acquisition price and the amount in the buyer's filing differ (earn-outs, stock price moves, cash
  acquired). Store both; the chart prefers the filing.
- Valuations of 2021 and 2022 rounds that were never refreshed are stale; mark them.
- A "merger" announced by one party is not a deal until a definitive agreement or a closing filing exists. A merger
  whose sources name no acquirer is `buyer_type: other`.
- An amount that first appears in a filing written after completion is not an announced price. A deal with no price at
  signing has no announced price; its filing amount stands alone.
- A figure from a pending-deal note in a quarterly filing is provisional: the completed-deal note replaces it, and the
  provisional one is not stored as the filing price.
- Currency: store the currency the source states; never convert.
- Never copy a figure from a database's summary of a press release. Open the press release.
