#include "backend_internal.h"

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <string_view>
#include <vector>

namespace swc {

namespace {

constexpr std::uint8_t kWireVarint = 0;
constexpr std::uint8_t kWireFixed64 = 1;
constexpr std::uint8_t kWireLengthDelimited = 2;
constexpr std::uint8_t kWireFixed32 = 5;

constexpr std::uint32_t kEnvelopeFieldKind = 1;
constexpr std::uint32_t kEnvelopeFieldHeartbeat = 4;
constexpr std::uint32_t kEnvelopeFieldHeartbeatOk = 5;
constexpr std::uint32_t kEnvelopeFieldClientJoin = 6;
constexpr std::uint32_t kEnvelopeFieldClientRequest = 7;
constexpr std::uint32_t kEnvelopeFieldClientResponse = 8;
constexpr std::uint32_t kEnvelopeFieldClientInfoRequest = 15;
constexpr std::uint32_t kEnvelopeFieldClientInfoReply = 16;
constexpr std::uint32_t kEnvelopeFieldClientRequestOk = 17;

constexpr std::uint32_t kMessageHeartbeat = 3;
constexpr std::uint32_t kMessageHeartbeatOk = 4;
constexpr std::uint32_t kMessageClientJoin = 5;
constexpr std::uint32_t kMessageClientRequest = 6;
constexpr std::uint32_t kMessageClientResponse = 7;
constexpr std::uint32_t kMessageClientInfoRequest = 14;
constexpr std::uint32_t kMessageClientInfoReply = 15;
constexpr std::uint32_t kMessageClientRequestOk = 16;

constexpr std::uint32_t kHeartbeatFieldMainNodeName = 1;
constexpr std::uint32_t kHeartbeatFieldUnixTimeMs = 2;

constexpr std::uint32_t kHeartbeatOkFieldNodeName = 1;
constexpr std::uint32_t kHeartbeatOkFieldHeartbeatUnixTimeMs = 2;
constexpr std::uint32_t kHeartbeatOkFieldReceivedUnixTimeMs = 3;
constexpr std::uint32_t kHeartbeatOkFieldActiveTaskIds = 4;

constexpr std::uint32_t kClientInfoRequestFieldClientId = 1;
constexpr std::uint32_t kClientInfoRequestFieldClientName = 2;
constexpr std::uint32_t kClientInfoRequestFieldTimestampMs = 3;

constexpr std::uint32_t kClientInfoReplyFieldClientId = 1;
constexpr std::uint32_t kClientInfoReplyFieldRequestTimestampMs = 2;
constexpr std::uint32_t kClientInfoReplyFieldReplyTimestampMs = 3;
constexpr std::uint32_t kClientInfoReplyFieldTimeoutMs = 4;
constexpr std::uint32_t kClientInfoReplyFieldHasActiveTasks = 5;
constexpr std::uint32_t kClientInfoReplyFieldActiveTaskIds = 6;

constexpr std::uint32_t kClientRequestOkFieldClientId = 1;
constexpr std::uint32_t kClientRequestOkFieldTaskId = 2;
constexpr std::uint32_t kClientRequestOkFieldMethod = 3;
constexpr std::uint32_t kClientRequestOkFieldObjectId = 4;
constexpr std::uint32_t kClientRequestOkFieldAcceptedTimestampMs = 5;
constexpr std::uint32_t kClientRequestOkFieldSize = 6;
constexpr std::uint32_t kClientRequestOkFieldUploadId = 7;
constexpr std::uint32_t kClientRequestOkFieldDownloadId = 8;
constexpr std::uint32_t kClientRequestOkFieldDataEndpointHost = 9;
constexpr std::uint32_t kClientRequestOkFieldDataEndpointPort = 10;

constexpr std::uint32_t kClientRequestFieldRequestId = 1;
constexpr std::uint32_t kClientRequestFieldClientName = 2;
constexpr std::uint32_t kClientRequestFieldMethod = 3;
constexpr std::uint32_t kClientRequestFieldObjectId = 4;
constexpr std::uint32_t kClientRequestFieldStreamId = 5;
constexpr std::uint32_t kClientRequestFieldTimestampMs = 6;
constexpr std::uint32_t kClientRequestFieldIterationCount = 7;
constexpr std::uint32_t kClientRequestFieldGemvPayload = 8;
constexpr std::uint32_t kClientRequestFieldConv2dPayload = 9;
constexpr std::uint32_t kClientRequestFieldSize = 10;

constexpr std::uint32_t kGemvRequestFieldVectorLength = 1;
constexpr std::uint32_t kGemvRequestFieldVectorData = 2;

constexpr std::uint32_t kConv2dRequestFieldTensorH = 1;
constexpr std::uint32_t kConv2dRequestFieldTensorW = 2;
constexpr std::uint32_t kConv2dRequestFieldChannelsIn = 3;
constexpr std::uint32_t kConv2dRequestFieldChannelsOut = 4;
constexpr std::uint32_t kConv2dRequestFieldKernelSize = 5;
constexpr std::uint32_t kConv2dRequestFieldPadding = 6;
constexpr std::uint32_t kConv2dRequestFieldStride = 7;
constexpr std::uint32_t kConv2dRequestFieldClientResponseMode = 8;
constexpr std::uint32_t kConv2dRequestFieldStatsMaxSamples = 9;
constexpr std::uint32_t kConv2dRequestFieldUploadSizeBytes = 10;
constexpr std::uint32_t kConv2dRequestFieldUploadChecksum = 11;

constexpr std::uint32_t kConv2dResponseFieldOutputLength = 1;
constexpr std::uint32_t kConv2dResponseFieldOutputVector = 2;
constexpr std::uint32_t kConv2dResponseFieldResultArtifactId = 3;
constexpr std::uint32_t kConv2dResponseFieldStatsElementCount = 4;
constexpr std::uint32_t kConv2dResponseFieldStatsSum = 5;
constexpr std::uint32_t kConv2dResponseFieldStatsSumSquares = 6;
constexpr std::uint32_t kConv2dResponseFieldStatsSamples = 7;

constexpr std::uint32_t kResponseTimingFieldDispatchMs = 1;
constexpr std::uint32_t kResponseTimingFieldTaskWindowMs = 2;
constexpr std::uint32_t kResponseTimingFieldAggregateMs = 3;
constexpr std::uint32_t kResponseTimingFieldWorkers = 4;

constexpr std::uint32_t kWorkerTimingFieldNodeId = 1;
constexpr std::uint32_t kWorkerTimingFieldTaskId = 2;
constexpr std::uint32_t kWorkerTimingFieldSlice = 3;
constexpr std::uint32_t kWorkerTimingFieldWallMs = 4;
constexpr std::uint32_t kWorkerTimingFieldArtifactFetchMs = 5;
constexpr std::uint32_t kWorkerTimingFieldComputationMs = 6;
constexpr std::uint32_t kWorkerTimingFieldPeripheralMs = 7;

enum class ReceiveStatus {
    kOk,
    kTimeout,
    kClosed,
    kError,
};

bool send_all(SOCKET socket_handle, const std::uint8_t* data, std::size_t size, char* error_message, std::uint32_t error_message_capacity) {
    std::size_t sent = 0;
    while (sent < size) {
        const int result = send(
            socket_handle,
            reinterpret_cast<const char*>(data + sent),
            static_cast<int>(size - sent),
            0);
        if (result == SOCKET_ERROR) {
            set_error(error_message, error_message_capacity, "send() failed on the client socket");
            return false;
        }
        if (result == 0) {
            set_error(error_message, error_message_capacity, "client socket closed while sending");
            return false;
        }
        sent += static_cast<std::size_t>(result);
    }
    return true;
}

ReceiveStatus recv_exactly(
    SOCKET socket_handle,
    std::uint8_t* data,
    std::size_t size,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::size_t received = 0;
    while (received < size) {
        const int result = recv(
            socket_handle,
            reinterpret_cast<char*>(data + received),
            static_cast<int>(size - received),
            0);
        if (result == SOCKET_ERROR) {
            const int last_error = WSAGetLastError();
            if (last_error == WSAETIMEDOUT) {
                if (received == 0U) {
                    return ReceiveStatus::kTimeout;
                }
                continue;
            }
            set_error(error_message, error_message_capacity, "recv() failed on the client socket");
            return ReceiveStatus::kError;
        }
        if (result == 0) {
            if (received == 0U) {
                set_error(error_message, error_message_capacity, "main node closed the TCP session");
            } else {
                set_error(error_message, error_message_capacity, "main node closed the TCP session mid-frame");
            }
            return ReceiveStatus::kClosed;
        }
        received += static_cast<std::size_t>(result);
    }
    return ReceiveStatus::kOk;
}

void append_varint(std::vector<std::uint8_t>* output, std::uint64_t value) {
    while (true) {
        std::uint8_t byte = static_cast<std::uint8_t>(value & 0x7FU);
        value >>= 7U;
        if (value != 0U) {
            output->push_back(static_cast<std::uint8_t>(byte | 0x80U));
        } else {
            output->push_back(byte);
            return;
        }
    }
}

bool read_varint(const std::vector<std::uint8_t>& input, std::size_t* offset, std::uint64_t* value) {
    std::uint64_t decoded = 0;
    std::uint32_t shift = 0;
    while (*offset < input.size()) {
        const std::uint8_t byte = input[*offset];
        *offset += 1U;
        decoded |= static_cast<std::uint64_t>(byte & 0x7FU) << shift;
        if ((byte & 0x80U) == 0U) {
            *value = decoded;
            return true;
        }
        shift += 7U;
        if (shift > 63U) {
            return false;
        }
    }
    return false;
}

bool read_fixed32(const std::vector<std::uint8_t>& input, std::size_t* offset, std::uint32_t* value) {
    if (*offset + sizeof(std::uint32_t) > input.size()) {
        return false;
    }
    std::uint32_t decoded = 0;
    std::memcpy(&decoded, input.data() + static_cast<std::ptrdiff_t>(*offset), sizeof(decoded));
    *value = decoded;
    *offset += sizeof(decoded);
    return true;
}

bool read_fixed64(const std::vector<std::uint8_t>& input, std::size_t* offset, std::uint64_t* value) {
    if (*offset + sizeof(std::uint64_t) > input.size()) {
        return false;
    }
    std::uint64_t decoded = 0;
    std::memcpy(&decoded, input.data() + static_cast<std::ptrdiff_t>(*offset), sizeof(decoded));
    *value = decoded;
    *offset += sizeof(decoded);
    return true;
}

void append_key(std::vector<std::uint8_t>* output, std::uint32_t field_number, std::uint8_t wire_type) {
    append_varint(output, (static_cast<std::uint64_t>(field_number) << 3U) | wire_type);
}

void append_uint_field(std::vector<std::uint8_t>* output, std::uint32_t field_number, std::uint64_t value) {
    append_key(output, field_number, kWireVarint);
    append_varint(output, value);
}

void append_string_field(std::vector<std::uint8_t>* output, std::uint32_t field_number, std::string_view value) {
    append_key(output, field_number, kWireLengthDelimited);
    append_varint(output, value.size());
    output->insert(output->end(), value.begin(), value.end());
}

void append_bytes_field(std::vector<std::uint8_t>* output, std::uint32_t field_number, const std::uint8_t* data, std::size_t size) {
    append_key(output, field_number, kWireLengthDelimited);
    append_varint(output, size);
    if (size != 0U && data != nullptr) {
        output->insert(output->end(), data, data + size);
    }
}

void append_message_field(std::vector<std::uint8_t>* output, std::uint32_t field_number, const std::vector<std::uint8_t>& payload) {
    append_bytes_field(output, field_number, payload.data(), payload.size());
}

bool skip_field(
    const std::vector<std::uint8_t>& input,
    std::size_t* offset,
    std::uint8_t wire_type,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (wire_type == kWireVarint) {
        std::uint64_t ignored = 0;
        if (!read_varint(input, offset, &ignored)) {
            set_error(error_message, error_message_capacity, "truncated protobuf varint field");
            return false;
        }
        return true;
    }
    if (wire_type == kWireLengthDelimited) {
        std::uint64_t length = 0;
        if (!read_varint(input, offset, &length)) {
            set_error(error_message, error_message_capacity, "truncated protobuf length field");
            return false;
        }
        if (*offset + length > input.size()) {
            set_error(error_message, error_message_capacity, "protobuf field payload exceeds message size");
            return false;
        }
        *offset += static_cast<std::size_t>(length);
        return true;
    }
    if (wire_type == kWireFixed64) {
        std::uint64_t ignored = 0;
        if (!read_fixed64(input, offset, &ignored)) {
            set_error(error_message, error_message_capacity, "truncated protobuf fixed64 field");
            return false;
        }
        return true;
    }
    if (wire_type == kWireFixed32) {
        std::uint32_t ignored = 0;
        if (!read_fixed32(input, offset, &ignored)) {
            set_error(error_message, error_message_capacity, "truncated protobuf fixed32 field");
            return false;
        }
        return true;
    }
    set_error(error_message, error_message_capacity, "unsupported protobuf wire type in client backend");
    return false;
}

bool read_length_delimited(
    const std::vector<std::uint8_t>& input,
    std::size_t* offset,
    std::vector<std::uint8_t>* output,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::uint64_t length = 0;
    if (!read_varint(input, offset, &length)) {
        set_error(error_message, error_message_capacity, "truncated protobuf length field");
        return false;
    }
    if (*offset + length > input.size()) {
        set_error(error_message, error_message_capacity, "protobuf field payload exceeds message size");
        return false;
    }
    output->assign(
        input.begin() + static_cast<std::ptrdiff_t>(*offset),
        input.begin() + static_cast<std::ptrdiff_t>(*offset + length));
    *offset += static_cast<std::size_t>(length);
    return true;
}

void copy_text(char* destination, std::size_t capacity, const std::string& value) {
    if (destination == nullptr || capacity == 0U) {
        return;
    }
    const std::size_t copy_length = std::min<std::size_t>(capacity - 1U, value.size());
    std::memcpy(destination, value.data(), copy_length);
    destination[copy_length] = '\0';
}

bool decode_float_vector(
    const std::vector<std::uint8_t>& raw,
    std::vector<float>* values,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (values == nullptr) {
        set_error(error_message, error_message_capacity, "output float vector target is null");
        return false;
    }
    if (raw.empty()) {
        values->clear();
        return true;
    }
    if (raw.size() % sizeof(float) != 0U) {
        set_error(error_message, error_message_capacity, "output vector byte length is not divisible by 4");
        return false;
    }

    values->resize(raw.size() / sizeof(float));
    std::memcpy(values->data(), raw.data(), raw.size());
    return true;
}

bool parse_gemv_response_payload(
    const std::vector<std::uint8_t>& payload,
    ClientResponseData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::size_t offset = 0;
    std::uint32_t output_length = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated gemv response payload key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == 1U) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid gemv output_length field");
                return false;
            }
            output_length = static_cast<std::uint32_t>(value);
            continue;
        }
        if (field_number == 2U) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            if (decoded->method == kMethodGemvMultiplication) {
                if (!decode_float_vector(raw, &decoded->output_values, error_message, error_message_capacity)) {
                    return false;
                }
            } else {
                decoded->output_text.assign(raw.begin(), raw.end());
            }
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }

    if (!decoded->output_values.empty() && output_length != 0U && output_length != decoded->output_values.size()) {
        set_error(error_message, error_message_capacity, "gemv output_length does not match decoded response vector size");
        return false;
    }
    decoded->output_length = output_length != 0U
        ? output_length
        : static_cast<std::uint32_t>(decoded->output_values.size());
    return true;
}

