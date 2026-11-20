#include "engine.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace cs2 {

const WeaponInfo WEAPONS[WEAPON_COUNT] = {
	{"glock", 200, 300, 0.0, KIND_PISTOL},
	{"usp", 200, 300, 0.05, KIND_PISTOL},
	{"p250", 300, 300, 0.25, KIND_PISTOL},
	{"tec9", 500, 300, 0.35, KIND_PISTOL},
	{"fiveseven", 500, 300, 0.35, KIND_PISTOL},
	{"deagle", 700, 300, 0.45, KIND_PISTOL},
	{"mac10", 1050, 600, 0.6, KIND_SMG},
	{"mp9", 1250, 600, 0.62, KIND_SMG},
	{"ump45", 1200, 600, 0.62, KIND_SMG},
	{"xm1014", 2000, 600, 0.6, KIND_SHOTGUN},
	{"galil", 1800, 300, 0.85, KIND_RIFLE},
	{"famas", 1950, 300, 0.85, KIND_RIFLE},
	{"ak47", 2700, 300, 1.0, KIND_RIFLE},
	{"m4a1s", 2900, 300, 1.0, KIND_RIFLE},
	{"ssg08", 1700, 300, 0.75, KIND_SNIPER},
	{"awp", 4750, 100, 1.3, KIND_SNIPER},
};

const char *BUY_NAMES[5] = {"pistol", "eco", "force", "half", "full"};
const char *PLAN_NAMES[4] = {"default", "execute", "rush", "fake"};
const char *REASON_NAMES[5] = {"", "elimination", "detonation", "defuse", "time"};

