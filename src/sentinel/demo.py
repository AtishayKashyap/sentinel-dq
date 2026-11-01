"""Synthetic veterinary clinic dataset with deliberately injected defects, plus a
time-travel simulation that produces N days of run history for the dashboard.

Injected defects (and the check that should catch each):
  * owners.email NULL for ~6%                         -> null_rate (max 2%)
  * animals.owner_id pointing at missing owners       -> referential
  * animals.species typos ("dgo", "Cat ")             -> accepted_values
  * vaccinations.dose_ml out of physical range        -> range
  * vaccinations.external_ref duplicated              -> unique
  * cattle weights shift +45% in the final days       -> drift (PSI)
  * row-count spike on the penultimate day            -> row_count_anomaly
  * ingestion stops on the last day                   -> freshness
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable

import sqlalchemy as sa

md = sa.MetaData()

owners = sa.Table(
    "owners",
    md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("full_name", sa.String(100), nullable=False),
    sa.Column("email", sa.String(150)),
    sa.Column("phone", sa.String(20)),
    sa.Column("city", sa.String(50)),
    sa.Column("created_at", sa.DateTime, nullable=False),
)
# No FK on owner_id/animal_id on purpose: many real sources (CSV loads, replicas,
# NoSQL exports) have none, which is exactly why referential checks exist.
animals = sa.Table(
    "animals",
    md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("owner_id", sa.Integer, index=True),
    sa.Column("name", sa.String(50)),
    sa.Column("species", sa.String(20)),
    sa.Column("weight_kg", sa.Float),
    sa.Column("registered_at", sa.DateTime, nullable=False, index=True),
)
vaccinations = sa.Table(
    "vaccinations",
    md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("animal_id", sa.Integer, index=True),
    sa.Column("vaccine_code", sa.String(20)),
    sa.Column("lot_number", sa.String(30)),
    sa.Column("dose_ml", sa.Float),
    sa.Column("external_ref", sa.String(40)),
    sa.Column("administered_at", sa.DateTime, nullable=False, index=True),
)

SPECIES_WEIGHT = {"dog": (22, 8), "cat": (4.5, 1.2), "cattle": (520, 70), "sheep": (65, 10)}
VACCINES = ["RAB-1", "DHPP", "FVRCP", "BVD", "CLOST-7", "LEPTO"]
CITIES = ["Hyderabad", "Pune", "Bengaluru", "Chennai", "Mumbai", "Delhi"]
FIRST = ["Asha", "Ravi", "Meera", "Arjun", "Kiran", "Sana", "Vikram", "Neha", "Rahul", "Divya"]
LAST = ["Rao", "Sharma", "Reddy", "Iyer", "Khan", "Patel", "Nair", "Gupta"]


class Generator:
    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.owner_id = self.animal_id = self.vacc_id = 0
        self.refs: list[str] = []
        self.vaccinable = 0  # animals registered before the current batch

    def owner(self, at: dt.datetime) -> dict[str, object]:
        self.owner_id += 1
        f, last = self.rng.choice(FIRST), self.rng.choice(LAST)
        email = (
            None if self.rng.random() < 0.06 else f"{f}.{last}{self.owner_id}@example.com".lower()
        )
        return {
            "id": self.owner_id,
            "full_name": f"{f} {last}",
            "email": email,
            "phone": f"+91{self.rng.randint(7000000000, 9999999999)}",
            "city": self.rng.choice(CITIES),
            "created_at": at,
        }

    def animal(self, at: dt.datetime, drift: float = 1.0) -> dict[str, object]:
        self.animal_id += 1
        species = self.rng.choices(list(SPECIES_WEIGHT), weights=[5, 3, 2, 1])[0]
        mu, sd = SPECIES_WEIGHT[species]
        if species == "cattle":
            mu *= drift
        owner = self.rng.randint(1, max(self.owner_id, 1))
        if self.rng.random() < 0.01:
            owner = self.owner_id + 10_000  # orphan
        sp: str = species
        if self.rng.random() < 0.008:
            sp = self.rng.choice(["dgo", "Cat ", "bovine"])
        return {
            "id": self.animal_id,
            "owner_id": owner,
            "name": self.rng.choice(FIRST),
            "species": sp,
            "weight_kg": round(max(0.5, self.rng.gauss(mu, sd)), 2),
            "registered_at": at,
        }

    def vaccination(self, at: dt.datetime) -> dict[str, object]:
        self.vacc_id += 1
        dose = round(self.rng.uniform(0.5, 5.0), 2)
        if self.rng.random() < 0.004:
            dose = self.rng.choice([0.0, -1.0, 55.0])
        ref = f"VX-{self.rng.randint(10**7, 10**8 - 1)}"
        if self.refs and self.rng.random() < 0.005:
            ref = self.rng.choice(self.refs)  # duplicated upstream message
        self.refs.append(ref)
        return {
            "id": self.vacc_id,
            "animal_id": self.rng.randint(1, max(self.vaccinable, 1)),
            "vaccine_code": self.rng.choice(VACCINES),
            "lot_number": None if self.rng.random() < 0.01 else f"L{self.rng.randint(1000, 9999)}",
            "dose_ml": dose,
            "external_ref": ref,
            "administered_at": at,
        }


def _ts(day: dt.datetime, rng: random.Random) -> dt.datetime:
    return (day + dt.timedelta(seconds=rng.randint(0, 86_399))).replace(tzinfo=None)


def load_day(
    conn: sa.Connection, g: Generator, day: dt.datetime, scale: float, drift: float
) -> None:
    n_owners, n_animals, n_vacc = int(40 * scale), int(150 * scale), int(250 * scale)
    g.vaccinable = g.animal_id
    conn.execute(owners.insert(), [g.owner(_ts(day, g.rng)) for _ in range(n_owners)])
    conn.execute(animals.insert(), [g.animal(_ts(day, g.rng), drift) for _ in range(n_animals)])
    if g.vaccinable:  # vaccinate only animals registered in earlier batches
        conn.execute(vaccinations.insert(), [g.vaccination(_ts(day, g.rng)) for _ in range(n_vacc)])


def simulate(
    source: sa.Engine,
    run_once: Callable[[dt.datetime], object],
    days: int = 14,
    end: dt.datetime | None = None,
    history_days: int = 60,
) -> None:
    """Rebuild the source DB, then for each simulated day load a batch and run checks
    "as of" that day so the results warehouse fills with realistic history."""
    # The final run happens at end-of-window +23h; anchor it to "now".
    end = (end or dt.datetime.now(dt.UTC) - dt.timedelta(hours=23)).replace(
        minute=0, second=0, microsecond=0
    )
    start = end - dt.timedelta(days=days)
    md.drop_all(source)
    md.create_all(source)
    g = Generator()
    with source.begin() as conn:
        for d in range(history_days, 1, -1):  # backfill history before the window
            load_day(conn, g, start - dt.timedelta(days=d), 1.0, 1.0)
    for i in range(days + 1):
        day = start + dt.timedelta(days=i)
        remaining = days - i
        drift = 1.45 if remaining <= 2 else 1.0
        scale = 6.0 if remaining == 1 else 1.0
        if remaining > 0:  # last day: pipeline "broken", nothing ingested
            with source.begin() as conn:
                load_day(conn, g, day - dt.timedelta(days=1), scale, drift)
        run_once(day + dt.timedelta(hours=23 if remaining == 0 else 2))
