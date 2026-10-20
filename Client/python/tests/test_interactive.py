"""Tests for interactive client helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from session import driver as interactive  # noqa: E402
from core.constants import METHOD_FREE_CONTENT  # noqa: E402
from session.session import RequestResult  # noqa: E402
from wire.control_plane import ArtifactDescriptor  # noqa: E402
from wire.runtime import ClientResponse  # noqa: E402


class InteractiveClientTests(unittest.TestCase):
    def test_auto_conv2d_download_name_keeps_required_size_suffix(self) -> None:
        with mock.patch(
            "session.driver._request_id_from_clock",
            return_value="conv2-abc",
        ):
            small_download_name = interactive._auto_conv2d_download_name("small")
            large_download_name = interactive._auto_conv2d_download_name("large")

        self.assertEqual(small_download_name, "conv2-abc-small")
        self.assertEqual(large_download_name, "conv2-abc-large")

    def test_conv2d_prompt_explains_download_name_and_auto_assignment(self) -> None:
        session = mock.Mock()
        session.request_conv2d.return_value = RequestResult(
            response=ClientResponse(
                request_id="req-1",
                method="conv2d",
                size="mid",
                object_id="conv2d/conv2d-object-1/mid",
                stream_id="",
                timestamp_ms=0,
                status_code=200,
                error_message="",
                worker_count=1,
                client_count=1,
                client_id="client-1",
                iteration_count=1,
                task_id="task-1",
                elapsed_ms=12,
            ),
            output_values=[],
            output_bytes=b"",
            saved_output_path=None,
        )
        args = mock.Mock(
            iteration_count=1,
            client_name="client-1",
            stream_id="",
            preview_count=8,
        )

        with (
            mock.patch("session.driver._prompt_size", return_value="mid"),
            mock.patch("session.driver._prompt_yes_no", return_value=True),
            mock.patch("session.driver._prompt_optional", return_value="") as prompt_optional_mock,
            mock.patch(
                "session.driver._request_id_from_clock",
                side_effect=["conv2-1", "conv2-2"],
            ),
            mock.patch("session.driver._print_response"),
            mock.patch("builtins.print"),
        ):
            interactive._send_conv2d_request(session, args)

        prompt_text = prompt_optional_mock.call_args.args[0]
        self.assertIn("local download name", prompt_text)
        self.assertIn("auto-assigned name", prompt_text)
        session.request_conv2d.assert_called_once()
        self.assertEqual(
            session.request_conv2d.call_args.kwargs["local_download_name"],
            "conv2-1-mid",
        )

    def test_free_content_menu_prompts_for_text_first_and_sends_default_method(self) -> None:
        session = mock.Mock()
        session.request_free_content.return_value = RequestResult(
            response=ClientResponse(
                request_id="req-1",
                method=METHOD_FREE_CONTENT,
                size="",
                object_id="",
                stream_id="",
                timestamp_ms=0,
                status_code=200,
                error_message="",
                worker_count=1,
                client_count=1,
                client_id="client-1",
                iteration_count=1,
                task_id="free-1",
                elapsed_ms=12,
            ),
            output_values=[],
            output_bytes=b"superweb overview",
            saved_output_path=None,
        )
        args = mock.Mock(
            iteration_count=1,
            client_name="client-1",
            preview_count=8,
            object_id="",
            stream_id="",
        )

        with (
            mock.patch("builtins.input", return_value="hello cluster") as input_mock,
            mock.patch("session.driver._request_id_from_clock", return_value="text-1"),
            mock.patch("session.driver._print_response"),
            mock.patch("builtins.print"),
        ):
            interactive._send_free_content(session, args)

        self.assertEqual(input_mock.call_args.args[0], "free content [press Enter to cancel]: ")
        session.request_free_content.assert_called_once_with(
            client_name="client-1",
            request_id="text-1",
            content="hello cluster",
            iteration_count=1,
            object_id="",
            stream_id="",
            method=METHOD_FREE_CONTENT,
            on_task_accepted=mock.ANY,
        )

    @mock.patch("builtins.print")
    def test_print_response_labels_cluster_artifact_and_skips_output_bytes_for_downloaded_file(self, print_mock: mock.Mock) -> None:
        response = ClientResponse(
            request_id="req-1",
            method="conv2d",
            size="large",
            object_id="conv2d/demo/large",
            stream_id="",
            timestamp_ms=0,
            status_code=200,
            error_message="",
            worker_count=1,
            client_count=1,
            client_id="client-1",
            iteration_count=1,
            task_id="task-1",
            elapsed_ms=42,
            result_artifact=ArtifactDescriptor(
                artifact_id="artifact-1",
                content_type="application/octet-stream",
                size_bytes=1234,
                checksum="checksum",
                producer_node_id="main-node",
                transfer_host="127.0.0.1",
                transfer_port=52021,
                chunk_size=262144,
                ready=True,
            ),
        )
        result = RequestResult(
            response=response,
            output_values=[],
            output_bytes=b"artifact-bytes",
            request_accept_latency_ms=14,
            client_elapsed_ms=58,
            transmission_overhead_ms=16,
            saved_output_path=Path("result.bin"),
            artifact_downloaded=True,
        )

        interactive._print_response(result, preview_count=8)

        printed_lines = [" ".join(str(item) for item in call.args) for call in print_mock.call_args_list]
        self.assertTrue(any("cluster_artifact_id=artifact-1 artifact_bytes=1234" in line for line in printed_lines))
        self.assertTrue(
            any("downloaded_to=" in line and "result.bin" in line for line in printed_lines)
        )
        self.assertTrue(
            any(
                "client_elapsed_ms=58 request_accept_latency_ms=14 transmission_overhead_ms=16" in line
                for line in printed_lines
            )
        )
        self.assertFalse(any("output_bytes=" in line for line in printed_lines))

    @mock.patch("builtins.print")
    def test_print_response_prints_checksum_for_artifact_backed_result(self, print_mock: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = Path(tmp) / "out.bin"
            artifact_path.write_bytes(b"payload")

            response = ClientResponse(
                request_id="req-1",
                method="conv2d",
                size="large",
                object_id="conv2d/demo/large",
                stream_id="",
                timestamp_ms=0,
                status_code=200,
                error_message="",
                worker_count=1,
                client_count=1,
                client_id="client-1",
                iteration_count=1,
                task_id="task-1",
                elapsed_ms=42,
                result_artifact=ArtifactDescriptor(
                    artifact_id="artifact-1",
                    content_type="application/octet-stream",
                    size_bytes=artifact_path.stat().st_size,
                    checksum="expected-checksum",
                    producer_node_id="main-node",
                    transfer_host="127.0.0.1",
                    transfer_port=52021,
                    chunk_size=262144,
                    ready=True,
                ),
            )
            result = RequestResult(
                response=response,
                output_values=[],
                output_bytes=b"",
                saved_output_path=artifact_path,
                artifact_path=artifact_path,
                artifact_downloaded=True,
                artifact_checksum="expected-checksum",
            )

            interactive._print_response(result, preview_count=4)

        printed_lines = [" ".join(str(item) for item in call.args) for call in print_mock.call_args_list]
        checksum_line = next((line for line in printed_lines if line.startswith("checksum=")), "")
        self.assertEqual(checksum_line, "checksum=expected-checksum")
        self.assertFalse(any(line.startswith("output_preview=") for line in printed_lines))


if __name__ == "__main__":
    unittest.main()
