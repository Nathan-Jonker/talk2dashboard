# Talk2Dashboard NL

Talk2Dashboard is a local, voice-first operational dashboard over Dutch live public data. An ElevenLabs Agent can query, aggregate and visualize data, but it can never write or invent source values. Every visual panel binds to an immutable data handle and every dashboard configuration is stored append-only.

The repository is designed as a portfolio project: architecture, source provenance, tool calls, latency events and visual state are inspectable and exportable.

## Implemented

- ElevenLabs Agent through the official React SDK: WebRTC for voice and a private WebSocket session for text-only chat.
- Native ElevenLabs `Qwen3.6-35B-A3B`, Flash v2.5 and voice `SXBL9NbvTrjsJQYay2kT`.
- Six blocking, batch-oriented client tools: workspace inspection, data queries, dashboard mutation, Nearby Places, screenshot capture and optional web search.
- A compact static routing catalog gives the agent stable stream IDs, metrics and panel bindings up front. Concrete requests therefore start with `data_batch`, not schema discovery; live values and source health still come only from tools.
- FastAPI control plane with SQLite, immutable snapshots, content-addressed assets, tool audit and monotone latency events.
- Vizro/Dash shell with a dynamic validated panel host. Live updates use SSE and never reload the page. One to twelve visible panels are composed into a deterministic viewport-filling grid; provenance remains available in the information drawer.
- Google Maps 2D and 3D as primary geo renderers, with an open-data Plotly fallback.
- Optional Cerebras Gemma 4 for initial presentation focus and screenshot QA only.
- Dutch speech formatting plus ElevenLabs `text_normalisation_type=elevenlabs`.
- Responsive RWS-inspired visual system and an in-app capabilities, policy, history and diagnostics drawer. The drawer documents every public tool's inputs, outputs and limits, and every stream's fields, metrics, supported analyses and caveats from the same backend catalog used by contract tests.

## Live sources

| Stream | Route | Key |
|---|---|---|
| P2000 signal | 112Radar REST; Alarmeringen RSS fallback | optional / required for 112Radar |
| KNMI observations | KNMI Open Data ten-minute in-situ dataset | required |
| Rijkswaterstaat water | DDAPI20 WFS | none |
| NDW road incidents | `actueel_beeld.xml.gz`, DATEX II | none |
| Luchtmeetnet | public Open API | none |
| NS disruptions | Reisinformatie API v3 `disruptions` | required |
| NOS context | NOS general news RSS | none |

NOS, P2000 and external search are visibly lower trust than official measurements and operational feeds. One adapter failure does not block or clear other streams: the latest successful snapshot is carried forward and marked stale/degraded.

## Setup

Prerequisites: Python 3.12+, `uv`, Node.js 20+ and npm.

```bash
cd /Users/nathanjonker/Documents/Codex/2026-07-10/talk2dashboard
make install
cp .env.example .env
make capture-install
```

Add only the keys you use to `.env`; never put secrets in frontend files.

```text
ELEVENLABS_API_KEY=                 # full voice/text mode
ELEVENLABS_AGENT_ID=agent_3301kx2t5vbbfsbaezy059s1e8t8
CEREBRAS_API_KEY=                   # optional auxiliary planner/visual QA
KNMI_API_KEY=                       # KNMI Open Data
NS_API_SUBSCRIPTION_KEY=            # NS Reisinformatie API product
P2000_PROVIDER_API_KEY=             # optional 112Radar Developer plan
GOOGLE_MAPS_BROWSER_API_KEY=        # Maps JavaScript API; referrer restricted
GOOGLE_MAP_ID=                      # recommended for advanced markers/3D style
GOOGLE_PLACES_SERVER_API_KEY=       # Nearby Search; IP/API restricted
GOOGLE_GEOCODING_SERVER_API_KEY=    # optional transient resolver only
BRAVE_SEARCH_API_KEY=               # optional; otherwise DDGS, then Google News RSS
```

The browser Maps key is intentionally delivered to the browser. Restrict it in Google Cloud to the Maps JavaScript API and to `http://127.0.0.1:*/*` or the final deployment origin. Keep Places and Geocoding on separate server-only keys with API and quota restrictions.

### Configure ElevenLabs

The platform configuration is source-controlled through an idempotent sync script. It never prints the API key.

```bash
make agent-check   # read-only diff; non-zero means changes are needed
make agent-sync    # create/update tools and patch the existing agent
make agent-acceptance-test  # run only new, changed or previously failed agent cases
make agent-route-test       # backwards-compatible alias
```