namespace {

constexpr int HALF_ROUNDS = 12;
constexpr int ROUNDS_TO_WIN = 13;
constexpr int OVERTIME_HALF_ROUNDS = 3;
constexpr int OVERTIME_ROUNDS_TO_WIN = 4;
constexpr int START_MONEY = 800;
constexpr int MAX_MONEY = 16000;
constexpr int OVERTIME_MONEY = 12500;
constexpr int WIN_ELIMINATION = 3250;
constexpr int WIN_DETONATION = 3500;
constexpr int WIN_DEFUSE = 3500;
constexpr int WIN_TIME = 3250;
constexpr int LOSS_BONUS_BASE = 1400;
constexpr int LOSS_BONUS_STEP = 500;
constexpr int LOSS_STREAK_START = 1;
constexpr int LOSS_STREAK_MAX = 4;
constexpr int PLANT_TEAM_BONUS = 600;
constexpr int PLANT_REWARD = 300;
constexpr int DEFUSE_REWARD = 300;
constexpr int CT_TEAM_KILL_BONUS = 50;
constexpr double ROUND_TIME = 115.0;
constexpr double BOMB_TIME = 40.0;
constexpr double DEFUSE_TIME = 10.0;
constexpr double DEFUSE_KIT_TIME = 5.0;
constexpr double PLANT_TIME = 3.2;
constexpr int KEVLAR_PRICE = 650;
constexpr int HELMET_PRICE = 1000;
constexpr int HELMET_UPGRADE_PRICE = 350;
constexpr int KIT_PRICE = 400;
constexpr int SMOKE_PRICE = 300;
constexpr int FLASH_PRICE = 200;
constexpr int HE_PRICE = 300;
constexpr int MOLOTOV_PRICE = 400;
constexpr int INCENDIARY_PRICE = 500;
constexpr int MAX_GRENADES = 4;
constexpr int MAX_FLASHES = 2;

int other_side(int side) {
	return side == CT ? T : CT;
}

int loss_bonus(int streak) {
	return LOSS_BONUS_BASE + LOSS_BONUS_STEP * std::clamp(streak, 0, LOSS_STREAK_MAX);
}

double snapped(double value, double step) {
	return std::floor(value / step + 0.5) * step;
}

int roundi(double value) {
	return int(std::round(value));
}

double lookup(const std::vector<std::pair<std::string, double>> &table, const std::string &key, double fallback) {
	for (const auto &entry : table) {
		if (entry.first == key) {
			return entry.second;
		}
	}
	return fallback;
}

int pick_index_weighted(Pcg &rng, const std::vector<float> &weights) {
	double total = 0.0;
	for (float weight : weights) {
		total += std::max(0.0, double(weight));
	}
	if (total <= 0.0) {
		return rng.randi_range(0, int(weights.size()) - 1);
	}
	double roll = rng.randf() * total;
	for (size_t i = 0; i < weights.size(); i++) {
		roll -= std::max(0.0, double(weights[i]));
		if (roll < 0.0) {
			return int(i);
		}
	}
	return int(weights.size()) - 1;
}

template <typename T>
void shuffle(Pcg &rng, std::vector<T> &items) {
	for (int i = int(items.size()) - 1; i > 0; i--) {
		int j = rng.randi_range(0, i);
		std::swap(items[i], items[j]);
	}
}

int rifle_for(int side) {
	return side == T ? AK47 : M4A1S;
}

int cheap_rifle_for(int side) {
	return side == T ? GALIL : FAMAS;
}

int smg_for(int side) {
	return side == T ? MAC10 : MP9;
}

double primary_rank(int weapon) {
	return weapon == NO_WEAPON ? -1.0 : WEAPONS[weapon].power;
}

int full_need(int side, const Loadout &loadout, bool wants_awp) {
	int target = wants_awp ? AWP : rifle_for(side);
	int weapon_cost = primary_rank(loadout.primary) >= primary_rank(target) - 0.01 ? 0 : WEAPONS[target].price;
	int armor_cost = 0;
	if (loadout.armor == 1) {
		armor_cost = HELMET_UPGRADE_PRICE;
	} else if (loadout.armor == 0) {
		armor_cost = HELMET_PRICE;
	}
	return weapon_cost + armor_cost + 500;
}

int decide_buy(int side, bool is_pistol_round, bool is_last_round_of_half, int opponent_needs, int loss_streak, bool won_pistol_just_now,
		bool pistol_then_lost, double rebound_force_money, const Team &team, const std::vector<int> &money, const std::vector<Loadout> &loadouts, Pcg &rng) {
	if (is_pistol_round) {
		return BUY_PISTOL;
	}
	int total = 0;
	int need = 0;
	int rifle_need = 0;
	int next_need = 0;
	for (size_t i = 0; i < money.size(); i++) {
		total += money[i];
		bool awper = team.players[i].role == ROLE_AWP;
		need += full_need(side, loadouts[i], awper);
		rifle_need += full_need(side, loadouts[i], false);
		next_need += full_need(side, Loadout::starter(side), awper);
	}
	if (total >= need || total >= rifle_need) {
		return BUY_FULL;
	}
	double average = double(total) / double(money.size());
	if (won_pistol_just_now && average >= 2500.0) {
		return BUY_HALF;
	}
	if (pistol_then_lost && average >= rebound_force_money && rng.randf() < 0.35 + 0.5 * team.force_buy_bias) {
		return BUY_FORCE;
	}
	if ((opponent_needs <= 1 || is_last_round_of_half) && average >= 1200.0) {
		return BUY_FORCE;
	}
	int next_total = total + loss_bonus(loss_streak) * int(money.size());
	if (next_total >= next_need) {
		if (average >= 3400.0 && rng.randf() < 0.25 * team.force_buy_bias) {
			return BUY_FORCE;
		}
		return BUY_ECO;
	}
	if (average >= 2000.0 && rng.randf() < 0.35 + 0.5 * team.force_buy_bias) {
		return BUY_FORCE;
	}
	return BUY_ECO;
}

bool spend(std::vector<int> &money, int i, int price) {
	if (money[i] < price) {
		return false;
	}
	money[i] -= price;
	return true;
}

void buy_armor(std::vector<int> &money, int i, Loadout &loadout, bool helmet) {
	if (loadout.armor == 0) {
		if (helmet && spend(money, i, HELMET_PRICE)) {
			loadout.armor = 2;
		} else if (spend(money, i, KEVLAR_PRICE)) {
			loadout.armor = 1;
		}
	} else if (loadout.armor == 1 && helmet && spend(money, i, HELMET_UPGRADE_PRICE)) {
		loadout.armor = 2;
	}
}

void buy_grenades(std::vector<int> &money, int i, Loadout &loadout, int side, int limit) {
	int fire = side == T ? MOLOTOV_PRICE : INCENDIARY_PRICE;
	static const int KINDS[5] = {0, 1, 2, 1, 3};
	for (int kind : KINDS) {
		if (loadout.grenade_count() >= std::min(limit, MAX_GRENADES)) {
			return;
		}
		if (kind == 0 && loadout.smokes == 0 && spend(money, i, SMOKE_PRICE)) {
			loadout.smokes += 1;
		} else if (kind == 1 && loadout.flashes < MAX_FLASHES && spend(money, i, FLASH_PRICE)) {
			loadout.flashes += 1;
		} else if (kind == 2 && loadout.molotovs == 0 && spend(money, i, fire)) {
			loadout.molotovs += 1;
		} else if (kind == 3 && loadout.hes == 0 && spend(money, i, HE_PRICE)) {
			loadout.hes += 1;
		}
	}
}

int armor_cost(const Loadout &loadout) {
	if (loadout.armor == 2) {
		return 0;
	}
	return loadout.armor == 1 ? HELMET_UPGRADE_PRICE : KEVLAR_PRICE;
}

int find_donor(const std::vector<int> &money, int i, int price, const std::vector<int> &reserve) {
	int donor = -1;
	for (int j = 0; j < int(money.size()); j++) {
		if (j == i) {
			continue;
		}
		if (money[j] - reserve[j] >= price && (donor < 0 || money[j] > money[donor])) {
			donor = j;
		}
	}
	return donor;
}

bool give_weapon(std::vector<int> &money, int i, Loadout &loadout, int weapon, const std::vector<int> &reserve) {
	if (primary_rank(loadout.primary) >= primary_rank(weapon) - 0.01) {
		return true;
	}
	int price = WEAPONS[weapon].price;
	if (money[i] - price >= armor_cost(loadout)) {
		money[i] -= price;
		loadout.primary = weapon;
		return true;
	}
	int donor = find_donor(money, i, price, reserve);
	if (donor >= 0) {
		money[donor] -= price;
		loadout.primary = weapon;
		return true;
	}
	if (spend(money, i, price)) {
		loadout.primary = weapon;
		return true;
	}
	return false;
}

void apply_buy(int buy_type, int side, std::vector<int> &money, std::vector<Loadout> &loadouts, const Team &team, Pcg &rng) {
	std::vector<int> order;
	for (int i = 0; i < int(money.size()); i++) {
		order.push_back(i);
	}
	std::stable_sort(order.begin(), order.end(), [&](int a, int b) { return money[a] < money[b]; });
	if (buy_type == BUY_ECO) {
		for (int i : order) {
			if (loadouts[i].primary == NO_WEAPON && money[i] >= 4500 && rng.randf() < 0.4 && spend(money, i, WEAPONS[DEAGLE].price)) {
				loadouts[i].pistol = DEAGLE;
			}
		}
		return;
	}
	if (buy_type == BUY_PISTOL) {
		for (int i : order) {
			Loadout &loadout = loadouts[i];
			double roll = rng.randf();
			if (roll < 0.55) {
				buy_armor(money, i, loadout, false);
			} else if (side == CT && roll < 0.75) {
				if (spend(money, i, KIT_PRICE)) {
					loadout.kit = true;
				}
				buy_grenades(money, i, loadout, side, 2);
			} else {
				if (spend(money, i, WEAPONS[P250].price)) {
					loadout.pistol = P250;
				}
				buy_grenades(money, i, loadout, side, 2);
			}
		}
		return;
	}
	int awp_index = -1;
	for (int i = 0; i < int(team.players.size()); i++) {
		if (team.players[i].role == ROLE_AWP) {
			awp_index = i;
			break;
		}
	}
	if (buy_type == BUY_FULL) {
		std::vector<int> reserve;
		for (int i = 0; i < int(money.size()); i++) {
			reserve.push_back(full_need(side, loadouts[i], i == awp_index));
		}
		for (int i : order) {
			int awp_price = WEAPONS[AWP].price;
			bool wants_awp = i == awp_index && (money[i] >= awp_price + armor_cost(loadouts[i]) || find_donor(money, i, awp_price, reserve) >= 0);
			if (!give_weapon(money, i, loadouts[i], wants_awp ? AWP : rifle_for(side), reserve)) {
				give_weapon(money, i, loadouts[i], cheap_rifle_for(side), reserve);
			}
			reserve[i] = 0;
		}
		int kits = 0;
		for (int k = int(order.size()) - 1; k >= 0; k--) {
			int i = order[k];
			Loadout &loadout = loadouts[i];
			buy_armor(money, i, loadout, true);
			if (side == CT && !loadout.kit && kits < 3 && spend(money, i, KIT_PRICE)) {
				loadout.kit = true;
				kits += 1;
			}
			buy_grenades(money, i, loadout, side, 4);
		}
		return;
	}
	std::vector<int> no_reserve(money.size(), 0);
	for (int i : order) {
		Loadout &loadout = loadouts[i];
		int cash = money[i];
		int weapon = NO_WEAPON;
		if (buy_type == BUY_FORCE && cash >= WEAPONS[cheap_rifle_for(side)].price + KEVLAR_PRICE) {
			weapon = cheap_rifle_for(side);
		} else if (cash >= WEAPONS[smg_for(side)].price + KEVLAR_PRICE) {
			weapon = smg_for(side);
		}
		if (weapon != NO_WEAPON) {
			give_weapon(money, i, loadout, weapon, no_reserve);
		} else if (loadout.primary == NO_WEAPON && cash >= WEAPONS[DEAGLE].price && spend(money, i, WEAPONS[DEAGLE].price)) {
			loadout.pistol = DEAGLE;
		}
		buy_armor(money, i, loadout, buy_type == BUY_HALF && money[i] >= HELMET_PRICE + 600);
		buy_grenades(money, i, loadout, side, buy_type == BUY_HALF ? 2 : 1);
	}
}

using Group = std::vector<Combatant *>;

class RoundSim {
public:
	Group ct;
	Group t;
	Group all;
	const Team *ct_team = nullptr;
	const Team *t_team = nullptr;
	int ct_buy = BUY_PISTOL;
	int t_buy = BUY_PISTOL;
	double ct_edge = 0.0;
	double pressure = 0.0;
	Pcg *rng = nullptr;
	const Tuning *tuning = nullptr;
	double time = 0.0;
	RoundResult result;
	std::vector<std::vector<std::pair<int, int>>> damage_taken;
	bool clutch_seen[2] = {false, false};
	Group no_flash;