bool parse_packed_float32_samples(
    const std::vector<std::uint8_t>& raw,
    std::vector<float>* output,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (output == nullptr) {
        set_error(error_message, error_message_capacity, "float sample target is null");
        return false;
    }
    if (raw.empty()) {
        output->clear();
        return true;
    }
    if (raw.size() % sizeof(float) != 0U) {
        set_error(error_message, error_message_capacity, "packed float32 sample payload length is invalid");
        return false;
    }

    output->resize(raw.size() / sizeof(float));
    std::memcpy(output->data(), raw.data(), raw.size());
    return true;
}

bool parse_worker_timing_payload(
    const std::vector<std::uint8_t>& payload,
    WorkerTimingData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (decoded == nullptr) {
        set_error(error_message, error_message_capacity, "worker timing target is null");
        return false;
    }

    *decoded = WorkerTimingData{};
    std::size_t offset = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated worker timing payload key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == kWorkerTimingFieldNodeId
            || field_number == kWorkerTimingFieldTaskId
            || field_number == kWorkerTimingFieldSlice) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            const std::string value(raw.begin(), raw.end());
            if (field_number == kWorkerTimingFieldNodeId) {
                decoded->node_id = value;
            } else if (field_number == kWorkerTimingFieldTaskId) {
                decoded->task_id = value;
            } else {
                decoded->slice = value;
            }
            continue;
        }
        if (field_number == kWorkerTimingFieldWallMs
            || field_number == kWorkerTimingFieldArtifactFetchMs
            || field_number == kWorkerTimingFieldComputationMs
            || field_number == kWorkerTimingFieldPeripheralMs) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid worker timing protobuf field");
                return false;
            }
            if (field_number == kWorkerTimingFieldWallMs) {
                decoded->wall_ms = value;
            } else if (field_number == kWorkerTimingFieldArtifactFetchMs) {
                decoded->artifact_fetch_ms = value;
            } else if (field_number == kWorkerTimingFieldComputationMs) {
                decoded->computation_ms = value;
            } else {
                decoded->peripheral_ms = value;
            }
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }
    return true;
}

