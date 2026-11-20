import numpy as np

SIX_TEAM_PRIORITIES = (
    ((0, 5), (1, 4), (2, 3)), ((0, 5), (1, 3), (2, 4)), ((0, 4), (1, 5), (2, 3)), ((0, 4), (1, 3), (2, 5)),
    ((0, 3), (1, 5), (2, 4)), ((0, 3), (1, 4), (2, 5)), ((0, 5), (1, 2), (3, 4)), ((0, 4), (1, 2), (3, 5)),
    ((0, 2), (1, 5), (3, 4)), ((0, 2), (1, 4), (3, 5)), ((0, 3), (1, 2), (4, 5)), ((0, 2), (1, 3), (4, 5)),
    ((0, 1), (2, 5), (3, 4)), ((0, 1), (2, 4), (3, 5)), ((0, 1), (2, 3), (4, 5)),
)


class StageMatch:
    __slots__ = ("round_number", "a", "b", "best_of", "record", "winner")

    def __init__(self, round_number, a, b, best_of, record):
        self.round_number = round_number
        self.a = a
        self.b = b
        self.best_of = best_of
        self.record = record
        self.winner = -1


class SwissStage:
    def __init__(self, team_count=16, first_round=(), all_bo3=False):
        self.team_count = team_count
        self.first_round = first_round
        self.all_bo3 = all_bo3
        self.round_number = 0
        self.wins = [0] * team_count
        self.losses = [0] * team_count
        self.opponents = [set() for _ in range(team_count)]
        self.matches = []
        self.current = []

    def is_finished(self):
        if any(entry.winner < 0 for entry in self.current):
            return False
        return all(self.wins[i] >= 3 or self.losses[i] >= 3 for i in range(self.team_count))

    def next_round(self, known=()):
        pending = [entry for entry in self.current if entry.winner < 0]
        if pending:
            return pending
        self.round_number += 1
        self.current = []
        snapshot_wins = list(self.wins)
        snapshot_losses = list(self.losses)
        fixed = set()
        for a, b in known:
            fixed.update((a, b))
            self._add_match(a, b, (self.wins[a], self.losses[a]))
        groups = {}
        for i in range(self.team_count):
            if self.wins[i] < 3 and self.losses[i] < 3 and i not in fixed:
                groups.setdefault((self.wins[i], self.losses[i]), []).append(i)
        for key in sorted(groups, key=lambda record: (-record[0], record[1])):
            group = groups[key]
            if self.round_number == 1 and not fixed:
                pairs = self.first_round or [(i, i + self.team_count // 2) for i in range(self.team_count // 2)]
            else:
                group.sort(key=lambda team: (-self._difficulty(team, snapshot_wins, snapshot_losses), team))
                pairs = self._pair(group)
            for a, b in pairs:
                self._add_match(a, b, key)
        return list(self.current)

    def _add_match(self, a, b, key):
        best_of = 3 if self.all_bo3 or key[0] == 2 or key[1] == 2 else 1
        entry = StageMatch(self.round_number, a, b, best_of, key)
        self.current.append(entry)
        self.matches.append(entry)

    def report(self, entry, winner):
        entry.winner = winner
        loser = entry.b if winner == entry.a else entry.a
        self.wins[winner] += 1
        self.losses[loser] += 1
        self.opponents[entry.a].add(entry.b)
        self.opponents[entry.b].add(entry.a)

    def _difficulty(self, team, snapshot_wins, snapshot_losses):
        return sum(snapshot_wins[opponent] - snapshot_losses[opponent] for opponent in self.opponents[team])

    def _pair(self, group):
        if len(group) == 6 and self.round_number >= 4:
            for pattern in SIX_TEAM_PRIORITIES:
                if all(group[j] not in self.opponents[group[i]] for i, j in pattern):
                    return [(group[i], group[j]) for i, j in pattern]
        pairs = []
        taken = set()
        for i, a in enumerate(group):
            if a in taken:
                continue
            for j in range(len(group) - 1, i, -1):
                b = group[j]
                if b in taken or b in self.opponents[a]:
                    continue
                pairs.append((a, b))
                taken.update((a, b))
                break
        leftover = [team for team in group if team not in taken]
        pairs.extend((leftover[k], leftover[k + 1]) for k in range(0, len(leftover) - 1, 2))
        return pairs


def rounds_from_matches(matches, index):
    rounds = []
    for match in matches:
        number = match["record"][0] + match["record"][1]
        while len(rounds) <= number:
            rounds.append([])
        winner = index[match["winner"]] if match.get("winner") is not None else -1
        rounds[number].append((index[match["a"]], index[match["b"]], winner))
    return rounds


def simulate(team_count, tables, rounds=(), all_bo3=False, sims=20000, seed=1):
    rng = np.random.default_rng(seed)
    wins = np.zeros((sims, team_count), dtype=np.int8)
    losses = np.zeros((sims, team_count), dtype=np.int8)
    bo1 = tables[1].tolist() if 1 in tables else None
    bo3 = tables[3].tolist()
    for s in range(sims):
        stage = SwissStage(team_count, (), all_bo3)
        round_index = 0
        draws = rng.random(40).tolist()
        while not stage.is_finished():
            known = rounds[round_index] if round_index < len(rounds) else ()
            decided = {(a, b): winner for a, b, winner in known}
            for entry in stage.next_round([(a, b) for a, b, _ in known]):
                winner = decided.get((entry.a, entry.b), -1)
                if winner < 0:
                    table = bo3 if entry.best_of >= 3 or bo1 is None else bo1
                    winner = entry.a if draws.pop() < table[entry.a][entry.b] else entry.b
                stage.report(entry, winner)
            round_index += 1
        wins[s] = stage.wins
        losses[s] = stage.losses
    return wins, losses
