using System.Buffers.Binary;
using System.Diagnostics;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace SuperWebClient.WinUI3.Services;

public sealed class NativeClientBackend : IDisposable
{
    private const string BackendDllName = "superweb_client_backend.dll";
    private const ushort DefaultTcpPort = 52020;
    private const string DefaultClientName = "windows winui3 client";
    private const string DefaultFreeContentMethod = "free_content";
    private const string Conv2dMethod = "conv2d";
    private const int ErrorCapacity = 2048;
    private const uint SmallVectorLength = 4096;
    private const uint MidVectorLength = 16384;
    private const uint LargeVectorLength = 32768;
    private const uint Conv2dClientResponseFull = 0;
    private const uint Conv2dClientResponseStatsOnly = 1;
    private const uint DefaultConv2dStatsMaxSamples = 16;
    private const int DefaultUploadChunkSize = 512 * 1024;
    private const double ArtifactTransferTimeoutSeconds = 3600.0;
    private const ulong DefaultConv2dWeightSeed = 0x0FEDCBA987654321UL;
    private const ulong SplitMix64Gamma = 0x9E3779B97F4A7C15UL;
    private const ulong SplitMix64Mix1 = 0xBF58476D1CE4E5B9UL;
    private const ulong SplitMix64Mix2 = 0x94D049BB133111EBUL;

    private static readonly SwcRequestAcceptedCallback RequestAcceptedCallbackThunk = HandleRequestAccepted;

    private IntPtr _handle = IntPtr.Zero;

    public bool IsConnected => _handle != IntPtr.Zero;

    public async Task<ConnectionSummary> ConnectAsync(string clientName, string? hostOverride, int? portOverride)
    {
        return await Task.Run(() => Connect(clientName, hostOverride, portOverride));
    }

    public async Task<RequestSummary> SendFixedVectorAsync(
        string clientName,
        string size,
        ulong vectorSeed,
        uint iterationCount,
        string? objectId,
        string? streamId,
        Action<RequestAcceptedSummary>? onAccepted = null)
    {
        return await Task.Run(() => SendFixedVector(clientName, size, vectorSeed, iterationCount, objectId, streamId, onAccepted));
    }

    public async Task<RequestSummary> SendFreeContentAsync(
        string clientName,
        string method,
        string content,
        uint iterationCount,
        string? objectId,
        string? streamId,
        Action<RequestAcceptedSummary>? onAccepted = null)
    {
        return await Task.Run(() => SendFreeContent(clientName, method, content, iterationCount, objectId, streamId, onAccepted));
    }

    public async Task<RequestSummary> SendConv2dAsync(
        string clientName,
        string size,
        Conv2dRequestShape shape,
        uint clientResponseMode,
        uint statsMaxSamples,
        uint iterationCount,
        string? objectId,
        string? streamId,
        string? preferredOutputName,
        Action<RequestAcceptedSummary>? onAccepted = null)
    {
        return await Task.Run(() => SendConv2d(
            clientName,
            size,
            shape,
            clientResponseMode,
            statsMaxSamples,
            iterationCount,
            objectId,
            streamId,
            preferredOutputName,
            onAccepted));
    }

    public void Disconnect()
    {
        if (_handle != IntPtr.Zero)
        {
            swc_close_client(_handle);
            _handle = IntPtr.Zero;
        }
    }

    public void Dispose()
    {
        Disconnect();
    }

    private ConnectionSummary Connect(string clientName, string? hostOverride, int? portOverride)
    {
        EnsureBackendPresent();
        Disconnect();

        var resolvedClientName = string.IsNullOrWhiteSpace(clientName) ? DefaultClientName : clientName.Trim();
        string host;
        ushort port;
        string source;

        if (string.IsNullOrWhiteSpace(hostOverride))
        {
            var endpoint = CreateEndpoint();
            EnsureNativeCall(
                error => swc_discover_main_node(resolvedClientName, IntPtr.Zero, ref endpoint, error, ErrorCapacity),
                "discover the main node");
            host = DecodeUtf8(endpoint.host);
            port = endpoint.port == 0 ? DefaultTcpPort : endpoint.port;
            source = DecodeUtf8(endpoint.source);
        }
        else
        {
            host = hostOverride.Trim();
            port = portOverride.HasValue && portOverride.Value > 0 ? checked((ushort)portOverride.Value) : DefaultTcpPort;
            source = "manual";
        }

        EnsureNativeCall(
            error => swc_open_client(host, port, 30000U, out _handle, error, ErrorCapacity),
            "open the TCP client session");

        var joinResponse = CreateResponse();
        try
        {
            EnsureNativeCall(
                error => swc_join_client(_handle, resolvedClientName, ref joinResponse, error, ErrorCapacity),
                "send CLIENT_JOIN");
            if (joinResponse.status_code != 200)
            {
                throw new InvalidOperationException(DecodeUtf8(joinResponse.error_message));
            }

            return new ConnectionSummary(
                Host: host,
                Port: port,
                Source: string.IsNullOrWhiteSpace(source) ? "discovery" : source,
                WorkerCount: joinResponse.worker_count,
                ClientCount: joinResponse.client_count,
                ClientId: DecodeUtf8(joinResponse.client_id));
        }
        finally
        {
            swc_reset_response(ref joinResponse);
        }
    }

    private RequestSummary SendFixedVector(
        string clientName,
        string size,
        ulong vectorSeed,
        uint iterationCount,
        string? objectId,
        string? streamId,
        Action<RequestAcceptedSummary>? onAccepted)
    {
        EnsureConnected();

        var resolvedSize = NormalizeGemvSize(size);
        var vectorLength = ResolveGemvVectorLength(resolvedSize);
        var vectorBuffer = new SwcVectorBuffer();
        var response = CreateResponse();
        var requestStartedAt = Stopwatch.GetTimestamp();
        RequestAcceptedSummary? acceptedSummary = null;
        long acceptedAt = 0;
        GCHandle callbackHandle = default;
        try
        {
            EnsureNativeCall(
                error => swc_generate_default_vector(vectorSeed, vectorLength, out vectorBuffer, error, ErrorCapacity),
                "generate the deterministic request vector");

            using var options = new RequestOptionsScope(
                string.IsNullOrWhiteSpace(clientName) ? DefaultClientName : clientName.Trim(),
                iterationCount,
                objectId,
                streamId);
            var nativeOptions = options.Value;
            var callbackUserData = PrepareAcceptedCallback(
                summary =>
                {
                    acceptedSummary = summary;
                    acceptedAt = Stopwatch.GetTimestamp();
                    onAccepted?.Invoke(summary);
                },
                out callbackHandle);

            EnsureNativeCall(
                error => swc_request_gemv(
                    _handle,
                    ref nativeOptions,
                    resolvedSize,
                    vectorBuffer.values,
                    vectorBuffer.length,
                    ref response,
                    onAccepted is null ? null : RequestAcceptedCallbackThunk,
                    callbackUserData,
                    error,
                    ErrorCapacity),
                "send the fixed-matrix-vector request");

            return BuildRequestSummary(
                response,
                requestStartedAt,
                acceptedAt,
                Stopwatch.GetTimestamp(),
                acceptedSummary,
                preferredOutputName: null,
                conv2dCoordinator: null);
        }
        finally
        {
            if (callbackHandle.IsAllocated)
            {
                callbackHandle.Free();
            }
            swc_free_vector(ref vectorBuffer);
            swc_reset_response(ref response);
        }
    }