bool parse_response_timing_payload(
    const std::vector<std::uint8_t>& payload,
    ResponseTimingData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (decoded == nullptr) {
        set_error(error_message, error_message_capacity, "response timing target is null");
        return false;
    }

    *decoded = ResponseTimingData{};
    std::size_t offset = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated response timing payload key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == kResponseTimingFieldDispatchMs
            || field_number == kResponseTimingFieldTaskWindowMs
            || field_number == kResponseTimingFieldAggregateMs) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid response timing protobuf field");
                return false;
            }
            if (field_number == kResponseTimingFieldDispatchMs) {
                decoded->dispatch_ms = value;
            } else if (field_number == kResponseTimingFieldTaskWindowMs) {
                decoded->task_window_ms = value;
            } else {
                decoded->aggregate_ms = value;
            }
            continue;
        }
        if (field_number == kResponseTimingFieldWorkers) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            WorkerTimingData worker;
            if (!parse_worker_timing_payload(raw, &worker, error_message, error_message_capacity)) {
                return false;
            }
            decoded->workers.push_back(std::move(worker));
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }
    return true;
}

bool parse_conv2d_response_payload(
    const std::vector<std::uint8_t>& payload,
    ClientResponseData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (decoded == nullptr) {
        set_error(error_message, error_message_capacity, "conv2d response target is null");
        return false;
    }

    std::size_t offset = 0;
    std::uint32_t output_length = 0;
    std::string result_artifact_id;
    decoded->conv2d_stats_samples.clear();
    decoded->conv2d_stats_element_count = 0;
    decoded->conv2d_stats_sum = 0.0;
    decoded->conv2d_stats_sum_squares = 0.0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated conv2d response payload key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == kConv2dResponseFieldOutputLength) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid conv2d output_length field");
                return false;
            }
            output_length = static_cast<std::uint32_t>(value);
            continue;
        }
        if (field_number == kConv2dResponseFieldOutputVector) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            if (!decode_float_vector(raw, &decoded->output_values, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }
        if (field_number == kConv2dResponseFieldResultArtifactId) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            result_artifact_id.assign(raw.begin(), raw.end());
            continue;
        }
        if (field_number == kConv2dResponseFieldStatsElementCount) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid conv2d stats_element_count field");
                return false;
            }
            decoded->conv2d_stats_element_count = value;
            continue;
        }
        if (field_number == kConv2dResponseFieldStatsSum || field_number == kConv2dResponseFieldStatsSumSquares) {
            std::uint64_t word = 0;
            if (wire_type != kWireFixed64 || !read_fixed64(payload, &offset, &word)) {
                set_error(error_message, error_message_capacity, "invalid conv2d stats double field");
                return false;
            }
            double value = 0.0;
            std::memcpy(&value, &word, sizeof(value));
            if (field_number == kConv2dResponseFieldStatsSum) {
                decoded->conv2d_stats_sum = value;
            } else {
                decoded->conv2d_stats_sum_squares = value;
            }
            continue;
        }
        if (field_number == kConv2dResponseFieldStatsSamples) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            if (!parse_packed_float32_samples(raw, &decoded->conv2d_stats_samples, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }

    if (!decoded->output_values.empty() && output_length != 0U && output_length != decoded->output_values.size()) {
        set_error(error_message, error_message_capacity, "conv2d output_length does not match decoded response vector size");
        return false;
    }
    decoded->output_length = output_length != 0U
        ? output_length
        : static_cast<std::uint32_t>(decoded->output_values.size());
    if (decoded->result_artifact.artifact_id[0] == '\0' && !result_artifact_id.empty()) {
        copy_text(decoded->result_artifact.artifact_id, sizeof(decoded->result_artifact.artifact_id), result_artifact_id);
    }
    return true;
}