	Group alive(int side) const {
		Group out;
		for (Combatant *c : side == CT ? ct : t) {
			if (c->alive) {
				out.push_back(c);
			}
		}
		return out;
	}

	int alive_count(int side) const {
		int count = 0;
		for (Combatant *c : side == CT ? ct : t) {
			if (c->alive) {
				count += 1;
			}
		}
		return count;
	}

	Line &line(const Combatant *c) {
		return result.lines[c->index];
	}

	const Team &team_of(int side) const {
		return side == CT ? *ct_team : *t_team;
	}

	double power(const Combatant *c, const Combatant *opponent) const {
		int weapon = c->loadout->active_weapon();
		double armor_factor = WEAPONS[opponent->loadout->active_weapon()].kind == KIND_PISTOL ? tuning->armor_vs_pistol : 1.0;
		double value = WEAPONS[weapon].power;
		if (weapon == AWP) {
			value += tuning->awp_skill_scale * (c->z_awp - 1.0);
		} else if (weapon == SSG08) {
			value += 0.08 * (c->z_awp - 1.0);
		}
		if (c->loadout->armor == 2) {
			value += tuning->armor_helmet * armor_factor;
		} else if (c->loadout->armor == 1) {
			value += tuning->armor_kevlar * armor_factor;
		}
		return value;
	}

	double clutch_edge(const Combatant *c) const {
		if (alive_count(c->side) != 1) {
			return 0.0;
		}
		return tuning->clutch_scale * c->z_clutch - tuning->clutch_numbers * double(alive_count(other_side(c->side)) - 1);
	}

	double headshot_chance(int weapon, const Combatant *c) const {
		Kind kind = WEAPONS[weapon].kind;
		double base = 0.52;
		if (kind == KIND_SNIPER) {
			base = 0.08;
		} else if (kind == KIND_PISTOL) {
			base = 0.6;
		} else if (kind == KIND_SMG) {
			base = 0.45;
		} else if (kind == KIND_SHOTGUN) {
			base = 0.3;
		}
		return std::clamp(base + 0.04 * c->z_aim, 0.02, 0.9);
	}

	void add_damage(const Combatant *attacker, const Combatant *victim, int amount) {
		if (amount <= 0) {
			return;
		}
		line(attacker).damage += amount;
		auto &taken = damage_taken[victim->index];
		for (auto &entry : taken) {
			if (entry.first == attacker->index) {
				entry.second += amount;
				return;
			}
		}
		taken.emplace_back(attacker->index, amount);
	}

	void chip(Combatant *attacker, Combatant *victim, float low, float high) {
		if (!victim->alive || victim->hp <= 1) {
			return;
		}
		int amount = std::min(victim->hp - 1, roundi(rng->randf_range(low, high)));
		victim->hp -= amount;
		add_damage(attacker, victim, amount);
	}

	Combatant *duel(Combatant *a, Combatant *b, double bonus_a, const Group &flash_team) {
		double logit = power(a, b) - power(b, a) + tuning->skill_scale * (a->fight - b->fight) + bonus_a;
		logit += a->side == CT ? ct_edge : -ct_edge;
		logit += tuning->hp_scale * double(a->hp - b->hp) / 100.0;
		logit += pressure * tuning->composure_scale * (a->z_composure - b->z_composure);
		logit += a->shock() - b->shock();
		logit += clutch_edge(a) - clutch_edge(b);
		Combatant *winner = rng->randf() < 1.0 / (1.0 + std::exp(-logit)) ? a : b;
		Combatant *loser = winner == a ? b : a;
		if (rng->randf() < tuning->hit_chance_on_loss) {
			int dealt = std::min(winner->hp - 1, roundi(rng->randf_range(10.0f, 80.0f)));
			if (dealt > 0) {
				winner->hp -= dealt;
				add_damage(loser, winner, dealt);
			}
		}
		kill(winner, loser, winner == a ? flash_team : no_flash);
		return winner;
	}

	void kill(Combatant *killer, Combatant *victim, const Group &flash_team) {
		int weapon = killer->loadout->active_weapon();
		add_damage(killer, victim, victim->hp);
		victim->hp = 0;
		victim->alive = false;
		Kill event;
		event.time = snapped(time, 0.1);
		event.killer = killer->index;
		event.victim = victim->index;
		event.weapon = weapon;
		event.opening = result.kills.empty();
		event.headshot = rng->randf() < headshot_chance(weapon, killer);
		if (event.opening) {
			result.first_kill_side = killer->side;
		}
		int best = 0;
		for (const auto &entry : damage_taken[victim->index]) {
			if (entry.first != killer->index && entry.second >= 40 && entry.second > best) {
				best = entry.second;
				event.assister = entry.first;
			}
		}
		if (!flash_team.empty() && rng->randf() < 0.18) {
			Group flashers;
			for (Combatant *c : flash_team) {
				if (c != killer && c->loadout->flashes > 0) {
					flashers.push_back(c);
				}
			}
			if (!flashers.empty()) {
				Combatant *flasher = flashers[rng->randi_range(0, int(flashers.size()) - 1)];
				flasher->loadout->flashes -= 1;
				event.flash_assister = flasher->index;
			}
		}
		Line &killer_line = line(killer);
		killer_line.kills += 1;
		if (event.headshot) {
			killer_line.headshots += 1;
		}
		if (event.opening) {
			killer_line.opening_kill = true;
		}
		Line &victim_line = line(victim);
		victim_line.deaths += 1;
		if (event.opening) {
			victim_line.opening_death = true;
		}
		if (event.assister >= 0) {
			result.lines[event.assister].assists += 1;
		}
		if (event.flash_assister >= 0) {
			result.lines[event.flash_assister].flash_assists += 1;
		}
		result.kills.push_back(event);
		track_clutch();
	}

	void track_clutch() {
		for (int side : {int(CT), int(T)}) {
			if (clutch_seen[side]) {
				continue;
			}
			Group mine = alive(side);
			int theirs = alive_count(other_side(side));
			if (mine.size() == 1 && theirs >= 1) {
				clutch_seen[side] = true;
				Clutch clutch;
				clutch.player = mine[0]->index;
				clutch.side = side;
				clutch.versus = theirs;
				result.clutches.push_back(clutch);
			}
		}
	}

