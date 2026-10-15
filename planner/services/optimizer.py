"""Cheapest refuelling plan along a fixed route.

Stations are points on a line (miles from the start) with a price per gallon. The
vehicle may buy any amount at a station, holds at most ``max_range`` miles of fuel and
must reach ``total_miles``. We minimise

    total fuel cost  +  stop_penalty * number_of_stops

The pure-cost optimum (stop_penalty=0) tends to suggest many tiny top-ups whenever the
next station is a cent cheaper; a small penalty (default a few dollars) trades pennies
for far fewer stops. The penalty only shapes the plan and is never part of the reported cost.

Algorithm -- exact dynamic programming over (station, fuel level):
  Fuel is discretised into ``unit_miles`` (1 mile = 0.1 gal by default), so a tank has
  max_range/unit + 1 levels. Walking the stations in route order, ``after[t]`` is the
  cheapest cost to be leaving the current station with t units in the tank:

    arrive[t] = after_prev[t + leg]                       (drive to the next station)
    after[t]  = min( arrive[t],                           (drive past)
                     min_{f<=t} arrive[f] + (t-f)*c + P ) (stop, buy t-f units at c)

  The inner min is a prefix-minimum of (arrive[f] - f*c), so each station costs O(levels)
  numpy work: about 5 ms for a 2,800 mile trip with ~450 candidate stations.

Start conditions:
  * start_full=False (default): the tank starts empty, so every gallon of the trip is
    bought and the total cost is the full trip's fuel bill. The first fill-up happens at
    one of the stations near the start (within ``origin_window`` miles along the route);
    the few miles driven to reach it are billed at that station's price.
  * start_full=True: the vehicle leaves with a full tank, so only top-ups are bought.
"""
from dataclasses import dataclass, field

import numpy as np

PASS, BUY, ENTRY = 0, 1, 2


class InfeasibleRoute(ValueError):
    """Some stretch of the route is longer than one tank with no station in between."""

    def __init__(self, gap_start_mi: float, gap_end_mi: float, max_range: float):
        self.gap_start_mi, self.gap_end_mi = gap_start_mi, gap_end_mi
        super().__init__(
            f"No fuel station reachable between mile {gap_start_mi:.0f} and mile {gap_end_mi:.0f} "
            f"of the route ({gap_end_mi - gap_start_mi:.0f} mi gap > {max_range:.0f} mi range)."
        )


@dataclass
class Purchase:
    index: int             # index into the positions/prices arrays given to plan_fuel
    position_mi: float
    miles_of_fuel: float   # fuel bought, expressed in miles of driving
    fuel_on_arrival_mi: float
    price: float


@dataclass
class FuelPlan:
    purchases: list[Purchase] = field(default_factory=list)
    mpg: float = 10.0

    @property
    def total_gallons(self) -> float:
        return sum(p.miles_of_fuel for p in self.purchases) / self.mpg

    @property
    def total_cost(self) -> float:
        return sum(p.miles_of_fuel / self.mpg * p.price for p in self.purchases)


def _drive(after: np.ndarray, leg: int) -> np.ndarray:
    """Fuel levels after driving ``leg`` units: level t on arrival needs t + leg on departure."""
    arrive = np.full_like(after, np.inf)
    if leg < len(after):
        arrive[: len(after) - leg] = after[leg:]
    return arrive


def _largest_gap(pos: list[float], total: float) -> tuple[float, float]:
    points = [0.0, *pos, total]
    i = max(range(len(points) - 1), key=lambda k: points[k + 1] - points[k])
    return points[i], points[i + 1]


