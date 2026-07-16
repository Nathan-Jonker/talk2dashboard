# Verkennende open-data-analyse: incident spuikokers IJmuiden

## Samenvatting

Op 2 november 2023 bleef volgens de [officiële evaluatiepagina van Rijkswaterstaat](https://open.rijkswaterstaat.nl/@275817/evaluatie-incident-spuikokers-spui/) een spuikoker bij IJmuiden door een storing ongecontroleerd openstaan toen het vloed werd. Zeewater stroomde het Noordzeekanaal in en het waterpeil steeg snel. Deze notitie onderzoekt een beperkte vervolgvraag: had een onafhankelijke monitor op basis van openbare meetreeksen eerder een afwijkend patroon kunnen signaleren?

Het voorzichtige antwoord is **waarschijnlijk wel voor eerdere waarschuwing, niet voor diagnose of preventie**. Een samengestelde regel over de aanhoudende stijging bij Buitenhuizen en IJmuiden, gecombineerd met het buiten-binnenverschil, gaf in de gereproduceerde analyse rond 05:30 een signaal. Dat was ongeveer vijftien minuten voor de eerste RWS-waarneming van het hoge peil en veertig minuten voor de diagnosehypothese van AGV. Publieke data toont echter niet of een schuif fysiek openstaat, welke bediening actief is, welke alarmen in SCADA zijn verschenen of welke operatoractie is uitgevoerd.

## Aanpak

De analyse is gebaseerd op:

- de publieke evaluatie van Rijkswaterstaat;
- openbare Rijkswaterstaat-waterstandsreeksen rond IJmuiden en het Noordzeekanaal;
- een reproduceerbare Codex-analyse van een jaar RWS-waterhoogtedata en KNMI-uurdata;
- de afzonderlijke ChatGPT-verkenning die aanleiding gaf om detectie, diagnose en preventie strikt uit elkaar te houden.

De waarden hieronder zijn een reconstructie voor hypothesevorming. Ze vormen geen gecertificeerde tijdlijn en zijn niet gevalideerd tegen interne logboeken, de volledige operationele historie of de precieze publicatievertraging van iedere openbare meting.

## Gereconstrueerde meetwaarden

| Tijd | Buitenhuizen | IJmuiden Noordersluis oost | IJmuiden Buitenhaven | IJgeul |
| --- | ---: | ---: | ---: | ---: |
| 03:40 | -39 cm | -37 cm | -40 cm | -38 cm |
| 04:30 | -31 cm | -31 cm | 11 cm | 16 cm |
| 05:30 | -23 cm | -23 cm | 79 cm | 84 cm |
| 06:10 | -17 cm | -20 cm | 78 cm | 81 cm |

Tussen 03:40 en ongeveer 05:50 steeg Buitenhuizen in deze reconstructie circa negentien centimeter. Om 05:30 was Buitenhuizen in twee uur zestien centimeter gestegen en de kanaalzijde bij de Noordersluis circa vijftien centimeter. De veel grotere stijging aan de buitenzijde past grotendeels bij het getij en is daarom op zichzelf geen alarmsignaal. Juist de combinatie van buitenwater, verwachte getijcurve en afwijkende stijging aan de binnenzijde is relevant.

## Vergelijking met de operationele tijdlijn

De [officiële RWS-evaluatie](https://open.rijkswaterstaat.nl/@275817/evaluatie-incident-spuikokers-spui/) en de gepubliceerde [tijdlijnsamenvatting van NH Nieuws](https://www.nhnieuws.nl/nieuws/340156/amsterdam-ontsnapt-aan-overstroming-alerte-medewerker-voorkomt-ramp) geven de noodzakelijke referentie:

| Tijd | Gebeurtenis |
| --- | --- |
| 03:52 | Het spuicomplex schakelt naar handbediening; de schuiven sluiten niet automatisch. |
| 05:45 | Het stijgende waterpeil wordt bij RWS in Schellingwoude opgemerkt. |
| Circa 06:10 | AGV verdenkt openstaande spuikokers als oorzaak en de operationele keten schaalt op. |
| 07:11 | Een monteur bevestigt ter plaatse dat alle zeven spuikokers openstaan. |
| 07:24-07:26 | De spuikokers zijn handmatig gesloten. |

Deze tijdlijn maakt het verschil tussen **signaleren** en **diagnosticeren** concreet. Een waarschuwing rond 05:30 kan eerder zijn dan de menselijke peildetectie; alleen interne status- en bedieningsdata kon daarna de oorzaak bevestigen.

## Mogelijke detectieregels

Een simpele monitor had meerdere niveaus kunnen gebruiken:

1. **Snelle stijging:** kanaalpeil stijgt meer dan een ingestelde drempel binnen dertig of zestig minuten.
2. **Afwijking van verwachting:** gemeten kanaalontwikkeling wijkt af van de verwachte ontwikkeling bij het actuele buitenwater en spuiprogramma.
3. **Ruimtelijke bevestiging:** twee onafhankelijke meetpunten aan de kanaalzijde vertonen tegelijk een uitzonderlijke stijging.
4. **Historische zeldzaamheid:** de gecombineerde verandering valt buiten een station- en seizoenafhankelijke baseline.

In de verkennende reconstructie:

| Regel | Eerste signaal | Alarmdagen in de gebruikte jaarbaseline |
| --- | --- | ---: |
| Buitenhuizen `+5 cm / 30 min` | 04:30 | 79 |
| Buitenhuizen `+8 cm / 60 min` | 04:30 | 16 |
| Buitenhuizen `+15 cm / 2 uur` | 05:30 | 0 |
| Buitenhuizen `+20 cm / 3 uur` | 05:50 | 0 |
| Buitenhuizen `+24 cm / 4 uur` | 06:10 | 0 |

De sterkste kandidaat combineerde drie voorwaarden: Buitenhuizen stijgt minimaal vijftien centimeter in twee uur, IJmuiden Noordersluis oost stijgt minimaal tien centimeter in twee uur en de buitenhaven staat minimaal vijftig centimeter hoger dan de kanaalzijde. Die regel gaf rond 05:30 een signaal en kwam niet voor op andere alarmdagen in de gebruikte jaarbaseline.

Dat is sterker dan een losse drempel, maar nog geen operationeel gevalideerd model. Waterinfo gebruikt rond deze reeks een gemiddelde over het vorige en volgende vijfminutenvenster; een 05:30-punt is dus pas ongeveer 05:35 compleet, plus eventuele publicatievertraging. Ook nul historische alarmdagen in één jaar bewijst niet dat de regel generaliseert.

## Wat publieke data wel en niet kan

| Kan ondersteunen | Kan niet vaststellen |
| --- | --- |
| Onafhankelijke afwijkingsdetectie op waterstanden | Fysieke positie van iedere spuikoker |
| Vergelijking tussen binnen- en buitenwater | Handmatige of automatische bedieningsmodus |
| Trend- en baselinewaarschuwingen | SCADA-alarmen en commandologs |
| Cross-check met getij, wind en neerslag | Sensor- en actuatorstatus in de installatie |
| Regionale impact via omliggende meetpunten | Operatorhandelingen en interne communicatie |

Een robuuste architectuur zou daarom uit drie lagen bestaan:

1. primaire installatiebeveiliging, telemetrie en harde alarmen;
2. onafhankelijke interne monitoring over SCADA-, positie- en procesdata;
3. een externe sanity check op publieke meetreeksen en verwachtingen.

Talk2Dashboard demonstreert vooral die derde laag. De waarde zit in snelle zichtbaarheid en bronvergelijking, niet in het vervangen van operationele beveiliging.

## Minimale uitbreiding voor serieus onderzoek

Voor een valide vervolgonderzoek zijn minimaal nodig:

- langere waterstandsreeksen met bekende publicatielatency;
- astronomisch en operationeel verwacht buitenwater;
- verwacht spuidebiet en spuiprogramma;
- KNMI-wind, luchtdruk en neerslag;
- meetreeksen van Amsterdam/AGV en andere kanaalpunten;
- interne schuifposities, bedieningstoestand, commando's en SCADA-alarmen;
- operator- en communicatielogs;
- een formeel gelabelde set normale en afwijkende situaties.

Daarna kunnen regels of modellen worden getest op detectietijd, false positives, gemiste incidenten en robuustheid tegen ontbrekende sensoren. Tot die validatie is iedere uitspraak over “eerder signaleren” een hypothese, geen bewezen operationele verbetering.

## Conclusie

De openbare meetreeksen waren voldoende om achteraf een afwijkende kanaalontwikkeling zichtbaar te maken. Een sustained-trendmonitor had rond 05:30 plausibel een early warning kunnen geven, ongeveer vijftien minuten voor de eerste RWS-waarneming en veertig minuten voor de AGV-diagnosehypothese. Dat is geen bewijs dat het incident voorkomen kon worden: de regel is achteraf ontwikkeld, de baseline is beperkt en publicatielatency telt mee. De data bewijst evenmin wat er technisch misging. De juiste positionering is daarom een aanvullende second-line monitor, naast interne telemetrie, SCADA-alarmen en fail-safe bedieningslogica.

Terug naar de [Talk2Dashboard README](../README.md).