	void attempt_trade(int victim_side, Combatant *killer, const Group &pool, double context) {
		if (!killer->alive) {
			return;
		}
		Group candidates;
		std::vector<float> weights;
		for (Combatant *c : pool) {
			if (c->alive && c->side == victim_side) {
				candidates.push_back(c);
				weights.push_back(float(1.0 + std::max(0.0, c->z_trading + 1.0)));
			}
		}
		if (candidates.empty()) {
			return;
		}
		Combatant *trader = candidates[pick_index_weighted(*rng, weights)];
		const Team &team = team_of(victim_side);
		double chance = std::clamp((tuning->trade_base + 0.05 * trader->z_trading + 0.25 * (team.chemistry - 0.5)) * context, 0.05, 0.85);
		if (rng->randf() >= chance) {
			return;
		}
		time += rng->randf_range(0.5f, 2.5f);
		int victim = result.kills.back().victim;
		size_t before = result.kills.size();
		Combatant *winner = duel(trader, killer, tuning->trade_bonus, no_flash);
		if (winner == trader && result.kills.size() > before) {
			result.kills.back().trade = true;
			result.lines[victim].traded = true;
		}
	}

	int choose_plan() {
		std::vector<float> weights(t_team->plan_weights, t_team->plan_weights + 4);
		if (t_buy == BUY_ECO) {
			weights[2] *= 3.0;
			weights[1] *= 0.3;
			weights[3] *= 0.3;
		} else if (t_buy == BUY_PISTOL) {
			weights[2] *= 1.6;
		}
		return pick_index_weighted(*rng, weights);
	}

	void setup_ct() {
		std::vector<int> zones = {SITE_A, SITE_A, MID, SITE_B, SITE_B};
		if (rng->randf() < ct_team->ct_stack_chance) {
			int stacked = rng->randf() < 0.5 ? SITE_A : SITE_B;
			int other = stacked == SITE_A ? SITE_B : SITE_A;
			zones = {stacked, stacked, stacked, MID, other};
		}
		shuffle(*rng, zones);
		Group order = ct;
		shuffle(*rng, order);
		for (size_t i = 0; i < order.size(); i++) {
			order[i]->zone = zones[i % zones.size()];
		}
		Combatant *awper = nullptr;
		for (Combatant *c : ct) {
			if (c->loadout->primary == AWP) {
				awper = c;
			}
		}
		if (awper != nullptr && awper->zone != MID && rng->randf() < 0.45) {
			for (Combatant *c : ct) {
				if (c->zone == MID) {
					c->zone = awper->zone;
					awper->zone = MID;
					break;
				}
			}
		}
	}

	double hit_time(int plan) {
		if (plan == PLAN_RUSH) {
			return rng->randf_range(12.0f, 26.0f);
		}
		if (plan == PLAN_EXECUTE) {
			return rng->randf_range(32.0f, 60.0f);
		}
		if (plan == PLAN_FAKE) {
			return rng->randf_range(45.0f, 75.0f);
		}
		return rng->randf_range(55.0f, 105.0f);
	}

	double utility_score(const Group &players, const Team &team) const {
		if (players.empty()) {
			return 0.0;
		}
		int grenades = 0;
		double skill = 0.0;
		for (const Combatant *c : players) {
			grenades += c->loadout->smokes + c->loadout->flashes + c->loadout->molotovs;
			skill += c->z_utility;
		}
		return std::min(1.0, grenades / 9.0) * (1.0 + 0.15 * skill / double(players.size())) * (0.75 + 0.5 * team.chemistry);
	}

	void spend_utility(const Group &players, double share) {
		for (Combatant *c : players) {
			if (c->loadout->smokes > 0 && rng->randf() < share) {
				c->loadout->smokes -= 1;
			}
			if (c->loadout->flashes > 0 && rng->randf() < share) {
				c->loadout->flashes -= 1;
			}
			if (c->loadout->molotovs > 0 && rng->randf() < share) {
				c->loadout->molotovs -= 1;
			}
		}
	}

	void damage_utility(const Group &throwers, const Group &targets) {
		if (targets.empty()) {
			return;
		}
		for (Combatant *c : throwers) {
			if (!c->alive) {
				continue;
			}
			if (c->loadout->molotovs > 0 && rng->randf() < 0.45) {
				chip(c, targets[rng->randi_range(0, int(targets.size()) - 1)], 8.0f, 40.0f);
			}
			if (c->loadout->hes > 0) {
				c->loadout->hes -= 1;
				if (rng->randf() < 0.55) {
					chip(c, targets[rng->randi_range(0, int(targets.size()) - 1)], 10.0f, 55.0f);
				}
			}
		}
	}

	void early_picks(int plan) {
		int count = 1;
		if (plan == PLAN_RUSH) {
			count = 0;
		} else if (plan == PLAN_EXECUTE) {
			count = rng->randf() < 0.45 ? 1 : 0;
		} else if (plan == PLAN_DEFAULT) {
			count = rng->randf() < 0.55 ? 2 : 1;
		}
		if (ct_buy == BUY_ECO && rng->randf() < 0.4) {
			count += 1;
		}
		for (int i = 0; i < count; i++) {
			Group ts = alive(T);
			Group cts = alive(CT);
			if (ts.empty() || cts.empty()) {
				return;
			}
			std::vector<float> t_weights;
			for (Combatant *c : ts) {
				t_weights.push_back(float(1.2 + c->z_entry + (c->player->role == ROLE_LURKER ? 0.8 : 0.0) + t_team->aggression));
			}
			std::vector<float> ct_weights;
			for (Combatant *c : cts) {
				ct_weights.push_back(float((c->zone == MID ? 2.5 : 1.0) + (c->loadout->primary == AWP ? 1.5 : 0.0)));
			}
			Combatant *aggressor = ts[pick_index_weighted(*rng, t_weights)];
			Combatant *defender = cts[pick_index_weighted(*rng, ct_weights)];
			time += rng->randf_range(6.0f, 16.0f);
			double bonus = tuning->entry_scale * aggressor->z_entry - 0.5 * tuning->entry_scale * defender->z_positioning;
			if (defender->loadout->primary == AWP) {
				bonus -= tuning->awp_hold_bonus;
			}
			Combatant *winner = duel(aggressor, defender, bonus, no_flash);
			Combatant *loser = winner == aggressor ? defender : aggressor;
			attempt_trade(loser->side, winner, alive(loser->side), 0.45);
		}
	}

	Group attackers(const Combatant *lurker) const {
		Group out;
		for (Combatant *c : t) {
			if (c->alive && c != lurker) {
				out.push_back(c);
			}
		}
		return out;
	}

	Group at_site(int site) const {
		Group out;
		for (Combatant *c : ct) {
			if (c->alive && c->zone == site) {
				out.push_back(c);
			}
		}
		return out;
	}