The sync enforces Dutch, an empty first message, Flash v2.5, the configured voice, Qwen, the ElevenLabs text normalizer, a finite duration, six blocking client tools, disabled pre-tool narration and `end_call`. It also removes only the six explicitly known legacy benchmark tools (`set_dashboard_view`, `filter_incidents`, `highlight_region`, `get_dashboard_state`, `reset_dashboard`, `web_search`). Other workspace tools are untouched. Publish the agent branch in ElevenLabs if the workspace requires an explicit publish step.

The sync reads the selected LLM's `supports_parallel_tool_calls` capability and
only enables ElevenLabs platform-level parallel calls when that model supports
them. The currently selected `qwen36-35b-a3b` reports `false`, so its public
tool roundtrips remain ordered. Independent query operations inside one
`data_batch` still share one immutable source bundle and execute concurrently.
Alias-dependent operations, Places-to-dashboard dependencies and dashboard
writes remain ordered; dashboard writes are additionally protected by
optimistic versioning. Eligible external source feeds are fetched concurrently
and persisted together after all fetches settle. Visible dashboard bindings are
deduplicated by logical query and materialized concurrently during a refresh.

Cross-source proximity questions use `query_nearby` inside that same
`data_batch`. An event or measurement handle is the immutable origin when it
contains coordinates. For P2000 records that only contain an address or place,
`query_nearby` accepts `origin_text` and uses a fifteen-minute Google Geocoding
resolution without turning it into canonical source data. Any of P2000, NDW,
station-linked NS disruptions, KNMI, RWS water or Luchtmeetnet can be the target.
Distances are calculated locally with haversine and capped at twenty-five kilometres.
An unfiltered target query is never a valid fallback for a radius request;
Google Places remains limited to external facilities.

The acceptance suite creates temporary ElevenLabs tool-call tests and deletes
them after the run. Forty-two cases cover all seven sources, all six public tools,
weather and air-quality variants, multi-source batches, aggregation, baseline,
correlation, Places, websearch, dashboard metadata, focus and layout changes,
2D/3D map modes, undo, panel/full capture and the no-false-causality route. It
validates the generated stream, operation, metric, sorting, limits, radius,
place types and presentation operation rather than only checking the first tool
name. Known source requests must start with `data_batch`; a preceding
`inspect_workspace` is a failure.

Results are cached in ignored `artifacts/elevenlabs-agent-acceptance.json`.
Normal runs skip unchanged PASS cases and call ElevenLabs only for new, changed
or failed cases. Use `--case <name>` for one explicit case, `--list` without API
calls, and `--all` only when a deliberate full paid rerun is required:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/test_elevenlabs_agent_routing.py --list
UV_CACHE_DIR=.uv-cache uv run python scripts/test_elevenlabs_agent_routing.py --pending
UV_CACHE_DIR=.uv-cache uv run python scripts/test_elevenlabs_agent_routing.py --case nearby-healthcare-education
UV_CACHE_DIR=.uv-cache uv run python scripts/test_elevenlabs_agent_routing.py --all
```

`nearby_places` accepts a normal Dutch place or address as `origin_text` and
performs transient Geocoding plus Places lookup in one public toolcall. Its
result includes deterministic `distance_m` and a `nearest` record. Existing
`resolution_id` and trusted data handles remain supported. `external_search`
is never a fallback for Places and remains disabled unless the user enables it.
Requested facility categories use Google `includedPrimaryTypes`, so a hospital
request cannot match a place that only has hospital as a secondary type. The
server validates returned primary types again before creating the places handle.

For a single-conversation portfolio walkthrough that exercises all seven live
sources and all six public tools, use
[`docs/PORTFOLIO_DEMO.md`](docs/PORTFOLIO_DEMO.md). It includes the exact Dutch
prompts, expected tool routes and pass criteria without requiring a full paid
acceptance-suite rerun.
The public contract exposes a fixed type enum. If a model still mixes supported
and unsupported types, the supported subset is executed and the result includes
a `PLACE_TYPES_IGNORED` warning; a request with no supported type remains a
machine-readable `UNSUPPORTED_PLACE_TYPES` error.

A `correlation` panel can only bind to a real correlation handle. A single
weather series cannot be presented as evidence that weather caused incidents;
the assistant must report insufficient paired history instead.

The current `rws_water` adapter uses the DDAPI20 WFS layer
`locatiesmetlaatstewaarneming`. It therefore exposes one latest observation per
measurement location, not a historical series. Increasing a query window does
not synthesize history. Records whose reported "latest" timestamp is more than
twenty-four hours older than the newest result are discarded as inactive source
locations. The agent must use a KPI, ranking or map unless a separate historical
adapter is added.

Every data handle includes `panel_compatibility`. This profiles numeric values,
distinct labels, coordinate coverage, metrics, units and points per
station-metric series. Dashboard mutations reject unsuitable combinations: a
cross-station snapshot cannot become a time series, mixed metrics cannot become
one ranking, and a feed without coordinates cannot become an empty map. KNMI is
currently a latest ten-minute station snapshot. Luchtmeetnet station details are
resolved and cached so air measurements carry names and coordinates; its short
latest window only supports a time series after filtering to one metric and no
more than eight station series.

### Start

```bash
make dev
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The first load uses a deterministic synthetic Moerdijk fixture until a complete live bundle has been ingested. Fixture records are always labelled synthetic.

