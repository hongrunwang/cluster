#include "backend_internal.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <sstream>

namespace swc {

namespace {

bool g_winsock_ready = false;

void copy_text(char* destination, std::size_t capacity, const std::string& value) {
    if (destination == nullptr || capacity == 0U) {
        return;
    }
    const std::size_t copy_length = std::min<std::size_t>(capacity - 1U, value.size());
    std::memcpy(destination, value.data(), copy_length);
    destination[copy_length] = '\0';
}

bool session_is_closed_locked(const ClientSession& session) {
    return !session.closed_reason.empty();
}

void close_socket_if_needed(SOCKET socket_handle) {
    if (socket_handle != INVALID_SOCKET) {
        closesocket(socket_handle);
    }
}

void request_session_stop(ClientSession* session, const std::string& reason = "") {
    SOCKET socket_handle = INVALID_SOCKET;
    {
        std::lock_guard<std::mutex> lock(session->state_mutex);
        if (!reason.empty() && session->closed_reason.empty()) {
            session->closed_reason = reason;
        }
        session->stop_requested = true;
        socket_handle = session->socket_handle;
        session->socket_handle = INVALID_SOCKET;
    }
    close_socket_if_needed(socket_handle);
    session->condition.notify_all();
}

void join_if_needed(std::thread* thread) {
    if (thread == nullptr || !thread->joinable()) {
        return;
    }
    if (thread->get_id() == std::this_thread::get_id()) {
        thread->detach();
        return;
    }
    thread->join();
}

std::vector<std::string> snapshot_active_task_ids(ClientSession* session) {
    std::lock_guard<std::mutex> lock(session->state_mutex);
    return std::vector<std::string>(session->pending_task_ids.begin(), session->pending_task_ids.end());
}

bool ensure_session_open(ClientSession* session, char* error_message, std::uint32_t error_message_capacity) {
    std::lock_guard<std::mutex> lock(session->state_mutex);
    if (!session->closed_reason.empty()) {
        set_error(error_message, error_message_capacity, session->closed_reason);
        return false;
    }
    if (session->socket_handle == INVALID_SOCKET) {
        set_error(error_message, error_message_capacity, "client TCP session is closed");
        return false;
    }
    return true;
}

void remove_awaiting_request_ok_locked(ClientSession* session, const std::string& local_request_id) {
    auto iterator = std::find(
        session->awaiting_request_ok_local_ids.begin(),
        session->awaiting_request_ok_local_ids.end(),
        local_request_id);
    if (iterator != session->awaiting_request_ok_local_ids.end()) {
        session->awaiting_request_ok_local_ids.erase(iterator);
    }
}

void cancel_awaiting_request_ok(ClientSession* session, const std::string& local_request_id) {
    std::lock_guard<std::mutex> lock(session->state_mutex);
    remove_awaiting_request_ok_locked(session, local_request_id);
}

void notify_request_accepted(
    SwcRequestAcceptedCallback callback,
    const std::string& request_id,
    const ClientRequestOkData& request_ok,
    void* user_data) {
    if (callback == nullptr) {
        return;
    }

    callback(
        request_id.c_str(),
        request_ok.task_id.c_str(),
        request_ok.method.c_str(),
        request_ok.size.c_str(),
        request_ok.object_id.c_str(),
        request_ok.client_id.c_str(),
        request_ok.accepted_timestamp_ms,
        request_ok.upload_id.c_str(),
        request_ok.download_id.c_str(),
        request_ok.data_endpoint_host.c_str(),
        request_ok.data_endpoint_port,
        user_data);
}

bool pop_client_response_locked(ClientSession* session, ClientResponseData* response) {
    if (session->response_queue.empty()) {
        return false;
    }
    *response = std::move(session->response_queue.front());
    session->response_queue.pop_front();
    return true;
}

bool pop_client_request_ok_locked(ClientSession* session, const std::string& local_request_id, ClientRequestOkData* request_ok) {
    auto queue_iterator = session->request_ok_queue_by_local_id.find(local_request_id);
    if (queue_iterator == session->request_ok_queue_by_local_id.end() || queue_iterator->second.empty()) {
        return false;
    }
    *request_ok = std::move(queue_iterator->second.front());
    queue_iterator->second.pop_front();
    if (queue_iterator->second.empty()) {
        session->request_ok_queue_by_local_id.erase(queue_iterator);
    }
    return true;
}

bool wait_for_client_response(
    ClientSession* session,
    ClientResponseData* response,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::unique_lock<std::mutex> lock(session->state_mutex);
    while (true) {
        if (pop_client_response_locked(session, response)) {
            return true;
        }
        if (session_is_closed_locked(*session)) {
            set_error(error_message, error_message_capacity, session->closed_reason);
            return false;
        }
        session->condition.wait_for(lock, std::chrono::milliseconds(500));
    }
}

bool wait_for_client_request_ok(
    ClientSession* session,
    const std::string& local_request_id,
    ClientRequestOkData* request_ok,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::unique_lock<std::mutex> lock(session->state_mutex);
    while (true) {
        if (pop_client_request_ok_locked(session, local_request_id, request_ok)) {
            return true;
        }
        if (session_is_closed_locked(*session)) {
            set_error(error_message, error_message_capacity, session->closed_reason);
            return false;
        }
        session->condition.wait_for(lock, std::chrono::milliseconds(500));
    }
}

void info_poll_loop(ClientSession* session);

bool start_info_polling(ClientSession* session, char* error_message, std::uint32_t error_message_capacity) {
    std::lock_guard<std::mutex> lock(session->state_mutex);
    if (session->info_poll_started) {
        return true;
    }
    try {
        session->info_poll_thread = std::thread(info_poll_loop, session);
        session->info_poll_started = true;
        return true;
    } catch (const std::exception& ex) {
        set_error(error_message, error_message_capacity, std::string("unable to start client info polling thread: ") + ex.what());
        return false;
    }
}

void mark_task_started(
    ClientSession* session,
    const std::string& local_request_id,
    const std::string& task_id,
    char* error_message,
    std::uint32_t error_message_capacity) {
    {
        std::lock_guard<std::mutex> lock(session->state_mutex);
        if (!task_id.empty()) {
            session->pending_task_ids.insert(task_id);
            session->local_request_id_by_task_id[task_id] = local_request_id;
        }
    }
    (void)start_info_polling(session, error_message, error_message_capacity);
}

void mark_task_finished(ClientSession* session, const std::string& task_id) {
    std::lock_guard<std::mutex> lock(session->state_mutex);
    if (!task_id.empty()) {
        session->pending_task_ids.erase(task_id);
        session->local_request_id_by_task_id.erase(task_id);
    }
    if (session->pending_task_ids.empty()) {
        session->info_request_in_flight_ms = 0;
        session->info_poll_failures = 0;
    }
}

void reader_loop(ClientSession* session) {
    char error_message[512]{};
    while (true) {
        {
            std::lock_guard<std::mutex> lock(session->state_mutex);
            if (session->stop_requested) {
                return;
            }
        }

        RuntimeMessageData message;
        bool timed_out = false;
        if (!receive_runtime_message(
                session->socket_handle,
                &message,
                &timed_out,
                error_message,
                static_cast<std::uint32_t>(sizeof(error_message)))) {
            std::lock_guard<std::mutex> lock(session->state_mutex);
            if (session->stop_requested) {
                return;
            }
            if (session->closed_reason.empty()) {
                session->closed_reason = error_message[0] != '\0'
                    ? error_message
                    : "reader loop failed on the client socket";
            }
            session->condition.notify_all();
            return;
        }

        if (timed_out) {
            continue;
        }

        if (message.kind == RuntimeMessageKind::kHeartbeat) {
            const std::vector<std::string> active_task_ids = snapshot_active_task_ids(session);
            std::string client_name;
            {
                std::lock_guard<std::mutex> lock(session->state_mutex);
                client_name = session->client_name.empty() ? kDefaultClientName : session->client_name;
            }

            char heartbeat_error[512]{};
            {
                std::lock_guard<std::mutex> send_lock(session->send_mutex);
                if (session->stop_requested || session->socket_handle == INVALID_SOCKET) {
                    return;
                }
                if (!send_heartbeat_ok(
                        session->socket_handle,
                        client_name,
                        message.heartbeat.unix_time_ms,
                        unix_time_ms(),
                        active_task_ids,
                        heartbeat_error,
                        static_cast<std::uint32_t>(sizeof(heartbeat_error)))) {
                    std::lock_guard<std::mutex> lock(session->state_mutex);
                    if (session->closed_reason.empty()) {
                        session->closed_reason = heartbeat_error[0] != '\0'
                            ? heartbeat_error
                            : "unable to acknowledge main-node heartbeat";
                    }
                    session->condition.notify_all();
                    return;
                }
            }
            continue;
        }

        std::lock_guard<std::mutex> lock(session->state_mutex);
        if (message.kind == RuntimeMessageKind::kClientResponse) {
            session->response_queue.push_back(std::move(message.client_response));
        } else if (message.kind == RuntimeMessageKind::kClientRequestOk) {
            if (session->awaiting_request_ok_local_ids.empty()) {
                if (session->closed_reason.empty()) {
                    session->closed_reason = "unexpected client request acknowledgement without a pending request";
                }
                session->condition.notify_all();
                return;
            }
            const std::string local_request_id = session->awaiting_request_ok_local_ids.front();
            session->awaiting_request_ok_local_ids.pop_front();
            if (session->client_id.empty() && !message.client_request_ok.client_id.empty()) {
                session->client_id = message.client_request_ok.client_id;
            }
            session->request_ok_queue_by_local_id[local_request_id].push_back(std::move(message.client_request_ok));
        } else if (message.kind == RuntimeMessageKind::kClientInfoReply) {
            const std::uint64_t request_timestamp_ms = message.client_info_reply.request_timestamp_ms;
            session->info_reply_queue[request_timestamp_ms].push_back(std::move(message.client_info_reply));
            session->info_poll_failures = 0;
            if (session->info_request_in_flight_ms == request_timestamp_ms) {
                session->info_request_in_flight_ms = 0;
            }
        } else {
            if (session->closed_reason.empty()) {
                session->closed_reason = "unexpected runtime message from main node";
            }
            session->condition.notify_all();
            return;
        }
        session->condition.notify_all();
    }
}

void info_poll_loop(ClientSession* session) {
    while (true) {
        std::string client_id;
        std::string client_name;
        std::uint64_t request_timestamp_ms = 0;
        bool should_close = false;

        {
            std::unique_lock<std::mutex> lock(session->state_mutex);
            if (session->condition.wait_for(
                    lock,
                    std::chrono::milliseconds(kDefaultClientInfoIntervalMs),
                    [session]() { return session->stop_requested; })) {
                return;
            }
            if (session->stop_requested) {
                return;
            }
            if (session->client_id.empty() || session->pending_task_ids.empty()) {
                session->info_request_in_flight_ms = 0;
                session->info_poll_failures = 0;
                continue;
            }
            if (session->info_request_in_flight_ms != 0U) {
                session->info_poll_failures += 1U;
                session->info_request_in_flight_ms = 0;
            }
            if (session->info_poll_failures >= kDefaultClientInfoFailureLimit) {
                session->system_dead = true;
                if (session->closed_reason.empty()) {
                    session->closed_reason =
                        "client info polling failed 10 consecutive times";
                }
                session->condition.notify_all();
                should_close = true;
            } else {
                request_timestamp_ms = unix_time_ms();
                session->info_request_in_flight_ms = request_timestamp_ms;
                client_id = session->client_id;
                client_name = session->client_name.empty() ? kDefaultClientName : session->client_name;
            }
        }

        if (should_close) {
            request_session_stop(session);
            return;
        }

        char error_message[512]{};
        bool send_failed = false;
        {
            std::lock_guard<std::mutex> send_lock(session->send_mutex);
            if (session->stop_requested || session->socket_handle == INVALID_SOCKET) {
                return;
            }
            send_failed = !send_client_info_request(
                session->socket_handle,
                client_id,
                client_name,
                request_timestamp_ms,
                error_message,
                static_cast<std::uint32_t>(sizeof(error_message)));
        }

        if (!send_failed) {
            continue;
        }

        {
            std::lock_guard<std::mutex> lock(session->state_mutex);
            session->info_request_in_flight_ms = 0;
            session->info_poll_failures += 1U;
            if (session->info_poll_failures >= kDefaultClientInfoFailureLimit) {
                session->system_dead = true;
                if (session->closed_reason.empty()) {
                    session->closed_reason =
                        "client info polling failed 10 consecutive times";
                }
                session->condition.notify_all();
                should_close = true;
            }
        }

        if (should_close) {
            request_session_stop(session);
            return;
        }
    }
}

bool start_reader_thread(ClientSession* session, char* error_message, std::uint32_t error_message_capacity) {
    try {
        session->reader_thread = std::thread(reader_loop, session);
        return true;
    } catch (const std::exception& ex) {
        set_error(error_message, error_message_capacity, std::string("unable to start client reader thread: ") + ex.what());
        return false;
    }
}

template <typename SendFn>
bool send_request_and_wait_for_response(
    ClientSession* session,
    const std::string& client_name,
    const std::string& local_request_id,
    SendFn&& send_request,
    SwcRequestAcceptedCallback on_request_accepted,
    void* user_data,
    SwcResponse* out_response,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (!ensure_session_open(session, error_message, error_message_capacity)) {
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(session->state_mutex);
        session->client_name = client_name;
        session->awaiting_request_ok_local_ids.push_back(local_request_id);
    }

    {
        std::lock_guard<std::mutex> send_lock(session->send_mutex);
        if (!send_request()) {
            cancel_awaiting_request_ok(session, local_request_id);
            return false;
        }
    }

    ClientRequestOkData request_ok;
    if (!wait_for_client_request_ok(session, local_request_id, &request_ok, error_message, error_message_capacity)) {
        cancel_awaiting_request_ok(session, local_request_id);
        return false;
    }

    notify_request_accepted(on_request_accepted, local_request_id, request_ok, user_data);

    if (!request_ok.client_id.empty()) {
        std::lock_guard<std::mutex> lock(session->state_mutex);
        session->client_id = request_ok.client_id;
    }
    mark_task_started(session, local_request_id, request_ok.task_id, error_message, error_message_capacity);

    ClientResponseData decoded;
    if (!wait_for_client_response(session, &decoded, error_message, error_message_capacity)) {
        return false;
    }

    const std::string task_id = decoded.task_id.empty() ? request_ok.task_id : decoded.task_id;
    if (!task_id.empty()) {
        mark_task_finished(session, task_id);
    }

    fill_response(decoded, out_response);
    return true;
}

}  // namespace

bool ensure_winsock(char* error_message, std::uint32_t error_message_capacity) {
    if (g_winsock_ready) {
        return true;
    }

    WSADATA winsock_data{};
    const int status = WSAStartup(MAKEWORD(2, 2), &winsock_data);
    if (status != 0) {
        set_error(error_message, error_message_capacity, "WSAStartup failed while initializing the Windows client backend");
        return false;
    }

    g_winsock_ready = true;
    return true;
}

void set_error(char* error_message, std::uint32_t error_message_capacity, const std::string& message) {
    if (error_message == nullptr || error_message_capacity == 0U) {
        return;
    }
    copy_text(error_message, error_message_capacity, message);
}

std::uint64_t unix_time_ms() {
    const auto now = std::chrono::time_point_cast<std::chrono::milliseconds>(std::chrono::system_clock::now());
    return static_cast<std::uint64_t>(now.time_since_epoch().count());
}

std::string make_request_id() {
    std::ostringstream builder;
    builder << "windows-client-" << unix_time_ms();
    return builder.str();
}

std::string trim_utf8(const char* value, const char* fallback) {
    std::string text = value != nullptr ? value : (fallback != nullptr ? fallback : "");
    const std::size_t first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return fallback != nullptr ? fallback : "";
    }
    const std::size_t last = text.find_last_not_of(" \t\r\n");
    return text.substr(first, last - first + 1U);
}