    private RequestSummary SendFreeContent(
        string clientName,
        string method,
        string content,
        uint iterationCount,
        string? objectId,
        string? streamId,
        Action<RequestAcceptedSummary>? onAccepted)
    {
        EnsureConnected();

        var response = CreateResponse();
        var requestStartedAt = Stopwatch.GetTimestamp();
        RequestAcceptedSummary? acceptedSummary = null;
        long acceptedAt = 0;
        GCHandle callbackHandle = default;
        try
        {
            using var options = new RequestOptionsScope(
                string.IsNullOrWhiteSpace(clientName) ? DefaultClientName : clientName.Trim(),
                iterationCount,
                objectId,
                streamId);
            var nativeOptions = options.Value;

            var resolvedMethod = string.IsNullOrWhiteSpace(method) ? DefaultFreeContentMethod : method.Trim();
            var callbackUserData = PrepareAcceptedCallback(
                summary =>
                {
                    acceptedSummary = summary;
                    acceptedAt = Stopwatch.GetTimestamp();
                    onAccepted?.Invoke(summary);
                },
                out callbackHandle);
            EnsureNativeCall(
                error => swc_request_free_content(
                    _handle,
                    ref nativeOptions,
                    resolvedMethod,
                    content ?? string.Empty,
                    ref response,
                    onAccepted is null ? null : RequestAcceptedCallbackThunk,
                    callbackUserData,
                    error,
                    ErrorCapacity),
                "send the free-content request");

            return BuildRequestSummary(
                response,
                requestStartedAt,
                acceptedAt,
                Stopwatch.GetTimestamp(),
                acceptedSummary,
                preferredOutputName: null,
                conv2dCoordinator: null);
        }
        finally
        {
            if (callbackHandle.IsAllocated)
            {
                callbackHandle.Free();
            }
            swc_reset_response(ref response);
        }
    }

    private RequestSummary SendConv2d(
        string clientName,
        string size,
        Conv2dRequestShape shape,
        uint clientResponseMode,
        uint statsMaxSamples,
        uint iterationCount,
        string? objectId,
        string? streamId,
        string? preferredOutputName,
        Action<RequestAcceptedSummary>? onAccepted)
    {
        EnsureConnected();

        var response = CreateResponse();
        var requestStartedAt = Stopwatch.GetTimestamp();
        RequestAcceptedSummary? acceptedSummary = null;
        long acceptedAt = 0;
        var weightBytes = GenerateDefaultConv2dWeightBytes(shape, DefaultConv2dWeightSeed);
        var weightChecksum = ComputeSha256Hex(weightBytes);
        var coordinator = new Conv2dTransferCoordinator(weightBytes, weightChecksum, preferredOutputName);
        GCHandle callbackHandle = default;
        try
        {
            using var options = new RequestOptionsScope(
                string.IsNullOrWhiteSpace(clientName) ? DefaultClientName : clientName.Trim(),
                iterationCount,
                objectId,
                streamId);
            var nativeOptions = options.Value;
            var nativeShape = new SwcConv2dShape
            {
                tensor_h = shape.TensorH,
                tensor_w = shape.TensorW,
                channels_in = shape.ChannelsIn,
                channels_out = shape.ChannelsOut,
                kernel_size = shape.KernelSize,
                padding = shape.Padding,
                stride = shape.Stride,
            };
            var conv2dOptions = new SwcConv2dRequestOptions
            {
                client_response_mode = clientResponseMode,
                stats_max_samples = clientResponseMode == Conv2dClientResponseStatsOnly
                    ? (statsMaxSamples == 0 ? DefaultConv2dStatsMaxSamples : statsMaxSamples)
                    : 0,
                upload_size_bytes = (ulong)weightBytes.Length,
                upload_checksum = Marshal.StringToCoTaskMemUTF8(weightChecksum),
            };

            try
            {
                var callbackUserData = PrepareAcceptedCallback(
                    summary =>
                    {
                        acceptedSummary = summary;
                        acceptedAt = Stopwatch.GetTimestamp();
                        coordinator.OnAccepted(summary);
                        onAccepted?.Invoke(summary);
                    },
                    out callbackHandle);
                EnsureNativeCall(
                    error => swc_request_conv2d(
                        _handle,
                        ref nativeOptions,
                        string.IsNullOrWhiteSpace(size) ? "small" : size.Trim(),
                        ref nativeShape,
                        ref conv2dOptions,
                        ref response,
                        RequestAcceptedCallbackThunk,
                        callbackUserData,
                        error,
                        ErrorCapacity),
                    "send the conv2d request");
            }
            finally
            {
                if (conv2dOptions.upload_checksum != IntPtr.Zero)
                {
                    Marshal.FreeCoTaskMem(conv2dOptions.upload_checksum);
                }
            }

            return BuildRequestSummary(
                response,
                requestStartedAt,
                acceptedAt,
                Stopwatch.GetTimestamp(),
                acceptedSummary,
                preferredOutputName,
                coordinator);
        }
        finally
        {
            if (callbackHandle.IsAllocated)
            {
                callbackHandle.Free();
            }
            swc_reset_response(ref response);
            coordinator.Dispose();
        }
    }

