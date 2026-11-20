def bracket(seeds, tables, final_best_of=5):
    series = tables[3]
    final = tables.get(final_best_of, tables[3])
    quarter = {}
    for m in range(4):
        a, b = seeds[2 * m], seeds[2 * m + 1]
        quarter[a] = float(series[a][b])
        quarter[b] = 1.0 - quarter[a]
    semi = {}
    halves = []
    for k in range(2):
        left = [seeds[4 * k], seeds[4 * k + 1]]
        right = [seeds[4 * k + 2], seeds[4 * k + 3]]
        halves.append((left, right))
        for mine, theirs in ((left, right), (right, left)):
            for t in mine:
                semi[t] = quarter[t] * sum(quarter[u] * float(series[t][u]) for u in theirs)
    top, bottom = seeds[:4], seeds[4:8]
    champion = {}
    for mine, theirs in ((top, bottom), (bottom, top)):
        for t in mine:
            champion[t] = semi[t] * sum(semi[u] * float(final[t][u]) for u in theirs)
    score_semi = {}
    for left, right in halves:
        for mine, theirs in ((left, right), (right, left)):
            other_best = max(quarter[theirs[0]], quarter[theirs[1]])
            for t in mine:
                score_semi[t] = semi[t] + quarter[t] + other_best
    best_champion = None
    best_total = -1.0
    for mine, theirs in ((top, bottom), (bottom, top)):
        other_best = max(score_semi[u] for u in theirs)
        for t in mine:
            total = champion[t] + score_semi[t] + other_best
            if total > best_total:
                best_total = total
                best_champion = t
    finalists = [best_champion if best_champion in half else max(half, key=lambda t: score_semi[t]) for half in (top, bottom)]
    quarter_winners = []
    for m in range(4):
        pair = (seeds[2 * m], seeds[2 * m + 1])
        pick = pair[0] if quarter[pair[0]] >= quarter[pair[1]] else pair[1]
        for finalist in finalists:
            if finalist in pair:
                pick = finalist
        quarter_winners.append(pick)
    return {
        "odds": {t: {"quarter": quarter[t], "semi": semi[t], "champion": champion[t]} for t in seeds},
        "champion": best_champion, "finalists": finalists, "quarter_winners": quarter_winners, "expected_correct": best_total,
    }
