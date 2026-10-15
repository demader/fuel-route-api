"""Independent reference solver used only by the tests to cross-check the DP optimizer.

Classic greedy for the fractional gas-station problem (provably optimal when there is no
per-stop penalty): at each station, if a cheaper station is within range buy just enough
to reach it; otherwise fill up (or buy just enough to finish) and go to the cheapest
station in range.
"""
from bisect import bisect_right


def greedy_cost(pos, prices, total, max_range, mpg, start_full, origin_window):
    n = len(pos)

    def run(start, fuel):
        cost, i = 0.0, start
        while True:
            reach = pos[i] + max_range
            j = next((k for k in range(i + 1, n) if pos[k] <= reach and prices[k] < prices[i]), None)
            if j is not None:
                buy = max(0.0, pos[j] - pos[i] - fuel)
                fuel += buy - (pos[j] - pos[i])
                cost += buy * prices[i]
                i = j
            elif total <= reach:
                return cost + max(0.0, total - pos[i] - fuel) * prices[i]
            else:
                hi = bisect_right(pos, reach)
                if hi <= i + 1:
                    return None
                k = min(range(i + 1, hi), key=lambda s: (prices[s], -pos[s]))
                cost += (max_range - fuel) * prices[i]
                fuel = max_range - (pos[k] - pos[i])
                i = k

    if start_full:
        if total <= max_range:
            return 0.0
        if not n or pos[0] > max_range:
            return None
        c = run(0, max_range - pos[0])
        return None if c is None else c / mpg
    window = min(max(origin_window, pos[0]), max_range) if n else 0
    best = None
    for s in range(bisect_right(pos, window)):
        c = run(s, 0.0)
        if c is not None:
            c += pos[s] * prices[s]
            best = c if best is None else min(best, c)
    return None if best is None else best / mpg
