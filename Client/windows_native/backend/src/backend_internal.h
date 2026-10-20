#pragma once

#include <winsock2.h>
#include <ws2tcpip.h>

#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "../include/superweb_client_backend.h"

namespace swc {

constexpr char kDefaultClientName[] = "windows superweb client";
constexpr char kMethodGemvMultiplication[] = "gemv";
constexpr char kMethodConv2d[] = "conv2d";
constexpr char kMethodFreeContent[] = "free_content";
constexpr char kDefaultMulticastGroup[] = "224.0.0.251";
constexpr char kMdnsServiceType[] = "_superweb-cluster._tcp.local.";
constexpr char kMdnsServiceRole[] = "main-node";
constexpr std::uint16_t kDefaultTcpPort = 52020;
constexpr std::uint16_t kDefaultDiscoveryPort = 5353;
constexpr std::uint32_t kDefaultVectorLength = 32768;
constexpr std::uint32_t kDefaultDiscoveryTimeoutMs = 3000;
constexpr std::uint32_t kDefaultDiscoveryAttempts = 3;
constexpr std::uint32_t kDefaultRetryDelayMs = 1000;
constexpr std::uint32_t kDefaultBufferSize = 2048;
constexpr std::uint8_t kDefaultMulticastTtl = 255;
constexpr std::uint32_t kMaxMessageSize = 4 * 1024 * 1024;
constexpr std::int32_t kStatusOk = 200;
constexpr std::uint32_t kDefaultClientInfoIntervalMs = 5000;
constexpr std::uint32_t kDefaultClientInfoFailureLimit = 10;

enum class RuntimeMessageKind : std::uint32_t {
    kUnknown = 0,
    kHeartbeat = 3,
    kClientResponse = 7,
    kClientInfoReply = 15,
    kClientRequestOk = 16,
};

struct HeartbeatData {
    std::string main_node_name;
    std::uint64_t unix_time_ms = 0;
};

struct ClientInfoReplyData {
    std::string client_id;
    std::uint64_t request_timestamp_ms = 0;
    std::uint64_t reply_timestamp_ms = 0;
    std::uint64_t timeout_ms = 0;
    bool has_active_tasks = false;
    std::vector<std::string> active_task_ids;
};

struct ClientRequestOkData {
    std::string client_id;
    std::string task_id;
    std::string method;
    std::string size;
    std::string object_id;
    std::uint64_t accepted_timestamp_ms = 0;
    std::string upload_id;
    std::string download_id;
    std::string data_endpoint_host;
    std::uint32_t data_endpoint_port = 0;
};

struct WorkerTimingData {
    std::string node_id;
    std::string task_id;
    std::string slice;
    std::uint64_t wall_ms = 0;
    std::uint64_t artifact_fetch_ms = 0;
    std::uint64_t computation_ms = 0;
    std::uint64_t peripheral_ms = 0;
};

struct ResponseTimingData {
    std::uint64_t dispatch_ms = 0;
    std::uint64_t task_window_ms = 0;
    std::uint64_t aggregate_ms = 0;
    std::vector<WorkerTimingData> workers;
};

struct ClientResponseData {
    std::string client_id;
    std::string request_id;
    std::string method;
    std::string size;
    std::string object_id;
    std::string stream_id;
    std::string task_id;
    std::uint64_t timestamp_ms = 0;
    std::uint64_t elapsed_ms = 0;
    std::int32_t status_code = 0;
    std::uint32_t output_length = 0;
    std::string error_message;
    std::string output_text;
    std::vector<float> output_values;
    std::uint64_t conv2d_stats_element_count = 0;
    double conv2d_stats_sum = 0.0;
    double conv2d_stats_sum_squares = 0.0;
    std::vector<float> conv2d_stats_samples;
    std::uint32_t worker_count = 0;
    std::uint32_t client_count = 0;
    std::uint32_t iteration_count = 1;
    SwcArtifactDescriptor result_artifact{};
    ResponseTimingData timing;
};

struct ClientSession {
    SOCKET socket_handle = INVALID_SOCKET;
    std::string client_name = kDefaultClientName;
    std::string client_id;
    std::mutex send_mutex;
    std::mutex state_mutex;
    std::condition_variable condition;
    std::thread reader_thread;
    std::thread info_poll_thread;
    bool info_poll_started = false;
    bool stop_requested = false;
    bool system_dead = false;
    std::string closed_reason;
    std::deque<ClientResponseData> response_queue;
    std::unordered_map<std::string, std::deque<ClientRequestOkData>> request_ok_queue_by_local_id;
    std::deque<std::string> awaiting_request_ok_local_ids;
    std::unordered_map<std::uint64_t, std::deque<ClientInfoReplyData>> info_reply_queue;
    std::unordered_set<std::string> pending_task_ids;
    std::unordered_map<std::string, std::string> local_request_id_by_task_id;
    std::uint64_t info_request_in_flight_ms = 0;
    std::uint32_t info_poll_failures = 0;
};

struct RuntimeMessageData {
    RuntimeMessageKind kind = RuntimeMessageKind::kUnknown;
    HeartbeatData heartbeat;
    ClientInfoReplyData client_info_reply;
    ClientRequestOkData client_request_ok;
    ClientResponseData client_response;
};

struct MainNodeEndpoint {
    std::string host;
    std::uint16_t port = 0;
    std::string source;
};

bool ensure_winsock(char* error_message, std::uint32_t error_message_capacity);
void set_error(char* error_message, std::uint32_t error_message_capacity, const std::string& message);
std::uint64_t unix_time_ms();
std::string make_request_id();
std::string trim_utf8(const char* value, const char* fallback = "");
void clear_response(SwcResponse* response);
void fill_response(const ClientResponseData& decoded, SwcResponse* response);

bool generate_default_vector(
    std::uint64_t seed,
    std::uint32_t vector_length,
    std::vector<float>* output,
    char* error_message,
    std::uint32_t error_message_capacity);

bool discover_main_node(
    const std::string& client_name,
    const SwcDiscoveryOptions* options,
    MainNodeEndpoint* endpoint,
    char* error_message,
    std::uint32_t error_message_capacity);

bool open_client_socket(
    const std::string& host,
    std::uint16_t port,
    std::uint32_t timeout_ms,
    ClientSession* session,
    char* error_message,
    std::uint32_t error_message_capacity);

bool send_client_join(
    SOCKET socket_handle,
    const std::string& client_name,
    char* error_message,
    std::uint32_t error_message_capacity);

bool send_heartbeat_ok(
    SOCKET socket_handle,
    const std::string& client_name,
    std::uint64_t heartbeat_unix_time_ms,
    std::uint64_t received_unix_time_ms,
    const std::vector<std::string>& active_task_ids,
    char* error_message,
    std::uint32_t error_message_capacity);

bool send_client_info_request(
    SOCKET socket_handle,
    const std::string& client_id,
    const std::string& client_name,
    std::uint64_t timestamp_ms,
    char* error_message,
    std::uint32_t error_message_capacity);

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
    std::uint32_t error_message_capacity);

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
    std::uint32_t error_message_capacity);

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
    std::uint32_t error_message_capacity);

bool receive_runtime_message(
    SOCKET socket_handle,
    RuntimeMessageData* message,
    bool* timed_out,
    char* error_message,
    std::uint32_t error_message_capacity);

}  // namespace swc
