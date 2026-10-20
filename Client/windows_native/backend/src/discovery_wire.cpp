#include "backend_internal.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstring>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace swc {

namespace {

constexpr std::uint16_t kDnsTypeA = 0x0001;
constexpr std::uint16_t kDnsTypePtr = 0x000C;
constexpr std::uint16_t kDnsTypeTxt = 0x0010;
constexpr std::uint16_t kDnsTypeSrv = 0x0021;
constexpr std::uint16_t kDnsClassIn = 0x0001;
constexpr std::uint16_t kDnsClassUnicastResponse = 0x8000;
constexpr std::uint16_t kDnsFlagResponse = 0x8000;

struct DnsHeader {
    std::uint16_t query_id = 0;
    std::uint16_t flags = 0;
    std::uint16_t question_count = 0;
    std::uint16_t answer_count = 0;
    std::uint16_t authority_count = 0;
    std::uint16_t additional_count = 0;
};

std::uint16_t read_u16_be(const std::vector<std::uint8_t>& data, std::size_t offset) {
    return static_cast<std::uint16_t>((static_cast<std::uint16_t>(data[offset]) << 8U) | data[offset + 1U]);
}

std::uint32_t read_u32_be(const std::vector<std::uint8_t>& data, std::size_t offset) {
    return (static_cast<std::uint32_t>(data[offset]) << 24U)
        | (static_cast<std::uint32_t>(data[offset + 1U]) << 16U)
        | (static_cast<std::uint32_t>(data[offset + 2U]) << 8U)
        | static_cast<std::uint32_t>(data[offset + 3U]);
}

void append_u16_be(std::vector<std::uint8_t>* output, std::uint16_t value) {
    output->push_back(static_cast<std::uint8_t>((value >> 8U) & 0xFFU));
    output->push_back(static_cast<std::uint8_t>(value & 0xFFU));
}

void append_name(std::vector<std::uint8_t>* output, const std::string& name) {
    std::string normalized = name;
    if (normalized.empty() || normalized.back() != '.') {
        normalized.push_back('.');
    }

    std::size_t start = 0;
    while (start < normalized.size()) {
        const std::size_t end = normalized.find('.', start);
        const std::size_t label_size = end - start;
        if (label_size == 0U) {
            break;
        }
        output->push_back(static_cast<std::uint8_t>(label_size));
        output->insert(output->end(), normalized.begin() + static_cast<std::ptrdiff_t>(start), normalized.begin() + static_cast<std::ptrdiff_t>(end));
        start = end + 1U;
    }
    output->push_back(0);
}

std::string canonical_name(const std::string& name) {
    std::string normalized = name;
    if (normalized.empty() || normalized.back() != '.') {
        normalized.push_back('.');
    }
    std::transform(
        normalized.begin(),
        normalized.end(),
        normalized.begin(),
        [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
    return normalized;
}

bool decode_name(
    const std::vector<std::uint8_t>& packet,
    std::size_t offset,
    std::string* out_name,
    std::size_t* out_next) {
    // mDNS packets may use compression pointers, so name decoding must track jumps.
    std::vector<std::string> labels;
    std::size_t cursor = offset;
    std::size_t end_offset = offset;
    bool jumped = false;
    std::vector<std::size_t> visited;

    while (cursor < packet.size()) {
        const std::uint8_t length = packet[cursor];
        if ((length & 0xC0U) == 0xC0U) {
            if (cursor + 1U >= packet.size()) {
                return false;
            }
            const std::size_t pointer = static_cast<std::size_t>(((length & 0x3FU) << 8U) | packet[cursor + 1U]);
            if (std::find(visited.begin(), visited.end(), pointer) != visited.end()) {
                return false;
            }
            visited.push_back(pointer);
            if (!jumped) {
                end_offset = cursor + 2U;
                jumped = true;
            }
            cursor = pointer;
            continue;
        }

        if (length == 0U) {
            if (!jumped) {
                end_offset = cursor + 1U;
            }
            break;
        }

        cursor += 1U;
        if (cursor + length > packet.size()) {
            return false;
        }
        labels.emplace_back(
            reinterpret_cast<const char*>(packet.data() + cursor),
            reinterpret_cast<const char*>(packet.data() + cursor + length));
        cursor += length;
        if (!jumped) {
            end_offset = cursor;
        }
    }

    std::string joined;
    for (const std::string& label : labels) {
        joined += label;
        joined.push_back('.');
    }
    if (joined.empty()) {
        joined = ".";
    }
    *out_name = joined;
    *out_next = end_offset;
    return true;
}

bool parse_txt_node_name(const std::vector<std::uint8_t>& rdata, std::string* out_node_name) {
    std::size_t offset = 0;
    while (offset < rdata.size()) {
        const std::uint8_t length = rdata[offset];
        offset += 1U;
        if (offset + length > rdata.size()) {
            return false;
        }
        const std::string value(
            reinterpret_cast<const char*>(rdata.data() + offset),
            reinterpret_cast<const char*>(rdata.data() + offset + length));
        offset += length;
        constexpr char kPrefix[] = "node=";
        if (value.rfind(kPrefix, 0) == 0) {
            *out_node_name = value.substr(sizeof(kPrefix) - 1U);
        }
    }
    return true;
}

bool parse_announce_packet(const std::vector<std::uint8_t>& packet, MainNodeEndpoint* endpoint) {
    if (packet.size() < 12U || endpoint == nullptr) {
        return false;
    }

    DnsHeader header{
        read_u16_be(packet, 0U),
        read_u16_be(packet, 2U),
        read_u16_be(packet, 4U),
        read_u16_be(packet, 6U),
        read_u16_be(packet, 8U),
        read_u16_be(packet, 10U),
    };
    if ((header.flags & kDnsFlagResponse) == 0U) {
        return false;
    }

    std::size_t offset = 12U;
    for (std::uint16_t index = 0; index < header.question_count; ++index) {
        std::string ignored_name;
        if (!decode_name(packet, offset, &ignored_name, &offset)) {
            return false;
        }
        if (offset + 4U > packet.size()) {
            return false;
        }
        offset += 4U;
    }

    const std::string target_service = canonical_name(kMdnsServiceType);
    std::vector<std::string> service_instances;
    std::unordered_map<std::string, std::pair<std::string, std::uint16_t>> service_targets;
    std::unordered_map<std::string, std::string> ipv4_addresses;

    // One announcement can spread the service information across answer and
    // additional records, so we collect the pieces first and resolve them after.
    const std::uint32_t record_count = static_cast<std::uint32_t>(header.answer_count)
        + static_cast<std::uint32_t>(header.authority_count)
        + static_cast<std::uint32_t>(header.additional_count);
    for (std::uint32_t index = 0; index < record_count; ++index) {
        std::string record_name;
        if (!decode_name(packet, offset, &record_name, &offset)) {
            return false;
        }
        if (offset + 10U > packet.size()) {
            return false;
        }

        const std::uint16_t record_type = read_u16_be(packet, offset);
        const std::uint16_t record_class = read_u16_be(packet, offset + 2U);
        const std::uint32_t ttl = read_u32_be(packet, offset + 4U);
        const std::uint16_t rd_length = read_u16_be(packet, offset + 8U);
        (void)record_class;
        (void)ttl;
        offset += 10U;

        if (offset + rd_length > packet.size()) {
            return false;
        }

        const std::size_t rdata_offset = offset;
        const std::vector<std::uint8_t> rdata(
            packet.begin() + static_cast<std::ptrdiff_t>(rdata_offset),
            packet.begin() + static_cast<std::ptrdiff_t>(rdata_offset + rd_length));
        offset += rd_length;

        const std::string canonical_record_name = canonical_name(record_name);
        if (record_type == kDnsTypePtr && canonical_record_name == target_service) {
            std::string instance_name;
            std::size_t ignored_next = 0;
            if (!decode_name(packet, rdata_offset, &instance_name, &ignored_next)) {
                return false;
            }
            service_instances.push_back(canonical_name(instance_name));
            continue;
        }

        if (record_type == kDnsTypeSrv) {
            if (rd_length < 6U) {
                return false;
            }
            const std::uint16_t port = static_cast<std::uint16_t>((static_cast<std::uint16_t>(rdata[4]) << 8U) | rdata[5]);
            std::string target_name;
            std::size_t ignored_next = 0;
            if (!decode_name(packet, rdata_offset + 6U, &target_name, &ignored_next)) {
                return false;
            }
            service_targets[canonical_record_name] = {canonical_name(target_name), port};
            continue;
        }

        if (record_type == kDnsTypeA && rd_length == 4U) {
            char host_buffer[INET_ADDRSTRLEN] = {};
            if (InetNtopA(AF_INET, rdata.data(), host_buffer, INET_ADDRSTRLEN) != nullptr) {
                ipv4_addresses[canonical_record_name] = host_buffer;
            }
            continue;
        }

        if (record_type == kDnsTypeTxt) {
            std::string ignored_node_name;
            if (!parse_txt_node_name(rdata, &ignored_node_name)) {
                return false;
            }
        }
    }

    for (const std::string& instance_name : service_instances) {
        const auto service_it = service_targets.find(instance_name);
        if (service_it == service_targets.end()) {
            continue;
        }
        const auto host_it = ipv4_addresses.find(service_it->second.first);
        if (host_it == ipv4_addresses.end()) {
            continue;
        }
        endpoint->host = host_it->second;
        endpoint->port = service_it->second.second;
        endpoint->source = "mdns";
        return true;
    }

    return false;
}

std::vector<std::uint8_t> build_discover_message() {
    std::vector<std::uint8_t> payload;
    append_u16_be(&payload, 0U);
    append_u16_be(&payload, 0U);
    append_u16_be(&payload, 1U);
    append_u16_be(&payload, 0U);
    append_u16_be(&payload, 0U);
    append_u16_be(&payload, 0U);
    append_name(&payload, kMdnsServiceType);
    append_u16_be(&payload, kDnsTypePtr);
    append_u16_be(&payload, static_cast<std::uint16_t>(kDnsClassIn | kDnsClassUnicastResponse));
    return payload;
}

std::string select_multicast_interface() {
    SOCKET probe = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (probe == INVALID_SOCKET) {
        return {};
    }

    sockaddr_in remote{};
    remote.sin_family = AF_INET;
    remote.sin_port = htons(80U);
    InetPtonA(AF_INET, "8.8.8.8", &remote.sin_addr);

    std::string local_ip;
    if (connect(probe, reinterpret_cast<const sockaddr*>(&remote), sizeof(remote)) == 0) {
        sockaddr_in local{};
        int local_length = sizeof(local);
        if (getsockname(probe, reinterpret_cast<sockaddr*>(&local), &local_length) == 0) {
            char host_buffer[INET_ADDRSTRLEN] = {};
            if (InetNtopA(AF_INET, &local.sin_addr, host_buffer, INET_ADDRSTRLEN) != nullptr) {
                local_ip = host_buffer;
            }
        }
    }
    closesocket(probe);

    if (local_ip.rfind("127.", 0) == 0) {
        return {};
    }
    return local_ip;
}

bool send_discover_query(
    SOCKET socket_handle,
    const std::string& multicast_group,
    std::uint16_t udp_port,
    char* error_message,
    std::uint32_t error_message_capacity) {
    sockaddr_in target{};
    target.sin_family = AF_INET;
    target.sin_port = htons(udp_port);
    if (InetPtonA(AF_INET, multicast_group.c_str(), &target.sin_addr) != 1) {
        set_error(error_message, error_message_capacity, "multicast_group is not a valid IPv4 address");
        return false;
    }

    const std::vector<std::uint8_t> query = build_discover_message();
    const int sent = sendto(
        socket_handle,
        reinterpret_cast<const char*>(query.data()),
        static_cast<int>(query.size()),
        0,
        reinterpret_cast<const sockaddr*>(&target),
        sizeof(target));
    if (sent == SOCKET_ERROR || sent != static_cast<int>(query.size())) {
        set_error(error_message, error_message_capacity, "unable to send the main-node mDNS discover packet");
        return false;
    }
    return true;
}

bool recv_announce(
    SOCKET socket_handle,
    std::uint32_t buffer_size,
    std::uint32_t discovery_timeout_ms,
    MainNodeEndpoint* endpoint,
    char* error_message,
    std::uint32_t error_message_capacity) {
    std::vector<std::uint8_t> buffer(std::max<std::uint32_t>(buffer_size, 512U));
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(discovery_timeout_ms);

    while (std::chrono::steady_clock::now() < deadline) {
        sockaddr_in peer{};
        int peer_length = sizeof(peer);
        const int received = recvfrom(
            socket_handle,
            reinterpret_cast<char*>(buffer.data()),
            static_cast<int>(buffer.size()),
            0,
            reinterpret_cast<sockaddr*>(&peer),
            &peer_length);
        if (received == SOCKET_ERROR) {
            const int error_code = WSAGetLastError();
            if (error_code == WSAETIMEDOUT || error_code == WSAEWOULDBLOCK) {
                break;
            }
            set_error(error_message, error_message_capacity, "unable to receive the main-node mDNS announce packet");
            return false;
        }
        if (received <= 0) {
            continue;
        }

        const std::vector<std::uint8_t> packet(buffer.begin(), buffer.begin() + received);
        if (parse_announce_packet(packet, endpoint)) {
            return true;
        }
    }

    set_error(error_message, error_message_capacity, "main-node discovery timed out");
    return false;
}

}  // namespace

bool discover_main_node(
    const std::string& client_name,
    const SwcDiscoveryOptions* options,
    MainNodeEndpoint* endpoint,
    char* error_message,
    std::uint32_t error_message_capacity) {
    (void)client_name;

    if (endpoint == nullptr) {
        set_error(error_message, error_message_capacity, "endpoint output is null");
        return false;
    }
    if (!ensure_winsock(error_message, error_message_capacity)) {
        return false;
    }

    const std::string multicast_group = trim_utf8(
        options != nullptr ? options->multicast_group : nullptr,
        kDefaultMulticastGroup);
    const std::uint16_t udp_port = options != nullptr && options->udp_port != 0U ? options->udp_port : kDefaultDiscoveryPort;
    const std::uint32_t discovery_timeout_ms =
        options != nullptr && options->discovery_timeout_ms != 0U ? options->discovery_timeout_ms : kDefaultDiscoveryTimeoutMs;
    const std::uint32_t discovery_attempts =
        options != nullptr && options->discovery_attempts != 0U ? options->discovery_attempts : kDefaultDiscoveryAttempts;
    const std::uint32_t retry_delay_ms =
        options != nullptr ? options->retry_delay_ms : kDefaultRetryDelayMs;
    const std::uint32_t buffer_size =
        options != nullptr && options->buffer_size != 0U ? options->buffer_size : kDefaultBufferSize;
    const std::uint8_t multicast_ttl =
        options != nullptr ? options->multicast_ttl : kDefaultMulticastTtl;

    *endpoint = MainNodeEndpoint{};

    for (std::uint32_t attempt = 0; attempt < discovery_attempts; ++attempt) {
        SOCKET socket_handle = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (socket_handle == INVALID_SOCKET) {
            set_error(error_message, error_message_capacity, "unable to create the UDP discovery socket");
            return false;
        }

        bool success = false;
        sockaddr_in bind_addr{};
        bind_addr.sin_family = AF_INET;
        bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);
        bind_addr.sin_port = htons(0U);

        if (bind(socket_handle, reinterpret_cast<const sockaddr*>(&bind_addr), sizeof(bind_addr)) == SOCKET_ERROR) {
            set_error(error_message, error_message_capacity, "unable to bind the UDP discovery socket");
        } else {
            const DWORD timeout_ms = discovery_timeout_ms;
            const unsigned char ttl = multicast_ttl;
            setsockopt(socket_handle, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms));
            setsockopt(socket_handle, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms));
            setsockopt(socket_handle, IPPROTO_IP, IP_MULTICAST_TTL, reinterpret_cast<const char*>(&ttl), sizeof(ttl));

            const std::string interface_ip = select_multicast_interface();
            if (!interface_ip.empty()) {
                in_addr interface_addr{};
                if (InetPtonA(AF_INET, interface_ip.c_str(), &interface_addr) == 1) {
                    setsockopt(
                        socket_handle,
                        IPPROTO_IP,
                        IP_MULTICAST_IF,
                        reinterpret_cast<const char*>(&interface_addr),
                        sizeof(interface_addr));
                }
            }

            if (send_discover_query(socket_handle, multicast_group, udp_port, error_message, error_message_capacity)) {
                success = recv_announce(
                    socket_handle,
                    buffer_size,
                    discovery_timeout_ms,
                    endpoint,
                    error_message,
                    error_message_capacity);
            }
        }

        closesocket(socket_handle);
        if (success) {
            return true;
        }
        if (attempt + 1U < discovery_attempts) {
            std::this_thread::sleep_for(std::chrono::milliseconds(retry_delay_ms));
        }
    }

    return false;
}

}  // namespace swc
