# Exploratory open-data analysis: IJmuiden discharge-sluice incident

## Key conclusion

**Public water data could probably have identified this incident earlier as an exceptional pattern, but it could not diagnose the cause.** The reconstruction combines three types of evidence: the official Rijkswaterstaat incident timeline; public ten-minute measurements from 1 through 5 November 2023; and a separate series from 1 November 2022 through 3 November 2023 used to test the same detection rules for historical false positives.

At 05:30, Buitenhuizen had risen by sixteen centimetres over two hours. Noordersluis East rose by approximately fifteen centimetres over the same window, while the outside harbour stood roughly 102 centimetres above the canal side. The isolated `Buitenhuizen +15 cm / 2 hours` rule occurred on zero other alarm days in the preceding one-year baseline used. The composite rule across both inside stations and the outside-inside difference did not occur there either. In hindsight, that makes 05:30 a plausible point for a serious second-line warning: around fifteen minutes before the first RWS observation and forty minutes before AGV's diagnosis hypothesis.

This does not prove the incident would have been prevented. The rule was developed retrospectively, one year of history is limited, and averaging plus publication delay means the 05:30 point was probably available only several minutes later. Public water data also cannot reveal that the discharge gates are open; that requires internal gate positions, control state, command feedback and SCADA alarms.

## Public sources and reproducibility