def plan_fuel(
    positions: list[float],
    prices: list[float],
    total_miles: float,
    max_range: float = 500.0,
    mpg: float = 10.0,
    start_full: bool = False,
    origin_window: float = 25.0,
    stop_penalty: float = 0.0,
    unit_miles: float = 1.0,
) -> FuelPlan:
    """``positions`` must be sorted ascending and lie within [0, total_miles]."""
    n = len(positions)
    # A hair of penalty breaks cost ties in favour of fewer stops (no 0.01-gal "stops").
    stop_penalty = max(stop_penalty, 1e-4)
    # Rounding positions to whole units can understate a leg by < 1 unit, so the DP plans
    # with one unit of reserve; _settle() then sizes purchases from the exact distances.
    cap = int(max_range / unit_miles) - 1
    levels = np.arange(cap + 1)
    q = [int(round(p / unit_miles)) for p in positions]           # station positions in units
    dest = max(int(round(total_miles / unit_miles)), q[-1] if q else 0)
    unit_price = [p * unit_miles / mpg for p in prices]           # $ per unit of fuel
    window = min(max(origin_window, positions[0]) / unit_miles, cap) if n else 0

    after = np.full(cap + 1, np.inf)
    if start_full:
        after[cap] = 0.0
    choice = np.zeros((n, cap + 1), dtype=np.int8)
    source = np.zeros((n, cap + 1), dtype=np.int32)

    prev = 0
    for i in range(n):
        arrive = _drive(after, q[i] - prev)
        c = unit_price[i]

        # Stop and buy: cheapest arrival level f <= t, found with a running (arg)min.
        vals = arrive - levels * c
        run_min = np.minimum.accumulate(vals)
        arg = np.maximum.accumulate(np.where(vals <= run_min, levels, 0))
        buy = run_min + levels * c + stop_penalty

        after = arrive.copy()
        better = buy < after
        after[better] = buy[better]
        choice[i, better] = BUY
        source[i, better] = arg[better]

        # Empty-tank start: begin the trip by filling up here (approach billed at this price).
        if not start_full and q[i] <= window:
            entry = (q[i] + levels) * c + stop_penalty
            better = entry < after
            after[better] = entry[better]
            choice[i, better] = ENTRY
        prev = q[i]

    final = _drive(after, dest - prev)
    if not np.isfinite(final).any():
        if not start_full and n == 0:
            raise InfeasibleRoute(0.0, total_miles, max_range)
        raise InfeasibleRoute(*_largest_gap(list(positions), total_miles), max_range)

    # Backtrack from the destination to recover the purchases.
    purchases: list[Purchase] = []
    final_level = int(np.argmin(final))
    level = final_level + (dest - prev)
    for i in range(n - 1, -1, -1):
        kind = choice[i, level]
        if kind == BUY:
            arrival = int(source[i, level])
            purchases.append(Purchase(i, positions[i], (level - arrival) * unit_miles,
                                      arrival * unit_miles, prices[i]))
            level = arrival
        elif kind == ENTRY:
            purchases.append(Purchase(i, positions[i], (level + q[i]) * unit_miles, 0.0, prices[i]))
            break
        level += q[i] - (q[i - 1] if i else 0)
    purchases.reverse()
    _settle(purchases, list(positions), total_miles, max_range, start_full, final_level * unit_miles)
    return FuelPlan(purchases, mpg)


def _settle(purchases: list[Purchase], positions, total_miles, max_range, start_full, final_arrival):
    """Re-size the chosen purchases using exact (unrounded) distances.

    Keeps the DP's intent -- which stations, and how much fuel to arrive at each with --
    while making every leg exactly feasible.
    """
    if not purchases:
        return
    first = purchases[0]
    fuel = max_range - first.position_mi if start_full else 0.0
    for k, p in enumerate(purchases):
        if k + 1 < len(purchases):
            nxt_pos, nxt_arrival = purchases[k + 1].position_mi, purchases[k + 1].fuel_on_arrival_mi
        else:
            nxt_pos, nxt_arrival = total_miles, final_arrival
        leg = nxt_pos - p.position_mi
        depart = min(max(leg + nxt_arrival, fuel), max_range)
        p.fuel_on_arrival_mi = fuel
        p.miles_of_fuel = depart - fuel
        if k == 0 and not start_full:
            p.miles_of_fuel += p.position_mi  # approach to the first stop, billed here
        fuel = depart - leg
