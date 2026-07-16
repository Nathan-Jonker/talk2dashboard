# Talk2Dashboard NL

[English version](README_EN.md)

Talk2Dashboard is een voice-first onderzoeksdemo waarmee je in gewoon Nederlands een live operationeel dashboard bevraagt en herstructureert. De agent leest zeven publieke Nederlandse datastromen, voert begrensde tools uit en past alleen de dashboardconfiguratie aan. De meetwaarden zelf blijven immutable en worden uitsluitend door deterministische backendcode verwerkt.

![Talk2Dashboard architectuur](docs/assets/architecture/talk2dashboard-architecture.png)

> [Bewerkbare Excalidraw-bron](docs/assets/architecture/talk2dashboard-architecture.excalidraw)

## Demo

De demo begint met een actueel werkbeeld. Je kunt daarna bijvoorbeeld zeggen:

> Focus op deze P2000-melding. Toon binnen tien kilometer ook wegincidenten, KNMI-metingen, luchtmeetpunten, waterstanden en spoorverstoringen. Zet alles op een 3D-kaart en vat alleen de belangrijkste afwijkingen samen.

De agent voert read-only queries uit, ontvangt opaque datahandles en mag daar panelen aan binden. Kaarten, ranglijsten, tijdreeksen, KPI's en feeds worden door een vaste registry gerenderd; de agent kan geen meetwaarde, timestamp, bron-ID, kleur of vrije HTML injecteren.

<!-- DEMO_VIDEO_PLACEHOLDER: voeg docs/assets/video/talk2dashboard-demo.mp4 toe zodra de productdemo is opgenomen. -->

**Demo-video:** wordt nog opgenomen. Het gevalideerde gespreksscenario staat in [docs/PORTFOLIO_DEMO.md](docs/PORTFOLIO_DEMO.md).

## Waarom ik dit heb gebouwd