    private static RequestSummary BuildRequestSummary(
        SwcResponse response,
        long requestStartedAt,
        long acceptedAt,
        long responseCompletedAt,
        RequestAcceptedSummary? acceptedSummary,
        string? preferredOutputName,
        Conv2dTransferCoordinator? conv2dCoordinator)
    {
        var method = DecodeUtf8(response.method);
        var requestId = DecodeUtf8(response.request_id);
        var artifactId = DecodeUtf8(response.result_artifact.artifact_id);
        var artifact = string.IsNullOrWhiteSpace(artifactId)
            ? null
            : new RequestArtifact(
                ArtifactId: artifactId,
                ContentType: DecodeUtf8(response.result_artifact.content_type),
                SizeBytes: response.result_artifact.size_bytes,
                Checksum: DecodeUtf8(response.result_artifact.checksum),
                ProducerNodeId: DecodeUtf8(response.result_artifact.producer_node_id),
                TransferHost: DecodeUtf8(response.result_artifact.transfer_host),
                TransferPort: response.result_artifact.transfer_port,
                ChunkSize: response.result_artifact.chunk_size,
                Ready: response.result_artifact.ready != 0);
        var outputValues = CopyFloatValues(response.output_vector.values, response.output_vector.length);
        var conv2dStats = BuildConv2dStats(response);
        var timing = BuildResponseTiming(response.timing);
        var materialization = conv2dCoordinator is not null
            ? conv2dCoordinator.MaterializeOutput(method, requestId, artifact, outputValues, conv2dStats)
            : TryMaterializeOutput(method, requestId, artifact, preferredOutputName, outputValues, conv2dStats);

        var requestAcceptLatencyMs = acceptedAt > 0 ? ElapsedMilliseconds(requestStartedAt, acceptedAt) : 0;
        var clientElapsedMs = acceptedAt > 0 ? ElapsedMilliseconds(acceptedAt, responseCompletedAt) : 0;
        var transmissionOverheadMs = Math.Max(0, clientElapsedMs - (long)response.elapsed_ms);

        return new RequestSummary(
            RequestId: requestId,
            Method: method,
            Size: DecodeUtf8(response.size),
            ObjectId: DecodeUtf8(response.object_id),
            StreamId: DecodeUtf8(response.stream_id),
            TaskId: DecodeUtf8(response.task_id),
            StatusCode: response.status_code,
            ErrorMessage: DecodeUtf8(response.error_message),
            OutputText: DecodeUtf8(response.output_text),
            OutputLength: response.output_length,
            WorkerCount: response.worker_count,
            ClientCount: response.client_count,
            ClientId: DecodeUtf8(response.client_id),
            IterationCount: response.iteration_count,
            ElapsedMs: response.elapsed_ms,
            RequestAcceptLatencyMs: requestAcceptLatencyMs,
            ClientElapsedMs: clientElapsedMs,
            TransmissionOverheadMs: transmissionOverheadMs,
            OutputValues: outputValues,
            ResultArtifact: artifact,
            Conv2dStats: conv2dStats,
            Timing: timing,
            AcceptedSummary: acceptedSummary,
            LocalOutputPath: materialization.LocalPath,
            OutputSaved: materialization.Saved,
            OutputSaveError: materialization.ErrorMessage,
            OutputChecksum: materialization.Checksum);
    }

    private static IntPtr PrepareAcceptedCallback(Action<RequestAcceptedSummary>? onAccepted, out GCHandle callbackHandle)
    {
        callbackHandle = default;
        if (onAccepted is null)
        {
            return IntPtr.Zero;
        }

        callbackHandle = GCHandle.Alloc(new RequestAcceptedCallbackState(onAccepted));
        return GCHandle.ToIntPtr(callbackHandle);
    }

    private static void HandleRequestAccepted(
        IntPtr requestId,
        IntPtr taskId,
        IntPtr method,
        IntPtr size,
        IntPtr objectId,
        IntPtr clientId,
        ulong acceptedTimestampMs,
        IntPtr uploadId,
        IntPtr downloadId,
        IntPtr dataEndpointHost,
        uint dataEndpointPort,
        IntPtr userData)
    {
        if (userData == IntPtr.Zero)
        {
            return;
        }

        try
        {
            var handle = GCHandle.FromIntPtr(userData);
            if (handle.Target is not RequestAcceptedCallbackState state)
            {
                return;
            }

            state.Report(new RequestAcceptedSummary(
                RequestId: Marshal.PtrToStringUTF8(requestId) ?? string.Empty,
                TaskId: Marshal.PtrToStringUTF8(taskId) ?? string.Empty,
                Method: Marshal.PtrToStringUTF8(method) ?? string.Empty,
                Size: Marshal.PtrToStringUTF8(size) ?? string.Empty,
                ObjectId: Marshal.PtrToStringUTF8(objectId) ?? string.Empty,
                ClientId: Marshal.PtrToStringUTF8(clientId) ?? string.Empty,
                AcceptedTimestampMs: acceptedTimestampMs,
                UploadId: Marshal.PtrToStringUTF8(uploadId) ?? string.Empty,
                DownloadId: Marshal.PtrToStringUTF8(downloadId) ?? string.Empty,
                DataEndpointHost: Marshal.PtrToStringUTF8(dataEndpointHost) ?? string.Empty,
                DataEndpointPort: dataEndpointPort));
        }
        catch
        {
            // Native callbacks must never throw across the ABI boundary.
        }
    }

    private static ResponseTimingSummary? BuildResponseTiming(SwcResponseTiming timing)
    {
        if (timing.worker_count == 0
            && timing.dispatch_ms == 0
            && timing.task_window_ms == 0
            && timing.aggregate_ms == 0)
        {
            return null;
        }

        var workers = new List<WorkerTimingSummary>(checked((int)timing.worker_count));
        if (timing.workers != IntPtr.Zero)
        {
            var stride = Marshal.SizeOf<SwcWorkerTiming>();
            for (var index = 0; index < timing.worker_count; index++)
            {
                var pointer = IntPtr.Add(timing.workers, checked(index * stride));
                var worker = Marshal.PtrToStructure<SwcWorkerTiming>(pointer);
                workers.Add(new WorkerTimingSummary(
                    NodeId: DecodeUtf8(worker.node_id),
                    TaskId: DecodeUtf8(worker.task_id),
                    Slice: DecodeUtf8(worker.slice),
                    WallMs: worker.wall_ms,
                    ArtifactFetchMs: worker.artifact_fetch_ms,
                    ComputationMs: worker.computation_ms,
                    PeripheralMs: worker.peripheral_ms));
            }
        }

        return new ResponseTimingSummary(
            DispatchMs: timing.dispatch_ms,
            TaskWindowMs: timing.task_window_ms,
            AggregateMs: timing.aggregate_ms,
            Workers: workers);
    }

