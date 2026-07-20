# Talk2Dashboard NL

[English version](README_EN.md)

Talk2Dashboard is een voice-first onderzoeksdemo voor situaties waarin je snel overzicht nodig hebt over wat er buiten gebeurt. Je kunt in gewoon Nederlands vragen om live hulpverleningsmeldingen, weer, waterstanden, verkeer, luchtkwaliteit, spoorstoringen en nieuws rond één incident of locatie te combineren. De agent bouwt daar tijdens het gesprek kaarten, tijdreeksen, ranglijsten en operationele werkbeelden van, zonder de onderliggende meetwaarden te kunnen veranderen.

De focus ligt dus niet op nog een algemene chatbot, maar op **spraak naar een controleerbaar operationeel dashboard**: van een eerste signaal naar lokale context, afwijkingen en relevante voorzieningen. De agent gebruikt daarvoor zes begrensde tools en zeven publieke Nederlandse datastromen. Alleen de dashboardconfiguratie verandert; alle brondata blijft immutable en wordt door deterministische backendcode gefilterd, gekoppeld en berekend.

![Talk2Dashboard architectuur](docs/assets/architecture/talk2dashboard-architecture.png)

## Demo

De demo begint met een actueel werkbeeld. Je kunt daarna bijvoorbeeld zeggen:

> Focus op deze P2000-melding. Toon binnen tien kilometer ook wegincidenten, KNMI-metingen, luchtmeetpunten, waterstanden en spoorverstoringen. Zet alles op een 3D-kaart en vat alleen de belangrijkste afwijkingen samen.

De agent voert read-only queries uit, ontvangt opaque datahandles en mag daar panelen aan binden. Kaarten, ranglijsten, tijdreeksen, KPI's en feeds worden door een vaste registry gerenderd; de agent kan geen meetwaarde, timestamp, bron-ID, kleur of vrije HTML injecteren.

<!-- DEMO_VIDEO_PLACEHOLDER: voeg docs/assets/video/talk2dashboard-demo.mp4 toe zodra de productdemo is opgenomen. -->

**Demo-video:** wordt nog opgenomen.

## Waarom ik dit heb gebouwd

