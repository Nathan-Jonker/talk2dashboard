from __future__ import annotations

STREAM_IDS = (
    "knmi_observations",
    "rws_water",
    "luchtmeetnet",
    "ndw_incidents",
    "p2000",
    "ns_disruptions",
    "nos_rss",
)

MEASUREMENT_METRICS = (
    "wind_gust_kmh",
    "wind_speed_ms",
    "rainfall_rate_mm_h",
    "air_temperature_c",
    "water_level_cm",
    "bcwb_ug_m3",
    "c10h8_ug_m3",
    "c6h6_ug_m3",
    "c7h8_ug_m3",
    "c8h10_ug_m3",
    "co_ug_m3",
    "fn_ug_m3",
    "h2s_ug_m3",
    "no2_ug_m3",
    "no_ug_m3",
    "nox_ug_m3",
    "o3_ug_m3",
    "pm10_ug_m3",
    "pm25_ug_m3",
    "ps_ug_m3",
    "so2_ug_m3",
)

PANEL_TYPES = (
    "kpi",
    "timeseries",
    "ranking",
    "comparison",
    "incident_timeline",
    "event_table",
    "source_health",
    "evidence",
    "map_2d",
    "map_3d_google",
    "nearby_places",
    "correlation",
    "change_summary",
    "ai_brief",
)

NEARBY_PLACE_TYPES = (
    "hospital",
    "school",
    "university",
    "pharmacy",
    "police",
    "fire_station",
    "gas_station",
    "supermarket",
    "transit_station",
    "train_station",
    "bus_station",
)

PANEL_HANDLE_KINDS: dict[str, tuple[str, ...]] = {
    "kpi": ("aggregate", "baseline", "series"),
    "timeseries": ("series",),
    "ranking": ("series", "aggregate"),
    "comparison": ("aggregate", "baseline"),
    "incident_timeline": ("events", "incident"),
    "event_table": ("events",),
    "source_health": (),
    "evidence": ("events", "series", "incident", "web_results"),
    "map_2d": ("events", "series", "places", "incident"),
    "map_3d_google": ("events", "series", "places", "incident"),
    "nearby_places": ("places",),
    "correlation": ("correlation",),
    "change_summary": ("diff",),
    "ai_brief": ("events", "series", "incident"),
}

STREAM_ROUTING_CATALOG = """Vaste dataroutering (geen inspectie nodig):
- knmi_observations: metingen; wind_gust_kmh, wind_speed_ms, rainfall_rate_mm_h, air_temperature_c; locatie is meetstation.
- rws_water: actuele momentopnamen; water_level_cm; locatie is RWS-meetpunt. De WFS-feed levert alleen de laatste waarneming per meetpunt; een groter window maakt geen historische reeks.
- luchtmeetnet: metingen; bcwb_ug_m3, c10h8_ug_m3, c6h6_ug_m3, c7h8_ug_m3, c8h10_ug_m3, co_ug_m3, fn_ug_m3, h2s_ug_m3, no2_ug_m3, no_ug_m3, nox_ug_m3, o3_ug_m3, pm10_ug_m3, pm25_ug_m3, ps_ug_m3 en so2_ug_m3; locatie is luchtmeetstation.
- ndw_incidents: events; actuele wegincidenten, files, afsluitingen en maatregelen.
- p2000: events; hulpverleningssignalen. Behandel als signaal, niet als bevestigd incident.
- ns_disruptions: events; actuele spoorstoringen en werkzaamheden.
- nos_rss: events; nieuwscontext. Niet gebruiken als bevestigde operationele bron.
Metingen hebben metric, value, unit, observed_at, location en source_ref. Events hebben category, title, description, severity, status, observed_at, location, attributes en source_ref."""

