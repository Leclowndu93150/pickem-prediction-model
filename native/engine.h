#pragma once

#include "pcg.h"

#include <string>
#include <utility>
#include <vector>

namespace cs2 {

enum Side { CT = 0, T = 1 };
enum Zone { SITE_A = 0, SITE_B = 1, MID = 2 };
enum Kind { KIND_PISTOL, KIND_SMG, KIND_SHOTGUN, KIND_RIFLE, KIND_SNIPER };
enum Weapon { GLOCK, USP, P250, TEC9, FIVESEVEN, DEAGLE, MAC10, MP9, UMP45, XM1014, GALIL, FAMAS, AK47, M4A1S, SSG08, AWP, WEAPON_COUNT, NO_WEAPON = -1 };
enum Role { ROLE_OTHER, ROLE_AWP, ROLE_ENTRY, ROLE_LURKER };
enum Buy { BUY_PISTOL, BUY_ECO, BUY_FORCE, BUY_HALF, BUY_FULL };
enum Plan { PLAN_DEFAULT, PLAN_EXECUTE, PLAN_RUSH, PLAN_FAKE };
enum Reason { REASON_NONE, REASON_ELIMINATION, REASON_DETONATION, REASON_DEFUSE, REASON_TIME };

struct WeaponInfo {
	const char *id;
	int price;
	int reward;
	double power;
	Kind kind;
};

extern const WeaponInfo WEAPONS[WEAPON_COUNT];
extern const char *BUY_NAMES[5];
extern const char *PLAN_NAMES[4];
extern const char *REASON_NAMES[5];

struct Tuning {
	double skill_scale = 0.1;
	double anchor_bonus = 0.15;
	double hold_bonus = 0.25;
	double site_crossfire = 0.05;
	double crossfire_per_player = 0.24;
	double trade_base = 0.5;
	double trade_bonus = 0.45;
	double execute_utility = 0.35;
	double retake_utility = 0.25;
	double post_plant_bonus = 0.48;
	double hit_chance_on_loss = 0.6;
	double stall_after_picks = 15.0;
	double hp_scale = 0.8;
	double entry_scale = 0.12;
	double clutch_scale = 0.12;
	double awp_skill_scale = 0.12;
	double armor_kevlar = 0.12;
	double armor_helmet = 0.18;
	double composure_scale = 0.08;
	double awp_hold_bonus = 0.1;
	double clutch_numbers = 0.0;
	double armor_vs_pistol = 1.0;
	double retake_save_factor = 0.8;
	double rebound_force_money = 1500.0;
	double pistol_ct_edge_factor = 1.0;
	double ct_edge_base = 0.08;
	std::vector<std::pair<std::string, double>> ct_edge_by_map;
	double form_sd_base = 0.3;
	double team_form_sd = 0.12;
	double map_skill_scale = 0.15;
	double pickup_chance = 0.6;
	double map_shock_sd = 0.0;
	double z_spread = 3.0;
	double veto_noise = 0.15;
	double veto_preference = 5.0;
	std::vector<std::pair<std::string, double>> map_ct_rates;
};

struct Player {
	std::string id;
	Role role = ROLE_OTHER;
	double aim = 10.5;
	double awp = 5.0;
	double entry = 10.5;
	double trading = 10.5;
	double utility = 10.5;
	double clutch = 10.5;
	double positioning = 10.5;
	double game_sense = 10.5;
	double leadership = 10.5;
	double composure = 10.5;
	double consistency = 10.5;
};

struct Team {
	std::vector<Player> players;
	float plan_weights[4] = {0.5f, 0.25f, 0.12f, 0.13f};
	double ct_stack_chance = 0.12;
	double force_buy_bias = 0.5;
	double save_discipline = 0.6;
	double aggression = 0.5;
	double chemistry = 0.5;
	std::vector<std::pair<std::string, double>> map_skill;
	double veto_noise_scale = 1.0;
	std::vector<std::pair<std::string, double>> veto_pref;