    private static Conv2dStatsSummary? BuildConv2dStats(SwcResponse response)
    {
        var samples = CopyFloatValues(response.conv2d_stats_samples, response.conv2d_stats_sample_count);
        if (response.conv2d_stats_element_count == 0 && samples.Length == 0)
        {
            return null;
        }

        return new Conv2dStatsSummary(
            StatsElementCount: response.conv2d_stats_element_count,
            StatsSum: response.conv2d_stats_sum,
            StatsSumSquares: response.conv2d_stats_sum_squares,
            StatsSamples: samples);
    }

    private static OutputMaterializationResult TryMaterializeOutput(
        string method,
        string requestId,
        RequestArtifact? artifact,
        string? preferredOutputName,
        IReadOnlyList<float> outputValues,
        Conv2dStatsSummary? conv2dStats)
    {
        if (artifact is not null)
        {
            if (!artifact.Ready)
            {
                return new OutputMaterializationResult(null, false, "artifact is not marked ready yet", string.Empty);
            }
            if (string.IsNullOrWhiteSpace(artifact.TransferHost) || artifact.TransferPort == 0)
            {
                return new OutputMaterializationResult(null, false, "artifact has no usable transfer address", string.Empty);
            }

            try
            {
                var destinationPath = BuildOutputDestinationPath(method, requestId, artifact.ContentType, preferredOutputName);
                var checksum = DownloadArtifactToFile(artifact, destinationPath);
                return new OutputMaterializationResult(destinationPath, true, string.Empty, checksum);
            }
            catch (Exception ex)
            {
                return new OutputMaterializationResult(null, false, ex.Message, string.Empty);
            }
        }

        if (string.Equals(method, DefaultFreeContentMethod, StringComparison.OrdinalIgnoreCase)
            || outputValues.Count == 0
            || (string.Equals(method, Conv2dMethod, StringComparison.OrdinalIgnoreCase)
                && conv2dStats is not null
                && conv2dStats.StatsElementCount > 0))
        {
            return OutputMaterializationResult.None;
        }

        try
        {
            var destinationPath = BuildOutputDestinationPath(method, requestId, "application/octet-stream", preferredOutputName);
            File.WriteAllBytes(destinationPath, FloatValuesToBytes(outputValues));
            return new OutputMaterializationResult(
                destinationPath,
                true,
                string.Empty,
                ComputeSha256Hex(File.ReadAllBytes(destinationPath)));
        }
        catch (Exception ex)
        {
            return new OutputMaterializationResult(null, false, ex.Message, string.Empty);
        }
    }

    private static string BuildOutputDestinationPath(string method, string requestId, string contentType, string? preferredOutputName)
    {
        var downloadDirectory = ResolveDownloadDirectory();
        Directory.CreateDirectory(downloadDirectory);

        var suffix = ResolveOutputSuffix(method, contentType);
        string baseName;
        if (!string.IsNullOrWhiteSpace(preferredOutputName))
        {
            (baseName, suffix) = ResolvePreferredOutputBasename(preferredOutputName, suffix);
        }
        else
        {
            baseName = SanitizeFileComponent($"{method}-{requestId}", "response");
        }

        var candidate = Path.Combine(downloadDirectory, $"{baseName}{suffix}");
        if (!File.Exists(candidate))
        {
            return candidate;
        }

        for (var index = 1; index < 10_000; index++)
        {
            candidate = Path.Combine(downloadDirectory, $"{baseName}-{index}{suffix}");
            if (!File.Exists(candidate))
            {
                return candidate;
            }
        }

        throw new IOException("Unable to allocate a unique output file name in the download directory.");
    }

    private static string ResolveDownloadDirectory()
    {
        return Path.Combine(AppContext.BaseDirectory, "download");
    }

    private static string ResolveOutputSuffix(string method, string contentType)
    {
        if ((!string.IsNullOrWhiteSpace(contentType) && contentType.StartsWith("text/", StringComparison.OrdinalIgnoreCase))
            || string.Equals(method, DefaultFreeContentMethod, StringComparison.OrdinalIgnoreCase))
        {
            return ".txt";
        }

        return ".bin";
    }

    private static (string BaseName, string Suffix) ResolvePreferredOutputBasename(string preferredOutputName, string defaultSuffix)
    {
        var leafName = Path.GetFileName(preferredOutputName.Trim());
        if (string.IsNullOrWhiteSpace(leafName))
        {
            return ("response", defaultSuffix);
        }

        var explicitSuffix = Path.GetExtension(leafName).ToLowerInvariant();
        if (explicitSuffix is ".bin" or ".txt")
        {
            return (SanitizeFileComponent(Path.GetFileNameWithoutExtension(leafName), "response"), explicitSuffix);
        }

        return (SanitizeFileComponent(leafName, "response"), defaultSuffix);
    }

    private static string SanitizeFileComponent(string value, string fallback)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var builder = new StringBuilder(value.Length);
        foreach (var ch in value)
        {
            builder.Append(invalid.Contains(ch) ? '-' : ch);
        }

