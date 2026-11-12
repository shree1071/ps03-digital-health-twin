# Attribution and Third-Party Notices

This project is a **derivative work**. It is not an original codebase.

## Upstream project

| | |
|---|---|
| Project | Human Health Digital Twin |
| Repository | https://github.com/Baranidharan16/Digital-Health-Twin |
| Commit used | `c793c21` ("README: add team Torque Titans, college, live website and app download") |
| Licence | MIT — `Copyright (c) 2026 Human Health Digital Twin contributors` |
| Authors | **Torque Titans**, R.M.K. Engineering College (ECE, third year): B. Barani Dharan (team lead), M. Jai Vignesh, K.S. Hari Haran, P. Mohan |
| Original purpose | Submission for the Happiest Health hackathon |

The upstream MIT licence text is retained verbatim in [LICENSE](LICENSE). The upstream
README is preserved unmodified at [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md).

## What was reused vs. newly written

Reused from upstream (MIT), with modifications:

- FastAPI application skeleton: `backend/app/factory.py`, `config.py`, `db.py`, `main.py`
- Source-adapter ingestion pattern: `backend/app/ingestion.py`
- Robust personal baseline (median / scaled MAD): `digital_twin/baseline.py`
- Anomaly assessment and plain-language explanation pattern: `digital_twin/anomaly.py`
- Engine synchronisation cycle: `digital_twin/engine.py`, `state_engine.py`, `physiology.py`
- WebSocket fan-out hub: `backend/app/services/hub.py`
- React dashboard shell and components: `frontend/src/` (`VitalsPanel`, `LiveChart`,
  `EventFeed`, `StatePanel`, `useLiveTwin`)
- 3D anatomy scene: `frontend/src/twin3d/TwinScene.tsx`, `frontend/src/lib/anatomy.ts`

Newly written for MineArena PS-03:

- FHIR R4 Vital Signs Observation ingestion and HAPI FHIR server integration
- NEWS2-style scoring engine and patient-specific personalization rules
- Sensor artifact detection and false-alert suppression
- Synthea discharge-profile generation and profile-driven scoring
- BIDMC PhysioNet dataset replay
- Scripted wearable-simulator events (drift, spikes, dropouts, motion noise, probe removal)
- Naive fixed-threshold comparator and performance report
- Clinician alert tiers (Watch / Review / Urgent) and alert explanations

## Third-party assets and data

### 3D anatomy model — CC BY-SA 2.1 JP (ShareAlike)

`frontend/public/models/anatomy_twin.glb` is derived from **BodyParts3D**,
© The Database Center for Life Science, licensed under **CC Attribution-ShareAlike 2.1 Japan**.
The derived model is shared under the same licence.

> Mitsuhashi N. et al., *BodyParts3D: 3D structure database for anatomical concepts*,
> Nucleic Acids Res. 2009;37:D782–5, doi:10.1093/nar/gkn613.

Because this asset is ShareAlike, any redistribution of the model must keep it under
CC BY-SA 2.1 JP with this attribution. This applies to the model asset, not to the
project source code.

### Real wearable sample — CC0 1.0

`data/sample_real/`: Furberg R., Brinton J., Keating M., Ortiz A. (2016).
*Crowd-sourced Fitbit datasets 03.12.2016–05.12.2016* [Data set], Zenodo,
doi:10.5281/zenodo.53894, CC0 1.0. 14-day excerpt for participant 5553957443.

### BIDMC PPG and Respiration Dataset — PhysioNet

Used for recorded HR, RR and SpO₂ replay.
https://www.physionet.org/content/bidmc/1.0.0/
Please cite the PhysioNet record and the original paper when publishing results.

### Synthea — Apache-2.0

Synthetic patient generator. https://synthetichealth.github.io/synthea/
Generated patients are synthetic and contain no real personal data.

### NEWS2

NEWS2 is a clinical early-warning scoring system published by the Royal College of
Physicians. https://www.rcp.ac.uk/file/9436/download
This project implements a NEWS2-*style* score for decision-support demonstration.
It is not a certified implementation of NEWS2 and is not a medical device.

### HAPI FHIR — Apache-2.0

HAPI FHIR JPA Server. https://hapifhir.io/
The server WAR is downloaded at setup time into `vendor/hapi/` and is not committed.

## Clinical limitation

**This system provides clinical decision support only. It is not a medical device and
does not produce medical diagnoses.** All patient data used in demonstrations is
synthetic. No real patient data is sent to any third-party service.