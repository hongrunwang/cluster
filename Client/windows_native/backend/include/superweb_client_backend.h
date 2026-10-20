#pragma once

#include <stdint.h>

#ifdef _WIN32
#if defined(SWC_BUILD_DLL)
#define SWC_API __declspec(dllexport)
#elif defined(SWC_USE_DLL)
#define SWC_API __declspec(dllimport)
#else
#define SWC_API
#endif
#define SWC_CALL __cdecl
#else
#define SWC_API
#define SWC_CALL
#endif

extern "C" {

/*
 * Stable C ABI for the Windows-native superweb client backend.
 *
 * The exported functions are synchronous by design so that a future WinUI3
 * frontend can choose its own threading model and wrap these calls behind
 * background tasks or coroutines.
 */

typedef void* SwcClientHandle;

typedef struct SwcDiscoveryOptions {
    const char* multicast_group;
    uint16_t udp_port;
    uint32_t discovery_timeout_ms;
    uint32_t discovery_attempts;
    uint32_t retry_delay_ms;
    uint32_t buffer_size;
    uint8_t multicast_ttl;
} SwcDiscoveryOptions;

typedef struct SwcEndpoint {
    char host[256];
    uint16_t port;
    char source[32];
} SwcEndpoint;

typedef struct SwcRequestOptions {
    const char* client_name;
    const char* request_id;
    const char* object_id;
    const char* stream_id;
    uint64_t timestamp_ms;
    uint32_t iteration_count;
} SwcRequestOptions;

typedef struct SwcConv2dShape {
    uint32_t tensor_h;
    uint32_t tensor_w;
    uint32_t channels_in;
    uint32_t channels_out;
    uint32_t kernel_size;
    uint32_t padding;
    uint32_t stride;
} SwcConv2dShape;

typedef struct SwcConv2dRequestOptions {
    uint32_t client_response_mode;
    uint32_t stats_max_samples;
    uint64_t upload_size_bytes;
    const char* upload_checksum;
} SwcConv2dRequestOptions;

typedef struct SwcVectorBuffer {
    float* values;
    uint32_t length;
} SwcVectorBuffer;

typedef struct SwcArtifactDescriptor {
    char artifact_id[128];
    char content_type[64];
    uint64_t size_bytes;
    char checksum[128];
    char producer_node_id[64];
    char transfer_host[256];
    uint32_t transfer_port;
    uint32_t chunk_size;
    uint8_t ready;
} SwcArtifactDescriptor;

typedef struct SwcWorkerTiming {
    char node_id[64];
    char task_id[128];
    char slice[64];
    uint64_t wall_ms;
    uint64_t artifact_fetch_ms;
    uint64_t computation_ms;
    uint64_t peripheral_ms;
} SwcWorkerTiming;

typedef struct SwcResponseTiming {
    uint64_t dispatch_ms;
    uint64_t task_window_ms;
    uint64_t aggregate_ms;
    SwcWorkerTiming* workers;
    uint32_t worker_count;
} SwcResponseTiming;

/*
 * Zero-initialize this struct before first use, for example:
 *   SwcResponse response{};
 * Call `swc_reset_response(&response)` before reusing it.
 */
typedef struct SwcResponse {
    int32_t status_code;
    uint64_t timestamp_ms;
    uint64_t elapsed_ms;
    uint32_t output_length;
    uint32_t worker_count;
    uint32_t client_count;
    uint32_t iteration_count;
    char client_id[64];
    char request_id[128];
    char method[128];
    char size[32];
    char object_id[128];
    char stream_id[128];
    char task_id[128];
    char error_message[512];
    char output_text[2048];
    SwcArtifactDescriptor result_artifact;
    uint64_t conv2d_stats_element_count;
    double conv2d_stats_sum;
    double conv2d_stats_sum_squares;
    float* conv2d_stats_samples;
    uint32_t conv2d_stats_sample_count;
    SwcResponseTiming timing;
    SwcVectorBuffer output_vector;
} SwcResponse;

typedef void (SWC_CALL *SwcRequestAcceptedCallback)(
    const char* request_id,
    const char* task_id,
    const char* method,
    const char* size,
    const char* object_id,
    const char* client_id,
    uint64_t accepted_timestamp_ms,
    const char* upload_id,
    const char* download_id,
    const char* data_endpoint_host,
    uint32_t data_endpoint_port,
    void* user_data);

SWC_API int SWC_CALL swc_generate_default_vector(
    uint64_t seed,
    uint32_t vector_length,
    SwcVectorBuffer* out_vector,
    char* error_message,
    uint32_t error_message_capacity);

SWC_API int SWC_CALL swc_discover_main_node(
    const char* client_name,
    const SwcDiscoveryOptions* options,
    SwcEndpoint* out_endpoint,
    char* error_message,
    uint32_t error_message_capacity);

SWC_API int SWC_CALL swc_open_client(
    const char* host,
    uint16_t port,
    uint32_t timeout_ms,
    SwcClientHandle* out_handle,
    char* error_message,
    uint32_t error_message_capacity);

SWC_API int SWC_CALL swc_join_client(
    SwcClientHandle handle,
    const char* client_name,
    SwcResponse* out_response,
    char* error_message,
    uint32_t error_message_capacity);

SWC_API int SWC_CALL swc_request_gemv(
    SwcClientHandle handle,
    const SwcRequestOptions* options,
    const char* size,
    const float* input_values,
    uint32_t value_count,
    SwcResponse* out_response,
    SwcRequestAcceptedCallback on_request_accepted,
    void* user_data,
    char* error_message,
    uint32_t error_message_capacity);

SWC_API int SWC_CALL swc_request_conv2d(
    SwcClientHandle handle,
    const SwcRequestOptions* options,
    const char* size,
    const SwcConv2dShape* shape,
    const SwcConv2dRequestOptions* conv2d_options,
    SwcResponse* out_response,
    SwcRequestAcceptedCallback on_request_accepted,
    void* user_data,
    char* error_message,
    uint32_t error_message_capacity);

SWC_API int SWC_CALL swc_request_free_content(
    SwcClientHandle handle,
    const SwcRequestOptions* options,
    const char* method,
    const char* content,
    SwcResponse* out_response,
    SwcRequestAcceptedCallback on_request_accepted,
    void* user_data,
    char* error_message,
    uint32_t error_message_capacity);

SWC_API void SWC_CALL swc_reset_response(SwcResponse* response);
SWC_API void SWC_CALL swc_free_vector(SwcVectorBuffer* buffer);
SWC_API void SWC_CALL swc_close_client(SwcClientHandle handle);

}
