# Portfolio demo: van landelijk beeld naar lokale actie

Deze demo laat in een gesprek zien dat Talk2Dashboard live brondata kan lezen,
een Vizro-dashboard kan hercomponeren en externe context strikt gescheiden houdt
van operationele feiten. Gebruik tekst of spraak; de toolroute is hetzelfde.

## Voorbereiding

- Open `http://127.0.0.1:8002/` en start een nieuwe agentsessie.
- Controleer dat de bronbalk live of stale status toont en niet stilzwijgend naar
  fixtures is overgeschakeld.
- Schakel websearch bewust in. De agent mag dit beleid niet zelf wijzigen.
- Gebruik een gesprek. Herhaal een mislukte stap niet automatisch.

## Scenario

### 1. Orientatie

> Welke zeven databronnen en zes mogelijkheden heb je beschikbaar, en wat is de
> actuele status van iedere bron? Geef alleen een korte samenvatting.

Verwacht: `inspect_workspace`, omdat actuele status niet uit de statische
catalogus mag worden afgeleid. De agent noemt de zeven vaste streams en maakt
geen dashboardwijziging.

### 2. Landelijk operationeel beeld

> Bouw nu een landelijk operationeel beeld met actuele P2000-signalen,
> NDW-wegmeldingen, NS-storingen, KNMI-windstoten,
> Rijkswaterstaat-waterstanden, Luchtmeetnet-NO2 en NOS-nieuwscontext. Houd
> nieuws apart van operationele bronnen, gebruik maximaal twaalf panelen en maak
> de kaart driedimensionaal.

Verwacht: een `data_batch` met alle zeven streams, gevolgd door een
`dashboard_batch`. Onafhankelijke reads draaien binnen de batch parallel. Het
dashboard toont maximaal twaalf panelen, met Google 3D als primaire kaart wanneer
beschikbaar. NOS blijft als context gelabeld; P2000 blijft een signaal en KNMI
toont een stale-waarschuwing wanneer de feed niet vers genoeg is.

### 3. Lokale handelingscontext

Selecteer eerst een concrete P2000- of NDW-marker via **Als focus gebruiken**.
De focuschip in de voice-overlay bevestigt welk immutable bronrecord actief is.

> Toon rond deze melding binnen vijfentwintig kilometer ook KNMI-metingen,
> Luchtmeetnetpunten, RWS-watermeetpunten en spoorstoringen. Zoek daarna binnen
> vijf kilometer ziekenhuizen, scholen, brandweerkazernes en politiebureaus,
> gesorteerd op afstand. Herschik het bestaande beeld met deze lokale
> kaartcontext en houd maximaal twaalf panelen.

Verwacht: bij broncoordinaten eerst een exacte origin-query en daarna vier
`query_nearby`-operaties met `radius_m=25000`. Heeft een P2000-record alleen een
adres of plaats, dan gebruiken die vier operaties direct dezelfde tijdelijke
`origin_text`; een landelijke query is geen fallback. Daarna gebruikt de agent
`nearby_places` met de geselecteerde locatie, `radius_m=5000`, de vier
toegestane place-types en `rank=distance`, gevolgd door `dashboard_batch`. De
agent gebruikt hiervoor geen websearch, inspecteert bekende schema's niet en
injecteert geen coordinaten.

### 4. Externe context

> Websearch staat aan. Zoek maximaal vijf recente publieke resultaten over de
> A16 bij Moerdijk en presenteer die duidelijk als onbevestigde externe context.

Verwacht: `external_search`, eventueel gevolgd door een `dashboard_batch` die
de `web_results`-handle uitsluitend zonder veldbindings aan `evidence` koppelt.
Het resultaat is `unverified_external`, blijft buiten de twaalf operationele
werkpanelen en wordt niet als operationele bevestiging gepresenteerd. Dat het
evidence-panel niet in de render-ack van het werkbeeld staat is dus verwacht;
de resultaten blijven in agentantwoord, audit en dashboardstate beschikbaar.

### 5. Visuele controle en bewijs

> Maak een screenshot en gestructureerde snapshot van het volledige dashboard
> en controleer kort of de belangrijkste bronstatus en onzekerheid leesbaar
> zijn.

Verwacht: `capture_dashboard` met `scope=full_dashboard` en structured state.
De capture hoort exact bij de gevraagde dashboardversie en mag de persistente
renderstatus niet veranderen.

## Wat deze demo bewijst

| Onderdeel | Bewijs in de demo |
| --- | --- |
| Zeven live bronnen | Een gezamenlijke `data_batch` met KNMI, RWS, Luchtmeetnet, NDW, P2000, NS en NOS |
| Begrensde agenttools | Alle zes publieke tools worden doelgericht gebruikt |
| Data-integriteit | Panelen binden aan server-issued handles; de agent injecteert geen waarden |
| Bronzekerheid | Stale, signaal- en externe-contextlabels blijven zichtbaar |
| Dynamische presentatie | Een atomaire Control Room-compositie met maximaal twaalf panelen |
| Lokale besluitvorming | Een geselecteerd bronrecord stuurt kruisbrononderzoek en begrensde Places-resultaten tot vijfentwintig kilometer |
| Auditability | Toolaudit, dashboardversie en screenshot/snapshot zijn reproduceerbaar |

## Slagingscriteria

- Alle vijf stappen eindigen zonder timeout of niet-afgehandelde toolcall.
- De eerste tool van stap twee is `data_batch`, niet `inspect_workspace`.
- Stap twee bevraagt alle zeven streams in een batch.
- Iedere dashboardwijziging gebruikt alleen ontvangen handles.
- De eindweergave heeft maximaal twaalf zichtbare panelen en geen pagina-overflow.
- De sessie wordt na de demo expliciet gestopt.

## Gevalideerde run

Op 15 juli 2026 is het scenario tegen de live ElevenLabs-agent uitgevoerd. Om
kosten te beperken zijn na reparaties alleen mislukte stappen opnieuw gedraaid.

| Stap | Toolresultaat | Gemeten toolduur |
| --- | --- | ---: |
| Orientatie | `inspect_workspace` geslaagd | 1,2 ms |
| Landelijk beeld | `data_batch` en `dashboard_batch` geslaagd | 1.280,7 ms + 416,8 ms |
| Lokale context | `nearby_places` en `dashboard_batch` geslaagd | 1.037,3 ms + 392,9 ms |
| Externe context, hertest | `external_search` en evidence-binding geslaagd | 1.318,8 ms + 262,9 ms |
| Capture | `capture_dashboard` geslaagd | 3.769,2 ms |

Alle zes publieke tools en alle zeven bronnen zijn in de route aangeroepen. De
capture meldde dat bronzekerheid niet in het werkbeeld zelf stond; die hoort in
de informatie- en provenance-drawer. Dit is een presentatiekeuze, geen gemiste
databinding.
