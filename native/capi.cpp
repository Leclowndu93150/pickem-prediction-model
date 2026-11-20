#include "engine.h"

#include <cstdint>
#include <string>
#include <vector>

#if defined(_WIN32)
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {

constexpr int PLAYER_FIELDS = 12;
constexpr int TEAM_PLAYERS = 5;
constexpr int TEAM_FIELDS = 10;
constexpr int SCORE_BINS = 14;

enum MapStat {
	MS_MAPS, MS_TEAM0_WINS, MS_OVERTIME_MAPS, MS_ROUNDS, MS_CT_ROUND_WINS, MS_PISTOL_ROUNDS, MS_PISTOL_CT_WINS,
	MS_ELIMINATIONS, MS_DETONATIONS, MS_DEFUSES, MS_TIMEOUTS, MS_PLANTS, MS_T_WINS_AFTER_PLANT,
	MS_FIRST_KILL_ROUNDS, MS_FIRST_KILL_CT, MS_FIRST_KILL_WINS, MS_AFTER_PISTOL_ROUNDS, MS_PISTOL_WINNER_CONVERTS,
	MS_PLAYER_STATS, MS_LOSER_SCORE = MS_PLAYER_STATS + cs2::STAT_COUNT, MS_COUNT = MS_LOSER_SCORE + SCORE_BINS,
};

struct Setup {
	int map_count;
	const char *const *maps;
};

cs2::Role role_of(double value) {
	int role = int(value);
	return role == 1 ? cs2::ROLE_AWP : role == 2 ? cs2::ROLE_ENTRY : role == 3 ? cs2::ROLE_LURKER : cs2::ROLE_OTHER;
}

cs2::Team read_team(const double *values, const Setup &setup, const double *map_skill, const double *veto_pref) {
	cs2::Team team;
	for (int i = 0; i < TEAM_PLAYERS; i++) {
		const double *p = values + i * PLAYER_FIELDS;
		cs2::Player player;
		player.id = std::to_string(i);
		player.role = role_of(p[0]);
		player.aim = p[1];
		player.awp = p[2];
		player.entry = p[3];
		player.trading = p[4];
		player.utility = p[5];
		player.clutch = p[6];
		player.positioning = p[7];
		player.game_sense = p[8];
		player.leadership = p[9];
		player.composure = p[10];
		player.consistency = p[11];
		team.players.push_back(player);
	}
	const double *t = values + TEAM_PLAYERS * PLAYER_FIELDS;
	for (int i = 0; i < 4; i++) {
		team.plan_weights[i] = float(t[i]);
	}
	team.ct_stack_chance = t[4];
	team.force_buy_bias = t[5];
	team.save_discipline = t[6];
	team.aggression = t[7];
	team.chemistry = t[8];
	team.veto_noise_scale = t[9];
	for (int m = 0; m < setup.map_count; m++) {
		team.map_skill.emplace_back(setup.maps[m], map_skill[m]);
		if (veto_pref != nullptr && veto_pref[m] != 0.0) {
			team.veto_pref.emplace_back(setup.maps[m], veto_pref[m]);
		}
	}
	return team;
}

cs2::Tuning read_tuning(const double *v, const Setup &setup, const double *ct_edge, const double *ct_rates) {
	cs2::Tuning tuning;
	double *fields[] = {
		&tuning.skill_scale, &tuning.anchor_bonus, &tuning.hold_bonus, &tuning.site_crossfire, &tuning.crossfire_per_player,
		&tuning.trade_base, &tuning.trade_bonus, &tuning.execute_utility, &tuning.retake_utility, &tuning.post_plant_bonus,
		&tuning.hit_chance_on_loss, &tuning.stall_after_picks, &tuning.hp_scale, &tuning.entry_scale, &tuning.clutch_scale,
		&tuning.awp_skill_scale, &tuning.armor_kevlar, &tuning.armor_helmet, &tuning.composure_scale, &tuning.awp_hold_bonus,
		&tuning.clutch_numbers, &tuning.armor_vs_pistol, &tuning.retake_save_factor, &tuning.rebound_force_money,
		&tuning.pistol_ct_edge_factor, &tuning.ct_edge_base, &tuning.form_sd_base, &tuning.team_form_sd, &tuning.map_skill_scale,
		&tuning.pickup_chance, &tuning.map_shock_sd, &tuning.z_spread, &tuning.veto_noise, &tuning.veto_preference,
	};
	int index = 0;
	for (double *field : fields) {
		*field = v[index++];
	}
	for (int m = 0; m < setup.map_count; m++) {
		tuning.ct_edge_by_map.emplace_back(setup.maps[m], ct_edge[m]);
		tuning.map_ct_rates.emplace_back(setup.maps[m], ct_rates[m]);
	}
	return tuning;
}

}