	bool site_fight(int plan, int site) {
		if (plan == PLAN_FAKE) {
			for (Combatant *c : alive(CT)) {
				if (c->zone == site && rng->randf() < 0.28 - 0.05 * c->z_game_sense) {
					c->zone = MID;
				}
			}
		}
		Group rotators;
		std::vector<int> arrivals;
		int delay = plan == PLAN_RUSH ? 1 : 0;
		for (Combatant *c : alive(CT)) {
			if (c->zone == site) {
				continue;
			}
			bool from_mid = c->zone == MID;
			double p = from_mid ? 0.75 : 0.45 + 0.05 * c->z_game_sense;
			if (rng->randf() < p) {
				rotators.push_back(c);
				arrivals.push_back((from_mid ? 1 : 2 + rng->randi_range(0, 1)) + delay);
			}
		}
		Combatant *lurker = nullptr;
		if (plan != PLAN_RUSH && alive_count(T) >= 3 && rng->randf() < 0.55) {
			for (Combatant *c : alive(T)) {
				if (c->player->role == ROLE_LURKER) {
					lurker = c;
					break;
				}
			}
		}
		if (lurker != nullptr) {
			for (size_t i = 0; i < rotators.size(); i++) {
				if (rotators[i]->zone != MID) {
					time += rng->randf_range(1.0f, 4.0f);
					Combatant *flank_winner = duel(lurker, rotators[i], 0.25, no_flash);
					if (flank_winner == lurker) {
						rotators.erase(rotators.begin() + i);
						arrivals.erase(arrivals.begin() + i);
					} else {
						lurker = nullptr;
					}
					break;
				}
			}
		}
		double t_util = utility_score(attackers(lurker), *t_team);
		double ct_util = utility_score(at_site(site), *ct_team);
		double plan_util = 0.6;
		if (plan == PLAN_EXECUTE || plan == PLAN_FAKE) {
			plan_util = 1.0;
		} else if (plan == PLAN_RUSH) {
			plan_util = 0.3;
		}
		double util_edge = tuning->execute_utility * (plan_util * t_util - 0.6 * ct_util);
		Group holders = at_site(site);
		damage_utility(attackers(lurker), holders);
		spend_utility(attackers(lurker), 0.55 * plan_util);
		int duel_index = 0;
		while (true) {
			for (size_t i = 0; i < rotators.size(); i++) {
				if (arrivals[i] <= duel_index && rotators[i]->alive) {
					rotators[i]->zone = site;
				}
			}
			if (lurker != nullptr && lurker->alive && duel_index >= 3) {
				lurker = nullptr;
			}
			if (alive_count(T) == 0 || alive_count(CT) == 0) {
				return false;
			}
			Group current_attackers = attackers(lurker);
			Group defenders = at_site(site);
			if (current_attackers.empty()) {
				return false;
			}
			if (defenders.empty()) {
				return true;
			}
			if (time >= ROUND_TIME - PLANT_TIME) {
				return false;
			}
			Combatant *attacker;
			if (duel_index == 0) {
				std::vector<float> weights;
				for (Combatant *c : current_attackers) {
					weights.push_back(float(1.5 + c->z_entry + (c->player->role == ROLE_ENTRY ? 1.5 : 0.0)));
				}
				attacker = current_attackers[pick_index_weighted(*rng, weights)];
			} else {
				attacker = current_attackers[rng->randi_range(0, int(current_attackers.size()) - 1)];
			}
			Combatant *defender = defenders[rng->randi_range(0, int(defenders.size()) - 1)];
			int diff = std::clamp(int(current_attackers.size()) - int(defenders.size()), -3, 3);
			double bonus = (duel_index < 2 ? util_edge : util_edge * 0.5) + tuning->site_crossfire * diff;
			if (std::find(holders.begin(), holders.end(), defender) != holders.end()) {
				bonus -= tuning->hold_bonus;
			}
			if (duel_index == 0) {
				bonus += tuning->entry_scale * attacker->z_entry - tuning->anchor_bonus;
			}
			time += rng->randf_range(1.5f, 4.5f);
			Combatant *winner = duel(attacker, defender, bonus, current_attackers);
			Combatant *loser = winner == attacker ? defender : attacker;
			Group pool = loser->side == T ? attackers(lurker) : at_site(site);
			attempt_trade(loser->side, winner, pool, 1.0);
			duel_index += 1;
		}
	}

	bool worth_saving() const {
		return ct_buy != BUY_PISTOL && ct_buy != BUY_ECO;
	}

	bool hopeless() const {
		int cts = alive_count(CT);
		int ts = alive_count(T);
		return cts > 0 && (ts - cts >= 2 || (cts == 1 && ts >= 3));
	}

	void finish_detonation(double plant_time) {
		time = plant_time + BOMB_TIME;
		result.winner = T;
		result.reason = REASON_DETONATION;
	}

	void retake(double plant_time) {
		if (alive_count(CT) > 0 && hopeless() && worth_saving() && rng->randf() < ct_team->save_discipline) {
			finish_detonation(plant_time);
			return;
		}
		double bomb_left = BOMB_TIME - rng->randf_range(4.0f, 10.0f);
		double ct_util = utility_score(alive(CT), *ct_team);
		spend_utility(alive(CT), 0.6);
		int duel_index = 0;
		while (alive_count(CT) > 0 && alive_count(T) > 0) {
			if (bomb_left <= 0.0) {
				finish_detonation(plant_time);
				return;
			}
			Group cts = alive(CT);
			Group ts = alive(T);
			Combatant *attacker = cts[rng->randi_range(0, int(cts.size()) - 1)];
			Combatant *defender = ts[rng->randi_range(0, int(ts.size()) - 1)];
			int diff = std::clamp(int(cts.size()) - int(ts.size()), -3, 3);
			double bonus = -tuning->post_plant_bonus + (duel_index < 2 ? tuning->retake_utility * ct_util : 0.0) + tuning->crossfire_per_player * diff;
			double elapsed = rng->randf_range(2.0f, 5.0f);
			bomb_left -= elapsed;
			time += elapsed;
			Combatant *winner = duel(attacker, defender, bonus, cts);
			Combatant *loser = winner == attacker ? defender : attacker;
			attempt_trade(loser->side, winner, alive(loser->side), 0.85);
			duel_index += 1;
			if (alive_count(CT) > 0 && alive_count(T) > 0 && hopeless() && worth_saving() && rng->randf() < ct_team->save_discipline * tuning->retake_save_factor) {
				finish_detonation(plant_time);
				return;
			}
		}
		if (alive_count(T) == 0) {
			Group cts = alive(CT);
			Combatant *kit_holder = nullptr;
			for (Combatant *c : cts) {
				if (c->loadout->kit) {
					kit_holder = c;
					break;
				}
			}
			double needed = kit_holder != nullptr ? DEFUSE_KIT_TIME : DEFUSE_TIME;
			if (bomb_left >= needed) {
				Combatant *defuser = kit_holder != nullptr ? kit_holder : cts[rng->randi_range(0, int(cts.size()) - 1)];
				time += needed;
				result.winner = CT;
				result.reason = REASON_DEFUSE;
				result.defuser = defuser->index;
				return;
			}
			finish_detonation(plant_time);
			return;
		}
		result.winner = T;
		result.reason = REASON_ELIMINATION;
	}

