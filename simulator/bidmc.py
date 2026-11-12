"""BIDMC PPG and Respiration dataset replay (PS-03 §3.2B).

Replays recorded heart rate, respiratory rate and SpO2 from the open BIDMC
PhysioNet dataset (https://www.physionet.org/content/bidmc/1.0.0/) into the
FHIR ingestion API, at real time or accelerated.

The dataset's ``*_Numerics.csv`` files are 1 Hz, ~8 minutes per record, with
columns ``Time [s], HR, PULSE, RESP, SpO2``. BIDMC provides no body
temperature and no blood pressure, so those are left absent here and supplied
by the simulator (§3.2A).

These recordings are mostly stable ICU patients. They are used to demonstrate
real recorded data and FHIR validity; the deterioration and artifact scenarios
come from the simulator, because an 8-minute stable recording cannot contain a
multi-day deterioration.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digital_twin.types import Observation

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "bidmc"

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "time": ("time [s]", "time", "time_s"),
    "heart_rate": ("hr", "heart_rate"),
    "respiratory_rate": ("resp", "rr", "respiratory_rate"),
    "spo2": ("spo2", "sp_o2"),
}


@dataclass(frozen=True)
class BidmcSample:
    time_s: float
    heart_rate: float | None
    respiratory_rate: float | None
    spo2: float | None


@dataclass(frozen=True)
class BidmcRecord:
    record_id: str
    samples: tuple[BidmcSample, ...]

    @property
    def duration_s(self) -> float:
        return self.samples[-1].time_s if self.samples else 0.0


def _normalize_header(name: str) -> str:
    return name.strip().lower()


def _column_index(header: list[str], metric: str) -> int | None:
    normalized = [_normalize_header(h) for h in header]
    for alias in COLUMN_ALIASES[metric]:
        if alias in normalized:
            return normalized.index(alias)
    return None


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() in {"nan", "null", "none", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number == number else None  # reject NaN


def load_record(path: str | Path) -> BidmcRecord:
    """Load one BIDMC Numerics CSV."""
    path = Path(path)
    samples: list[BidmcSample] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            raise ValueError(f"{path} is empty")
        idx = {metric: _column_index(header, metric) for metric in COLUMN_ALIASES}
        if idx["time"] is None or idx["heart_rate"] is None:
            raise ValueError(f"{path} missing required columns; header={header}")
        for row in reader:
            if not row:
                continue

            def pick(metric: str) -> float | None:
                i = idx[metric]
                return _number(row[i]) if i is not None and i < len(row) else None

            time_s = pick("time")
            if time_s is None:
                continue
            samples.append(
                BidmcSample(
                    time_s=time_s,
                    heart_rate=pick("heart_rate"),
                    respiratory_rate=pick("respiratory_rate"),
                    spo2=pick("spo2"),
                )
            )
    if not samples:
        raise ValueError(f"{path} contained no samples")
    record_id = path.stem.replace("_Numerics", "")
    return BidmcRecord(record_id=record_id, samples=tuple(samples))


def load_records(data_dir: str | Path = DEFAULT_DATA_DIR) -> list[BidmcRecord]:
    directory = Path(data_dir)
    paths = sorted(directory.glob("*_Numerics.csv"))
    if not paths:
        raise FileNotFoundError(
            f"No BIDMC Numerics CSVs in {directory}. Run scripts/fetch_bidmc.py first."
        )
    return [load_record(p) for p in paths]


class BidmcReplay:
    """Replays BIDMC records as Observations.

    ``speed`` is the acceleration factor: 1.0 is real time (one sample per
    second), 60.0 plays a minute of recording per second.
    """

    def __init__(
        self,
        records: list[BidmcRecord],
        *,
        patient_id: str,
        speed: float = 1.0,
        start: datetime | None = None,
        loop: bool = False,
    ) -> None:
        if not records:
            raise ValueError("no records to replay")
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.records = records
        self.patient_id = patient_id
        self.speed = speed
        self.start = start or datetime.now(timezone.utc)
        self.loop = loop
        self._record_index = 0
        self._sample_index = 0
        self._emitted = 0
        self._clock = self.start

    @property
    def current_record(self) -> BidmcRecord:
        return self.records[self._record_index]

    @property
    def finished(self) -> bool:
        return not self.loop and self._record_index >= len(self.records)

    @property
    def elapsed_s(self) -> float:
        """Recorded seconds replayed so far (not wall-clock time)."""
        return (self._clock - self.start).total_seconds()

    @property
    def progress(self) -> dict:
        record = self.current_record if self._record_index < len(self.records) else None
        return {
            "source": "bidmc",
            "record_id": record.record_id if record else None,
            "record_index": self._record_index,
            "records_total": len(self.records),
            "sample_index": self._sample_index,
            "samples_total": len(record.samples) if record else 0,
            "emitted": self._emitted,
            "speed": self.speed,
            "finished": self.finished,
        }

    def next(self) -> tuple[Observation, float] | None:
        """Return the next observation and the wall-clock delay before it.

        Returns ``None`` when the replay is exhausted.
        """
        if self.finished:
            return None
        record = self.records[self._record_index]
        if self._sample_index >= len(record.samples):
            self._record_index += 1
            self._sample_index = 0
            if self.finished:
                return None
            if self._record_index >= len(self.records):
                if not self.loop:
                    return None
                self._record_index = 0
            record = self.records[self._record_index]

        sample = record.samples[self._sample_index]
        if self._sample_index > 0:
            interval_s = max(sample.time_s - record.samples[self._sample_index - 1].time_s, 0.0)
        else:
            # The first sample has no predecessor; use the record's own cadence
            # so every emitted timestamp is strictly increasing.
            interval_s = (
                record.samples[1].time_s - sample.time_s if len(record.samples) > 1 else 1.0
            )
        delay_s = interval_s / self.speed

        observation = Observation(
            timestamp=self._clock,
            heart_rate=sample.heart_rate if sample.heart_rate is not None else 0.0,
            spo2=sample.spo2,
            respiratory_rate=sample.respiratory_rate,
            temperature=None,  # BIDMC has no body temperature
            systolic_bp=None,
            diastolic_bp=None,
            activity_intensity=0.0,
            source=f"bidmc:{record.record_id}",
        )
        self._clock = self._clock + timedelta(seconds=interval_s)
        self._sample_index += 1
        self._emitted += 1
        return observation, delay_s

    def observations(self):
        """Iterate all observations, ignoring wall-clock pacing."""
        while True:
            item = self.next()
            if item is None:
                return
            yield item[0]