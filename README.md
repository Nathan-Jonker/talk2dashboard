# Talk2Dashboard NL

Talk2Dashboard is a local, voice-first operational dashboard over Dutch live public data. An ElevenLabs Agent can query, aggregate and visualize data, but it can never write or invent source values. Every visual panel binds to an immutable data handle and every dashboard configuration is stored append-only.

The repository is designed as a portfolio project: architecture, source provenance, tool calls, latency events and visual state are inspectable and exportable.

## Implemented

- ElevenLabs Agent through the official React SDK: WebRTC for voice and a private WebSocket session for text-only chat.
- Native ElevenLabs `Qwen3.6-35B-A3B`, Flash v2.5 and voice `SXBL9NbvTrjsJQYay2kT`.
- Six blocking, batch-oriented client tools: workspace inspection, data queries, dashboard mutation, Nearby Places, screenshot capture and optional web search.
- FastAPI control plane with SQLite, immutable snapshots, content-addressed assets, tool audit and monotone latency events.
- Vizro/Dash shell with a dynamic validated panel host. Live updates use SSE and never reload the page.
- Google Maps 2D and 3D as primary geo renderers, with an open-data Plotly fallback.
- Optional Cerebras Gemma 4 for initial presentation focus and screenshot QA only.
- Dutch speech formatting plus ElevenLabs `text_normalisation_type=elevenlabs`.
- Responsive RWS-inspired visual system and an in-app capabilities, policy, history and diagnostics drawer.

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
BRAVE_SEARCH_API_KEY=               # optional; websearch remains off by default
```

The browser Maps key is intentionally delivered to the browser. Restrict it in Google Cloud to the Maps JavaScript API and to `http://127.0.0.1:*/*` or the final deployment origin. Keep Places and Geocoding on separate server-only keys with API and quota restrictions.

### Configure ElevenLabs

The platform configuration is source-controlled through an idempotent sync script. It never prints the API key.

```bash
make agent-check   # read-only diff; non-zero means changes are needed
make agent-sync    # create/update tools and patch the existing agent
```

The sync enforces Dutch, an empty first message, Flash v2.5, the configured voice, Qwen, the ElevenLabs text normalizer, a finite duration, six blocking client tools and `end_call`. Publish the agent branch in ElevenLabs if the workspace requires an explicit publish step.

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
2. “Toon de windstoten van het afgelopen uur als lijngrafiek.”
3. “Bouw een incidentbeeld rond Moerdijk met P2000 en verkeersdata.”
4. “Welke ziekenhuizen en scholen liggen binnen vijf kilometer van dit incident?”
5. Enable web search in the info drawer, then ask: “Zoek publieke achtergrondinformatie en houd dit los van de operationele bronnen.”
6. “Maak een screenshot van het hele dashboard en controleer de layout.”
7. “Maak de laatste dashboardwijziging ongedaan.”

Fixed cases are available at `/api/evaluation/cases`. Metrics, tool audit and complete state can be downloaded from the info drawer or:

- `/api/export/metrics.csv`
- `/api/export/tools.csv`
- `/api/export/state.json`

## Safety model

- Source records and source bundles are immutable.
- The agent returns query handles and can mutate only a validated `DashboardSpec`.
- Unknown query fields, free expressions, SQL, regex and generated literal series are rejected.
- Dashboard batches are atomic and version-checked.
- Web search is disabled by default and only a user-owned endpoint can enable it.
- Nearby Places uses only a location in a trusted handle or `LocationRef`; radius is capped at five kilometres.
- Google Geocoding output is transient and is not persisted as canonical geometry.
- Browser sessions call `endSession` on Stop, timeout, page hide and component cleanup.
- Screenshots use a separate read-only headless browser and never inherit microphone or conversation secrets.

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
  |-- Places / Brave / Cerebras auxiliary integrations
  `-- SSE events -> Vizro/Dash panel host -> Google Maps / Plotly
```

Logical panel bindings store a canonical query, not a concrete value. On a new source bundle, visible bindings resolve to new handles at render time. Dashboard version and undo history remain unchanged.

## Quality checks

```bash
make smoke       # actual keyless public feeds
make quality     # Ruff, Pyright, pytest, ESLint, Vitest, TypeScript/Vite
```

Automated fixture-browser QA covers 320, 375, 414, 768 and desktop widths, keyboard focus containment, no-overlap behavior, dynamic panel insertion without reload, disabled providers and screenshot capture. Live ElevenLabs voice, text-only transport, barge-in and Stop/tab-close cleanup remain manual checks because they require the configured external agent and microphone permission.

## Repository map

```text
src/talk2dashboard/
  api/             FastAPI routes and lifecycle
  integrations/    Google, Brave and Cerebras clients
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
