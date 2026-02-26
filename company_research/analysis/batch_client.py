"""OpenAI Batch API client for 50% cost savings on LLM extraction."""

from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Submit LLM requests via OpenAI Batch API for 50% cost savings."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def submit_batch(
        self,
        requests: list[dict],
    ) -> str:
        """Write JSONL, upload, create batch. Returns batch_id.

        Each request dict: {"id": str, "prompt": str, "max_tokens": int, "temperature": float}
        """
        if not requests:
            raise ValueError("No requests to submit")

        # Build JSONL content
        lines = []
        for req in requests:
            line = {
                "custom_id": req["id"],
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": self.model,
                    "messages": [{"role": "user", "content": req["prompt"]}],
                    "max_tokens": req.get("max_tokens", 5000),
                    "temperature": req.get("temperature", 0),
                },
            }
            lines.append(json.dumps(line))

        jsonl_content = "\n".join(lines)

        # Write to temp file and upload
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8",
        ) as f:
            f.write(jsonl_content)
            tmp_path = f.name

        try:
            with open(tmp_path, "rb") as f:
                file_obj = self.client.files.create(file=f, purpose="batch")

            batch = self.client.batches.create(
                input_file_id=file_obj.id,
                endpoint="/v1/chat/completions",
                completion_window="24h",
            )
            logger.info("Batch submitted: %s (%d requests)", batch.id, len(requests))
            return batch.id
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def poll_batch(
        self,
        batch_id: str,
        poll_interval: int = 30,
        timeout: int = 3600,
        status_callback: object | None = None,
    ) -> dict[str, str]:
        """Poll until complete. Returns {custom_id: response_text}.

        Args:
            batch_id: The batch ID to poll.
            poll_interval: Seconds between status checks.
            timeout: Max total wait time in seconds.
            status_callback: Optional callable(elapsed_s, status, completed, total)
                for progress reporting.
        """
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                logger.warning("Batch %s timed out after %ds, cancelling", batch_id, timeout)
                try:
                    self.client.batches.cancel(batch_id)
                except Exception:
                    pass
                raise TimeoutError(
                    f"Batch {batch_id} did not complete within {timeout}s"
                )

            batch = self.client.batches.retrieve(batch_id)
            status = batch.status
            completed = (batch.request_counts.completed or 0) if batch.request_counts else 0
            total = (batch.request_counts.total or 0) if batch.request_counts else 0

            if status_callback:
                try:
                    status_callback(int(elapsed), status, completed, total)
                except Exception:
                    pass

            if status == "completed":
                return self._download_results(batch.output_file_id)

            if status in ("failed", "expired", "cancelled"):
                errors = []
                if batch.errors and batch.errors.data:
                    errors = [e.message for e in batch.errors.data[:3]]
                raise RuntimeError(
                    f"Batch {batch_id} {status}: {'; '.join(errors) or 'no details'}"
                )

            time.sleep(poll_interval)

    def _download_results(self, output_file_id: str) -> dict[str, str]:
        """Download and parse batch results into {custom_id: response_text}."""
        content = self.client.files.content(output_file_id)
        results: dict[str, str] = {}

        for line in content.text.strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            custom_id = entry["custom_id"]
            response_body = entry.get("response", {}).get("body", {})

            # Extract the text content from the chat completion response
            choices = response_body.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "")
                results[custom_id] = text
            else:
                error = entry.get("error", {})
                logger.warning(
                    "Batch request %s had no choices: %s", custom_id, error
                )
                results[custom_id] = ""

        return results

    def submit_and_wait(
        self,
        requests: list[dict],
        poll_interval: int = 30,
        timeout: int = 3600,
        status_callback: object | None = None,
    ) -> dict[str, str]:
        """Convenience: submit batch + poll until complete + return results.

        Returns {custom_id: response_text}.
        """
        batch_id = self.submit_batch(requests)
        return self.poll_batch(
            batch_id,
            poll_interval=poll_interval,
            timeout=timeout,
            status_callback=status_callback,
        )