        var sanitized = builder.ToString().Trim(' ', '.', '-', '_');
        return string.IsNullOrWhiteSpace(sanitized) ? fallback : sanitized;
    }

    private static byte[] FloatValuesToBytes(IReadOnlyList<float> values)
    {
        if (values.Count == 0)
        {
            return [];
        }

        var output = new byte[values.Count * sizeof(float)];
        if (values is float[] floatArray)
        {
            Buffer.BlockCopy(floatArray, 0, output, 0, output.Length);
            return output;
        }

        for (var index = 0; index < values.Count; index++)
        {
            var raw = BitConverter.SingleToUInt32Bits(values[index]);
            BinaryPrimitives.WriteUInt32LittleEndian(output.AsSpan(index * sizeof(float), sizeof(float)), raw);
        }
        return output;
    }

    private static string DownloadArtifactToFile(RequestArtifact artifact, string destinationPath)
    {
        using var client = new TcpClient();
        client.ReceiveTimeout = (int)TimeSpan.FromSeconds(ArtifactTransferTimeoutSeconds).TotalMilliseconds;
        client.SendTimeout = client.ReceiveTimeout;
        client.Connect(artifact.TransferHost, checked((int)artifact.TransferPort));

        using var stream = client.GetStream();
        return DownloadFromOpenStream(stream, artifact.ArtifactId, destinationPath, artifact.SizeBytes);
    }

    private static string DownloadFromOpenStream(NetworkStream stream, string downloadId, string destinationPath, ulong? expectedSizeBytes = null)
    {
        var requestPayload = Encoding.UTF8.GetBytes(downloadId);
        stream.Write(BuildDownloadRequest(requestPayload));

        var tmpPath = $"{destinationPath}.tmp";
        Directory.CreateDirectory(Path.GetDirectoryName(destinationPath)!);

        var headerPrefix = ReadExactly(stream, 6);
        ValidateHeaderPrefix(headerPrefix);
        var messageType = headerPrefix[5];
        string checksum;
        ulong declaredSizeBytes;

        if (messageType == 2)
        {
            var initHeader = Combine(headerPrefix, ReadExactly(stream, 20));
            declaredSizeBytes = BinaryPrimitives.ReadUInt64BigEndian(initHeader.AsSpan(6, 8));
            var checksumLength = BinaryPrimitives.ReadUInt32BigEndian(initHeader.AsSpan(18, 4));
            var contentTypeLength = BinaryPrimitives.ReadUInt32BigEndian(initHeader.AsSpan(22, 4));
            var payload = ReadExactly(stream, checked((int)(checksumLength + contentTypeLength)));
            checksum = Encoding.UTF8.GetString(payload, 0, checked((int)checksumLength));
            _ = Encoding.UTF8.GetString(payload, checked((int)checksumLength), checked((int)contentTypeLength));
        }
        else if (messageType == 5)
        {
            ThrowArtifactServerError(stream, headerPrefix);
            return string.Empty;
        }
        else
        {
            throw new InvalidDataException("Unknown initial frame type on the artifact download channel.");
        }

        ulong bytesWritten = 0;
        string digest;
        using (var fileStream = File.Create(tmpPath))
        using (var sha256 = SHA256.Create())
        {
            while (true)
            {
                headerPrefix = ReadExactly(stream, 6);
                ValidateHeaderPrefix(headerPrefix);
                messageType = headerPrefix[5];

                if (messageType == 3)
                {
                    var chunkHeader = Combine(headerPrefix, ReadExactly(stream, 12));
                    var length = BinaryPrimitives.ReadUInt32BigEndian(chunkHeader.AsSpan(14, 4));
                    var payload = ReadExactly(stream, checked((int)length));
                    fileStream.Write(payload);
                    sha256.TransformBlock(payload, 0, payload.Length, null, 0);
                    bytesWritten += (ulong)payload.Length;
                    continue;
                }

                if (messageType == 4)
                {
                    var endHeader = Combine(headerPrefix, ReadExactly(stream, 8));
                    var finalSize = BinaryPrimitives.ReadUInt64BigEndian(endHeader.AsSpan(6, 8));
                    if (finalSize != bytesWritten)
                    {
                        throw new InvalidDataException("Artifact end-frame size does not match the bytes downloaded.");
                    }
                    break;
                }

                if (messageType == 5)
                {
                    ThrowArtifactServerError(stream, headerPrefix);
                    return string.Empty;
                }

                throw new InvalidDataException("Unknown data frame type on the artifact download channel.");
            }

            sha256.TransformFinalBlock([], 0, 0);
            digest = Convert.ToHexString(sha256.Hash ?? []).ToLowerInvariant();
        }

        if (!string.Equals(digest, checksum, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("artifact checksum mismatch");
        }
        if ((expectedSizeBytes.HasValue && bytesWritten != expectedSizeBytes.Value) || bytesWritten != declaredSizeBytes)
        {
            throw new InvalidDataException("artifact size mismatch");
        }

        if (File.Exists(destinationPath))
        {
            File.Delete(destinationPath);
        }
        File.Move(tmpPath, destinationPath);
        return digest;
    }

    private static byte[] BuildDownloadRequest(byte[] artifactIdBytes)
    {
        var header = new byte[10];
        Encoding.ASCII.GetBytes("SWAD").CopyTo(header, 0);
        header[4] = 1;
        header[5] = 1;
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(6, 4), checked((uint)artifactIdBytes.Length));
        return Combine(header, artifactIdBytes);
    }

    private static byte[] BuildDeliverRequest(string uploadId, ulong sizeBytes, string checksum, string contentType)
    {
        var uploadIdBytes = Encoding.UTF8.GetBytes(uploadId);
        var checksumBytes = Encoding.UTF8.GetBytes(checksum);
        var contentTypeBytes = Encoding.UTF8.GetBytes(contentType);
        var header = new byte[26];
        Encoding.ASCII.GetBytes("SWAD").CopyTo(header, 0);
        header[4] = 1;
        header[5] = 6;
        BinaryPrimitives.WriteUInt64BigEndian(header.AsSpan(6, 8), sizeBytes);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(14, 4), checked((uint)uploadIdBytes.Length));
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(18, 4), checked((uint)checksumBytes.Length));
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(22, 4), checked((uint)contentTypeBytes.Length));
        return Combine(Combine(Combine(header, uploadIdBytes), checksumBytes), contentTypeBytes);
    }

    private static byte[] BuildChunkFrame(ulong offset, ReadOnlySpan<byte> payload)
    {
        var header = new byte[18];
        Encoding.ASCII.GetBytes("SWAD").CopyTo(header, 0);
        header[4] = 1;
        header[5] = 3;
        BinaryPrimitives.WriteUInt64BigEndian(header.AsSpan(6, 8), offset);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(14, 4), checked((uint)payload.Length));
        return Combine(header, payload.ToArray());
    }

    private static byte[] BuildEndFrame(ulong sizeBytes)
    {
        var header = new byte[14];
        Encoding.ASCII.GetBytes("SWAD").CopyTo(header, 0);
        header[4] = 1;
        header[5] = 4;
        BinaryPrimitives.WriteUInt64BigEndian(header.AsSpan(6, 8), sizeBytes);
        return header;
    }

    private static long ElapsedMilliseconds(long startTimestamp, long endTimestamp)
    {
        return (long)Math.Round((endTimestamp - startTimestamp) * 1000.0 / Stopwatch.Frequency);
    }

    private static string ComputeSha256Hex(byte[] payload)
    {
        using var sha256 = SHA256.Create();
        return Convert.ToHexString(sha256.ComputeHash(payload)).ToLowerInvariant();
    }

    private static byte[] GenerateDefaultConv2dWeightBytes(Conv2dRequestShape shape, ulong seed)
    {
        var valueCount = checked((int)(shape.KernelSize * shape.KernelSize * shape.ChannelsIn * shape.ChannelsOut));
        var output = new byte[checked(valueCount * sizeof(float))];
        for (var index = 0; index < valueCount; index++)
        {
            var mixed = SplitMix64Value((ulong)index, seed);
            var word = Float32WordFromValue(mixed);
            BinaryPrimitives.WriteUInt32LittleEndian(output.AsSpan(index * sizeof(float), sizeof(float)), word);
        }

        return output;
    }

    private static ulong SplitMix64Value(ulong counter, ulong seed)
    {
        var value = unchecked(seed + ((counter + 1UL) * SplitMix64Gamma));
        value = unchecked((value ^ (value >> 30)) * SplitMix64Mix1);
        value = unchecked((value ^ (value >> 27)) * SplitMix64Mix2);
        return value ^ (value >> 31);
    }

    private static uint Float32WordFromValue(ulong value)
    {
        var lower = (uint)(value & 0xFFFFFFFFUL);
        var sign = (lower & 0x1U) << 31;
        var mantissa = (lower >> 1) & 0x007FFFFFU;
        return sign | 0x3F000000U | mantissa;
    }

    private sealed class Conv2dTransferCoordinator : IDisposable
    {
        private const string Conv2dWeightContentType = "application/x-superweb-conv2d-weight";

        private readonly byte[] _weightBytes;
        private readonly string _weightChecksum;
        private readonly string? _preferredOutputName;

        private TcpClient? _client;
        private NetworkStream? _stream;
        private string _downloadId = string.Empty;
        private string _dataEndpointHost = string.Empty;
        private uint _dataEndpointPort;
        private Exception? _error;

        public Conv2dTransferCoordinator(byte[] weightBytes, string weightChecksum, string? preferredOutputName)
        {
            _weightBytes = weightBytes;
            _weightChecksum = weightChecksum;
            _preferredOutputName = preferredOutputName;
        }

        public void OnAccepted(RequestAcceptedSummary summary)
        {
            _downloadId = summary.DownloadId;
            _dataEndpointHost = summary.DataEndpointHost;
            _dataEndpointPort = summary.DataEndpointPort;
            if (string.IsNullOrWhiteSpace(summary.UploadId))
            {
                return;
            }
            if (string.IsNullOrWhiteSpace(summary.DataEndpointHost) || summary.DataEndpointPort == 0)
            {
                _error = new InvalidOperationException("main did not advertise a data-plane endpoint for conv2d");
                return;
            }

            try
            {
                EnsureOpenSocket(summary.DataEndpointHost, summary.DataEndpointPort);
                Debug.Assert(_stream is not null);
                _stream.Write(BuildDeliverRequest(summary.UploadId, (ulong)_weightBytes.Length, _weightChecksum, Conv2dWeightContentType));
                ulong offset = 0;
                while (offset < (ulong)_weightBytes.Length)
                {
                    var chunkLength = Math.Min(DefaultUploadChunkSize, _weightBytes.Length - (int)offset);
                    _stream.Write(BuildChunkFrame(offset, _weightBytes.AsSpan((int)offset, chunkLength)));
                    offset += (ulong)chunkLength;
                }
                _stream.Write(BuildEndFrame((ulong)_weightBytes.Length));
            }
            catch (Exception ex)
            {
                _error = ex;
                Dispose();
            }
        }

        public OutputMaterializationResult MaterializeOutput(
            string method,
            string requestId,
            RequestArtifact? artifact,
            IReadOnlyList<float> outputValues,
            Conv2dStatsSummary? conv2dStats)
        {
            try
            {
                if (_error is not null)
                {
                    throw _error;
                }

                if (!string.IsNullOrWhiteSpace(_downloadId))
                {
                    if (_stream is null)
                    {
                        if (string.IsNullOrWhiteSpace(_dataEndpointHost) || _dataEndpointPort == 0)
                        {
                            throw new InvalidOperationException("main did not advertise a data-plane endpoint for conv2d download");
                        }
                        EnsureOpenSocket(_dataEndpointHost, _dataEndpointPort);
                    }

                    Debug.Assert(_stream is not null);
                    var destinationPath = BuildOutputDestinationPath(
                        method,
                        requestId,
                        artifact?.ContentType ?? "application/octet-stream",
                        _preferredOutputName);
                    var checksum = DownloadFromOpenStream(_stream, _downloadId, destinationPath, artifact?.SizeBytes);
                    return new OutputMaterializationResult(destinationPath, true, string.Empty, checksum);
                }

                return TryMaterializeOutput(method, requestId, artifact, _preferredOutputName, outputValues, conv2dStats);
            }
            catch (Exception ex)
            {
                return new OutputMaterializationResult(null, false, ex.Message, string.Empty);
            }
            finally
            {
                Dispose();
            }
        }

        public void Dispose()
        {
            try
            {
                _stream?.Dispose();
            }
            catch
            {
                // best effort
            }

            try
            {
                _client?.Close();
            }
            catch
            {
                // best effort
            }

            _stream = null;
            _client = null;
        }

        private void EnsureOpenSocket(string host, uint port)
        {
            if (_stream is not null)
            {
                return;
            }

            _client = new TcpClient
            {
                ReceiveTimeout = (int)TimeSpan.FromSeconds(ArtifactTransferTimeoutSeconds).TotalMilliseconds,
                SendTimeout = (int)TimeSpan.FromSeconds(ArtifactTransferTimeoutSeconds).TotalMilliseconds,
            };
            _client.Connect(host, checked((int)port));
            _stream = _client.GetStream();
        }
    }

    private static void ValidateHeaderPrefix(byte[] headerPrefix)
    {
        if (headerPrefix.Length != 6
            || headerPrefix[0] != (byte)'S'
            || headerPrefix[1] != (byte)'W'
            || headerPrefix[2] != (byte)'A'
            || headerPrefix[3] != (byte)'D'
            || headerPrefix[4] != 1)
        {
            throw new InvalidDataException("Invalid artifact transfer protocol header.");
        }
    }

    private static void ThrowArtifactServerError(NetworkStream stream, byte[] headerPrefix)
    {
        var errorHeader = Combine(headerPrefix, ReadExactly(stream, 4));
        var length = BinaryPrimitives.ReadUInt32BigEndian(errorHeader.AsSpan(6, 4));
        var payload = ReadExactly(stream, checked((int)length));
        throw new InvalidOperationException(Encoding.UTF8.GetString(payload));
    }

    private static byte[] ReadExactly(NetworkStream stream, int size)
    {
        var buffer = new byte[size];
        var offset = 0;
        while (offset < size)
        {
            var read = stream.Read(buffer, offset, size - offset);
            if (read <= 0)
            {
                throw new IOException("Artifact data-plane connection was closed mid-transfer.");
            }
            offset += read;
        }

        return buffer;
    }

    private static byte[] Combine(byte[] first, byte[] second)
    {
        var combined = new byte[first.Length + second.Length];
        Buffer.BlockCopy(first, 0, combined, 0, first.Length);
        Buffer.BlockCopy(second, 0, combined, first.Length, second.Length);
        return combined;
    }

    private static string NormalizeGemvSize(string? size)
    {
        return (size ?? string.Empty).Trim().ToLowerInvariant() switch
        {
            "small" => "small",
            "mid" => "mid",
            "medium" => "mid",
            _ => "large",
        };
    }

    private static uint ResolveGemvVectorLength(string? size)
    {
        return NormalizeGemvSize(size) switch
        {
            "small" => SmallVectorLength,
            "mid" => MidVectorLength,
            _ => LargeVectorLength,
        };
    }

    private void EnsureConnected()
    {
        EnsureBackendPresent();
        if (!IsConnected)
        {
            throw new InvalidOperationException("Not connected yet. Connect or discover the main node first.");
        }
    }

    private static void EnsureBackendPresent()
    {
        var dllPath = Path.Combine(AppContext.BaseDirectory, BackendDllName);
        if (!File.Exists(dllPath))
        {
            throw new FileNotFoundException(
                $"The native backend DLL was not found at '{dllPath}'. Build windows_native/backend first so WinUI3 can load it.",
                dllPath);
        }
    }

    private static void EnsureNativeCall(Func<byte[], int> nativeCall, string action)
    {
        var error = CreateErrorBuffer();
        try
        {
            if (nativeCall(error) == 0)
            {
                var detail = DecodeUtf8(error);
                if (string.IsNullOrWhiteSpace(detail))
                {
                    detail = $"Unable to {action}.";
                }

                throw new InvalidOperationException(detail);
            }
        }
        catch (DllNotFoundException ex)
        {
            throw new InvalidOperationException($"Unable to load '{BackendDllName}'. {ex.Message}", ex);
        }
        catch (EntryPointNotFoundException ex)
        {
            throw new InvalidOperationException(
                $"The native backend is out of date for '{action}'. Rebuild windows_native/backend first.",
                ex);
        }
    }

    private static byte[] CreateErrorBuffer()
    {
        return new byte[ErrorCapacity];
    }

    private static SwcEndpoint CreateEndpoint()
    {
        return new SwcEndpoint
        {
            host = new byte[256],
            source = new byte[32],
        };
    }

    private static SwcResponse CreateResponse()
    {
        return new SwcResponse
        {
            client_id = new byte[64],
            request_id = new byte[128],
            method = new byte[128],
            size = new byte[32],
            object_id = new byte[128],
            stream_id = new byte[128],
            task_id = new byte[128],
            error_message = new byte[512],
            output_text = new byte[2048],
            result_artifact = new SwcArtifactDescriptor
            {
                artifact_id = new byte[128],
                content_type = new byte[64],
                checksum = new byte[128],
                producer_node_id = new byte[64],
                transfer_host = new byte[256],
            },
        };
    }

    private static float[] CopyFloatValues(IntPtr valuesPointer, uint length)
    {
        if (valuesPointer == IntPtr.Zero || length == 0)
        {
            return Array.Empty<float>();
        }

        var values = new float[length];
        Marshal.Copy(valuesPointer, values, 0, checked((int)length));
        return values;
    }

    private static string DecodeUtf8(byte[] raw)
    {
        var zeroIndex = Array.IndexOf(raw, (byte)0);
        var length = zeroIndex >= 0 ? zeroIndex : raw.Length;
        return Encoding.UTF8.GetString(raw, 0, length);
    }

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern int swc_generate_default_vector(
        ulong seed,
        uint vector_length,
        out SwcVectorBuffer out_vector,
        byte[] error_message,
        uint error_message_capacity);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern int swc_discover_main_node(
        [MarshalAs(UnmanagedType.LPUTF8Str)] string client_name,
        IntPtr options,
        ref SwcEndpoint out_endpoint,
        byte[] error_message,
        uint error_message_capacity);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern int swc_open_client(
        [MarshalAs(UnmanagedType.LPUTF8Str)] string host,
        ushort port,
        uint timeout_ms,
        out IntPtr out_handle,
        byte[] error_message,
        uint error_message_capacity);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern int swc_join_client(
        IntPtr handle,
        [MarshalAs(UnmanagedType.LPUTF8Str)] string client_name,
        ref SwcResponse out_response,
        byte[] error_message,
        uint error_message_capacity);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern int swc_request_gemv(
        IntPtr handle,
        ref SwcRequestOptions options,
        [MarshalAs(UnmanagedType.LPUTF8Str)] string size,
        IntPtr input_values,
        uint value_count,
        ref SwcResponse out_response,
        SwcRequestAcceptedCallback? on_request_accepted,
        IntPtr user_data,
        byte[] error_message,
        uint error_message_capacity);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern int swc_request_conv2d(
        IntPtr handle,
        ref SwcRequestOptions options,
        [MarshalAs(UnmanagedType.LPUTF8Str)] string size,
        ref SwcConv2dShape shape,
        ref SwcConv2dRequestOptions conv2d_options,
        ref SwcResponse out_response,
        SwcRequestAcceptedCallback? on_request_accepted,
        IntPtr user_data,
        byte[] error_message,
        uint error_message_capacity);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern int swc_request_free_content(
        IntPtr handle,
        ref SwcRequestOptions options,
        [MarshalAs(UnmanagedType.LPUTF8Str)] string method,
        [MarshalAs(UnmanagedType.LPUTF8Str)] string content,
        ref SwcResponse out_response,
        SwcRequestAcceptedCallback? on_request_accepted,
        IntPtr user_data,
        byte[] error_message,
        uint error_message_capacity);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern void swc_reset_response(ref SwcResponse response);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern void swc_free_vector(ref SwcVectorBuffer buffer);

    [DllImport(BackendDllName, CallingConvention = CallingConvention.Cdecl)]
    private static extern void swc_close_client(IntPtr handle);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void SwcRequestAcceptedCallback(
        IntPtr request_id,
        IntPtr task_id,
        IntPtr method,
        IntPtr size,
        IntPtr object_id,
        IntPtr client_id,
        ulong accepted_timestamp_ms,
        IntPtr upload_id,
        IntPtr download_id,
        IntPtr data_endpoint_host,
        uint data_endpoint_port,
        IntPtr user_data);

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcEndpoint
    {
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 256)]
        public byte[] host;

        public ushort port;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 32)]
        public byte[] source;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcRequestOptions
    {
        public IntPtr client_name;
        public IntPtr request_id;
        public IntPtr object_id;
        public IntPtr stream_id;
        public ulong timestamp_ms;
        public uint iteration_count;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcConv2dShape
    {
        public uint tensor_h;
        public uint tensor_w;
        public uint channels_in;
        public uint channels_out;
        public uint kernel_size;
        public uint padding;
        public uint stride;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcConv2dRequestOptions
    {
        public uint client_response_mode;
        public uint stats_max_samples;
        public ulong upload_size_bytes;
        public IntPtr upload_checksum;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcVectorBuffer
    {
        public IntPtr values;
        public uint length;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcArtifactDescriptor
    {
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] artifact_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
        public byte[] content_type;

        public ulong size_bytes;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] checksum;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
        public byte[] producer_node_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 256)]
        public byte[] transfer_host;

        public uint transfer_port;
        public uint chunk_size;
        public byte ready;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcWorkerTiming
    {
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
        public byte[] node_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] task_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
        public byte[] slice;

        public ulong wall_ms;
        public ulong artifact_fetch_ms;
        public ulong computation_ms;
        public ulong peripheral_ms;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcResponseTiming
    {
        public ulong dispatch_ms;
        public ulong task_window_ms;
        public ulong aggregate_ms;
        public IntPtr workers;
        public uint worker_count;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SwcResponse
    {
        public int status_code;
        public ulong timestamp_ms;
        public ulong elapsed_ms;
        public uint output_length;
        public uint worker_count;
        public uint client_count;
        public uint iteration_count;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
        public byte[] client_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] request_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] method;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 32)]
        public byte[] size;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] object_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] stream_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 128)]
        public byte[] task_id;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 512)]
        public byte[] error_message;

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 2048)]
        public byte[] output_text;

        public SwcArtifactDescriptor result_artifact;
        public ulong conv2d_stats_element_count;
        public double conv2d_stats_sum;
        public double conv2d_stats_sum_squares;
        public IntPtr conv2d_stats_samples;
        public uint conv2d_stats_sample_count;
        public SwcResponseTiming timing;

        public SwcVectorBuffer output_vector;
    }

    private sealed class RequestOptionsScope : IDisposable
    {
        private readonly List<IntPtr> _allocated = [];

        public RequestOptionsScope(string clientName, uint iterationCount, string? objectId, string? streamId)
        {
            Value = new SwcRequestOptions
            {
                client_name = Alloc(clientName),
                request_id = Alloc($"winui3-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}"),
                object_id = Alloc(objectId),
                stream_id = Alloc(streamId),
                timestamp_ms = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                iteration_count = iterationCount == 0 ? 1U : iterationCount,
            };
        }

        public SwcRequestOptions Value { get; }

        public void Dispose()
        {
            foreach (var pointer in _allocated)
            {
                if (pointer != IntPtr.Zero)
                {
                    Marshal.FreeCoTaskMem(pointer);
                }
            }
        }

        private IntPtr Alloc(string? value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return IntPtr.Zero;
            }

            var pointer = Marshal.StringToCoTaskMemUTF8(value.Trim());
            _allocated.Add(pointer);
            return pointer;
        }
    }

    private sealed class RequestAcceptedCallbackState
    {
        private readonly Action<RequestAcceptedSummary> _onAccepted;

        public RequestAcceptedCallbackState(Action<RequestAcceptedSummary> onAccepted)
        {
            _onAccepted = onAccepted;
        }

        public void Report(RequestAcceptedSummary summary)
        {
            _onAccepted(summary);
        }
    }

    private readonly record struct OutputMaterializationResult(
        string? LocalPath,
        bool Saved,
        string ErrorMessage,
        string Checksum)
    {
        public static OutputMaterializationResult None => new(null, false, string.Empty, string.Empty);
    }
}

