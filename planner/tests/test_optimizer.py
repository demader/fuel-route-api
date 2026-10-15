import random

from django.test import SimpleTestCase

from planner.services.optimizer import InfeasibleRoute, plan_fuel

from .reference import greedy_cost

RANGE, MPG = 500, 10


def simulate(plan, positions, total, start_full):
    """Replay a plan and assert the tank never goes negative or overflows."""
    stops = {p.index: p for p in plan.purchases}
    if start_full:
        begin, fuel, last = 0, float(RANGE), 0.0
    else:
        # Empty-tank start: the approach to the first stop is billed at that stop, so the
        # vehicle "owes" those miles on arrival and the first purchase covers them.
        begin = plan.purchases[0].index
        fuel, last = -positions[begin], positions[begin]
    for i in range(begin, len(positions)):
        fuel -= positions[i] - last
        last = positions[i]
        if not (i == begin and not start_full):
            assert fuel >= -1e-6, f"ran dry before station {i}"
        if i in stops:
            fuel += stops[i].miles_of_fuel
            assert -1e-6 <= fuel <= RANGE + 1e-6, f"bad fill at station {i}: {fuel}"
    assert fuel - (total - last) >= -1e-6, "ran dry before destination"


class OptimizerTests(SimpleTestCase):
    def test_matches_reference_greedy_without_penalty(self):
        # The DP plans with a 1-mile reserve, so its cost lies between the true optimum
        # for a 500-mile tank and for a 499-mile tank.
        rng = random.Random(42)
        checked = 0
        for _ in range(400):
            total = rng.randint(50, 3000)
            positions = sorted(rng.randint(0, total) for _ in range(rng.randint(1, 120)))
            prices = [round(rng.uniform(2.5, 4.5), 3) for _ in positions]
            start_full = rng.random() < 0.3
            best = greedy_cost(positions, prices, total, RANGE, MPG, start_full, 25)
            with_reserve = greedy_cost(positions, prices, total, RANGE - 1, MPG, start_full, 25)
            if with_reserve is None:
                with self.assertRaises(InfeasibleRoute):
                    plan_fuel(positions, prices, total, start_full=start_full, stop_penalty=0)
                continue
            plan = plan_fuel(positions, prices, total, start_full=start_full, stop_penalty=0)
            self.assertGreaterEqual(plan.total_cost, best - 0.01)
            self.assertLessEqual(plan.total_cost, with_reserve + 0.01)
            simulate(plan, positions, total, start_full)
            checked += 1
        self.assertGreater(checked, 300)

    def test_fractional_positions_are_exactly_feasible(self):
        rng = random.Random(11)
        for _ in range(200):
            total = rng.uniform(100, 3000)
            positions = sorted(rng.uniform(0, total) for _ in range(rng.randint(5, 300)))
            prices = [rng.uniform(2.8, 3.8) for _ in positions]
            start_full = rng.random() < 0.3
            try:
                plan = plan_fuel(positions, prices, total, start_full=start_full, stop_penalty=rng.choice([0, 5]))
            except InfeasibleRoute:
                continue
            simulate(plan, positions, total, start_full)
            if not start_full:
                self.assertAlmostEqual(plan.total_gallons, total / MPG, delta=0.1)

    def test_empty_start_buys_fuel_for_whole_trip(self):
        positions = [0, 300, 600, 900]
        plan = plan_fuel(positions, [3.0, 3.5, 2.5, 3.2], 1100, stop_penalty=5)
        self.assertAlmostEqual(plan.total_gallons, 110.0)
        simulate(plan, positions, 1100, start_full=False)

    def test_buys_more_at_cheap_station_to_skip_expensive_one(self):
        # Cheap at 0, expensive at 400, destination at 450: fill enough at 0 to never stop at 400.
        plan = plan_fuel([0, 400], [2.0, 5.0], 450, stop_penalty=0)
        self.assertEqual([p.index for p in plan.purchases], [0])
        self.assertAlmostEqual(plan.total_cost, 45 * 2.0)

    def test_full_tank_short_trip_needs_no_stops(self):
        plan = plan_fuel([100, 200], [3.0, 3.0], 450, start_full=True)
        self.assertEqual(plan.purchases, [])
        self.assertEqual(plan.total_cost, 0)

    def test_full_tank_long_trip_only_tops_up(self):
        plan = plan_fuel([100, 450, 700], [3.0, 2.0, 3.0], 900, start_full=True, stop_penalty=0)
        self.assertAlmostEqual(plan.total_gallons, 40.0)  # 900 mi - 500 mi already in the tank
        self.assertEqual(plan.purchases[0].index, 1)    # tops up at the cheapest station

    def test_stop_penalty_reduces_stops(self):
        rng = random.Random(3)
        positions = sorted(rng.uniform(0, 2800) for _ in range(400))
        prices = [rng.uniform(2.9, 3.4) for _ in positions]
        cheapest = plan_fuel(positions, prices, 2800, stop_penalty=0)
        practical = plan_fuel(positions, prices, 2800, stop_penalty=10)
        self.assertLess(len(practical.purchases), len(cheapest.purchases))
        self.assertGreaterEqual(practical.total_cost, cheapest.total_cost - 1e-6)
        simulate(practical, positions, 2800, start_full=False)

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(InfeasibleRoute) as ctx:
            plan_fuel([0, 100, 700], [3, 3, 3], 900)
        self.assertEqual((ctx.exception.gap_start_mi, ctx.exception.gap_end_mi), (100, 700))

    def test_no_stations_is_infeasible_from_empty(self):
        with self.assertRaises(InfeasibleRoute):
            plan_fuel([], [], 50)

    def test_first_station_beyond_one_tank_is_infeasible(self):
        with self.assertRaises(InfeasibleRoute):
            plan_fuel([564, 880], [3.0, 3.0], 1000)