EXPORT int cs2_tuning_fields() {
	return 34;
}

EXPORT int cs2_map_stat_fields() {
	return MS_COUNT;
}

EXPORT int cs2_series_wins(const double *team_a, const double *team_b, const double *skill_a, const double *skill_b,
		int map_count, const char *const *maps, const double *ct_edge, const double *ct_rates, const double *tuning_values,
		int pool_count, const int *pool, int best_of, uint64_t seed, int sims) {
	Setup setup{map_count, maps};
	cs2::Team first = read_team(team_a, setup, skill_a, nullptr);
	cs2::Team second = read_team(team_b, setup, skill_b, nullptr);
	const cs2::Team *teams[2] = {&first, &second};
	cs2::Tuning tuning = read_tuning(tuning_values, setup, ct_edge, ct_rates);
	std::vector<std::string> pool_maps;
	for (int i = 0; i < pool_count; i++) {
		pool_maps.emplace_back(maps[pool[i]]);
	}
	cs2::Pcg rng;
	rng.state = seed;
	int wins = 0;
	for (int n = 0; n < sims; n++) {
		if (cs2::simulate_series(teams, best_of, pool_maps, rng, tuning, false).winner == 0) {
			wins += 1;
		}
	}
	return wins;
}

EXPORT void cs2_map_stats(const double *team_a, const double *team_b, const double *skill_a, const double *skill_b,
		int map_count, const char *const *maps, const double *ct_edge, const double *ct_rates, const double *tuning_values,
		int map_index, uint64_t seed, int sims, int64_t *out) {
	Setup setup{map_count, maps};
	cs2::Team first = read_team(team_a, setup, skill_a, nullptr);
	cs2::Team second = read_team(team_b, setup, skill_b, nullptr);
	const cs2::Team *teams[2] = {&first, &second};
	cs2::Tuning tuning = read_tuning(tuning_values, setup, ct_edge, ct_rates);
	cs2::Pcg rng;
	rng.state = seed;
	for (int n = 0; n < sims; n++) {
		cs2::MapResult map = cs2::simulate_map(teams, maps[map_index], n % 2, rng, tuning, true);
		out[MS_MAPS] += 1;
		out[MS_TEAM0_WINS] += map.winner == 0 ? 1 : 0;
		out[MS_OVERTIME_MAPS] += map.overtimes > 0 ? 1 : 0;
		int loser = map.score[1 - map.winner];
		out[MS_LOSER_SCORE + (loser < SCORE_BINS - 1 ? loser : SCORE_BINS - 1)] += 1;
		int previous_winner = -1;
		for (const cs2::RoundRecord &record : map.rounds) {
			const cs2::RoundResult &result = record.result;
			bool ct_won = record.winner_team == record.ct_team;
			bool pistol = record.number == 1 || record.number == 13;
			out[MS_ROUNDS] += 1;
			out[MS_CT_ROUND_WINS] += ct_won ? 1 : 0;
			if (pistol) {
				out[MS_PISTOL_ROUNDS] += 1;
				out[MS_PISTOL_CT_WINS] += ct_won ? 1 : 0;
			}
			if (record.number == 2 || record.number == 14) {
				out[MS_AFTER_PISTOL_ROUNDS] += 1;
				out[MS_PISTOL_WINNER_CONVERTS] += record.winner_team == previous_winner ? 1 : 0;
			}
			previous_winner = record.winner_team;
			out[MS_ELIMINATIONS] += result.reason == cs2::REASON_ELIMINATION ? 1 : 0;
			out[MS_DETONATIONS] += result.reason == cs2::REASON_DETONATION ? 1 : 0;
			out[MS_DEFUSES] += result.reason == cs2::REASON_DEFUSE ? 1 : 0;
			out[MS_TIMEOUTS] += result.reason == cs2::REASON_TIME ? 1 : 0;
			if (result.planted) {
				out[MS_PLANTS] += 1;
				out[MS_T_WINS_AFTER_PLANT] += ct_won ? 0 : 1;
			}
			if (result.first_kill_side >= 0) {
				out[MS_FIRST_KILL_ROUNDS] += 1;
				out[MS_FIRST_KILL_CT] += result.first_kill_side == cs2::CT ? 1 : 0;
				out[MS_FIRST_KILL_WINS] += result.first_kill_side == result.winner ? 1 : 0;
			}
		}
		for (int team = 0; team < 2; team++) {
			for (size_t i = 0; i < map.stats[team].size(); i++) {
				out[MS_PLAYER_STATS + int(i % cs2::STAT_COUNT)] += map.stats[team][i];
			}
		}
	}
}