DIRECT_ACTION_RECIPES = """Directe recepten:
- Operatorselectie: de browser kan stille context sturen met source_ref, stream, record_id en label. Koppel woorden als 'dit', 'hier', 'deze melding', 'dit meetpunt' en 'deze plek' direct aan dat exacte record. Query het origin-record op stream+record_id en ga zonder inspect_workspace door naar de gevraagde data_batch-operaties. Gebruik nooit een oudere selectie wanneer de context meldt dat deze is gewist.
- Ranglijst: data_batch query_measurements met bekende stream+metric, sort=value, order=desc, limit; daarna ranking-panel met label=location.label en y=value.
- Trend: query_measurements met stream+metric+window, sort=observed_at en order=asc; gebruik daarna alleen timeseries wanneer panel_compatibility dit toestaat. Twee globale timestamps zijn niet genoeg: minstens een station-metriekcombinatie moet twee meetmomenten bevatten. Filter bij meer dan acht reeksen eerst op locatie. Kies anders een ranglijst, kaart of geaggregeerde KPI.
- Meldingenlijst: query_events met streams/window/filter; daarna incident_timeline of event_table. Gebruik bindings=[...] wanneer onafhankelijke eventhandles samen in een feed horen.
- Kaart: query_events of query_measurements met locatie; gebruik standaard map_3d_google met latitude=location.latitude en longitude=location.longitude. Combineer meerdere geo-handles in een kaart via bindings=[...], zodat iedere bron een eigen kleur, legenda en herkomst houdt. Gebruik map_2d alleen als de gebruiker expliciet een platte kaart vraagt of Google 3D niet beschikbaar is.
- Bronnen rond een bronrecord: woorden als 'binnen vijfentwintig kilometer', 'in de buurt van deze melding/meting' of 'rond dit bronrecord' vereisen query_nearby voor IEDERE doelbron. Heeft het origin-record coordinaten, query het eerst met save_as=origin en gebruik origin_handle=@origin. Ontbreken coordinaten maar bevat de geselecteerde P2000-melding een adres of plaats, gebruik dan direct origin_text met titel plus omschrijving in query_nearby. Voorbeeld: {operation:query_nearby,stream:rws_water,origin_text:'Lupinestraat Dedemsvaart',radius_m:25000,save_as:water_nearby}. Gebruik nooit een gewone query_events/query_measurements als fallback: die is landelijk en dus niet ruimtelijk gefilterd. Dit werkt tussen P2000, NDW, NS, KNMI, RWS-water en Luchtmeetnet. nearby_places is uitsluitend voor voorzieningen. Ieder doelrecord moet distance_m bevatten en binnen radius_m vallen.
- Telling/vergelijking: aggregate met group_by stream_id/category/metric en fn count/mean/max/p95; gebruik kpi alleen voor één waarde per binding, comparison voor samen minstens twee waarden en ranking voor vergelijkbare numerieke groepen. Bind meerdere bronnen met bindings=[...] in hetzelfde coherente panel.
- Kaartgeschiktheid: gebruik map_2d of map_3d_google alleen wanneer panel_compatibility een kaart aanbeveelt. Records zonder coördinaten blijven beschikbaar als feed maar mogen niet tot een lege kaart leiden.
- Onderbouwing: gebruik preview, freshness en source_status uit hetzelfde data_batch-resultaat; inspecteer daarvoor niet apart.
- Externe webcontext: external_search retourneert een web_results-handle. Bind die uitsluitend aan het evidence-panel; gebruik nooit event_table, incident_timeline, kaart, ranglijst of grafiek. Webresultaten blijven onbevestigde context en horen niet als operationele bron in het werkbeeld.
- Voorzieningen bij een plaatsnaam: roep nearby_places direct aan met origin_text, included_types, radius_m en rank=distance. Toegestane typen zijn hospital, school, university, pharmacy, police, fire_station, gas_station, supermarket, transit_station, train_station en bus_station. Gebruik hiervoor geen data_batch of external_search.
- Een places-handle mag uitsluitend naar nearby_places, map_2d of map_3d_google. Bind places nooit aan kpi, ranking of timeseries. Benoem de dichtstbijzijnde afstand uit nearby_places.nearest mondeling of toon de resultaten als nearby_places-paneel.
- De places-handle bevat naast voorzieningen ook de gegeocodeerde oorsprong als is_origin=true. Kaarten tonen die als aparte focuslaag; nearby_places-panelen verbergen deze technische oorsprongsrij automatisch.
- Causaliteit/correlatie: roep bij een concrete vraag altijd eerst data_batch aan om de relevante reeksen en beschikbare koppeling te controleren. Claim nooit dat weer tot incidenten heeft geleid op basis van een actuele momentopname. Gebruik alleen een correlation-panel met een echte correlation-handle; meld anders dat gepaarde historische reeksen ontbreken."""