	RoundResult run(int combatant_count) {
		result.lines.assign(combatant_count, Line());
		damage_taken.assign(combatant_count, {});
		int plan = choose_plan();
		int site = rng->randf() < 0.5 ? SITE_A : SITE_B;
		result.plan = plan;
		result.site = site;
		setup_ct();
		for (Combatant *c : t) {
			c->zone = site;
		}
		early_picks(plan);
		if (alive_count(T) == 0) {
			result.winner = CT;
			result.reason = REASON_ELIMINATION;
		} else if (alive_count(CT) == 0) {
			result.winner = T;
			result.reason = REASON_ELIMINATION;
		} else {
			time = std::max(time, hit_time(plan));
			if (alive_count(CT) - alive_count(T) >= 2) {
				time += rng->randf_range(0.5f, 1.5f) * tuning->stall_after_picks;
			}
			bool control = time < ROUND_TIME - PLANT_TIME && site_fight(plan, site);
			if (alive_count(T) == 0) {
				result.winner = CT;
				result.reason = REASON_ELIMINATION;
			} else if (alive_count(CT) == 0) {
				result.winner = T;
				result.reason = REASON_ELIMINATION;
			} else if (!control) {
				result.winner = CT;
				result.reason = REASON_TIME;
				time = ROUND_TIME;
			} else {
				result.planted = true;
				std::vector<float> weights;
				Group ts = alive(T);
				for (Combatant *c : ts) {
					weights.push_back(c->loadout->primary == AWP ? 0.3f : 1.0f);
				}
				result.planter = ts[pick_index_weighted(*rng, weights)]->index;
				time += PLANT_TIME;
				retake(time);
			}
		}
		for (Combatant *c : all) {
			if (c->alive) {
				line(c).survived = true;
			}
		}
		for (Clutch &clutch : result.clutches) {
			clutch.won = clutch.side == result.winner;
		}
		result.end_time = snapped(time, 0.1);
		return result;
	}
};

class MapRunner {
public:
	const Team *teams[2];
	std::string map_id;
	int starting_ct = 0;
	Pcg *rng = nullptr;
	const Tuning *tuning = nullptr;
	bool with_rounds = true;
	MapResult out;
	std::vector<Combatant> combatants;
	std::vector<int> team_start[2];
	std::vector<int> money[2];
	std::vector<Loadout> loadouts[2];
	int streak[2] = {LOSS_STREAK_START, LOSS_STREAK_START};
	int last_winner_team = -1;
	int prev_winner_team = -1;
	int rounds_played = 0;

	double edge() const {
		return tuning->ct_edge_base + lookup(tuning->ct_edge_by_map, map_id, 0.0);
	}

	Combatant &member(int team_index, int i) {
		return combatants[team_start[team_index][i]];
	}

	void prepare() {
		size_t total = teams[0]->players.size() + teams[1]->players.size();
		combatants.reserve(total);
		for (int team_index = 0; team_index < 2; team_index++) {
			const Team &team = *teams[team_index];
			double team_form = rng->randfn(0.0f, float(tuning->team_form_sd)) + tuning->map_skill_scale * team.skill_on(map_id);
			double shock_ct = tuning->map_shock_sd > 0.0 ? double(rng->randfn(0.0f, float(tuning->map_shock_sd))) : 0.0;
			double shock_t = tuning->map_shock_sd > 0.0 ? double(rng->randfn(0.0f, float(tuning->map_shock_sd))) : 0.0;
			for (const Player &player : team.players) {
				double sd = std::clamp(tuning->form_sd_base - 0.025 * (player.consistency - 10.5), 0.1, 0.5);
				Combatant c;
				c.player = &player;
				c.index = int(combatants.size());
				c.team = team_index;
				c.z_aim = (player.aim - 10.5) / tuning->z_spread;
				c.z_awp = (player.awp - 10.5) / tuning->z_spread;
				c.z_entry = (player.entry - 10.5) / tuning->z_spread;
				c.z_trading = (player.trading - 10.5) / tuning->z_spread;
				c.z_utility = (player.utility - 10.5) / tuning->z_spread;
				c.z_clutch = (player.clutch - 10.5) / tuning->z_spread;
				c.z_positioning = (player.positioning - 10.5) / tuning->z_spread;
				c.z_game_sense = (player.game_sense - 10.5) / tuning->z_spread;
				c.z_composure = (player.composure - 10.5) / tuning->z_spread;
				c.form = rng->randfn(0.0f, float(sd)) + team_form;
				c.fight = 0.7 * c.z_aim + 0.15 * c.z_positioning + 0.15 * c.z_game_sense + c.form;
				c.shock_ct = shock_ct;
				c.shock_t = shock_t;
				team_start[team_index].push_back(int(combatants.size()));
				combatants.push_back(c);
			}
			money[team_index].assign(team.players.size(), 0);
			loadouts[team_index].assign(team.players.size(), Loadout());
			out.stats[team_index].assign(team.players.size() * STAT_COUNT, 0);
		}
	}

	int side_of(int team_index, int round_number) const {
		int first_side = team_index == starting_ct ? CT : T;
		bool flipped = false;
		if (round_number <= HALF_ROUNDS * 2) {
			flipped = round_number > HALF_ROUNDS;
		} else {
			int ot_round = round_number - HALF_ROUNDS * 2 - 1;
			int ot_index = int(std::floor(double(ot_round) / double(OVERTIME_HALF_ROUNDS * 2)));
			bool second_half = (ot_round % (OVERTIME_HALF_ROUNDS * 2)) >= OVERTIME_HALF_ROUNDS;
			flipped = (ot_index % 2 == 0) != second_half;
		}
		return flipped ? other_side(first_side) : first_side;
	}

	int target() const {
		if (out.overtimes == 0) {
			return ROUNDS_TO_WIN;
		}
		return HALF_ROUNDS + OVERTIME_HALF_ROUNDS * (out.overtimes - 1) + OVERTIME_ROUNDS_TO_WIN;
	}

	void reset_half(int round_number) {
		int start = round_number <= HALF_ROUNDS * 2 ? START_MONEY : OVERTIME_MONEY;
		for (int team_index = 0; team_index < 2; team_index++) {
			int side = side_of(team_index, round_number);
			for (size_t i = 0; i < money[team_index].size(); i++) {
				money[team_index][i] = start;
				loadouts[team_index][i] = Loadout::starter(side);
			}
			streak[team_index] = LOSS_STREAK_START;
		}
	}

	static bool is_half_start(int round_number) {
		if (round_number == 1 || round_number == HALF_ROUNDS + 1) {
			return true;
		}
		if (round_number > HALF_ROUNDS * 2) {
			return (round_number - HALF_ROUNDS * 2 - 1) % OVERTIME_HALF_ROUNDS == 0;
		}
		return false;
	}

	static bool is_half_end(int round_number) {
		if (round_number == HALF_ROUNDS || round_number == HALF_ROUNDS * 2) {
			return true;
		}
		if (round_number > HALF_ROUNDS * 2) {
			return (round_number - HALF_ROUNDS * 2) % OVERTIME_HALF_ROUNDS == 0;
		}
		return false;
	}

	void play() {
		int round_number = 0;
		while (out.winner < 0) {
			round_number += 1;
			if (round_number == HALF_ROUNDS * 2 + 1 || (round_number > HALF_ROUNDS * 2 && (round_number - HALF_ROUNDS * 2 - 1) % (OVERTIME_HALF_ROUNDS * 2) == 0)) {
				out.overtimes += 1;
			}
			if (is_half_start(round_number)) {
				reset_half(round_number);
			}
			play_round(round_number);
			int goal = target();
			for (int team_index = 0; team_index < 2; team_index++) {
				if (out.score[team_index] >= goal) {
					out.winner = team_index;
				}
			}
		}
	}