- [Rijkswaterstaat Waterinfo](https://waterinfo.rws.nl/) for viewing and exporting measurement series;
- [Rijkswaterstaat Waterdata](https://www.rijkswaterstaat.nl/water/waterdata) for access to current and historical water data; historical water-quantity data can be requested and downloaded through Waterinfo's **Expert** tab;
- the [official incident evaluation](https://open.rijkswaterstaat.nl/@275817/evaluatie-incident-spuikokers-spui/) for the operational timeline and reports.

The incident reconstruction used a Waterinfo export covering 1 through 5 November 2023. The false-positive check deliberately used a separate, longer RWS series from 1 November 2022 through 3 November 2023 and counted only days before the incident window. Therefore, `0` in the table below means **zero other alarm days on which that exact rule fired in this baseline**. It does not guarantee zero false positives across every season or future year.

## Summary

On 2 November 2023, according to the [official Rijkswaterstaat evaluation page](https://open.rijkswaterstaat.nl/@275817/evaluatie-incident-spuikokers-spui/), a discharge sluice at IJmuiden remained uncontrolled and open after a malfunction as the tide came in. Seawater entered the North Sea Canal and the water level rose rapidly. This note explores a limited follow-up question: could an independent monitor using public time series have flagged an anomalous pattern earlier?

The cautious answer is **probably for an earlier warning, but not for diagnosis or prevention**. A composite rule over the sustained rise at Buitenhuizen and IJmuiden, combined with the outside-inside level difference, fired at approximately 05:30 in the reproduced analysis. That was about fifteen minutes before RWS first noticed the high level and forty minutes before AGV formed its diagnosis hypothesis. Public data does not reveal physical gate position, control mode, SCADA alarms or operator actions.

## Method

This analysis uses the public Rijkswaterstaat evaluation, public [Rijkswaterstaat Waterinfo](https://waterinfo.rws.nl/) series around IJmuiden and the North Sea Canal, a reproducible Codex analysis of one year of RWS water-level and KNMI hourly data, and the separate ChatGPT exploration that prompted a strict distinction between detection, diagnosis and prevention.

The values below are exploratory. They are not a certified timeline and have not been validated against internal logs, a complete operational history or the exact publication delay of each public measurement.

## Reconstructed measurements

| Time | Buitenhuizen | IJmuiden Noordersluis east | IJmuiden Buitenhaven | IJgeul |
| --- | ---: | ---: | ---: | ---: |
| 03:40 | -39 cm | -37 cm | -40 cm | -38 cm |
| 04:30 | -31 cm | -31 cm | 11 cm | 16 cm |
| 05:30 | -23 cm | -23 cm | 79 cm | 84 cm |
| 06:10 | -17 cm | -20 cm | 78 cm | 81 cm |

Between 03:40 and approximately 05:50, Buitenhuizen rose by about nineteen centimetres in this reconstruction. By 05:30, Buitenhuizen had risen sixteen centimetres over two hours and the canal side near the Noordersluis roughly fifteen centimetres. The much larger rise outside broadly follows the tide and is not an alarm by itself. The relevant signal is the combination of outside water, expected tidal development and an anomalous rise on the canal side.

## Comparison with the operational timeline

The [official RWS evaluation](https://open.rijkswaterstaat.nl/@275817/evaluatie-incident-spuikokers-spui/) and the published [NH Nieuws timeline summary](https://www.nhnieuws.nl/nieuws/340156/amsterdam-ontsnapt-aan-overstroming-alerte-medewerker-voorkomt-ramp) provide the necessary reference:

| Time | Event |
| --- | --- |
| 03:52 | The discharge complex switches to manual mode; the gates do not close automatically. |
| 05:45 | RWS staff in Schellingwoude notice the rising water level. |
| Around 06:10 | AGV suspects open discharge gates and the operational chain escalates. |
| 07:11 | An engineer confirms on site that all seven gates are open. |
| 07:24-07:26 | The gates are closed manually. |

This timeline makes the distinction between **warning** and **diagnosis** concrete. A warning at approximately 05:30 could precede human detection of the level rise; only internal status and control data could then confirm the cause.

## Candidate detection rules

An independent monitor could combine several layers:

1. rapid canal-level rise over thirty or sixty minutes;
2. deviation from expected canal development under current outside water and discharge plans;
3. confirmation by two independent canal-side stations;
4. rarity against a station- and season-specific historical baseline.

In the exploratory reconstruction:

| Rule | First signal | Alarm days in the one-year baseline used |
| --- | --- | ---: |
| Buitenhuizen `+5 cm / 30 min` | 04:30 | 79 |
| Buitenhuizen `+8 cm / 60 min` | 04:30 | 16 |
| Buitenhuizen `+15 cm / 2 hours` | 05:30 | 0 |
| Buitenhuizen `+20 cm / 3 hours` | 05:50 | 0 |
| Buitenhuizen `+24 cm / 4 hours` | 06:10 | 0 |

The `Buitenhuizen +15 cm / 2 hours` rule matters because the shorter thresholds fired frequently: `+5 cm / 30 min` on 79 days and `+8 cm / 60 min` on 16 days. Fifteen centimetres over two hours captured a **sustained** rise and occurred on zero other alarm days in the baseline used. At 05:30, it was also corroborated by an almost identical canal-side rise at Noordersluis East, reducing the likelihood of a single noisy sensor.

The strongest candidate therefore combined three conditions: Buitenhuizen rises at least fifteen centimetres over two hours, IJmuiden Noordersluis East rises at least ten centimetres over two hours, and the outside harbour is at least fifty centimetres above the canal side. It fired at approximately 05:30 and did not occur on another alarm day in the one-year baseline used. Duration, spatial confirmation and the outside-inside gradient make this more credible as an early signal than one isolated level threshold.

That is stronger than a single threshold, but it is not an operationally validated model. Waterinfo uses an average over the preceding and following five-minute interval for this series; a 05:30 point is therefore only complete at around 05:35, plus publication latency. Zero historical alarm days in one year also does not prove that the rule generalises.

## What public data can and cannot do

| Can support | Cannot determine |
| --- | --- |
| Independent anomaly detection on water levels | Physical position of each sluice gate |
| Comparison of inside and outside water | Manual or automatic control mode |
| Trend and baseline alerts | SCADA alarms and command logs |
| Cross-checks with tide, wind and precipitation | Installation sensor and actuator status |
| Regional impact through surrounding stations | Operator actions and internal communication |

A robust architecture would therefore use three layers: primary installation protection and hard alarms; independent internal monitoring over SCADA, position and process data; and an external sanity check over public measurements and expectations. Talk2Dashboard mainly demonstrates the third layer.

## Requirements for serious follow-up research

A valid study would require longer water-level histories with known publication latency, astronomical and operational outside-water expectations, expected discharge, KNMI weather inputs, surrounding Amsterdam/AGV series, internal gate position and control-state data, SCADA alarms, operator logs and a formally labelled set of normal and abnormal situations.

Only then can candidate rules or models be evaluated on detection time, false positives, missed incidents and resilience to missing sensors. Until that validation, “earlier detection” remains a hypothesis rather than a proven operational improvement.

## Conclusion

Public measurements were sufficient to reconstruct an anomalous canal-level development after the fact. A sustained-trend monitor could plausibly have produced an early warning at around 05:30, about fifteen minutes before the first RWS observation and forty minutes before AGV's diagnosis hypothesis. That does not prove the incident could have been prevented: the rule was developed retrospectively, the baseline is limited and publication latency matters. The data does not establish the technical root cause either. Its appropriate role is therefore an additional second-line monitor alongside internal telemetry, SCADA alarms and fail-safe control logic.

Return to the [Talk2Dashboard README](../README_EN.md).