LAYOUT_RULES = """Presentatieregels:
- Een of twee gelijkwaardige feeds, ranglijsten of trends: gebruik span=standard; de renderer verdeelt ze automatisch gelijkmatig.
- Gebruik span=wide voor een hoofdvisualisatie met een kleiner ondersteunend panel en span=full alleen als de gebruiker expliciet om een schermbrede hoofdweergave vraagt.
- Eventfeeds gebruiken event_table met label=title en time=observed_at. Voeg nooit een leeg presentatiepanel toe.
- Webresultaten gebruiken uitsluitend evidence en hebben geen observed_at-binding. Het evidence-panel blijft buiten de twaalf zichtbare werkpanelen.
- Houd titels kort, concreet en Nederlands; hergebruik een bestaand panel_id wanneer dezelfde weergave wordt aangepast.
- Gebruik composition_mode=replace_visible bij een nieuwe focus, 'alleen dit' of een volledige herinrichting. Gebruik merge alleen wanneer de gebruiker expliciet 'voeg toe' of 'ook' zegt; gebruik anders adaptive.
- Houd maximaal twaalf zichtbare panelen aan. De renderer kiest zelf een beeldvullende 1-12-paneelindeling."""


# This catalog is deliberately UI-neutral. It is served by the capability API and
# keeps the operator documentation aligned with the contracts used by the agent.
TOOL_CAPABILITIES: dict[str, dict[str, object]] = {
    "inspect_workspace": {
        "inputs": [
            {
                "name": "sections",
                "type": "lijst",
                "required": True,
                "description": "streams, stream_schema, panel_types, dashboard, policies of incidents",
            },
            {
                "name": "ids",
                "type": "lijst",
                "required": False,
                "description": "Beperk full-detail tot een tot vijf expliciete IDs",
            },
            {
                "name": "detail",
                "type": "keuze",
                "required": False,
                "description": "ids, compact of full",
            },
        ],
        "outputs": [
            "Gevraagde metadata en schema's",
            "Huidige dashboardversie en panelen",
            "Bronstatus of incidentmetadata",
        ],
        "constraints": [
            "Read-only",
            "Niet nodig voor bekende streams en metrics",
            "Full-detail heeft een harde payloadlimiet",
        ],
        "examples": [
            "Welke paneltypes zijn beschikbaar?",
            "Laat de huidige dashboardconfiguratie zien.",
        ],
    },
    "data_batch": {
        "inputs": [
            {
                "name": "operations",
                "type": "lijst",
                "required": True,
                "description": "query_events, query_measurements, query_nearby, aggregate, baseline, correlate, get_incident, diff, answer_slice of resolve_location",
            },
            {
                "name": "stream / streams",
                "type": "bron-ID",
                "required": False,
                "description": "Een of meer van de zeven vaste streams",
            },
            {
                "name": "metric / metrics",
                "type": "metric-ID",
                "required": False,
                "description": "Meetwaarde zoals wind_gust_kmh, water_level_cm of no2_ug_m3",
            },
            {
                "name": "window",
                "type": "ISO-duur",
                "required": False,
                "description": "Bijvoorbeeld PT60M, PT24H of P14D",
            },
            {
                "name": "filters",
                "type": "lijst",
                "required": False,
                "description": "eq, in, gte, lte, between, contains of within_radius_handle",
            },
            {
                "name": "origin_handle / origin_text / origin_resolution_id / radius_m",
                "type": "ruimtelijke koppeling",
                "required": False,
                "description": "Filter een doelstream rond broncoordinaten of een tijdelijk gegeocodeerd adres; maximaal 25000 meter",
            },
            {
                "name": "sort / order / limit",
                "type": "selectie",
                "required": False,
                "description": "Sorteerveld, asc/desc en maximaal aantal rijen",
            },
            {
                "name": "group_by / fn",
                "type": "aggregatie",
                "required": False,
                "description": "Groepering plus count, sum, mean, min, max, median of p95",
            },
            {
                "name": "save_as",
                "type": "alias",
                "required": False,
                "description": "Verwijs binnen dezelfde batch naar een eerder resultaat",
            },
        ],
        "outputs": [
            "Een immutable handle per operatie",
            "Compacte preview met records of aggregaties",
            "Freshness, bronstatus, kwaliteit en provenance",
            "Panelcompatibiliteit op basis van labels, coördinaten, metriek, eenheid en punten per stationreeks",
            "Voor meetseries: distinct_timestamps, series_with_history, supports_timeseries en aanbevolen paneltypen",
            "Structured errors bij onvoldoende baseline of series",
            "query_nearby voegt distance_m, distance_origin_record_id en distance_origin_label toe",
        ],
        "constraints": [
            "Read-only: bronrecords worden nooit gewijzigd",
            "Onafhankelijke operaties worden parallel uitgevoerd",
            "Panelen binden aan handles, niet aan door de agent verzonnen waarden",
            "Bron-naar-bronafstand gebruikt lokale haversine; Google alleen voor vrije locatietekst",
        ],
        "examples": [
            "Maak een ranglijst van actuele windstoten.",
            "Vergelijk waterstanden per meetpunt.",
            "Toon actieve wegmeldingen van het laatste uur.",
            "Toon binnen vijfentwintig kilometer van deze P2000-melding ook lucht-, water-, weer- en spoorpunten.",
            "Selecteer een kaartpunt en vraag welke bronmetingen en verstoringen er rondom liggen.",
        ],
    },
    "dashboard_batch": {
        "inputs": [
            {
                "name": "operations",
                "type": "lijst",
                "required": True,
                "description": "set_meta, set_layout_template, upsert_panel, remove_panel, set_global_filter, set_focus, set_map_mode of undo",
            },
            {
                "name": "expected_version",
                "type": "integer",
                "required": False,
                "description": "Optimistische versiecontrole; de client vult deze automatisch aan",
            },
            {
                "name": "composition_mode",
                "type": "keuze",
                "required": False,
                "description": "adaptive, merge of replace_visible; vervang bij een nieuwe focus de zichtbare werkruimte",
            },
            {
                "name": "panel_id / panel_type",
                "type": "presentatie",
                "required": False,
                "description": "Stabiele ID en een geregistreerd Vizro-paneeltype",
            },
            {
                "name": "binding",
                "type": "handlebinding",
                "required": False,
                "description": "Handle plus expliciete veldkoppelingen voor het paneel",
            },
            {
                "name": "bindings",
                "type": "lijst met handlebindings",
                "required": False,
                "description": "Een tot zes handles in een gecombineerde kaart, feed, ranglijst, tijdreeks, vergelijking, KPI- of evidenceweergave, met eigen bronkleur en herkomst",
            },
            {
                "name": "span / position",
                "type": "layout",
                "required": False,
                "description": "compact, standard, wide of full en optionele positie",
            },
            {
                "name": "reason",
                "type": "tekst",
                "required": True,
                "description": "Korte auditreden voor de wijziging",
            },
        ],
        "outputs": [
            "Nieuwe append-only dashboardversie",
            "Lijst van aangepaste, toegevoegde of verwijderde panelen",
            "Automatisch verwijderde panel-IDs wanneer adaptive de limiet bewaakt",
            "Renderstatus en bronbundelversie",
        ],
        "constraints": [
            "Wijzigt alleen presentatie en filters",
            "Kan geen meetwaarden of bronrecords injecteren",
            "Een versieconflict wordt maximaal eenmaal herprobeerd",
            "Maximaal twaalf zichtbare panelen; verborgen provenance blijft via de infolade beschikbaar",
        ],
        "examples": [
            "Maak van de windgrafiek een ranglijst.",
            "Verwijder alle panelen die niet over IJmuiden gaan.",
            "Zet de kaart in 3D.",
        ],
    },
    "nearby_places": {
        "inputs": [
            {
                "name": "origin_text",
                "type": "plaatsnaam",
                "required": False,
                "description": "Bijvoorbeeld IJmuiden of Almere Centrum",
            },
            {
                "name": "origin_handle / location_ref / resolution_id",
                "type": "locatie",
                "required": False,
                "description": "Een vertrouwde of tijdelijk opgeloste locatie",
            },
            {
                "name": "included_types",
                "type": "lijst",
                "required": True,
                "description": "Ziekenhuis, school, universiteit, apotheek, politie, brandweer, tankstation, supermarkt of OV-station",
            },
            {
                "name": "radius_m",
                "type": "meter",
                "required": False,
                "description": "Zoekstraal, standaard en maximaal vijfentwintigduizend meter",
            },
            {
                "name": "max_results / rank",
                "type": "selectie",
                "required": False,
                "description": "Aantal resultaten en sortering op afstand of populariteit",
            },
        ],
        "outputs": [
            "Places-handle met maximaal vijftien locaties",
            "Dichtstbijzijnde locatie en afstand",
            "Google-attributie en tijdelijke resolutiemetadata",
        ],
        "constraints": [
            "Alleen vaste Place-types",
            "Maximaal vijfentwintig kilometer",
            "Resultaten zijn externe context en geen bronmeting",
        ],
        "examples": [
            "Wat is het dichtstbijzijnde ziekenhuis bij deze melding?",
            "Toon scholen binnen twee kilometer van Moerdijk.",
        ],
    },
    "capture_dashboard": {
        "inputs": [
            {
                "name": "dashboard_version",
                "type": "integer",
                "required": False,
                "description": "Exacte versie; standaard de huidige",
            },
            {
                "name": "scope",
                "type": "keuze",
                "required": False,
                "description": "viewport, full_dashboard of panel",
            },
            {
                "name": "panel_id",
                "type": "paneel-ID",
                "required": False,
                "description": "Verplicht wanneer scope panel is",
            },
            {
                "name": "include_structured / analyze",
                "type": "boolean",
                "required": False,
                "description": "Voeg state toe of laat Cerebras de screenshot beoordelen",
            },
            {
                "name": "wait_for_render_seconds",
                "type": "seconden",
                "required": False,
                "description": "Maximale wachttijd op de exacte render",
            },
        ],
        "outputs": [
            "PNG-screenshot met vaste viewport",
            "Dashboardversie, handle-IDs en structured state",
            "Optionele visuele analyse",
        ],
        "constraints": [
            "Capture wijzigt geen renderstatus",
            "Geen success wanneer de gevraagde versie niet klaar is",
            "Analyse gebruikt Cerebras Gemma 4",
        ],
        "examples": [
            "Maak een screenshot van het hele dashboard.",
            "Controleer visueel of de panelen logisch zijn ingedeeld.",
        ],
    },
    "external_search": {
        "inputs": [
            {
                "name": "query",
                "type": "tekst",
                "required": True,
                "description": "Gerichte zoekopdracht",
            },
            {
                "name": "max_results",
                "type": "integer",
                "required": False,
                "description": "Een tot vijf resultaten",
            },
            {
                "name": "recency_days",
                "type": "integer",
                "required": False,
                "description": "Optioneel recentievenster",
            },
            {
                "name": "domain_allowlist",
                "type": "lijst",
                "required": False,
                "description": "Beperk tot expliciete domeinen",
            },
        ],
        "outputs": [
            "Webresultaten-handle",
            "Titel, URL en compacte snippet per resultaat",
            "Provider- en tijdmetadata",
        ],
        "constraints": [
            "Werkt alleen wanneer websearch aan staat",
            "DDGS is eerste keyloze route; Brave is optionele fallback",
            "Webresultaten zijn onbevestigde context",
        ],
        "examples": [
            "Zoek het laatste nieuws over Almere.",
            "Zoek alleen op rijkswaterstaat.nl naar berichtgeving over de A12.",
        ],
    },
}