	double skill_on(const std::string &map_id) const;
	double veto_bias(const std::string &map_id) const;
};

struct Loadout {
	int primary = NO_WEAPON;
	int pistol = GLOCK;
	int armor = 0;
	bool kit = false;
	int smokes = 0;
	int flashes = 0;
	int molotovs = 0;
	int hes = 0;

	static Loadout starter(int side);
	int active_weapon() const { return primary != NO_WEAPON ? primary : pistol; }
	int grenade_count() const { return smokes + flashes + molotovs + hes; }
	int equipment_value(int side) const;
};

struct Combatant {
	const Player *player = nullptr;
	int index = 0;
	int team = 0;
	int side = CT;
	bool alive = true;
	int hp = 100;
	Loadout *loadout = nullptr;
	int zone = MID;
	double form = 0.0;
	double z_aim = 0.0;
	double z_awp = 0.0;
	double z_entry = 0.0;
	double z_trading = 0.0;
	double z_utility = 0.0;
	double z_clutch = 0.0;
	double z_positioning = 0.0;
	double z_game_sense = 0.0;
	double z_composure = 0.0;
	double fight = 0.0;
	double shock_ct = 0.0;
	double shock_t = 0.0;

	double shock() const { return side == CT ? shock_ct : shock_t; }
};

struct Line {
	int kills = 0;
	int deaths = 0;
	int assists = 0;
	int flash_assists = 0;
	int damage = 0;
	int headshots = 0;
	bool opening_kill = false;
	bool opening_death = false;
	bool survived = false;
	bool traded = false;
};

struct Kill {
	double time = 0.0;
	int killer = -1;
	int victim = -1;
	int weapon = NO_WEAPON;
	bool headshot = false;
	bool opening = false;
	bool trade = false;
	int assister = -1;
	int flash_assister = -1;
};

struct Clutch {
	int player = -1;
	int side = CT;
	int versus = 0;
	bool won = false;
};

struct RoundResult {
	int winner = CT;
	int reason = REASON_NONE;
	int plan = PLAN_DEFAULT;
	int site = SITE_A;
	bool planted = false;
	int planter = -1;
	int defuser = -1;
	int first_kill_side = -1;
	std::vector<Kill> kills;
	std::vector<Line> lines;
	std::vector<Clutch> clutches;
	double end_time = 0.0;
};

struct RoundRecord {
	int number = 0;
	int ct_team = 0;
	int winner_team = 0;
	RoundResult result;
	int buys[2] = {0, 0};
	int equipment[2] = {0, 0};
	std::vector<int> money_before;
	std::vector<int> money_after;
	int score_after[2] = {0, 0};
};

enum StatField { ST_ROUNDS, ST_KILLS, ST_DEATHS, ST_ASSISTS, ST_FLASH_ASSISTS, ST_DAMAGE, ST_HEADSHOTS, ST_OPENING_KILLS, ST_OPENING_DEATHS,
	ST_MULTI_KILL_ROUNDS, ST_KAST_ROUNDS, ST_TRADED_DEATHS, ST_CLUTCHES_PLAYED, ST_CLUTCHES_WON, STAT_COUNT };

struct MapResult {
	std::string map_id;
	int starting_ct = 0;
	int score[2] = {0, 0};
	int winner = -1;
	int overtimes = 0;
	std::vector<int> stats[2];
	std::vector<RoundRecord> rounds;
};

struct VetoStep {
	int team = -1;
	std::string action;
	std::string map_id;
	int side_chooser = -1;
	int chosen_ct = -1;
};

struct VetoResult {
	std::vector<VetoStep> steps;
	std::vector<std::string> maps;
	std::vector<int> ct_first;
};

struct SeriesResult {
	VetoResult veto;
	std::vector<MapResult> maps;
	int score[2] = {0, 0};
	int winner = -1;
};

MapResult simulate_map(const Team *teams[2], const std::string &map_id, int ct_first, Pcg &rng, const Tuning &tuning, bool with_rounds);
VetoResult run_veto(const Team *teams[2], const std::vector<std::string> &pool, int best_of, Pcg &rng, const Tuning &tuning);
SeriesResult simulate_series(const Team *teams[2], int best_of, const std::vector<std::string> &pool, Pcg &rng, const Tuning &tuning, bool with_rounds);

}
