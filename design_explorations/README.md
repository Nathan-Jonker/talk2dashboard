# Talk2Dashboard design explorations

These static prototypes deliberately sit outside the production Dash/Vizro application. They explore five different compositions using the current dashboard as visual source material; no APIs, tools, or agent flows are connected.

## Current design diagnosis

- The product identity is recognizable, but navy panel headers give primary and secondary information equal weight.
- The map is the strongest visual asset, yet often behaves like one panel among many.
- Repeated bordered surfaces make the workspace feel assembled from widgets instead of composed for an operator.
- Voice controls are useful, but need a clearer relationship to the live operational context.

## Concepts

### 1. RWS Control Room

- **Visual thesis:** precise public-service command centre with a white workspace, navy structure and one yellow action line.
- **Content plan:** live incident queue, dominant map, compact operational metrics, voice command strip.
- **Interaction thesis:** incident-row focus, map-marker pulse and a concise voice activity reveal.

### 2. Atlas Focus

- **Visual thesis:** the three-dimensional map is the entire product surface; context floats at its edges.
- **Content plan:** full-canvas map, narrow source rail, selected-incident briefing, compact voice orb.
- **Interaction thesis:** depth shift on selection, rail expansion and map-focus transition.

### 3. Signal Desk

- **Visual thesis:** an editorial intelligence desk that reads like a composed public-sector briefing, not a widget grid.
- **Content plan:** lead map, ranked signals, source notes, timeline and spoken briefing.
- **Interaction thesis:** horizontal briefing ticker, underline-based hover and transcript reveal.

### 4. Field Glass

- **Visual thesis:** a calm field interface with restrained translucent controls over one continuous spatial view.
- **Content plan:** full-screen map, status ribbon, three task surfaces and a movable voice console.
- **Interaction thesis:** glass surface elevation, listening-state colour response and contextual detail reveal.

### 5. Briefing Wall

- **Visual thesis:** a high-contrast situation room for large displays, built around a map and decisive numeric hierarchy.
- **Content plan:** national map, live signal count, priority feed, compact source confidence and command channel.
- **Interaction thesis:** staged panel entrance, urgent-signal scan line and command-channel expansion.

## Running locally

```bash
python3 -m http.server 8010 --directory design_explorations
```

Open `http://127.0.0.1:8010/`.