Ik ben gefascineerd door snelle taalmodellen, in het bijzonder [Gemma 4 op Cerebras](https://huggingface.co/blog/cerebras-gemma4-voice-ai) en de [multimodale Cerebras-demo's](https://www.cerebras.ai/blog/first-look-gemma-4-on-cerebras-3-fast-multimodal-apps-we-built). Bij modelkeuze gaat veel aandacht naar kwaliteit en kosten, maar snelheid is een derde hoek van hetzelfde Pareto-front. In interactieve toepassingen telt niet alleen hoeveel tokens een model produceert, maar vooral hoe snel de eerste bruikbare zin, toolcall en schermupdate verschijnen. Die wachttijd stapelt bovendien op in agent- en multi-agentworkflows.

De [Hugging Voice-demo](https://huggingface.co/spaces/HuggingFaceM4/hugging-voice#1-one-line-migration) bracht mij op een simpele vraag: wat als je niet een half uur op een analyse- of dashboardworkflow wacht, maar tijdens een gesprek direct een bruikbaar werkbeeld opbouwt?

Het [incident bij de spuikokers van IJmuiden](https://open.rijkswaterstaat.nl/@275817/evaluatie-incident-spuikokers-spui/) maakte die use case concreet. In crisissituaties is een snel, bronvast overzicht waardevol, zeker wanneer publieke signalen later met interne operationele data worden gecombineerd. Mijn verkennende reconstructie staat in [Analyse incident spuikokers IJmuiden](docs/IJMUIDEN_INCIDENT_ANALYSIS.md). De conclusie is bewust beperkt: publieke meetreeksen hadden mogelijk eerder een afwijkend patroon zichtbaar kunnen maken, maar waren niet voldoende voor diagnose of preventie.

## Het centrale ontwerpbesluit

Op momenten waarop informatie ertoe doet, wil je voorkomen dat een taalmodel zelf data gaat herschrijven of aanvullen. Daarom is de scheiding hard:

- adapters halen brondata op en normaliseren die;
- snapshots en queryresultaten zijn immutable;
- de backend berekent filters, aggregaties, correlaties en afstanden;
- de agent krijgt alleen handles en configuratiegereedschap;
- de renderer accepteert uitsluitend gevalideerde paneltypen en bindings;
- websearch staat standaard uit en wordt altijd als onbevestigde externe context gelabeld.

Het dashboard is dus bewust begrensd. Dit is geen algemene websitegenerator en ook geen operationeel beslissysteem. De agent componeert een toegestane weergave van bestaande data.

## Proces

### 1. Nederlandse TTS kiezen

Nederlandse uitspraak blijft een lastige TTS-test. Ik vergeleek cloud- en lokale modellen op eerste audio, totale generatietijd en subjectieve verstaanbaarheid:

| Route | Gebruik in de benchmark | Observatie |
| --- | --- | --- |
| ElevenLabs Flash v2.5 | Streaming endpoint | Veruit de beste latency-kwaliteitbalans voor deze demo; Flash levert iets in op expressiviteit ten opzichte van zwaardere modellen. |
| Speechify | Streaming endpoint | Nederlandse output was bruikbaar, maar de eerste audio kwam in mijn runs later. |
| Google TTS | `stream: true` | Goede controle en kwaliteit, maar wisselender en trager in deze kleine test. |
| Voxtral / Mistral | Niet-streamend aangesloten in mijn benchmark | [Voxtral TTS ondersteunt wel streaming, Nederlands en voice cloning](https://docs.mistral.ai/studio-api/audio/text_to_speech); een goede Nederlandse clone heb ik nog niet getest. |
| OmniVoice | Warme lokale server, volledige generatie voor playback | Een van de weinige lokale opties die Nederlands ondersteunt en op mijn 16 GB Apple Silicon-machine paste. |

![Nederlandse TTS-benchmark met Speechify, Google, ElevenLabs, Voxtral en OmniVoice](docs/assets/media/tts-benchmark-overview.png)

*De TTS-proef maakte vooral het verschil in time-to-first-audio zichtbaar. Dit is een momentopname uit mijn lokale benchmark, geen onafhankelijke providerbenchmark.*

De lokale kandidaten waren [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice), [Higgs Audio V2 / Higgs TTS 3](https://huggingface.co/bosonai/higgs-tts-3-4b) en [Fish Audio S2 Pro](https://huggingface.co/fishaudio/s2-pro). OmniVoice was niet snel genoeg voor deze voice-agentroute, maar het is indrukwekkend dat een meertalig model lokaal op deze hardware bruikbare Nederlandse audio kan maken.

[Beluister de Nederlandse OmniVoice-sample (MP3)](docs/assets/audio/omnivoice-dutch-sample.mp3)

### 2. De voice-agentroute kiezen

Daarna vergeleek ik vier routes met dezelfde Nederlandse opdrachten, dashboardtools en replay-audio:

1. lokale Parakeet V3 STT, Gemma 4 via Cerebras en ElevenLabs Flash v2.5;
2. OpenAI Realtime;
3. ElevenLabs Agents;
4. Google Live.

![Realtime replaybenchmark van vier Nederlandse voice-agentroutes](docs/assets/media/voice-agent-benchmark-suite.png)

*De replay-suite stuurde dezelfde opnames naar alle vier routes en legde transcript-, tool-, audio- en turnlatency vast.*

OpenAI en Google waren native speech-to-speechroutes. De Cerebras-route was een cascade van STT, LLM en TTS. ElevenLabs Agents orkestreerde de gesprekspipeline en client tools als platformdienst. Parakeet was de snelste bruikbare lokale Nederlandse transcriptieroute die ik testte, maar de resterende transcriptiefouten waren relevant voor plaatsnamen en operationele termen.

![Voice-agentbenchmark met live events, replay en een gedeeld Vizro-dashboard](docs/assets/media/voice-agent-operational-console.png)

*Iedere route kreeg dezelfde dashboardstate en tools, zodat naast spraakkwaliteit ook de uitgevoerde aanpassing zichtbaar bleef.*

OpenAI voelde in deze test nog onnatuurlijk in het Nederlands. Google Live was veelbelovend, maar het previewmodel en de live toolcyclus waren soms instabiel. ElevenLabs combineerde goede stemmen met een volwassen agentdashboard, session lifecycle en uitgebreide instellingen. Uiteindelijk koos ik een ElevenLabs-native, non-reasoning `Qwen3.6-35B-A3B` in plaats van een externe Cerebras-call voor ieder antwoord: in mijn kleine test bleef toolcalling goed genoeg en was time-to-first-sentence het laagst.

Voor korte gesproken antwoorden is TTFT/TTFS belangrijker dan maximale tokens per seconde. De vergelijking was verkennend en geen statistisch modelonderzoek. Zie ook de bredere [Artificial Analysis-modelvergelijking](https://artificialanalysis.ai/models/gemini-3-pro?models=qwen3-6-35b-a3b%2Cqwen3-6-35b-a3b-non-reasoning%2Cqwen3-5-397b-a17b%2Cqwen3-5-397b-a17b-non-reasoning%2Cgemma-4-31b-non-reasoning%2Cgemma-4-31b).

![ElevenLabs Spotlight met gesprekken, topics en operationele agentmetrics](docs/assets/media/elevenlabs-agent-spotlight.png)

*De platformlaag gaf daarnaast inzicht in gesprekken, topics, latency en mislukte toolcalls.*

![Time to first sentence in de ElevenLabs-modeltest](docs/assets/media/elevenlabs-time-to-first-sentence.jpeg)

*In deze kleine modelproef waren blauw Gemma 4 via Cerebras, geel Qwen 35B en paars Qwen 397B. Qwen 35B bood hier de beste combinatie van eerste-zinlatency en bruikbare toolcalling.*

De aparte TTS- en voice-agentvergelijkers zijn niet opgenomen in deze repository, maar de code is op aanvraag beschikbaar.

### 3. Een dashboardrenderer kiezen

Ik probeerde Gradio, Tremor, Vizro en Taipy. Vizro won op snelheid van implementatie, een klein componentoppervlak en eenvoudige Plotly-integratie. Dat beperkte ook wat de agent moest begrijpen.

Die keuze heeft nadelen: de standaardcomponenten en responsieve layout zijn minder verfijnd dan een volledig custom frontend. Daarom gebruikt deze demo inmiddels een gecontroleerde Dash/Vizro panelhost, een eigen Control Room-stijl en een vaste panelregistry. Voor een volgende versie zou ik de renderer opnieuw evalueren.

### 4. Publieke data en tools begrenzen

De bronnen zijn bewust een praktische, enigszins willekeurige doorsnede van publieke Nederlandse operationele data. Ze bewijzen het patroon; met interne data wordt het pas echt organisatiespecifiek.

| Stream | Bron | Rol in de demo |
| --- | --- | --- |
| `p2000` | 112Radar REST, met Alarmeringen RSS als fallback | Hulpverleningssignalen; nooit zelfstandig als bevestigd incident behandelen. |
| `knmi_observations` | [KNMI Open Data](https://developer.dataplatform.knmi.nl/) | Weerwaarnemingen, waarschuwingen, wind en neerslag. |
| `rws_water` | [Rijkswaterstaat Waterdata](https://waterinfo.rws.nl/) | Waterstanden en meetpuntcontext. |
| `ndw_incidents` | [NDW DATEX II](https://opendata.ndw.nu/) | Wegincidenten, afsluitingen en verkeersmaatregelen. |
| `luchtmeetnet` | [Luchtmeetnet Open API](https://api-docs.luchtmeetnet.nl/) | LKI, fijnstof, stikstofdioxide en ozon per station. |
| `ns_disruptions` | [NS Reisinformatie API](https://apiportal.ns.nl/) | Actuele storingen en geplande werkzaamheden. |
| `nos_rss` | [NOS RSS](https://feeds.nos.nl/nosnieuwsalgemeen) | Nieuwscontext, niet operationele grondwaarheid. |

Google Maps 2D/3D, Geocoding en Places leveren alleen visualisatie en tijdelijke omgevingscontext. De [Google Maps 3D-weergave](https://mapsplatform.google.com/maps-products/3d-maps/) was een belangrijke visuele inspiratie.

De agent heeft zes brede tools:

| Tool | Wat hij mag doen |
| --- | --- |
| `inspect_workspace` | Bronnen, status, schema's, paneltypen en dashboardstate opvragen wanneer dat echt nodig is. |
| `data_batch` | Meerdere read-only queries, aggregaties, baselines, correlaties en radiusqueries in één call uitvoeren. |
| `dashboard_batch` | Panelen, layout, filters en metadata atomair aanpassen; nooit bronwaarden schrijven. |
| `nearby_places` | Maximaal toegestane Google Places rond een gevalideerde bronlocatie zoeken. |
| `capture_dashboard` | Een reproduceerbare screenshot en gestructureerde state van één dashboardversie maken. |
| `external_search` | Alleen na opt-in actuele webresultaten als onbevestigde context ophalen. |

Panelen zijn eveneens begrensd: feeds, events, KPI's, ranglijsten, tijdreeksen, vergelijkingen, correlaties, 2D/3D-kaarten, Places, bronstatus, evidence en een duidelijk gelabelde AI-samenvatting. Een panel bindt aan maximaal zes gevalideerde bronnen; het dashboard toont maximaal twaalf operationele panelen.

## Wat ik hoop dat dit laat zien

Voice is niet alleen een chatlaag boven een dashboard. Met snelle modellen en kleine, goed ontworpen tools kan een gesprek de interface zelf aanpassen terwijl de data-integriteit bij deterministische systemen blijft. Dit project is vooral bedoeld als inspiratie voor real-time analytics en crisiscontext, niet als claim dat dit al een productierijp overheidsdashboard is.

## Inspiratie en credits

- [Reson8](https://console.reson8.dev/custom-models), een interessant Nederlands voicebedrijf waarvan ik de custom-modelroute graag verder wil testen.
- [Hex](https://hex.tech/), dat analytics en dashboarding de afgelopen tijd veel productiever en leuker heeft gemaakt. Ik ben benieuwd wat hun time-to-dashboard zou worden met modellen zoals Gemma 4 op Cerebras.
- [Thinking Machines: Interaction Models](https://thinkingmachines.ai/blog/interaction-models/), een sterk perspectief op interfaces die zich rond gebruikersintentie vormen.
- [World Monitor](https://www.worldmonitor.app/dashboard), een indrukwekkend openbaar informatieproject dat qua schaal en afwerking veel verder gaat dan deze demo.
- [Hugging Face speech-to-speech](https://huggingface.co/blog/cerebras-gemma4-voice-ai) en [Cerebras](https://www.cerebras.ai/blog/first-look-gemma-4-on-cerebras-3-fast-multimodal-apps-we-built) voor de oorspronkelijke latency-inspiratie.

## Beperkingen en toekomstwerk

- De productdemo-MP4 moet nog worden opgenomen.
- Betrouwbare parallelle toolcalls zijn afhankelijk van het gekozen agentmodel; onafhankelijke operaties binnen `data_batch` draaien wel parallel.
- Toolcalls tijdens doorlopende spraak en echte full-duplex interactie verdienen een nieuwe test met [GPT-Live](https://openai.com/index/introducing-gpt-live/) en volgende generaties Gemini Live.
- Nederlandse STT blijft kwetsbaar voor adressen, afkortingen en plaatsnamen.
- De toolcatalogus is doelbewust klein, maar schaalt niet onbeperkt; een volgende versie kan specialistische subagents of een capability router gebruiken.
- De UI en panelrenderer kunnen verder richting een volledig custom frontend.
- Historie is lokaal opgebouwd en in v1 begrensd op twee dagen. Een verse installatie kan geen historie terugwerkend uitvinden.
- Publieke bronnen kennen vertraging, uitval, onvolledigheid en uiteenlopende trustniveaus. P2000 is geen algemene officiële overheidsfeed.
- Automatische triggers op nieuws, P2000 of waterdrempels zijn interessant toekomstwerk, maar vereisen zorgvuldige false-positiveanalyse. Zie de [IJmuiden-notitie](docs/IJMUIDEN_INCIDENT_ANALYSIS.md).
- Meer bronnen zouden de applicatie richting een systeem als World Monitor bewegen, maar vragen ook om strengere bronselectie en informatiearchitectuur.
- Productiegebruik vereist authenticatie, secretsbeheer, observability, rate limiting, privacybeoordeling, beheerprocessen en aansluiting op interne operationele systemen.

<details>
<summary><strong>De demo lokaal starten</strong></summary>

Dit project is gebouwd als lokale single-user onderzoeksdemo.

```bash
git clone https://github.com/Nathan-Jonker/talk2dashboard.git
cd talk2dashboard
make install
make capture-install
cp .env.example .env
```

Minimaal voor voice:

```dotenv
ELEVENLABS_API_KEY=
ELEVENLABS_AGENT_ID=agent_3301kx2t5vbbfsbaezy059s1e8t8
```

Optioneel voor kaarten en enrichment:

```dotenv
GOOGLE_MAPS_BROWSER_API_KEY=
GOOGLE_MAP_ID=
GOOGLE_PLACES_SERVER_API_KEY=
GOOGLE_GEOCODING_SERVER_API_KEY=
CEREBRAS_API_KEY=
```

Synchroniseer alleen wanneer je de ElevenLabs-agentconfiguratie wilt bijwerken:

```bash
make agent-check
make agent-sync
```

Start daarna de app:

```bash
make dev
```

Open `http://127.0.0.1:8002/`. Voor een keyless, deterministische testomgeving:

```bash
make fixture
```

Kwaliteitscontrole:

```bash
make smoke
make quality
```

Raadpleeg `.env.example`, `TECH_SPEC.md` en `docs/PORTFOLIO_DEMO.md` voor de volledige configuratie en het gevalideerde scenario.

</details>

Codex was the Agentic engineering driver