Ik ben de laatste tijd gefascineerd door [Gemma 4 op Cerebras](https://huggingface.co/blog/cerebras-gemma4-voice-ai) en de [snelle multimodale apps die Cerebras ermee bouwde](https://www.cerebras.ai/blog/first-look-gemma-4-on-cerebras-3-fast-multimodal-apps-we-built). Bij modellen gaat het vaak over kwaliteit en kosten. Snelheid voelt een beetje als het ondergeschoven derde punt van die driehoek, terwijl juist die in echte toepassingen ontzettend belangrijk is. En in een agent- of multi-agentflow stapelt iedere seconde wachten zich gewoon op.

Toen zag ik de [Hugging Voice-demo](https://huggingface.co/spaces/HuggingFaceM4/hugging-voice#1-one-line-migration). Daaruit kwam eigenlijk een simpele gedachte: wat als je dit gebruikt op momenten waarop je heel snel een goed dashboard nodig hebt? We zijn inmiddels gewend om voor zo'n analyse rustig een half uur op Claude Code, Cowork of Codex te wachten. Maar soms wil je tijdens het gesprek al iets bruikbaars zien.

Toen moest ik denken aan het [incident bij de spuikokers van IJmuiden](https://open.rijkswaterstaat.nl/@275817/evaluatie-incident-spuikokers-spui/). Juist in zo'n crisissituatie wil je snel verschillende signalen bij elkaar kunnen zetten. Dat wordt natuurlijk pas echt waardevol als je er ook interne operationele data aan toevoegt.

Mijn verkennende [analyse van het incident](docs/IJMUIDEN_INCIDENT_ANALYSIS.md) maakt dat concreet. In de openbare tienminutenmetingen was Buitenhuizen om 05:30 zestien centimeter in twee uur gestegen, terwijl Noordersluis Oost tegelijk circa vijftien centimeter steeg en de buitenhaven ongeveer 102 centimeter hoger stond dan de kanaalzijde. Die samengestelde regel kwam in de gebruikte voorafgaande jaarbaseline nul keer voor. Met publicatievertraging als belangrijke kanttekening had dit dus plausibel een second-line waarschuwing kunnen geven voor of rond de eerste menselijke waarneming. Niet de diagnose: daarvoor heb je interne schuifposities, bedieningsstatus en SCADA-data nodig.

## Het centrale ontwerpbesluit

Het belangrijkste ontwerpbesluit is vrij simpel: de LLM mag niet aan de data zitten. Op de momenten dat het ertoe doet wil je betrouwbare bronnen, niet een model dat ongemerkt een waarde invult of een meetpunt verandert. Daarom is de scheiding hard:

- adapters halen brondata op en normaliseren die;
- snapshots en queryresultaten zijn immutable;
- de backend berekent filters, aggregaties, correlaties en afstanden;
- de agent krijgt alleen handles en configuratiegereedschap;
- de renderer accepteert uitsluitend gevalideerde paneltypen en bindings;
- websearch staat standaard uit en wordt altijd als onbevestigde externe context gelabeld.

Het dashboard kan dus niet onbeperkt genereren. De agent krijgt vooral configuratiegereedschap en geen vrijheid om de onderliggende data te modelleren. Daarom zit er ook een websearch-toggle in: externe context kan handig zijn, maar staat standaard uit en wordt nooit vermengd met de brondata.

Dat is eigenlijk wat ik met deze demo hoop te laten zien: voice kan meer zijn dan een chatbox boven een dashboard. Met snelle modellen en een paar goed begrensde tools kun je de interface tijdens het gesprek laten meebewegen, terwijl een normale backend verantwoordelijk blijft voor de feiten. Vooral bedoeld als inspiratie voor wat er met voice en snelle LLM's in real time kan, niet als claim dat dit al een productierijp crisisdashboard is.

## Proces

### 1. Nederlandse TTS kiezen

Dit deel was uiteindelijk redelijk simpel: Nederlandse uitspraak is nog steeds moeilijk voor TTS. Ik heb cloud- en lokale modellen vergeleken op eerste audio, totale generatietijd en vooral of ik er in het Nederlands prettig naar kon luisteren.

| Route | Gebruik in de benchmark | Observatie |
| --- | --- | --- |
| [ElevenLabs Flash v2.5](https://elevenlabs.io/docs/overview/models#eleven-flash-v25) | Streaming endpoint | De beste latency-kwaliteitbalans in mijn test. Flash levert wat kwaliteit en expressiviteit in, maar de eerste audio is echt snel. |
| [Speechify Simba Multilingual](https://docs.speechify.ai/tts/text-to-speech/get-started/models) | Streaming endpoint | Nederlandse output was bruikbaar, maar de eerste audio kwam in mijn runs duidelijk later. |
| [Google Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation) | Streaming met `stream: true` | Veel controle en prima kwaliteit, maar in deze kleine test wisselender en trager. |
| [Voxtral / Mistral](https://docs.mistral.ai/studio-api/audio/text_to_speech) | In mijn app niet-streamend aangesloten | De API biedt wel streaming, Nederlands en voice cloning, maar geen Nederlandse standaardstem die mij overtuigde. |
| [OmniVoice](https://github.com/k2-fsa/OmniVoice) | Lokale server; pas playback na volledige generatie | Een van de weinige lokale modellen met Nederlands die gewoon op mijn 16 GB M4 MacBook paste. |

![Nederlandse TTS-benchmark met Speechify, Google, ElevenLabs, Voxtral en OmniVoice](docs/assets/media/tts-benchmark-overview.png)

*De TTS-proef maakte vooral het verschil in time-to-first-audio zichtbaar. Dit is een momentopname uit mijn lokale benchmark, geen onafhankelijke providerbenchmark.*

Ik heb lokaal onder meer gekeken naar [OmniVoice](https://github.com/k2-fsa/OmniVoice), [Higgs TTS 3 4B](https://huggingface.co/bosonai/higgs-tts-3-4b), [Voxtral Mini 4B](https://docs.mistral.ai/studio-api/audio/text_to_speech) en [Fish Audio S2 Pro](https://huggingface.co/fishaudio/s2-pro). Veel modellen vielen op een 16 GB MacBook al snel af. OmniVoice was een van de weinige modellen die én paste én Nederlands ondersteunde. Daar moest wel een eigen warme lokale wrapper omheen en de audio kwam pas na de volledige generatie terug. Niet snel genoeg voor deze route, maar wel best vet dat dit lokaal draait en de kwaliteit niet om te janken is.

Voxtral was een apart geval. Mistral ondersteunt Nederlands als taal en de TTS-API kan streamen, maar er zat geen Nederlandse standaardstem tussen die voor deze test werkte. Ik kwam er later achter dat de interessante route juist [voice cloning](https://docs.mistral.ai/studio-api/audio/text_to_speech) is. Dat wil ik nog eens testen met mijn eigen stem of een goede Nederlandse referentiestem, want de latency lijkt wel veelbelovend.

ElevenLabs won uiteindelijk vrij overtuigend. Hun stemmen vind ik sowieso ongekend goed en Flash v2.5 leverde in mijn meting veruit het snelst bruikbare audio. Natuurlijk lever je ten opzichte van een zwaarder kwaliteitsmodel iets in, maar voor een gesprek als dit weegt die latency heel zwaar.

[Beluister de Nederlandse OmniVoice-sample (MP3)](docs/assets/audio/omnivoice-dutch-sample.mp3)

### 2. De voice-agentroute kiezen

Daarna vergeleek ik vier routes met dezelfde Nederlandse opdrachten, dashboardtools en replay-audio:

1. lokale Parakeet V3 STT, Gemma 4 via Cerebras en ElevenLabs Flash v2.5;
2. OpenAI Realtime;
3. ElevenLabs Agents;
4. Google Live.

![Realtime replaybenchmark van vier Nederlandse voice-agentroutes](docs/assets/media/voice-agent-benchmark-suite.png)

*De replay-suite stuurde dezelfde opnames naar alle vier routes en legde transcript-, tool-, audio- en turnlatency vast.*

OpenAI Realtime en Google Live waren in deze vergelijking de native live speech-to-speechmodellen. De Cerebras-route was heel expliciet een cascade: lokale Parakeet V3 voor STT, Gemma 4 op Cerebras als LLM en ElevenLabs Flash voor TTS. ElevenLabs Agents is onder de motorkap óók STT → LLM → TTS, met daarnaast een eigen turn-takingmodel en de hele agent-, tool- en monitoringlaag eromheen. Dat staat ook zo in de [ElevenLabs-architectuur](https://elevenlabs.io/docs/eleven-agents/overview).

Parakeet was het snelste en beste lokale Nederlandse transcriptiemodel dat ik kende, maar uiteindelijk nog niet goed genoeg voor alle adressen, plaatsnamen en operationele afkortingen. OpenAI voelde in mijn Nederlandse gesprekken nog te nep en viel daardoor vrij snel af. Ik kan wel niet wachten om deze use case opnieuw te testen met [GPT-Live](https://openai.com/index/introducing-gpt-live/). Google Live vond ik heel veelbelovend, maar de preview-API en vooral de toolcyclus waren soms nog flaky.

ElevenLabs had naast de stem ook een praktisch voordeel: een volwassen dashboard, veel instellingen, goede gespreksmonitoring en een platform dat de hele cascade al op lage latency probeert te optimaliseren. Grappig genoeg kwam ik daarmee weer uit bij STT → LLM → TTS. Native voice-to-voice was voor mijn Nederlandse use case nog niet automatisch de beste ervaring.

Daarna heb ik binnen ElevenLabs nog modellen vergeleken. Ik begon met Gemma 4 via Cerebras, maar iedere externe LLM-hop kost opnieuw latency. Daarom ben ik uiteindelijk overgestapt op de door ElevenLabs gehoste, non-reasoning `Qwen3.6-35B-A3B`. Geen grote wetenschappelijke test, maar de toolcalling was goed genoeg en time-to-first-sentence was het laagst. Voor korte gesproken antwoorden vind ik TTFT/TTFS belangrijker dan een indrukwekkende maximale tokens-per-seconde-score.

![Time to first sentence in de ElevenLabs-modeltest](docs/assets/media/elevenlabs-time-to-first-sentence.jpeg)

*In deze kleine modelproef waren blauw Gemma 4 via Cerebras, geel Qwen 35B en paars Qwen 397B. Qwen 35B bood hier de beste combinatie van eerste-zinlatency en bruikbare toolcalling.*

Zie voor bredere modelmetingen ook de [Artificial Analysis-vergelijking](https://artificialanalysis.ai/models/gemini-3-pro?models=qwen3-6-35b-a3b%2Cqwen3-6-35b-a3b-non-reasoning%2Cqwen3-5-397b-a17b%2Cqwen3-5-397b-a17b-non-reasoning%2Cgemma-4-31b-non-reasoning%2Cgemma-4-31b).

![Voice-agentbenchmark met live events, replay en een gedeeld Vizro-dashboard](docs/assets/media/voice-agent-operational-console.png)

*Iedere route kreeg dezelfde dashboardstate en tools, zodat naast spraakkwaliteit ook de uitgevoerde aanpassing zichtbaar bleef.*

![ElevenLabs Spotlight met gesprekken, topics en operationele agentmetrics](docs/assets/media/elevenlabs-agent-spotlight.png)

*De platformlaag gaf daarnaast inzicht in gesprekken, topics, latency en mislukte toolcalls.*

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

## Inspiratie en credits

- [Reson8](https://console.reson8.dev/custom-models), een interessant Nederlands voicebedrijf waarvan ik de custom-modelroute graag verder wil testen.
- [Murmel](https://the-ai-factory.com/insights/murmel-dutch-speech-to-text), een interessante Nederlandse speech-to-textontwikkeling van The AI Factory om in de gaten te houden.
- [Hex](https://hex.tech/), dat analytics en dashboarding de afgelopen tijd veel productiever en leuker heeft gemaakt. Ik ben benieuwd wat hun time-to-dashboard zou worden met modellen zoals Gemma 4 op Cerebras.
- [Thinking Machines: Interaction Models](https://thinkingmachines.ai/blog/interaction-models/), een sterk perspectief op interfaces die zich rond gebruikersintentie vormen.
- [World Monitor](https://www.worldmonitor.app/dashboard), een indrukwekkend openbaar informatieproject dat qua schaal en afwerking veel verder gaat dan deze demo.
- [Hugging Face speech-to-speech](https://huggingface.co/blog/cerebras-gemma4-voice-ai) en [Cerebras](https://www.cerebras.ai/blog/first-look-gemma-4-on-cerebras-3-fast-multimodal-apps-we-built) voor de oorspronkelijke latency-inspiratie.

## Beperkingen en toekomstwerk

- De productdemo-MP4 moet nog worden opgenomen.
- Parallelle toolcalls door de ElevenLabs-agent zelf zijn op dit moment niet gedocumenteerd en zitten ook niet in deze demo. De [client-toolflow](https://elevenlabs.io/docs/eleven-agents/customization/tools/client-tools) wacht bij een blocking tool op het resultaat. Binnen onze ene `data_batch`-call draaien onafhankelijke queries wel parallel, maar dat is batching achter de tool en dus iets anders. Echte parallelle toolcalls blijven een interessant opvolgpunt.
- Toolcalls terwijl de agent al praat, en uiteindelijk een model dat echt tegelijk kan luisteren, spreken en handelen.
- Beter nadenken over hoeveel tools je een agent geeft. Zes brede tools werkt voor deze demo, maar dit schaalt niet vanzelf netjes door. Een multi-agentopzet of slimmere capability router is een logisch vervolg.
- De UX/UI en de panelen mogen nog veel mooier. Vizro was snel om dit te bouwen, maar voor een volgende versie zou ik waarschijnlijk verder richting een custom frontend gaan.
- Meer en vooral interne bronnen. Met alleen maar meer openbare feeds schuift dit al snel richting iets als [World Monitor](https://www.worldmonitor.app/dashboard); met interne data wordt het pas echt een gespecialiseerde operationele toepassing.
- Automatisch starten bij specifieke nieuwsberichten, P2000-meldingen of waterdrempels. Dat zou bij een scenario zoals IJmuiden interessant zijn, maar vraagt natuurlijk om goede drempels en false-positivecontrole. Zie de [IJmuiden-analyse](docs/IJMUIDEN_INCIDENT_ANALYSIS.md).
- Nederlandse STT blijft lastig bij adressen, afkortingen en plaatsnamen. Ook Voxtral met een goede Nederlandse voice clone wil ik nog testen.
- En uiteindelijk nog een serieuze productieronde voor beveiliging, privacy, broncontracten en beheer. Dit blijft nu bewust een lokale onderzoeksdemo.

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

Raadpleeg `.env.example` voor alle configuratieopties.

</details>

Codex was the Agentic engineering driver
