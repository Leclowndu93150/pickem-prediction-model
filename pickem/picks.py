import numpy as np

RECORDS = ("3-0", "3-1", "3-2", "2-3", "1-3", "0-3")
KINDS = (0, 0, 1, 1, 2, 2, 2, 2, 2, 2)
SLOT_NAMES = ("three_zero", "zero_three", "advance")
ADVANCE_INCLUDES_3_0 = False


def hit_table(wins, losses):
    three_zero = (wins == 3) & (losses == 0)
    zero_three = (wins == 0) & (losses == 3)
    advance = (wins == 3) & ((losses > 0) | ADVANCE_INCLUDES_3_0)
    return np.stack([three_zero, zero_three, advance]).astype(np.int16)


def record_odds(wins, losses):
    sims, count = wins.shape
    out = []
    for team in range(count):
        odds = {}
        for record in RECORDS:
            w, l = (int(x) for x in record.split("-"))
            odds[record] = float(((wins[:, team] == w) & (losses[:, team] == l)).mean())
        odds["advance"] = odds["3-0"] + odds["3-1"] + odds["3-2"]
        out.append(odds)
    return out


def _climb(hits, picks, goal):
    count = hits.shape[2]
    score = sum(hits[KINDS[slot], :, picks[slot]] for slot in range(len(KINDS))).astype(np.int16)
    current = float((score >= goal).mean())
    improved = True
    while improved:
        improved = False
        for slot, kind in enumerate(KINDS):
            base = score - hits[kind, :, picks[slot]]
            for team in range(count):
                if team in picks:
                    continue
                rate = float((base + hits[kind, :, team] >= goal).mean())
                if rate > current + 1e-9:
                    picks[slot] = team
                    score = base + hits[kind, :, team]
                    base = score - hits[kind, :, team]
                    current = rate
                    improved = True
        for first in range(len(KINDS)):
            for second in range(first + 1, len(KINDS)):
                if KINDS[first] == KINDS[second]:
                    continue
                a, b = picks[first], picks[second]
                swapped = score - hits[KINDS[first], :, a] - hits[KINDS[second], :, b] + hits[KINDS[first], :, b] + hits[KINDS[second], :, a]
                rate = float((swapped >= goal).mean())
                if rate > current + 1e-9:
                    picks[first], picks[second] = b, a
                    score = swapped
                    current = rate
                    improved = True
    return picks, score, current


def best_picks(wins, losses, goal=5, restarts=24, seed=1):
    hits = hit_table(wins, losses)
    count = hits.shape[2]
    rates = hits.mean(axis=1)
    greedy = []
    for kind in KINDS:
        order = np.argsort(-rates[kind], kind="stable")
        greedy.append(next(int(team) for team in order if int(team) not in greedy))
    rng = np.random.default_rng(seed)
    starts = [greedy] + [rng.permutation(count)[:len(KINDS)].tolist() for _ in range(restarts)]
    best = None
    for start in starts:
        picks, score, rate = _climb(hits, list(start), goal)
        if best is None or rate > best[2] + 1e-9:
            best = (picks, score, rate)
    picks, score, rate = best
    distribution = np.bincount(score, minlength=len(KINDS) + 1) / len(score)
    return {
        "three_zero": sorted(picks[0:2]), "zero_three": sorted(picks[2:4]), "advance": sorted(picks[4:]),
        "p_goal": rate, "expected_correct": float(score.mean()), "distribution": distribution.tolist(),
    }


def evaluate(wins, losses, picks, goal=5):
    hits = hit_table(wins, losses)
    score = np.zeros(wins.shape[0], dtype=np.int16)
    for kind, name in enumerate(SLOT_NAMES):
        for team in picks.get(name, ()):
            score += hits[kind, :, team]
    return {"p_goal": float((score >= goal).mean()), "expected_correct": float(score.mean())}


def correct_picks(picks, records):
    correct = sum(1 for team in picks["three_zero"] if records.get(team) == (3, 0))
    correct += sum(1 for team in picks["zero_three"] if records.get(team) == (0, 3))
    for team in picks["advance"]:
        won, lost = records.get(team, (0, 0))
        correct += 1 if won == 3 and (lost > 0 or ADVANCE_INCLUDES_3_0) else 0
    return correct


def seed_picks(order):
    return {"three_zero": list(order[:2]), "advance": list(order[2:8]), "zero_three": list(order[-2:])}