public sealed record ConnectionSummary(
    string Host,
    ushort Port,
    string Source,
    uint WorkerCount,
    uint ClientCount,
    string ClientId);

public sealed record Conv2dRequestShape(
    uint TensorH,
    uint TensorW,
    uint ChannelsIn,
    uint ChannelsOut,
    uint KernelSize,
    uint Padding,
    uint Stride);

public sealed record RequestArtifact(
    string ArtifactId,
    string ContentType,
    ulong SizeBytes,
    string Checksum,
    string ProducerNodeId,
    string TransferHost,
    uint TransferPort,
    uint ChunkSize,
    bool Ready);

public sealed record Conv2dStatsSummary(
    ulong StatsElementCount,
    double StatsSum,
    double StatsSumSquares,
    IReadOnlyList<float> StatsSamples);

public sealed record WorkerTimingSummary(
    string NodeId,
    string TaskId,
    string Slice,
    ulong WallMs,
    ulong ArtifactFetchMs,
    ulong ComputationMs,
    ulong PeripheralMs);

public sealed record ResponseTimingSummary(
    ulong DispatchMs,
    ulong TaskWindowMs,
    ulong AggregateMs,
    IReadOnlyList<WorkerTimingSummary> Workers);

public sealed record RequestSummary(
    string RequestId,
    string Method,
    string Size,
    string ObjectId,
    string StreamId,
    string TaskId,
    int StatusCode,
    string ErrorMessage,
    string OutputText,
    uint OutputLength,
    uint WorkerCount,
    uint ClientCount,
    string ClientId,
    uint IterationCount,
    ulong ElapsedMs,
    long RequestAcceptLatencyMs,
    long ClientElapsedMs,
    long TransmissionOverheadMs,
    IReadOnlyList<float> OutputValues,
    RequestArtifact? ResultArtifact,
    Conv2dStatsSummary? Conv2dStats,
    ResponseTimingSummary? Timing,
    RequestAcceptedSummary? AcceptedSummary,
    string? LocalOutputPath,
    bool OutputSaved,
    string OutputSaveError,
    string OutputChecksum);

public sealed record RequestAcceptedSummary(
    string RequestId,
    string TaskId,
    string Method,
    string Size,
    string ObjectId,
    string ClientId,
    ulong AcceptedTimestampMs,
    string UploadId,
    string DownloadId,
    string DataEndpointHost,
    uint DataEndpointPort);