bool parse_artifact_descriptor(
    const std::vector<std::uint8_t>& payload,
    SwcArtifactDescriptor* descriptor,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (descriptor == nullptr) {
        set_error(error_message, error_message_capacity, "artifact descriptor target is null");
        return false;
    }

    std::memset(descriptor, 0, sizeof(*descriptor));
    std::size_t offset = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated artifact descriptor key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == 1U || field_number == 2U || field_number == 4U || field_number == 5U || field_number == 6U) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            const std::string value(raw.begin(), raw.end());
            if (field_number == 1U) {
                copy_text(descriptor->artifact_id, sizeof(descriptor->artifact_id), value);
            } else if (field_number == 2U) {
                copy_text(descriptor->content_type, sizeof(descriptor->content_type), value);
            } else if (field_number == 4U) {
                copy_text(descriptor->checksum, sizeof(descriptor->checksum), value);
            } else if (field_number == 5U) {
                copy_text(descriptor->producer_node_id, sizeof(descriptor->producer_node_id), value);
            } else {
                copy_text(descriptor->transfer_host, sizeof(descriptor->transfer_host), value);
            }
            continue;
        }

        if (field_number == 3U || field_number == 7U || field_number == 8U || field_number == 9U) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid artifact descriptor varint field");
                return false;
            }
            if (field_number == 3U) {
                descriptor->size_bytes = value;
            } else if (field_number == 7U) {
                descriptor->transfer_port = static_cast<std::uint32_t>(value);
            } else if (field_number == 8U) {
                descriptor->chunk_size = static_cast<std::uint32_t>(value);
            } else {
                descriptor->ready = static_cast<std::uint8_t>(value != 0U ? 1U : 0U);
            }
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }
    return true;
}