def _source_query_inputs(
    stream_id: str,
    kind: str,
    metrics: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    operation = "query_measurements" if kind == "metingen" else "query_events"
    inputs: list[dict[str, object]] = [
        {
            "name": "operation",
            "type": "vaste keuze",
            "required": True,
            "description": f"Gebruik {operation} voor normale selecties uit deze bron.",
        },
        {
            "name": "stream",
            "type": "vaste bron-ID",
            "required": True,
            "description": stream_id,
        },
    ]
    if kind == "metingen":
        inputs.append(
            {
                "name": "metric / metrics",
                "type": "meetwaarde-ID",
                "required": True,
                "description": ", ".join(metrics),
            }
        )
    else:
        inputs.extend(
            [
                {
                    "name": "category / categories",
                    "type": "tekst of lijst",
                    "required": False,
                    "description": "Exacte categorieen uit de actuele bronrecords.",
                },
                {
                    "name": "severity / status",
                    "type": "tekst",
                    "required": False,
                    "description": "Filter op ernst of actuele eventstatus.",
                },
                {
                    "name": "text",
                    "type": "vrije tekst",
                    "required": False,
                    "description": "Zoekt hoofdletterongevoelig in het volledige genormaliseerde record.",
                },
            ]
        )
    inputs.extend(
        [
            {
                "name": "window",
                "type": "ISO-duur",
                "required": False,
                "description": "Bijvoorbeeld PT60M, PT6H, PT24H of P14D.",
            },
            {
                "name": "filters[].field / op / value",
                "type": "filterlijst",
                "required": False,
                "description": "Velden hieronder met eq, in, gte, lte, between, contains of within_radius_handle.",
            },
            {
                "name": "sort / order / limit",
                "type": "selectie",
                "required": False,
                "description": "Sorteerveld, asc of desc en maximaal een tot tweeduizend records.",
            },
            {
                "name": "group_by / fn",
                "type": "aggregatie",
                "required": False,
                "description": "Voor aggregate: groeperingsveld plus count, sum, mean, median, min, max, p95, latest, delta of percent_change.",
            },
        ]
    )
    return inputs


STREAM_CAPABILITIES: dict[str, dict[str, object]] = {
    "knmi_observations": {
        "display_name": "KNMI-weermetingen",
        "kind": "metingen",
        "description": "Actuele waarnemingen per KNMI-meetstation.",
        "inputs": _source_query_inputs(
            "knmi_observations",
            "metingen",
            ("wind_gust_kmh", "wind_speed_ms", "rainfall_rate_mm_h", "air_temperature_c"),
        ),
        "metrics": [
            {"id": "wind_gust_kmh", "label": "Windstoot", "unit": "km/h"},
            {"id": "wind_speed_ms", "label": "Windsnelheid", "unit": "m/s"},
            {"id": "rainfall_rate_mm_h", "label": "Neerslagintensiteit", "unit": "mm/h"},
            {"id": "air_temperature_c", "label": "Luchttemperatuur", "unit": "°C"},
        ],
        "fields": [
            "metric",
            "value",
            "unit",
            "observed_at",
            "location.label",
            "location.latitude",
            "location.longitude",
            "source_ref",
        ],
        "possibilities": [
            "actuele ranglijst",
            "kaart",
            "KPI",
            "aggregatie",
            "vergelijking",
            "andere bronnen binnen 10 km",
        ],
        "examples": [
            "Rangschik de actuele windstoten van hoog naar laag.",
            "Toon de actuele temperatuur per station.",
        ],
        "limitations": [
            "Stationmetingen zijn niet hetzelfde als een gebiedsgemiddelde.",
            "De huidige KNMI-adapter levert de nieuwste tienminutensnapshot; deze is geschikt voor stationvergelijking, niet voor een tijdtrend.",
            "Een actuele meting bewijst geen oorzaak van incidenten.",
        ],
    },
    "rws_water": {
        "display_name": "Rijkswaterstaat Waterdata",
        "kind": "metingen",
        "description": "Actuele waterstanden van Rijkswaterstaat-meetpunten.",
        "inputs": _source_query_inputs("rws_water", "metingen", ("water_level_cm",)),
        "metrics": [{"id": "water_level_cm", "label": "Waterstand", "unit": "cm"}],
        "fields": [
            "metric",
            "value",
            "unit",
            "observed_at",
            "location.label",
            "location.latitude",
            "location.longitude",
            "source_ref",
        ],
        "possibilities": [
            "actuele ranglijst",
            "kaart",
            "KPI",
            "verschil",
            "aggregatie",
            "andere bronnen binnen 10 km",
        ],
        "examples": [
            "Toon de hoogste waterstanden van vandaag.",
            "Vergelijk de waterstand bij twee meetpunten.",
        ],
        "limitations": [
            "Waarden zijn meetpuntgebonden.",
            "De DDAPI20 WFS-feed bevat alleen de laatste waarneming per meetpunt.",
            "Een groter queryvenster maakt geen historische reeks; daarvoor is een afzonderlijke historische bronadapter nodig.",
        ],
    },
    "luchtmeetnet": {
        "display_name": "Luchtmeetnet",
        "kind": "metingen",
        "description": "Actuele concentraties van luchtverontreinigende stoffen per meetstation.",
        "inputs": _source_query_inputs(
            "luchtmeetnet",
            "metingen",
            tuple(metric for metric in MEASUREMENT_METRICS if metric.endswith("_ug_m3")),
        ),
        "metrics": [
            {"id": metric, "label": metric.removesuffix("_ug_m3").upper(), "unit": "µg/m³"}
            for metric in MEASUREMENT_METRICS
            if metric.endswith("_ug_m3")
        ],
        "fields": [
            "metric",
            "value",
            "unit",
            "observed_at",
            "location.label",
            "location.latitude",
            "location.longitude",
            "source_ref",
        ],
        "possibilities": [
            "tijdreeks",
            "ranglijst",
            "kaart",
            "KPI",
            "aggregatie",
            "vergelijking",
            "andere bronnen binnen 10 km",
        ],
        "examples": [
            "Toon de hoogste NO2-metingen van het laatste uur.",
            "Vergelijk PM10 en PM2,5 per station.",
        ],
        "limitations": [
            "Beschikbare stoffen verschillen per station.",
            "De latest-feed bevat slechts enkele recente uren. Een tijdreeks vereist één metriek en maximaal acht stations; filter bij voorkeur op één station.",
            "Vergelijk verschillende stoffen alleen na een expliciete aggregatie; dezelfde eenheid betekent niet dezelfde gezondheidsbetekenis.",
            "Meetwaarden zijn geen individueel gezondheidsadvies.",
        ],
    },
    "ndw_incidents": {
        "display_name": "NDW-wegmeldingen",
        "kind": "events",
        "description": "Actuele wegincidenten, files, afsluitingen en verkeersmaatregelen.",
        "inputs": _source_query_inputs("ndw_incidents", "events"),
        "metrics": [],
        "fields": [
            "category",
            "title",
            "description",
            "severity",
            "status",
            "observed_at",
            "location",
            "attributes",
            "source_ref",
        ],
        "possibilities": [
            "live feed",
            "kaart",
            "filter",
            "telling",
            "tijdlijn",
            "incidentanalyse",
            "andere bronnen binnen 10 km",
        ],
        "examples": [
            "Toon actieve wegafsluitingen rond Utrecht.",
            "Hoeveel wegmeldingen zijn er het laatste uur?",
        ],
        "limitations": [
            "Meldingen kunnen wijzigen of worden ingetrokken.",
            "Niet ieder event bevat dezelfde detailvelden of coördinaten; een kaart toont alleen het lokaliseerbare deel.",
        ],
    },
    "p2000": {
        "display_name": "P2000-hulpverleningssignalen",
        "kind": "events",
        "description": "Live alarmeringssignalen voor Nederlandse hulpdiensten.",
        "inputs": _source_query_inputs("p2000", "events"),
        "metrics": [],
        "fields": [
            "category",
            "title",
            "description",
            "severity",
            "status",
            "observed_at",
            "location",
            "attributes",
            "source_ref",
        ],
        "possibilities": [
            "live feed",
            "regiofilter",
            "telling",
            "tijdlijn",
            "nabije voorzieningen",
            "andere bronnen binnen 10 km",
        ],
        "examples": [
            "Toon recente P2000-signalen rond Breda.",
            "Welke scholen liggen bij deze melding?",
        ],
        "limitations": [
            "Een alarmering is een signaal, geen bevestigd incident.",
            "Adres- en omschrijvingskwaliteit verschilt per melding.",
            "De primaire 112Radar-feed levert coördinaten; RSS-fallbackrecords zonder betrouwbare locatie blijven feed-only.",
        ],
    },
    "ns_disruptions": {
        "display_name": "NS-storingen en werkzaamheden",
        "kind": "events",
        "description": "Actuele spoorstoringen en geplande werkzaamheden van NS.",
        "inputs": _source_query_inputs("ns_disruptions", "events"),
        "metrics": [],
        "fields": [
            "category",
            "title",
            "description",
            "severity",
            "status",
            "observed_at",
            "location",
            "attributes",
            "source_ref",
        ],
        "possibilities": [
            "live feed",
            "filter",
            "telling",
            "tijdlijn",
            "kaart",
            "andere bronnen binnen 10 km",
        ],
        "examples": [
            "Welke actuele storingen zijn er rond Utrecht Centraal?",
            "Toon alleen geplande werkzaamheden.",
        ],
        "limitations": [
            "Locaties kunnen een traject in plaats van één station beschrijven.",
            "Storingen worden waar mogelijk per betrokken station aan de officiele stationscoordinaten gekoppeld; trajectbrede of onbekende locaties kunnen zonder geometrie blijven.",
            "Reisadvies blijft onderhevig aan actuele wijzigingen.",
        ],
    },
    "nos_rss": {
        "display_name": "NOS-nieuwsfeed",
        "kind": "events",
        "description": "Recente NOS-koppen en samenvattingen als publieke contextbron.",
        "inputs": _source_query_inputs("nos_rss", "events"),
        "metrics": [],
        "fields": [
            "category",
            "title",
            "description",
            "observed_at",
            "attributes.url",
            "source_ref",
        ],
        "possibilities": ["nieuwsfeed", "tekstfilter", "tijdlijn", "contextpaneel"],
        "examples": [
            "Toon recent nieuws over Almere uit de vaste feed.",
            "Filter de nieuwsfeed op infrastructuur.",
        ],
        "limitations": [
            "Nieuwscontext is geen bevestigde operationele bron.",
            "RSS bevat alleen de beschikbare feeditems, geen volledige webzoekindex.",
        ],
    },
}