For a credential-free, network-free UI and tool test, use a separate temporary database:

```bash
make fixture
```

Open [http://127.0.0.1:8001](http://127.0.0.1:8001). This route deliberately ignores `.env`, disables live refresh and Cerebras planning, and never modifies the normal application database.

## Test conversation

1. “Welke bronnen zijn actueel en welke lopen achter?”
2. “Maak een ranglijst van de tien hoogste actuele windstoten en onderbouw die met de bronactualiteit.”
3. “Bouw een incidentbeeld rond Moerdijk met P2000 en verkeersdata.”
4. Klik bij een kaartmarker of melding op **Als focus gebruiken** en vraag: “Welke weer-, lucht-, water-, verkeer- en spoorrecords liggen binnen vijfentwintig kilometer van dit punt?”
5. “Welke ziekenhuizen en scholen liggen binnen vijf kilometer van deze focus?”
6. Enable web search in the info drawer, then ask: “Zoek publieke achtergrondinformatie en houd dit los van de operationele bronnen.”
7. “Maak een screenshot van het hele dashboard en controleer de layout.”
8. “Maak de laatste dashboardwijziging ongedaan.”

The operator selection is ephemeral browser context, not source data or a dashboard value. The selected feed row and map marker remain highlighted, while the voice dock shows the source, exact record ID, title and description in a removable chip. A record with coordinates is also shown as a yellow, clickable focus marker above every current map layer; otherwise the chip explicitly reports that the source record has no map location. The dock sends this identity to the active ElevenLabs conversation through a silent contextual update. Follow-up words such as “dit”, “hier” and “deze melding” therefore resolve to the selected immutable `source_ref` without an `inspect_workspace` roundtrip. Dash rerenders reapply the same visual selection instead of silently losing it.

Fixed cases are available at `/api/evaluation/cases`. Metrics, tool audit and complete state can be downloaded from the info drawer or:

- `/api/export/metrics.csv`
- `/api/export/tools.csv`
- `/api/export/state.json`

The operator-facing capability contracts are also machine-readable:

- `/api/agent-tools` for the six public tools, including inputs, outputs, examples and constraints.
- `/api/source-catalog` for the seven fixed streams, including fields, metrics, supported views and limitations.
- `/api/streams` for the current live status, record count, provider and freshness of those streams.

## Safety model

- Source records and source bundles are immutable.
- The agent returns query handles and can mutate only a validated `DashboardSpec`.
- Unknown query fields, free expressions, SQL, regex and generated literal series are rejected.
- Dashboard batches are atomic and version-checked.
- The browser injects the current dashboard version into `dashboard_batch` and retries one version conflict without an agent-side inspection round.
- Web search is disabled by default and only a user-owned endpoint can enable it.
- Nearby Places uses a trusted handle, `LocationRef` or temporary geocoded origin; radius is capped at twenty-five kilometres and results at fifteen.
- Google Geocoding output is stored only in a dedicated fifteen-minute policy store. It never becomes canonical geometry or a generic data handle; maintenance deletes expired resolutions.
- Browser sessions call `endSession` on Stop, Stop all, timeout, page hide and component cleanup. Stop all also aborts pending client tools and pauses source refresh until the user explicitly enables automatic refresh again.
- Dashboard configurations remain `pending` until the real browser confirms matching version, bundle, handles, panels, maps and paint. Screenshot browsers set only a local readiness marker and never mutate persisted render state.
- Agent numeric claims are audited after the final transcript against successful tool results from the same turn and labelled as an AI summary. The native ElevenLabs/Qwen pipeline does not permit hard pre-speech claim blocking in this version.
- Screenshots use a separate read-only headless browser and never inherit microphone or conversation secrets. A render timeout returns structured state plus a warning and no misleading success image.

This is a local single-user research demo, not a production crisis-management system. P2000/RSS may contain sensitive operational context; do not add personal-data enrichment or publish raw dispatch details.

## Architecture

```text
ElevenLabs React SDK (voice/text)
       | client tools, same origin
       v
FastAPI control plane
  |-- source adapters -> immutable snapshots/bundles -> SQLite/raw assets
  |-- query engine -> immutable handles
  |-- dashboard service -> append-only DashboardSpec versions
  |-- Places / search / Cerebras auxiliary integrations
  `-- SSE events -> Vizro/Dash panel host -> Google Maps / Plotly
```

Logical panel bindings store a canonical query, not a concrete value. On a new source bundle, visible bindings resolve to new handles at render time. Dashboard version and undo history remain unchanged.

Data panels can combine up to six independently refreshed source bindings when the sources belong in one coherent view. Maps, feeds, rankings, time series, comparisons, KPI groups, Places, evidence and summaries preserve a distinct source color, provenance and refresh status per binding. Correlation remains a single deterministic server result. Geo-handles render by default as an interactive Google 3D map; marker clicks expose a compact source-backed popover and link to the evidence drawer. A 2D Google map remains available on request and as the deterministic fallback when the 3D runtime is unavailable.

Record provenance lookups use a composite record index, targeted bundle lookup
and a bounded sixty-second server cache. The browser prefetches evidence on
pointer hover and reuses in-flight and recent requests, so opening individual
records does not block on repeated provenance scans.

The Cerebras startup planner is requested after the first real browser paint. A fresh system dashboard is composed immediately. After that, an automatic refresh-triggered redesign is allowed only when the latest persisted dashboard configuration is at least fifteen minutes old. This cooldown survives server restarts, and any saved agent, user, restore, or planner change resets it; live source refreshes do not. The settings drawer contains an explicit manual redesign action that bypasses the cooldown. The planner selects a focus from current source health, executes fixed read-only query recipes through `data_batch`, and atomically replaces the visible workspace through `dashboard_batch`. A later user request uses `replace_visible` for a new focus, `merge` only for an explicit addition, and otherwise `adaptive`, which keeps the workspace at no more than twelve visible panels.

## Quality checks

```bash
make smoke       # actual keyless public feeds
make quality     # Ruff, Pyright, pytest, ESLint, Vitest, TypeScript/Vite and generated asset diff
```

Automated fixture-browser QA covers 320, 375, 414, 768 and desktop widths, keyboard focus containment, no-overlap behavior, dynamic panel insertion without reload, disabled providers and screenshot capture. Live ElevenLabs voice, text-only transport, barge-in and Stop/tab-close cleanup remain manual checks because they require the configured external agent and microphone permission.

Fixture mode additionally exposes `POST /api/evaluation/fixtures/select` and `POST /api/evaluation/fixtures/control`. Controls support `normal`, `fail`, `empty` and `stale` per stream and are rejected outside fixture-only mode. Source status distinguishes `healthy`, `stale`, `fixture`, `degraded`, `offline` and `disabled`; the UI counts available sources instead of treating fixtures as healthy.

The local database compatibility-column shim is retained only for pre-release databases and is deprecated. Remove it after the first fixed schema release; Alembic migration `0002` is authoritative for new hardening storage.

## Repository map

```text
src/talk2dashboard/
  api/             FastAPI routes and lifecycle
  integrations/    Google, Brave/DDGS and Cerebras clients
  renderer/        Vizro/Dash shell and browser assets
  sources/         adapters, normalization and bundle service
  storage/         SQLAlchemy models and content-addressed assets
  tools/           six public agent tool contracts and executor
  dashboard.py     append-only dashboard state
  query.py         bounded deterministic query engine
voice_dock/        React/ElevenLabs conversation shell
scripts/           agent sync and live source smoke checks
tests/             unit, contract and integration tests
data/evaluation/   fixed Dutch evaluation cases
```

The complete implementation contract is in [TECH_SPEC.md](./TECH_SPEC.md).

## Upstream documentation

- [ElevenLabs React SDK](https://elevenlabs.io/docs/eleven-agents/libraries/react)
- [ElevenLabs agent update API](https://elevenlabs.io/docs/eleven-agents/api-reference/agents/update)
- [ElevenLabs normalization](https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide)
- [Google Maps JavaScript loading](https://developers.google.com/maps/documentation/javascript/load-maps-js-api)
- [Google Maps 3D reference](https://developers.google.com/maps/documentation/javascript/reference/3d-map)
- [NDW incident data](https://docs.ndw.nu/en/faq/incidenten/)
- [KNMI Open Data API](https://developer.dataplatform.knmi.nl/open-data-api)
- [Luchtmeetnet Open API](https://api-docs.luchtmeetnet.nl/)