void clear_response(SwcResponse* response) {
    if (response == nullptr) {
        return;
    }
    std::memset(response, 0, sizeof(*response));
}

void fill_response(const ClientResponseData& decoded, SwcResponse* response) {
    if (response == nullptr) {
        return;
    }

    clear_response(response);
    response->status_code = decoded.status_code;
    response->timestamp_ms = decoded.timestamp_ms;
    response->elapsed_ms = decoded.elapsed_ms;
    response->output_length = decoded.output_length;
    response->worker_count = decoded.worker_count;
    response->client_count = decoded.client_count;
    response->iteration_count = decoded.iteration_count;

    copy_text(response->client_id, sizeof(response->client_id), decoded.client_id);
    copy_text(response->request_id, sizeof(response->request_id), decoded.request_id);
    copy_text(response->method, sizeof(response->method), decoded.method);
    copy_text(response->size, sizeof(response->size), decoded.size);
    copy_text(response->object_id, sizeof(response->object_id), decoded.object_id);
    copy_text(response->stream_id, sizeof(response->stream_id), decoded.stream_id);
    copy_text(response->task_id, sizeof(response->task_id), decoded.task_id);
    copy_text(response->error_message, sizeof(response->error_message), decoded.error_message);
    copy_text(response->output_text, sizeof(response->output_text), decoded.output_text);
    response->result_artifact = decoded.result_artifact;
    response->conv2d_stats_element_count = decoded.conv2d_stats_element_count;
    response->conv2d_stats_sum = decoded.conv2d_stats_sum;
    response->conv2d_stats_sum_squares = decoded.conv2d_stats_sum_squares;
    response->timing.dispatch_ms = decoded.timing.dispatch_ms;
    response->timing.task_window_ms = decoded.timing.task_window_ms;
    response->timing.aggregate_ms = decoded.timing.aggregate_ms;

    if (!decoded.output_values.empty()) {
        const std::size_t byte_count = decoded.output_values.size() * sizeof(float);
        response->output_vector.values = static_cast<float*>(std::malloc(byte_count));
        if (response->output_vector.values != nullptr) {
            std::memcpy(response->output_vector.values, decoded.output_values.data(), byte_count);
            response->output_vector.length = static_cast<std::uint32_t>(decoded.output_values.size());
        } else {
            response->output_length = 0;
            response->output_vector.length = 0;
            copy_text(response->error_message, sizeof(response->error_message), "unable to allocate the response output buffer");
        }
    }

    if (!decoded.conv2d_stats_samples.empty()) {
        const std::size_t byte_count = decoded.conv2d_stats_samples.size() * sizeof(float);
        response->conv2d_stats_samples = static_cast<float*>(std::malloc(byte_count));
        if (response->conv2d_stats_samples != nullptr) {
            std::memcpy(response->conv2d_stats_samples, decoded.conv2d_stats_samples.data(), byte_count);
            response->conv2d_stats_sample_count = static_cast<std::uint32_t>(decoded.conv2d_stats_samples.size());
        } else {
            response->conv2d_stats_sample_count = 0;
            copy_text(response->error_message, sizeof(response->error_message), "unable to allocate conv2d stats samples");
        }
    }

    if (!decoded.timing.workers.empty()) {
        const std::size_t byte_count = decoded.timing.workers.size() * sizeof(SwcWorkerTiming);
        response->timing.workers = static_cast<SwcWorkerTiming*>(std::malloc(byte_count));
        if (response->timing.workers != nullptr) {
            std::memset(response->timing.workers, 0, byte_count);
            response->timing.worker_count = static_cast<std::uint32_t>(decoded.timing.workers.size());
            for (std::size_t index = 0; index < decoded.timing.workers.size(); ++index) {
                const WorkerTimingData& source = decoded.timing.workers[index];
                SwcWorkerTiming& target = response->timing.workers[index];
                copy_text(target.node_id, sizeof(target.node_id), source.node_id);
                copy_text(target.task_id, sizeof(target.task_id), source.task_id);
                copy_text(target.slice, sizeof(target.slice), source.slice);
                target.wall_ms = source.wall_ms;
                target.artifact_fetch_ms = source.artifact_fetch_ms;
                target.computation_ms = source.computation_ms;
                target.peripheral_ms = source.peripheral_ms;
            }
        } else {
            response->timing.worker_count = 0;
            copy_text(response->error_message, sizeof(response->error_message), "unable to allocate response timing workers");
        }
    }
}

