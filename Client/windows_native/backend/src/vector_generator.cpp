#include "backend_internal.h"

#include <cstring>

namespace swc {

namespace {

constexpr std::uint64_t kMask64 = 0xFFFFFFFFFFFFFFFFULL;
constexpr std::uint64_t kSplitMix64Gamma = 0x9E3779B97F4A7C15ULL;
constexpr std::uint64_t kSplitMix64Mix1 = 0xBF58476D1CE4E5B9ULL;
constexpr std::uint64_t kSplitMix64Mix2 = 0x94D049BB133111EBULL;

std::uint64_t splitmix64_value(std::uint64_t counter, std::uint64_t seed) {
    std::uint64_t z = (seed + ((counter + 1ULL) * kSplitMix64Gamma)) & kMask64;
    z = ((z ^ (z >> 30)) * kSplitMix64Mix1) & kMask64;
    z = ((z ^ (z >> 27)) * kSplitMix64Mix2) & kMask64;
    z ^= z >> 31;
    return z & kMask64;
}

std::uint32_t float32_word_from_value(std::uint64_t value) {
    const std::uint32_t lower = static_cast<std::uint32_t>(value & 0xFFFFFFFFULL);
    const std::uint32_t sign = (lower & 0x1U) << 31U;
    const std::uint32_t mantissa = (lower >> 1U) & 0x007FFFFFU;
    return sign | 0x3F000000U | mantissa;
}

float float32_from_word(std::uint32_t word) {
    float value = 0.0f;
    std::memcpy(&value, &word, sizeof(value));
    return value;
}

}  // namespace

bool generate_default_vector(
    std::uint64_t seed,
    std::uint32_t vector_length,
    std::vector<float>* output,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (output == nullptr) {
        set_error(error_message, error_message_capacity, "output vector storage is null");
        return false;
    }

    const std::uint32_t resolved_length = vector_length == 0U ? kDefaultVectorLength : vector_length;
    if (resolved_length == 0U) {
        set_error(error_message, error_message_capacity, "vector length must be positive");
        return false;
    }

    output->clear();
    output->reserve(resolved_length);
    for (std::uint64_t index = 0; index < resolved_length; ++index) {
        output->push_back(float32_from_word(float32_word_from_value(splitmix64_value(index, seed))));
    }
    return true;
}

}  // namespace swc