	void play_round(int round_number) {
		RoundRecord record;
		record.number = round_number;
		int goal = target();
		int buys[2] = {BUY_PISTOL, BUY_PISTOL};
		int sides[2] = {side_of(0, round_number), side_of(1, round_number)};
		record.ct_team = sides[0] == CT ? 0 : 1;
		bool pistol = round_number == 1 || round_number == HALF_ROUNDS + 1;
		bool after_pistol = round_number == 2 || round_number == HALF_ROUNDS + 2;
		bool last_of_half = is_half_end(round_number);
		for (int team_index = 0; team_index < 2; team_index++) {
			if (with_rounds) {
				record.money_before.insert(record.money_before.end(), money[team_index].begin(), money[team_index].end());
			}
			int opponent_needs = goal - out.score[1 - team_index];
			bool won_pistol = after_pistol && rounds_played > 0 && last_winner_team == team_index;
			bool third = round_number == 3 || round_number == HALF_ROUNDS + 3;
			bool rebound = third && rounds_played >= 2 && prev_winner_team == team_index && last_winner_team != team_index;
			buys[team_index] = decide_buy(sides[team_index], pistol, last_of_half, opponent_needs, streak[team_index], won_pistol, rebound,
					tuning->rebound_force_money, *teams[team_index], money[team_index], loadouts[team_index], *rng);
			apply_buy(buys[team_index], sides[team_index], money[team_index], loadouts[team_index], *teams[team_index], *rng);
		}
		record.buys[0] = buys[0];
		record.buys[1] = buys[1];
		if (with_rounds) {
			for (int team_index = 0; team_index < 2; team_index++) {
				record.money_after.insert(record.money_after.end(), money[team_index].begin(), money[team_index].end());
			}
		}
		Group ct_list;
		Group t_list;
		for (int team_index = 0; team_index < 2; team_index++) {
			for (size_t i = 0; i < team_start[team_index].size(); i++) {
				Combatant &c = member(team_index, int(i));
				c.side = sides[team_index];
				c.alive = true;
				c.hp = 100;
				c.loadout = &loadouts[team_index][i];
				record.equipment[team_index] += c.loadout->equipment_value(c.side);
				if (c.side == CT) {
					ct_list.push_back(&c);
				} else {
					t_list.push_back(&c);
				}
			}
		}
		int ct_index = record.ct_team;
		double pressure = 0.0;
		if (out.overtimes > 0 || goal - out.score[0] <= 1 || goal - out.score[1] <= 1) {
			pressure = 1.0;
		} else if (last_of_half) {
			pressure = 0.4;
		}
		RoundSim sim;
		sim.ct = ct_list;
		sim.t = t_list;
		sim.all = ct_list;
		sim.all.insert(sim.all.end(), t_list.begin(), t_list.end());
		sim.ct_team = teams[ct_index];
		sim.t_team = teams[1 - ct_index];
		sim.ct_buy = buys[ct_index];
		sim.t_buy = buys[1 - ct_index];
		sim.ct_edge = pistol ? edge() * tuning->pistol_ct_edge_factor : edge();
		sim.pressure = pressure;
		sim.rng = rng;
		sim.tuning = tuning;
		RoundResult result = sim.run(int(combatants.size()));
		int winner_team = result.winner == CT ? ct_index : 1 - ct_index;
		out.score[winner_team] += 1;
		record.winner_team = winner_team;
		record.score_after[0] = out.score[0];
		record.score_after[1] = out.score[1];
		pay(result, winner_team, ct_index);
		carry_over(winner_team);
		record_stats(result);
		prev_winner_team = last_winner_team;
		last_winner_team = winner_team;
		rounds_played += 1;
		if (with_rounds) {
			record.result = std::move(result);
			out.rounds.push_back(std::move(record));
		}
	}

	void give(int combatant_index, int amount) {
		const Combatant &c = combatants[combatant_index];
		const std::vector<int> &starts = team_start[c.team];
		for (size_t i = 0; i < starts.size(); i++) {
			if (starts[i] == combatant_index) {
				money[c.team][i] += amount;
				return;
			}
		}
	}

	void pay(const RoundResult &result, int winner_team, int ct_index) {
		int loser_team = 1 - winner_team;
		int reward = WIN_ELIMINATION;
		if (result.reason == REASON_DETONATION) {
			reward = WIN_DETONATION;
		} else if (result.reason == REASON_DEFUSE) {
			reward = WIN_DEFUSE;
		} else if (result.reason == REASON_TIME) {
			reward = WIN_TIME;
		}
		for (int &value : money[winner_team]) {
			value += reward;
		}
		int bonus = loss_bonus(streak[loser_team]);
		for (size_t i = 0; i < money[loser_team].size(); i++) {
			const Combatant &c = member(loser_team, int(i));
			bool survived_timeout = result.reason == REASON_TIME && c.side == T && c.alive;
			if (!survived_timeout) {
				money[loser_team][i] += bonus;
			}
			if (result.planted && c.side == T) {
				money[loser_team][i] += PLANT_TEAM_BONUS;
			}
		}
		streak[loser_team] = std::min(streak[loser_team] + 1, LOSS_STREAK_MAX);
		streak[winner_team] = std::max(streak[winner_team] - 1, 0);
		if (result.planter >= 0) {
			give(result.planter, PLANT_REWARD);
		}
		if (result.defuser >= 0) {
			give(result.defuser, DEFUSE_REWARD);
		}
		int ts_killed_by_ct = 0;
		for (const Kill &kill : result.kills) {
			give(kill.killer, WEAPONS[kill.weapon].reward);
			if (combatants[kill.killer].team == ct_index) {
				ts_killed_by_ct += 1;
			}
		}
		if (ts_killed_by_ct > 0) {
			for (int &value : money[ct_index]) {
				value += CT_TEAM_KILL_BONUS * ts_killed_by_ct;
			}
		}
		for (int team_index = 0; team_index < 2; team_index++) {
			for (int &value : money[team_index]) {
				value = std::clamp(value, 0, MAX_MONEY);
			}
		}
	}

	void carry_over(int winner_team) {
		std::vector<int> dropped;
		for (int team_index = 0; team_index < 2; team_index++) {
			for (size_t i = 0; i < team_start[team_index].size(); i++) {
				Combatant &c = member(team_index, int(i));
				if (!c.alive) {
					if (c.loadout->primary != NO_WEAPON) {
						dropped.push_back(c.loadout->primary);
					}
					loadouts[team_index][i] = Loadout::starter(c.side);
				}
			}
		}
		if (dropped.empty()) {
			return;
		}
		std::stable_sort(dropped.begin(), dropped.end(), [](int a, int b) { return WEAPONS[a].power > WEAPONS[b].power; });
		for (size_t i = 0; i < team_start[winner_team].size(); i++) {
			Combatant &c = member(winner_team, int(i));
			if (dropped.empty()) {
				return;
			}
			if (!c.alive) {
				continue;
			}
			int best = dropped[0];
			if (primary_rank(c.loadout->primary) < WEAPONS[best].power - 0.01 && rng->randf() < tuning->pickup_chance) {
				c.loadout->primary = best;
				dropped.erase(dropped.begin());
			}
		}
	}