bool open_client_socket(
    const std::string& host,
    std::uint16_t port,
    std::uint32_t timeout_ms,
    ClientSession* session,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (session == nullptr) {
        set_error(error_message, error_message_capacity, "session output is null");
        return false;
    }
    if (!ensure_winsock(error_message, error_message_capacity)) {
        return false;
    }

    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;

    addrinfo* results = nullptr;
    const std::string port_text = std::to_string(port);
    if (getaddrinfo(host.c_str(), port_text.c_str(), &hints, &results) != 0) {
        set_error(error_message, error_message_capacity, "unable to resolve the main-node TCP endpoint");
        return false;
    }

    std::unique_ptr<addrinfo, decltype(&freeaddrinfo)> result_guard(results, freeaddrinfo);
    for (addrinfo* current = results; current != nullptr; current = current->ai_next) {
        SOCKET socket_handle = socket(current->ai_family, current->ai_socktype, current->ai_protocol);
        if (socket_handle == INVALID_SOCKET) {
            continue;
        }

        const DWORD socket_timeout = timeout_ms;
        setsockopt(socket_handle, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&socket_timeout), sizeof(socket_timeout));
        setsockopt(socket_handle, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&socket_timeout), sizeof(socket_timeout));

        if (connect(socket_handle, current->ai_addr, static_cast<int>(current->ai_addrlen)) == 0) {
            session->socket_handle = socket_handle;
            return true;
        }

        closesocket(socket_handle);
    }

    set_error(error_message, error_message_capacity, "unable to connect to the main-node TCP endpoint");
    return false;
}

}  // namespace swc

