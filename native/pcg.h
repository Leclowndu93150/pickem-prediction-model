#pragma once

#include <cmath>
#include <cstdint>

#if defined(_MSC_VER)
#include <intrin.h>
#endif

namespace cs2 {

inline uint32_t count_leading_zeros(uint32_t value) {
#if defined(_MSC_VER)
	unsigned long index;
	_BitScanReverse(&index, value);
	return 31 - index;
#else
	return __builtin_clz(value);
#endif
}

struct Pcg {
	static constexpr uint64_t DEFAULT_INC = 1442695040888963407ULL;
	static constexpr double CMP_EPSILON = 0.00001;
	static constexpr double TAU = 6.2831853071795864769252867666;

	uint64_t state = 0;
	uint64_t inc = (DEFAULT_INC << 1u) | 1u;

	uint32_t next() {
		uint64_t old = state;
		state = old * 6364136223846793005ULL + (inc | 1);
		uint32_t xorshifted = uint32_t(((old >> 18u) ^ old) >> 27u);
		uint32_t rot = uint32_t(old >> 59u);
		return (xorshifted >> rot) | (xorshifted << ((-rot) & 31));
	}

	uint32_t bounded(uint32_t bound) {
		uint32_t threshold = -bound % bound;
		for (;;) {
			uint32_t r = next();
			if (r >= threshold) {
				return r % bound;
			}
		}
	}

	float randf() {
		uint32_t proto = next();
		if (proto == 0) {
			return 0;
		}
		return std::ldexp((float)(next() | 0x80000001u), -32 - int(count_leading_zeros(proto)));
	}

	float randfn(float mean, float deviation) {
		float temp = randf();
		if (temp < CMP_EPSILON) {
			temp += CMP_EPSILON;
		}
		float angle = std::cos((float)TAU * randf());
		return (float)(mean + deviation * (angle * std::sqrt(-2.0 * std::log(temp))));
	}

	float randf_range(float from, float to) {
		return randf() * (to - from) + from;
	}

	int randi_range(int from, int to) {
		if (from == to) {
			return from;
		}
		int64_t low = from < to ? from : to;
		int64_t high = from < to ? to : from;
		uint32_t diff = uint32_t(high - low);
		if (diff == UINT32_MAX) {
			return int(int64_t(next()) + low);
		}
		return int(int64_t(bounded(diff + 1u)) + low);
	}
};

}