bool parse_client_response_payload(
    const std::vector<std::uint8_t>& payload,
    ClientResponseData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::size_t offset = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated client response protobuf key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);

        if (field_number == 1U || field_number == 2U || field_number == 3U || field_number == 4U || field_number == 7U
            || field_number == 10U || field_number == 15U || field_number == 17U) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            const std::string value(raw.begin(), raw.end());
            if (field_number == 1U) {
                decoded->request_id = value;
            } else if (field_number == 2U) {
                decoded->method = value;
            } else if (field_number == 17U) {
                decoded->size = value;
            } else if (field_number == 3U) {
                decoded->object_id = value;
            } else if (field_number == 4U) {
                decoded->stream_id = value;
            } else if (field_number == 7U) {
                decoded->error_message = value;
            } else if (field_number == 10U) {
                decoded->client_id = value;
            } else {
                decoded->task_id = value;
            }
            continue;
        }

        if (field_number == 12U) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            if (!parse_gemv_response_payload(raw, decoded, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }

        if (field_number == 13U) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            if (!parse_conv2d_response_payload(raw, decoded, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }

        if (field_number == 14U) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            if (!parse_artifact_descriptor(raw, &decoded->result_artifact, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }

        if (field_number == 18U) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            if (!parse_response_timing_payload(raw, &decoded->timing, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }

        if (field_number == 5U || field_number == 6U || field_number == 8U || field_number == 9U || field_number == 11U || field_number == 16U) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid protobuf varint field in client response");
                return false;
            }
            if (field_number == 5U) {
                decoded->timestamp_ms = value;
            } else if (field_number == 6U) {
                decoded->status_code = static_cast<std::int32_t>(value);
            } else if (field_number == 8U) {
                decoded->worker_count = static_cast<std::uint32_t>(value);
            } else if (field_number == 9U) {
                decoded->client_count = static_cast<std::uint32_t>(value);
            } else if (field_number == 11U) {
                decoded->iteration_count = static_cast<std::uint32_t>(value);
            } else {
                decoded->elapsed_ms = value;
            }
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }
    return true;
}

bool parse_heartbeat_payload(
    const std::vector<std::uint8_t>& payload,
    HeartbeatData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (decoded == nullptr) {
        set_error(error_message, error_message_capacity, "heartbeat target is null");
        return false;
    }

    *decoded = HeartbeatData{};
    std::size_t offset = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated heartbeat payload key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == kHeartbeatFieldMainNodeName) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            decoded->main_node_name.assign(raw.begin(), raw.end());
            continue;
        }
        if (field_number == kHeartbeatFieldUnixTimeMs) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid heartbeat unix_time_ms field");
                return false;
            }
            decoded->unix_time_ms = value;
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }
    return true;
}

bool parse_client_info_reply_payload(
    const std::vector<std::uint8_t>& payload,
    ClientInfoReplyData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (decoded == nullptr) {
        set_error(error_message, error_message_capacity, "client info reply target is null");
        return false;
    }

    *decoded = ClientInfoReplyData{};
    std::size_t offset = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated client info reply key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == kClientInfoReplyFieldClientId || field_number == kClientInfoReplyFieldActiveTaskIds) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            const std::string value(raw.begin(), raw.end());
            if (field_number == kClientInfoReplyFieldClientId) {
                decoded->client_id = value;
            } else {
                decoded->active_task_ids.push_back(value);
            }
            continue;
        }
        if (field_number == kClientInfoReplyFieldRequestTimestampMs
            || field_number == kClientInfoReplyFieldReplyTimestampMs
            || field_number == kClientInfoReplyFieldTimeoutMs
            || field_number == kClientInfoReplyFieldHasActiveTasks) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid protobuf varint field in client info reply");
                return false;
            }
            if (field_number == kClientInfoReplyFieldRequestTimestampMs) {
                decoded->request_timestamp_ms = value;
            } else if (field_number == kClientInfoReplyFieldReplyTimestampMs) {
                decoded->reply_timestamp_ms = value;
            } else if (field_number == kClientInfoReplyFieldTimeoutMs) {
                decoded->timeout_ms = value;
            } else {
                decoded->has_active_tasks = value != 0U;
            }
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }
    return true;
}