extern "C" {

int SWC_CALL swc_generate_default_vector(
    std::uint64_t seed,
    std::uint32_t vector_length,
    SwcVectorBuffer* out_vector,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (out_vector == nullptr) {
        swc::set_error(error_message, error_message_capacity, "out_vector is null");
        return 0;
    }

    out_vector->values = nullptr;
    out_vector->length = 0;

    std::vector<float> values;
    if (!swc::generate_default_vector(seed, vector_length, &values, error_message, error_message_capacity)) {
        return 0;
    }

    const std::size_t byte_count = values.size() * sizeof(float);
    out_vector->values = static_cast<float*>(std::malloc(byte_count));
    if (out_vector->values == nullptr) {
        swc::set_error(error_message, error_message_capacity, "unable to allocate the request vector buffer");
        return 0;
    }

    std::memcpy(out_vector->values, values.data(), byte_count);
    out_vector->length = static_cast<std::uint32_t>(values.size());
    return 1;
}

int SWC_CALL swc_discover_main_node(
    const char* client_name,
    const SwcDiscoveryOptions* options,
    SwcEndpoint* out_endpoint,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (out_endpoint == nullptr) {
        swc::set_error(error_message, error_message_capacity, "out_endpoint is null");
        return 0;
    }

    std::memset(out_endpoint, 0, sizeof(*out_endpoint));
    swc::MainNodeEndpoint endpoint;
    if (!swc::discover_main_node(
            swc::trim_utf8(client_name, swc::kDefaultClientName),
            options,
            &endpoint,
            error_message,
            error_message_capacity)) {
        return 0;
    }

    swc::copy_text(out_endpoint->host, sizeof(out_endpoint->host), endpoint.host);
    out_endpoint->port = endpoint.port;
    swc::copy_text(out_endpoint->source, sizeof(out_endpoint->source), endpoint.source);
    return 1;
}

int SWC_CALL swc_open_client(
    const char* host,
    std::uint16_t port,
    std::uint32_t timeout_ms,
    SwcClientHandle* out_handle,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (out_handle == nullptr) {
        swc::set_error(error_message, error_message_capacity, "out_handle is null");
        return 0;
    }

    *out_handle = nullptr;
    const std::string resolved_host = swc::trim_utf8(host);
    if (resolved_host.empty()) {
        swc::set_error(error_message, error_message_capacity, "host is empty");
        return 0;
    }

    auto session = std::make_unique<swc::ClientSession>();
    const std::uint16_t resolved_port = port != 0U ? port : swc::kDefaultTcpPort;
    const std::uint32_t resolved_timeout = timeout_ms != 0U ? timeout_ms : 30000U;
    if (!swc::open_client_socket(
            resolved_host,
            resolved_port,
            resolved_timeout,
            session.get(),
            error_message,
            error_message_capacity)) {
        return 0;
    }

    if (!swc::start_reader_thread(session.get(), error_message, error_message_capacity)) {
        swc::request_session_stop(session.get(), error_message != nullptr ? error_message : "");
        return 0;
    }

    *out_handle = session.release();
    return 1;
}

int SWC_CALL swc_join_client(
    SwcClientHandle handle,
    const char* client_name,
    SwcResponse* out_response,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (handle == nullptr) {
        swc::set_error(error_message, error_message_capacity, "client handle is null");
        return 0;
    }
    if (out_response == nullptr) {
        swc::set_error(error_message, error_message_capacity, "out_response is null");
        return 0;
    }

    swc::clear_response(out_response);
    auto* session = reinterpret_cast<swc::ClientSession*>(handle);
    const std::string resolved_client_name = swc::trim_utf8(client_name, swc::kDefaultClientName);

    if (!swc::ensure_session_open(session, error_message, error_message_capacity)) {
        return 0;
    }
    {
        std::lock_guard<std::mutex> lock(session->state_mutex);
        session->client_name = resolved_client_name;
    }
    {
        std::lock_guard<std::mutex> send_lock(session->send_mutex);
        if (!swc::send_client_join(session->socket_handle, resolved_client_name, error_message, error_message_capacity)) {
            return 0;
        }
    }

    swc::ClientResponseData decoded;
    if (!swc::wait_for_client_response(session, &decoded, error_message, error_message_capacity)) {
        return 0;
    }

    {
        std::lock_guard<std::mutex> lock(session->state_mutex);
        session->client_id = decoded.client_id;
    }
    swc::fill_response(decoded, out_response);
    return 1;
}

int SWC_CALL swc_request_gemv(
    SwcClientHandle handle,
    const SwcRequestOptions* options,
    const char* size,
    const float* input_values,
    std::uint32_t value_count,
    SwcResponse* out_response,
    SwcRequestAcceptedCallback on_request_accepted,
    void* user_data,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (handle == nullptr) {
        swc::set_error(error_message, error_message_capacity, "client handle is null");
        return 0;
    }
    if (input_values == nullptr || value_count == 0U) {
        swc::set_error(error_message, error_message_capacity, "input_values are empty");
        return 0;
    }
    if (out_response == nullptr) {
        swc::set_error(error_message, error_message_capacity, "out_response is null");
        return 0;
    }

    swc::clear_response(out_response);
    auto* session = reinterpret_cast<swc::ClientSession*>(handle);

    const std::string client_name = swc::trim_utf8(
        options != nullptr ? options->client_name : nullptr,
        swc::kDefaultClientName);
    const std::string request_id = swc::trim_utf8(
        options != nullptr ? options->request_id : nullptr);
    const std::string local_request_id = request_id.empty() ? swc::make_request_id() : request_id;
    const std::string resolved_size = swc::trim_utf8(size, "large");
    const std::string object_id = swc::trim_utf8(
        options != nullptr ? options->object_id : nullptr);
    const std::string stream_id = swc::trim_utf8(
        options != nullptr ? options->stream_id : nullptr);
    const std::uint64_t timestamp_ms =
        options != nullptr && options->timestamp_ms != 0U ? options->timestamp_ms : swc::unix_time_ms();
    const std::uint32_t iteration_count =
        options != nullptr && options->iteration_count != 0U ? options->iteration_count : 1U;

    return swc::send_request_and_wait_for_response(
        session,
        client_name,
        local_request_id,
        [&]() {
            return swc::send_gemv_request(
                session->socket_handle,
                client_name,
                local_request_id,
                resolved_size,
                object_id,
                stream_id,
                timestamp_ms,
                iteration_count,
                input_values,
                value_count,
                error_message,
                error_message_capacity);
        },
        on_request_accepted,
        user_data,
        out_response,
        error_message,
        error_message_capacity) ? 1 : 0;
}

int SWC_CALL swc_request_conv2d(
    SwcClientHandle handle,
    const SwcRequestOptions* options,
    const char* size,
    const SwcConv2dShape* shape,
    const SwcConv2dRequestOptions* conv2d_options,
    SwcResponse* out_response,
    SwcRequestAcceptedCallback on_request_accepted,
    void* user_data,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (handle == nullptr) {
        swc::set_error(error_message, error_message_capacity, "client handle is null");
        return 0;
    }
    if (shape == nullptr) {
        swc::set_error(error_message, error_message_capacity, "conv2d shape is null");
        return 0;
    }
    if (out_response == nullptr) {
        swc::set_error(error_message, error_message_capacity, "out_response is null");
        return 0;
    }

    swc::clear_response(out_response);
    auto* session = reinterpret_cast<swc::ClientSession*>(handle);

    const std::string client_name = swc::trim_utf8(
        options != nullptr ? options->client_name : nullptr,
        swc::kDefaultClientName);
    const std::string request_id = swc::trim_utf8(
        options != nullptr ? options->request_id : nullptr);
    const std::string local_request_id = request_id.empty() ? swc::make_request_id() : request_id;
    const std::string resolved_size = swc::trim_utf8(size, "small");
    const std::string default_object_id = "conv2d/" + resolved_size;
    const std::string object_id = swc::trim_utf8(
        options != nullptr ? options->object_id : nullptr,
        default_object_id.c_str());
    const std::string stream_id = swc::trim_utf8(
        options != nullptr ? options->stream_id : nullptr);
    const std::uint64_t timestamp_ms =
        options != nullptr && options->timestamp_ms != 0U ? options->timestamp_ms : swc::unix_time_ms();
    const std::uint32_t iteration_count =
        options != nullptr && options->iteration_count != 0U ? options->iteration_count : 1U;

    return swc::send_request_and_wait_for_response(
        session,
        client_name,
        local_request_id,
        [&]() {
            return swc::send_conv2d_request(
                session->socket_handle,
                client_name,
                local_request_id,
                resolved_size,
                object_id,
                stream_id,
                timestamp_ms,
                iteration_count,
                *shape,
                conv2d_options,
                error_message,
                error_message_capacity);
        },
        on_request_accepted,
        user_data,
        out_response,
        error_message,
        error_message_capacity) ? 1 : 0;
}

int SWC_CALL swc_request_free_content(
    SwcClientHandle handle,
    const SwcRequestOptions* options,
    const char* method,
    const char* content,
    SwcResponse* out_response,
    SwcRequestAcceptedCallback on_request_accepted,
    void* user_data,
    char* error_message,
    std::uint32_t error_message_capacity) {
    if (handle == nullptr) {
        swc::set_error(error_message, error_message_capacity, "client handle is null");
        return 0;
    }
    if (content == nullptr) {
        swc::set_error(error_message, error_message_capacity, "content is null");
        return 0;
    }
    if (out_response == nullptr) {
        swc::set_error(error_message, error_message_capacity, "out_response is null");
        return 0;
    }

    swc::clear_response(out_response);
    auto* session = reinterpret_cast<swc::ClientSession*>(handle);

    const std::string client_name = swc::trim_utf8(
        options != nullptr ? options->client_name : nullptr,
        swc::kDefaultClientName);
    const std::string request_id = swc::trim_utf8(
        options != nullptr ? options->request_id : nullptr);
    const std::string local_request_id = request_id.empty() ? swc::make_request_id() : request_id;
    const std::string resolved_method = swc::trim_utf8(method, swc::kMethodFreeContent);
    const std::string object_id = swc::trim_utf8(
        options != nullptr ? options->object_id : nullptr);
    const std::string stream_id = swc::trim_utf8(
        options != nullptr ? options->stream_id : nullptr);
    const std::string resolved_content = content != nullptr ? content : "";
    const std::uint64_t timestamp_ms =
        options != nullptr && options->timestamp_ms != 0U ? options->timestamp_ms : swc::unix_time_ms();
    const std::uint32_t iteration_count =
        options != nullptr && options->iteration_count != 0U ? options->iteration_count : 1U;

    return swc::send_request_and_wait_for_response(
        session,
        client_name,
        local_request_id,
        [&]() {
            return swc::send_free_content_request(
                session->socket_handle,
                client_name,
                local_request_id,
                resolved_method,
                object_id,
                stream_id,
                timestamp_ms,
                iteration_count,
                resolved_content,
                error_message,
                error_message_capacity);
        },
        on_request_accepted,
        user_data,
        out_response,
        error_message,
        error_message_capacity) ? 1 : 0;
}

void SWC_CALL swc_free_vector(SwcVectorBuffer* buffer) {
    if (buffer == nullptr) {
        return;
    }
    if (buffer->values != nullptr) {
        std::free(buffer->values);
        buffer->values = nullptr;
    }
    buffer->length = 0;
}

void SWC_CALL swc_reset_response(SwcResponse* response) {
    if (response == nullptr) {
        return;
    }
    if (response->output_vector.values != nullptr) {
        std::free(response->output_vector.values);
    }
    if (response->conv2d_stats_samples != nullptr) {
        std::free(response->conv2d_stats_samples);
    }
    if (response->timing.workers != nullptr) {
        std::free(response->timing.workers);
    }
    swc::clear_response(response);
}

void SWC_CALL swc_close_client(SwcClientHandle handle) {
    if (handle == nullptr) {
        return;
    }

    auto* session = reinterpret_cast<swc::ClientSession*>(handle);
    swc::request_session_stop(session);
    swc::join_if_needed(&session->reader_thread);
    swc::join_if_needed(&session->info_poll_thread);
    delete session;
}

}  // extern "C"