	void record_stats(const RoundResult &result) {
		for (size_t index = 0; index < combatants.size(); index++) {
			const Line &line = result.lines[index];
			const Combatant &c = combatants[index];
			int position = 0;
			for (size_t i = 0; i < team_start[c.team].size(); i++) {
				if (team_start[c.team][i] == int(index)) {
					position = int(i);
				}
			}
			int *total = &out.stats[c.team][position * STAT_COUNT];
			total[ST_ROUNDS] += 1;
			total[ST_KILLS] += line.kills;
			total[ST_DEATHS] += line.deaths;
			total[ST_ASSISTS] += line.assists;
			total[ST_FLASH_ASSISTS] += line.flash_assists;
			total[ST_DAMAGE] += line.damage;
			total[ST_HEADSHOTS] += line.headshots;
			if (line.opening_kill) {
				total[ST_OPENING_KILLS] += 1;
			}
			if (line.opening_death) {
				total[ST_OPENING_DEATHS] += 1;
			}
			if (line.kills >= 2) {
				total[ST_MULTI_KILL_ROUNDS] += 1;
			}
			if (line.kills > 0 || line.assists > 0 || line.survived || line.traded) {
				total[ST_KAST_ROUNDS] += 1;
			}
			if (line.traded) {
				total[ST_TRADED_DEATHS] += 1;
			}
		}
		for (const Clutch &clutch : result.clutches) {
			const Combatant &c = combatants[clutch.player];
			int position = 0;
			for (size_t i = 0; i < team_start[c.team].size(); i++) {
				if (team_start[c.team][i] == clutch.player) {
					position = int(i);
				}
			}
			int *total = &out.stats[c.team][position * STAT_COUNT];
			total[ST_CLUTCHES_PLAYED] += 1;
			if (clutch.won) {
				total[ST_CLUTCHES_WON] += 1;
			}
		}
	}
};

bool choose_ct(const std::string &map_id, Pcg &rng, const Tuning &tuning) {
	double ct_rate = lookup(tuning.map_ct_rates, map_id, 0.5);
	return ct_rate + rng.randfn(0.0f, 0.03f) >= 0.5;
}

}

double Team::skill_on(const std::string &map_id) const {
	return lookup(map_skill, map_id, 0.0);
}

double Team::veto_bias(const std::string &map_id) const {
	return lookup(veto_pref, map_id, 0.0);
}

Loadout Loadout::starter(int side) {
	Loadout loadout;
	loadout.pistol = side == T ? GLOCK : USP;
	return loadout;
}

int Loadout::equipment_value(int side) const {
	int value = 0;
	if (primary != NO_WEAPON) {
		value += WEAPONS[primary].price;
	}
	if (pistol != GLOCK && pistol != USP) {
		value += WEAPONS[pistol].price;
	}
	if (armor == 2) {
		value += HELMET_PRICE;
	} else if (armor == 1) {
		value += KEVLAR_PRICE;
	}
	if (kit) {
		value += KIT_PRICE;
	}
	int fire = side == T ? MOLOTOV_PRICE : INCENDIARY_PRICE;
	return value + smokes * SMOKE_PRICE + flashes * FLASH_PRICE + molotovs * fire + hes * HE_PRICE;
}

MapResult simulate_map(const Team *teams[2], const std::string &map_id, int ct_first, Pcg &rng, const Tuning &tuning, bool with_rounds) {
	MapRunner runner;
	runner.teams[0] = teams[0];
	runner.teams[1] = teams[1];
	runner.map_id = map_id;
	runner.starting_ct = ct_first;
	runner.rng = &rng;
	runner.tuning = &tuning;
	runner.with_rounds = with_rounds;
	runner.out.map_id = map_id;
	runner.out.starting_ct = ct_first;
	runner.prepare();
	runner.play();
	return std::move(runner.out);
}

VetoResult run_veto(const Team *teams[2], const std::vector<std::string> &pool, int best_of, Pcg &rng, const Tuning &tuning) {
	static const std::pair<int, const char *> BO1[6] = {{0, "remove"}, {0, "remove"}, {1, "remove"}, {1, "remove"}, {1, "remove"}, {0, "remove"}};
	static const std::pair<int, const char *> BO3[6] = {{0, "remove"}, {1, "remove"}, {0, "pick"}, {1, "pick"}, {1, "remove"}, {0, "remove"}};
	static const std::pair<int, const char *> BO5[6] = {{0, "remove"}, {1, "remove"}, {0, "pick"}, {1, "pick"}, {0, "pick"}, {1, "pick"}};
	const std::pair<int, const char *> *sequence = best_of == 1 ? BO1 : (best_of == 3 ? BO3 : BO5);
	VetoResult veto;
	std::vector<std::string> remaining = pool;
	if (remaining.empty()) {
		return veto;
	}
	for (int s = 0; s < 6 && remaining.size() > 1; s++) {
		int team = sequence[s].first;
		std::string action = sequence[s].second;
		std::string best;
		double best_score = -std::numeric_limits<double>::infinity();
		for (const std::string &map_id : remaining) {
			double score = teams[team]->skill_on(map_id) - teams[1 - team]->skill_on(map_id) + rng.randfn(0.0f, float(tuning.veto_noise * teams[team]->veto_noise_scale));
			score += tuning.veto_preference * teams[team]->veto_bias(map_id);
			if (action == "remove") {
				score = -score;
			}
			if (score > best_score) {
				best_score = score;
				best = map_id;
			}
		}
		remaining.erase(std::find(remaining.begin(), remaining.end(), best));
		VetoStep step;
		step.team = team;
		step.action = action;
		step.map_id = best;
		if (action == "pick") {
			int chooser = 1 - team;
			step.side_chooser = chooser;
			bool ct = choose_ct(best, rng, tuning);
			step.chosen_ct = ct ? chooser : team;
			veto.maps.push_back(best);
			veto.ct_first.push_back(step.chosen_ct);
		}
		veto.steps.push_back(step);
	}
	VetoStep decider;
	decider.team = -1;
	decider.action = "decider";
	decider.map_id = remaining[0];
	decider.side_chooser = 1;
	decider.chosen_ct = choose_ct(remaining[0], rng, tuning) ? 1 : 0;
	veto.steps.push_back(decider);
	veto.maps.push_back(remaining[0]);
	veto.ct_first.push_back(decider.chosen_ct);
	return veto;
}

SeriesResult simulate_series(const Team *teams[2], int best_of, const std::vector<std::string> &pool, Pcg &rng, const Tuning &tuning, bool with_rounds) {
	SeriesResult series;
	series.veto = run_veto(teams, pool, best_of, rng, tuning);
	int needed = int(std::ceil(best_of / 2.0));
	for (size_t i = 0; i < series.veto.maps.size(); i++) {
		MapResult map = simulate_map(teams, series.veto.maps[i], series.veto.ct_first[i], rng, tuning, with_rounds);
		int map_winner = map.winner;
		series.maps.push_back(std::move(map));
		series.score[map_winner] += 1;
		if (series.score[map_winner] >= needed) {
			series.winner = map_winner;
			break;
		}
	}
	return series;
}

}