bool parse_client_request_ok_payload(
    const std::vector<std::uint8_t>& payload,
    ClientRequestOkData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (decoded == nullptr) {
        set_error(error_message, error_message_capacity, "client request ok target is null");
        return false;
    }

    *decoded = ClientRequestOkData{};
    std::size_t offset = 0;
    while (offset < payload.size()) {
        std::uint64_t key = 0;
        if (!read_varint(payload, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated client request ok key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);
        if (field_number == kClientRequestOkFieldClientId
            || field_number == kClientRequestOkFieldTaskId
            || field_number == kClientRequestOkFieldMethod
            || field_number == kClientRequestOkFieldObjectId
            || field_number == kClientRequestOkFieldSize
            || field_number == kClientRequestOkFieldUploadId
            || field_number == kClientRequestOkFieldDownloadId
            || field_number == kClientRequestOkFieldDataEndpointHost) {
            std::vector<std::uint8_t> raw;
            if (!read_length_delimited(payload, &offset, &raw, error_message, error_message_capacity)) {
                return false;
            }
            const std::string value(raw.begin(), raw.end());
            if (field_number == kClientRequestOkFieldClientId) {
                decoded->client_id = value;
            } else if (field_number == kClientRequestOkFieldTaskId) {
                decoded->task_id = value;
            } else if (field_number == kClientRequestOkFieldMethod) {
                decoded->method = value;
            } else if (field_number == kClientRequestOkFieldObjectId) {
                decoded->object_id = value;
            } else if (field_number == kClientRequestOkFieldSize) {
                decoded->size = value;
            } else if (field_number == kClientRequestOkFieldUploadId) {
                decoded->upload_id = value;
            } else if (field_number == kClientRequestOkFieldDownloadId) {
                decoded->download_id = value;
            } else {
                decoded->data_endpoint_host = value;
            }
            continue;
        }
        if (field_number == kClientRequestOkFieldAcceptedTimestampMs
            || field_number == kClientRequestOkFieldDataEndpointPort) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(payload, &offset, &value)) {
                set_error(error_message, error_message_capacity, "invalid accepted_timestamp_ms in client request ok");
                return false;
            }
            if (field_number == kClientRequestOkFieldAcceptedTimestampMs) {
                decoded->accepted_timestamp_ms = value;
            } else {
                decoded->data_endpoint_port = static_cast<std::uint32_t>(value);
            }
            continue;
        }

        if (!skip_field(payload, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }
    return true;
}

bool parse_runtime_envelope(
    const std::vector<std::uint8_t>& envelope,
    RuntimeMessageData* decoded,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (decoded == nullptr) {
        set_error(error_message, error_message_capacity, "runtime message output is null");
        return false;
    }

    *decoded = RuntimeMessageData{};
    std::size_t offset = 0;
    std::uint32_t kind = 0;
    std::vector<std::uint8_t> heartbeat_payload;
    std::vector<std::uint8_t> response_payload;
    std::vector<std::uint8_t> client_info_reply_payload;
    std::vector<std::uint8_t> client_request_ok_payload;

    while (offset < envelope.size()) {
        std::uint64_t key = 0;
        if (!read_varint(envelope, &offset, &key)) {
            set_error(error_message, error_message_capacity, "truncated runtime envelope key");
            return false;
        }

        const std::uint32_t field_number = static_cast<std::uint32_t>(key >> 3U);
        const std::uint8_t wire_type = static_cast<std::uint8_t>(key & 0x07U);

        if (field_number == kEnvelopeFieldKind) {
            std::uint64_t value = 0;
            if (wire_type != kWireVarint || !read_varint(envelope, &offset, &value)) {
                set_error(error_message, error_message_capacity, "runtime envelope kind is invalid");
                return false;
            }
            kind = static_cast<std::uint32_t>(value);
            continue;
        }
        if (field_number == kEnvelopeFieldHeartbeat) {
            if (!read_length_delimited(envelope, &offset, &heartbeat_payload, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }
        if (field_number == kEnvelopeFieldClientResponse) {
            if (!read_length_delimited(envelope, &offset, &response_payload, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }
        if (field_number == kEnvelopeFieldClientInfoReply) {
            if (!read_length_delimited(envelope, &offset, &client_info_reply_payload, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }
        if (field_number == kEnvelopeFieldClientRequestOk) {
            if (!read_length_delimited(envelope, &offset, &client_request_ok_payload, error_message, error_message_capacity)) {
                return false;
            }
            continue;
        }

        if (!skip_field(envelope, &offset, wire_type, error_message, error_message_capacity)) {
            return false;
        }
    }

    decoded->kind = static_cast<RuntimeMessageKind>(kind);
    switch (kind) {
    case kMessageHeartbeat:
        if (heartbeat_payload.empty()) {
            set_error(error_message, error_message_capacity, "expected HEARTBEAT payload from main node");
            return false;
        }
        return parse_heartbeat_payload(heartbeat_payload, &decoded->heartbeat, error_message, error_message_capacity);
    case kMessageClientResponse:
        if (response_payload.empty()) {
            set_error(error_message, error_message_capacity, "expected CLIENT_RESPONSE payload from main node");
            return false;
        }
        return parse_client_response_payload(response_payload, &decoded->client_response, error_message, error_message_capacity);
    case kMessageClientInfoReply:
        if (client_info_reply_payload.empty()) {
            set_error(error_message, error_message_capacity, "expected CLIENT_INFO_REPLY payload from main node");
            return false;
        }
        return parse_client_info_reply_payload(client_info_reply_payload, &decoded->client_info_reply, error_message, error_message_capacity);
    case kMessageClientRequestOk:
        if (client_request_ok_payload.empty()) {
            set_error(error_message, error_message_capacity, "expected CLIENT_REQUEST_OK payload from main node");
            return false;
        }
        return parse_client_request_ok_payload(client_request_ok_payload, &decoded->client_request_ok, error_message, error_message_capacity);
    default:
        set_error(error_message, error_message_capacity, "unexpected runtime message from main node");
        return false;
    }
}

std::vector<std::uint8_t> build_client_join_envelope(const std::string& client_name) {
    std::vector<std::uint8_t> join_payload;
    append_string_field(&join_payload, 1U, client_name);

    std::vector<std::uint8_t> envelope;
    append_uint_field(&envelope, kEnvelopeFieldKind, kMessageClientJoin);
    append_message_field(&envelope, kEnvelopeFieldClientJoin, join_payload);
    return envelope;
}

std::vector<std::uint8_t> build_heartbeat_ok_envelope(
    const std::string& client_name,
    std::uint64_t heartbeat_unix_time_ms,
    std::uint64_t received_unix_time_ms,
    const std::vector<std::string>& active_task_ids) {
    std::vector<std::uint8_t> payload;
    append_string_field(&payload, kHeartbeatOkFieldNodeName, client_name);
    append_uint_field(&payload, kHeartbeatOkFieldHeartbeatUnixTimeMs, heartbeat_unix_time_ms);
    append_uint_field(&payload, kHeartbeatOkFieldReceivedUnixTimeMs, received_unix_time_ms);
    for (const std::string& task_id : active_task_ids) {
        append_string_field(&payload, kHeartbeatOkFieldActiveTaskIds, task_id);
    }

    std::vector<std::uint8_t> envelope;
    append_uint_field(&envelope, kEnvelopeFieldKind, kMessageHeartbeatOk);
    append_message_field(&envelope, kEnvelopeFieldHeartbeatOk, payload);
    return envelope;
}

std::vector<std::uint8_t> build_client_info_request_envelope(
    const std::string& client_id,
    const std::string& client_name,
    std::uint64_t timestamp_ms) {
    std::vector<std::uint8_t> payload;
    append_string_field(&payload, kClientInfoRequestFieldClientId, client_id);
    append_string_field(&payload, kClientInfoRequestFieldClientName, client_name);
    append_uint_field(&payload, kClientInfoRequestFieldTimestampMs, timestamp_ms);

    std::vector<std::uint8_t> envelope;
    append_uint_field(&envelope, kEnvelopeFieldKind, kMessageClientInfoRequest);
    append_message_field(&envelope, kEnvelopeFieldClientInfoRequest, payload);
    return envelope;
}

std::vector<std::uint8_t> build_client_request_envelope(
    const std::string& client_name,
    const std::string& request_id,
    const std::string& method,
    const std::string& size,
    const std::string& object_id,
    const std::string& stream_id,
    std::uint64_t timestamp_ms,
    std::uint32_t iteration_count,
    std::uint32_t request_payload_field_number,
    const std::vector<std::uint8_t>& method_payload) {
    std::vector<std::uint8_t> client_request;
    append_string_field(&client_request, kClientRequestFieldRequestId, request_id);
    append_string_field(&client_request, kClientRequestFieldClientName, client_name);
    append_string_field(&client_request, kClientRequestFieldMethod, method);
    append_string_field(&client_request, kClientRequestFieldSize, size);
    append_string_field(&client_request, kClientRequestFieldObjectId, object_id);
    append_string_field(&client_request, kClientRequestFieldStreamId, stream_id);
    append_uint_field(&client_request, kClientRequestFieldTimestampMs, timestamp_ms);
    append_uint_field(&client_request, kClientRequestFieldIterationCount, iteration_count);
    if (request_payload_field_number != 0U && !method_payload.empty()) {
        append_message_field(&client_request, request_payload_field_number, method_payload);
    }

    std::vector<std::uint8_t> envelope;
    append_uint_field(&envelope, kEnvelopeFieldKind, kMessageClientRequest);
    append_message_field(&envelope, kEnvelopeFieldClientRequest, client_request);
    return envelope;
}

std::vector<std::uint8_t> build_gemv_request_payload(
    const std::uint8_t* raw_bytes,
    std::size_t byte_count,
    std::uint32_t vector_length) {
    std::vector<std::uint8_t> payload;
    append_uint_field(&payload, kGemvRequestFieldVectorLength, vector_length);
    append_bytes_field(&payload, kGemvRequestFieldVectorData, raw_bytes, byte_count);
    return payload;
}

std::vector<std::uint8_t> build_conv2d_request_payload(
    const SwcConv2dShape& shape,
    const SwcConv2dRequestOptions* conv2d_options) {
    std::vector<std::uint8_t> payload;
    append_uint_field(&payload, kConv2dRequestFieldTensorH, shape.tensor_h);
    append_uint_field(&payload, kConv2dRequestFieldTensorW, shape.tensor_w);
    append_uint_field(&payload, kConv2dRequestFieldChannelsIn, shape.channels_in);
    append_uint_field(&payload, kConv2dRequestFieldChannelsOut, shape.channels_out);
    append_uint_field(&payload, kConv2dRequestFieldKernelSize, shape.kernel_size);
    append_uint_field(&payload, kConv2dRequestFieldPadding, shape.padding);
    append_uint_field(&payload, kConv2dRequestFieldStride, shape.stride);
    if (conv2d_options != nullptr) {
        append_uint_field(&payload, kConv2dRequestFieldClientResponseMode, conv2d_options->client_response_mode);
        if (conv2d_options->stats_max_samples != 0U) {
            append_uint_field(&payload, kConv2dRequestFieldStatsMaxSamples, conv2d_options->stats_max_samples);
        }
        if (conv2d_options->upload_size_bytes != 0U) {
            append_uint_field(&payload, kConv2dRequestFieldUploadSizeBytes, conv2d_options->upload_size_bytes);
        }
        const std::string upload_checksum = trim_utf8(
            conv2d_options->upload_checksum,
            "");
        if (!upload_checksum.empty()) {
            append_string_field(&payload, kConv2dRequestFieldUploadChecksum, upload_checksum);
        }
    }
    return payload;
}

bool send_framed_message(
    SOCKET socket_handle,
    const std::vector<std::uint8_t>& payload,
    char* error_message,
    std::uint32_t error_message_capacity) {
    const std::uint32_t payload_size = static_cast<std::uint32_t>(payload.size());
    const std::uint32_t network_size = htonl(payload_size);
    if (!send_all(socket_handle, reinterpret_cast<const std::uint8_t*>(&network_size), sizeof(network_size), error_message, error_message_capacity)) {
        return false;
    }
    return send_all(socket_handle, payload.data(), payload.size(), error_message, error_message_capacity);
}

ReceiveStatus recv_framed_message(
    SOCKET socket_handle,
    std::vector<std::uint8_t>* payload,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::uint32_t network_size = 0;
    const ReceiveStatus header_status = recv_exactly(
        socket_handle,
        reinterpret_cast<std::uint8_t*>(&network_size),
        sizeof(network_size),
        error_message,
        error_message_capacity);
    if (header_status != ReceiveStatus::kOk) {
        return header_status;
    }

    const std::uint32_t payload_size = ntohl(network_size);
    if (payload_size == 0U || payload_size > kMaxMessageSize) {
        set_error(error_message, error_message_capacity, "runtime message size is invalid");
        return ReceiveStatus::kError;
    }

    payload->assign(payload_size, 0);
    return recv_exactly(socket_handle, payload->data(), payload->size(), error_message, error_message_capacity);
}

}  // namespace

bool send_client_join(
    SOCKET socket_handle,
    const std::string& client_name,
    char* error_message,
    std::uint32_t error_message_capacity) {
    return send_framed_message(
        socket_handle,
        build_client_join_envelope(client_name),
        error_message,
        error_message_capacity);
}

bool send_heartbeat_ok(
    SOCKET socket_handle,
    const std::string& client_name,
    std::uint64_t heartbeat_unix_time_ms,
    std::uint64_t received_unix_time_ms,
    const std::vector<std::string>& active_task_ids,
    char* error_message,
    std::uint32_t error_message_capacity) {
    return send_framed_message(
        socket_handle,
        build_heartbeat_ok_envelope(
            client_name,
            heartbeat_unix_time_ms,
            received_unix_time_ms,
            active_task_ids),
        error_message,
        error_message_capacity);
}

bool send_client_info_request(
    SOCKET socket_handle,
    const std::string& client_id,
    const std::string& client_name,
    std::uint64_t timestamp_ms,
    char* error_message,
    std::uint32_t error_message_capacity) {
    return send_framed_message(
        socket_handle,
        build_client_info_request_envelope(client_id, client_name, timestamp_ms),
        error_message,
        error_message_capacity);
}

bool send_gemv_request(
    SOCKET socket_handle,
    const std::string& client_name,
    const std::string& request_id,
    const std::string& size,
    const std::string& object_id,
    const std::string& stream_id,
    std::uint64_t timestamp_ms,
    std::uint32_t iteration_count,
    const float* values,
    std::uint32_t value_count,
    char* error_message,
    std::uint32_t error_message_capacity) {
    return send_framed_message(
        socket_handle,
        build_client_request_envelope(
            client_name,
            request_id,
            kMethodGemvMultiplication,
            size,
            object_id,
            stream_id,
            timestamp_ms,
            iteration_count,
            kClientRequestFieldGemvPayload,
            build_gemv_request_payload(
                reinterpret_cast<const std::uint8_t*>(values),
                static_cast<std::size_t>(value_count) * sizeof(float),
                value_count)),
        error_message,
        error_message_capacity);
}

bool send_conv2d_request(
    SOCKET socket_handle,
    const std::string& client_name,
    const std::string& request_id,
    const std::string& size,
    const std::string& object_id,
    const std::string& stream_id,
    std::uint64_t timestamp_ms,
    std::uint32_t iteration_count,
    const SwcConv2dShape& shape,
    const SwcConv2dRequestOptions* conv2d_options,
    char* error_message,
    std::uint32_t error_message_capacity) {
    return send_framed_message(
        socket_handle,
        build_client_request_envelope(
            client_name,
            request_id,
            kMethodConv2d,
            size,
            object_id,
            stream_id,
            timestamp_ms,
            iteration_count,
            kClientRequestFieldConv2dPayload,
            build_conv2d_request_payload(shape, conv2d_options)),
        error_message,
        error_message_capacity);
}

bool send_free_content_request(
    SOCKET socket_handle,
    const std::string& client_name,
    const std::string& request_id,
    const std::string& method,
    const std::string& object_id,
    const std::string& stream_id,
    std::uint64_t timestamp_ms,
    std::uint32_t iteration_count,
    const std::string& content,
    char* error_message,
    std::uint32_t error_message_capacity) {
    return send_framed_message(
        socket_handle,
        build_client_request_envelope(
            client_name,
            request_id,
            method,
            "",
            object_id,
            stream_id,
            timestamp_ms,
            iteration_count,
            kClientRequestFieldGemvPayload,
            build_gemv_request_payload(
                reinterpret_cast<const std::uint8_t*>(content.data()),
                content.size(),
                static_cast<std::uint32_t>(content.size()))),
        error_message,
        error_message_capacity);
}

bool receive_runtime_message(
    SOCKET socket_handle,
    RuntimeMessageData* message,
    bool* timed_out,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (message == nullptr) {
        set_error(error_message, error_message_capacity, "runtime message output is null");
        return false;
    }
    if (timed_out != nullptr) {
        *timed_out = false;
    }

    *message = RuntimeMessageData{};
    std::vector<std::uint8_t> envelope;
    const ReceiveStatus status = recv_framed_message(socket_handle, &envelope, error_message, error_message_capacity);
    if (status == ReceiveStatus::kTimeout) {
        if (timed_out != nullptr) {
            *timed_out = true;
        }
        return true;
    }
    if (status != ReceiveStatus::kOk) {
        return false;
    }
    return parse_runtime_envelope(envelope, message, error_message, error_message_capacity);
}

}  // namespace swc
