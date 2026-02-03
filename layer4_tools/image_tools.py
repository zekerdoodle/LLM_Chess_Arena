"""InvokeAI image generation tool integration.

This module replaces the previous AUTOMATIC1111 implementation. It submits
InvokeAI workflows via the queue API, polls for completion, downloads the
rendered image, and returns metadata to the orchestrator.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import requests

from utils.config_loader import load_config
from utils.logger import get_logger

try:  # pragma: no cover - vault may not be available in unit tests
    from utils.vault_paths import get_vault_root
except Exception:  # pragma: no cover - fallback when vault utilities missing
    get_vault_root = lambda: "vault"  # type: ignore

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_JOB_TIMEOUT = 600


class _SemaphoreContext:
    """Context manager wrapper for threading semaphores."""

    def __init__(self, semaphore: threading.Semaphore):
        self._semaphore = semaphore

    def __enter__(self):
        self._semaphore.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._semaphore.release()
        return False


_SEMAPHORE_LOCK = threading.Lock()
_SEMAPHORE: Optional[threading.BoundedSemaphore] = None


@dataclass(slots=True)
class InvokeAISettings:
    base_url: str
    queue_id: str
    timeout: int
    poll_interval: float
    save_images: bool
    save_dir: Path
    max_concurrent_requests: int
    workflow: Dict[str, Any]
    negative_prompt_default: str
    default_width: int
    default_height: int
    default_steps: int
    default_cfg_scale: float
    job_timeout: int = 600


def _acquire_invokeai_slot(limit: int) -> _SemaphoreContext:
    """Return a context manager enforcing max concurrent InvokeAI requests."""

    normalized_limit = 1
    if limit > 1:
        logger.debug(
            "L4.tools [image_generation] - max_concurrent_requests=%s overridden to 1",
            limit,
        )

    global _SEMAPHORE
    with _SEMAPHORE_LOCK:
        if _SEMAPHORE is None:
            _SEMAPHORE = threading.BoundedSemaphore(1)
        semaphore = _SEMAPHORE
    return _SemaphoreContext(semaphore)


def _resolve_base_url(config_section: Dict[str, Any]) -> str:
    base_url = str(config_section.get("base_url") or "").strip()
    if base_url:
        return base_url.rstrip("/")

    host = str(config_section.get("host", "desktop") or "desktop").strip()
    port = config_section.get("port", 9090)
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        port_int = 9090
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return f"http://{host}:{port_int}"


def _resolve_save_dir(config_section: Dict[str, Any]) -> Path:
    root = Path(config_section.get("save_dir") or "")
    if not root:
        try:  # pragma: no cover - dependent on vault availability
            root = Path(get_vault_root()) / "generated_images"
        except Exception:
            root = Path("vault/generated_images")
    if not root.is_absolute():
        root = _PROJECT_ROOT / root
    return root


def _load_workflow_template(config_section: Dict[str, Any]) -> Dict[str, Any]:
    workflow_config = config_section.get("workflow") or {}

    if isinstance(workflow_config, dict):
        if "json" in workflow_config and workflow_config["json"]:
            payload = workflow_config["json"]
            if isinstance(payload, str):
                return json.loads(payload)
            if isinstance(payload, dict):
                return json.loads(json.dumps(payload))  # copy
        path_value = workflow_config.get("path")
        if path_value:
            workflow_path = Path(path_value)
            if not workflow_path.is_absolute():
                workflow_path = _PROJECT_ROOT / workflow_path
            if not workflow_path.exists():
                raise FileNotFoundError(
                    f"InvokeAI workflow template not found at {workflow_path}. "
                    "Export a workflow from InvokeAI and update config.invokeai.workflow.path."
                )
            with workflow_path.open("r", encoding="utf-8") as wf:
                return json.load(wf)
    raise ValueError(
        "InvokeAI workflow template not configured. Please set config.invokeai.workflow.path "
        "or config.invokeai.workflow.json."
    )


def _get_invokeai_settings() -> InvokeAISettings:
    config = load_config()
    section = config.get("invokeai", {})

    try:
        from utils.vault_paths import get_vault_root  # type: ignore[redefined-outer-name]
    except Exception:  # pragma: no cover - fallback already defined at top
        pass

    base_url = _resolve_base_url(section)
    queue_id = str(section.get("queue_id", "default"))
    timeout = int(section.get("timeout", 300))
    poll_interval = float(section.get("poll_interval", 2.0))
    max_concurrent_requests = int(section.get("max_concurrent_requests", 1))
    save_images = bool(section.get("save_images", True))
    save_dir = _resolve_save_dir(section)
    workflow = _load_workflow_template(section)

    negative_prompt_default = str(section.get("negative_prompt", ""))
    default_width = int(section.get("default_width", 832))
    default_height = int(section.get("default_height", 1216))
    default_steps = int(section.get("default_steps", 35))
    default_cfg_scale = float(section.get("default_cfg_scale", 4.5))
    job_timeout = int(section.get("job_timeout", 0) or max(timeout * 2, _DEFAULT_JOB_TIMEOUT))
    if job_timeout < timeout:
        job_timeout = timeout

    return InvokeAISettings(
        base_url=base_url,
        queue_id=queue_id,
        timeout=timeout,
        poll_interval=poll_interval,
        save_images=save_images,
        save_dir=save_dir,
        max_concurrent_requests=max_concurrent_requests,
        workflow=workflow,
        negative_prompt_default=negative_prompt_default,
        default_width=default_width,
        default_height=default_height,
        default_steps=default_steps,
        default_cfg_scale=default_cfg_scale,
        job_timeout=job_timeout,
    )


def _replace_placeholders(obj: Any, mapping: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {key: _replace_placeholders(value, mapping) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholders(item, mapping) for item in obj]
    if isinstance(obj, str) and obj in mapping:
        return mapping[obj]
    return obj


def _coerce_positive_int(value: Optional[int], fallback: int) -> int:
    try:
        if value is None:
            return fallback
        ivalue = int(value)
        return ivalue if ivalue > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _coerce_positive_float(value: Optional[float], fallback: float) -> float:
    try:
        if value is None:
            return fallback
        fvalue = float(value)
        return fvalue if fvalue > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _normalize_seed(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return None
    return ivalue if ivalue >= 0 else None


def _enforce_generation_limits(
    settings: InvokeAISettings,
    width: int,
    height: int,
    steps: int,
) -> Tuple[int, int, int]:
    # Preserve the caller-provided generation parameters without applying
    # automatic clamps so Theo can run fully custom jobs.
    return width, height, steps


def _set_input_value(inputs: Dict[str, Any], field: str, value: Any) -> None:
    if value is None:
        return
    slot = inputs.get(field)
    if isinstance(slot, dict):
        slot["value"] = value
    else:
        inputs[field] = {"name": field, "value": value}


def _assign_node_value(data: Dict[str, Any], field: str, value: Any) -> None:
    """Update either an `inputs` entry or a direct node attribute."""

    if value is None:
        return

    inputs = data.get("inputs")
    if isinstance(inputs, dict):
        _set_input_value(inputs, field, value)
    else:
        data[field] = value


def _iterate_nodes(graph: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    nodes = graph.get("nodes")
    if isinstance(nodes, dict):
        return nodes.values()
    if isinstance(nodes, list):
        return nodes
    return []


def _convert_node_data(entry: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(entry)  # shallow copy
    result: Dict[str, Any] = {
        "id": data.get("id") or str(uuid.uuid4()),
        "type": data.get("type"),
        "is_intermediate": bool(data.get("isIntermediate", data.get("is_intermediate", True))),
        "use_cache": bool(data.get("useCache", data.get("use_cache", True))),
    }

    inputs = data.get("inputs")
    if isinstance(inputs, dict):
        for field, value in inputs.items():
            if isinstance(value, dict):
                if "value" in value:
                    result[field] = value.get("value")
                else:
                    meta_keys = set(value.keys())
                    if meta_keys <= {"name", "label", "description"}:
                        result[field] = None
                    else:
                        result[field] = value
            else:
                result[field] = value

    # Carry through additional explicit fields (e.g., metadata)
    for key in ("metadata", "notes", "label"):
        if key in data:
            result[key] = data[key]

    return result


def _apply_graph_overrides(
    graph: Dict[str, Any],
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    seed: Optional[int],
) -> None:
    # Map node ids to their outgoing destination fields to disambiguate positive vs negative conditioning
    destination_fields: Dict[str, set[str]] = {}
    edges = graph.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = edge.get("source") or {}
            dest = edge.get("destination") or {}
            src_id = src.get("node_id")
            dest_field = dest.get("field")
            if not (src_id and dest_field):
                continue
            destination_fields.setdefault(str(src_id), set()).add(str(dest_field))

    for node in _iterate_nodes(graph):
        if not isinstance(node, dict):
            continue
        data = node.get("data") if "data" in node else node
        if not isinstance(data, dict):
            continue
        node_type = str(data.get("type") or "").lower()
        label = str(data.get("label") or "").lower()

        if node_type == "compel":
            node_id = str(data.get("id") or node.get("id") or "")
            dest_fields = destination_fields.get(node_id, set())
            if "negative" in label or "negative_conditioning" in dest_fields:
                _assign_node_value(data, "prompt", negative_prompt)
            else:
                _assign_node_value(data, "prompt", prompt)
        elif node_type == "sdxl_compel_prompt":
            node_id = str(data.get("id") or node.get("id") or "")
            dest_fields = destination_fields.get(node_id, set())
            is_negative = "negative" in label or any(
                "negative" in field for field in dest_fields
            )
            selected_prompt = negative_prompt if is_negative else prompt
            _assign_node_value(data, "prompt", selected_prompt)
            _assign_node_value(data, "style", "")
            for field_name in ("original_width", "target_width"):
                _assign_node_value(data, field_name, width)
            for field_name in ("original_height", "target_height"):
                _assign_node_value(data, field_name, height)
        elif node_type == "noise":
            _assign_node_value(data, "width", width)
            _assign_node_value(data, "height", height)
            if seed is not None:
                _assign_node_value(data, "seed", seed)
        elif node_type == "denoise_latents":
            _assign_node_value(data, "steps", steps)
            _assign_node_value(data, "cfg_scale", cfg_scale)
        elif node_type == "rand_int" and seed is not None:
            _assign_node_value(data, "low", seed)
            _assign_node_value(data, "high", seed + 1)
        elif node_type == "string":
            node_id = str(data.get("id") or node.get("id") or "")
            value_hint = str(data.get("value") or "")
            is_negative = "negative" in label or "negative" in value_hint.lower()
            selected_prompt = negative_prompt if is_negative else prompt
            _assign_node_value(data, "value", selected_prompt)

        if node_type in {"l2i", "esrgan", "save_image"}:
            data["board"] = None


def _prepare_batch(
    settings: InvokeAISettings,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    seed: Optional[int],
) -> Dict[str, Any]:
    template = json.loads(json.dumps(settings.workflow))  # deep copy

    replacements = {
        "{{prompt}}": prompt,
        "{{positive_prompt}}": prompt,
        "{{negative_prompt}}": negative_prompt,
        "{{width}}": str(width),
        "{{height}}": str(height),
        "{{steps}}": str(steps),
        "{{cfg_scale}}": str(cfg_scale),
        "{{seed}}": str(seed) if seed is not None else "",
    }

    template = _replace_placeholders(template, replacements)

    # The template may represent either a bare graph or a workflow wrapper. Normalise into a batch.
    if "graph" in template:
        graph = template.get("graph") or {}
        workflow_fragment = {k: v for k, v in template.items() if k != "graph"}
    else:
        graph = template
        workflow_fragment = {}

    if isinstance(graph, dict):
        graph = json.loads(json.dumps(graph))
    else:
        raise ValueError("InvokeAI workflow template missing 'graph' definition")

    nodes = graph.get("nodes")
    if isinstance(nodes, list):
        transformed_nodes: Dict[str, Dict[str, Any]] = {}
        for entry in nodes:
            if not isinstance(entry, dict):
                continue
            node_id = entry.get("id")
            data = entry.get("data")
            if node_id is None or not isinstance(data, dict):
                continue
            transformed_nodes[str(node_id)] = _convert_node_data(data)
        graph["nodes"] = transformed_nodes

    edges = graph.get("edges")
    if isinstance(edges, list):
        transformed_edges = []
        for entry in edges:
            if not isinstance(entry, dict):
                continue

            source_dict = entry.get("source")
            dest_dict = entry.get("destination")

            transformed: Dict[str, Any] | None = None

            if isinstance(source_dict, dict) and isinstance(dest_dict, dict):
                src_node = source_dict.get("node_id")
                src_field = source_dict.get("field")
                dst_node = dest_dict.get("node_id")
                dst_field = dest_dict.get("field")
                if src_node and src_field and dst_node and dst_field:
                    transformed = {
                        "source": {"node_id": str(src_node), "field": str(src_field)},
                        "destination": {"node_id": str(dst_node), "field": str(dst_field)},
                    }
            else:
                source_id = entry.get("source")
                target_id = entry.get("target")
                source_field = entry.get("sourceHandle")
                target_field = entry.get("targetHandle")
                if source_id and target_id and source_field and target_field:
                    transformed = {
                        "source": {"node_id": str(source_id), "field": str(source_field)},
                        "destination": {"node_id": str(target_id), "field": str(target_field)},
                    }

            if transformed is None:
                continue

            for key, value in entry.items():
                if key in {"source", "destination", "sourceHandle", "targetHandle", "target"}:
                    continue
                transformed[key] = value

            transformed_edges.append(transformed)

        if transformed_edges:
            graph["edges"] = transformed_edges

    _apply_graph_overrides(graph, prompt, negative_prompt, width, height, steps, cfg_scale, seed)

    graph_id = f"theo-{uuid.uuid4().hex}"
    graph["id"] = graph_id

    batch: Dict[str, Any] = {
        "graph": graph,
        "origin": "theo-image-tool",
        "destination": "theo",
        "runs": 1,
    }

    if workflow_fragment:
        batch["workflow"] = workflow_fragment

    return batch


def _enqueue_batch(settings: InvokeAISettings, batch: Dict[str, Any]) -> Tuple[str, Iterable[int]]:
    url = f"{settings.base_url}/api/v1/queue/{settings.queue_id}/enqueue_batch"
    response = requests.post(url, json={"batch": batch}, timeout=settings.timeout)
    response.raise_for_status()
    payload = response.json()

    item_ids = payload.get("item_ids") or []
    if not item_ids:
        raise RuntimeError("InvokeAI did not return queued item ids")
    batch_id = payload.get("batch", {}).get("batch_id") or batch.get("batch_id") or str(uuid.uuid4())
    return batch_id, item_ids


def _poll_for_completion(
    settings: InvokeAISettings,
    item_id: int,
) -> Dict[str, Any]:
    url = f"{settings.base_url}/api/v1/queue/{settings.queue_id}/i/{item_id}"
    max_wait = max(settings.job_timeout, settings.timeout)
    deadline = time.time() + max_wait

    while True:
        response = requests.get(url, timeout=settings.timeout)
        response.raise_for_status()
        item = response.json()
        status = (item.get("status") or "").lower()

        if status == "completed":
            return item
        if status in {"failed", "canceled"}:
            error_message = item.get("error_message") or "InvokeAI job failed"
            raise RuntimeError(error_message)
        if time.time() >= deadline:
            raise TimeoutError(
                f"InvokeAI job {item_id} did not complete within {max_wait} seconds"
            )
        time.sleep(settings.poll_interval)


def _extract_image_name(queue_item: Dict[str, Any]) -> str:
    session = queue_item.get("session") or {}
    results = session.get("results") or {}

    if isinstance(results, dict):
        for result in results.values():
            if isinstance(result, dict):
                # Direct image output
                image_field = result.get("image")
                if isinstance(image_field, dict) and image_field.get("image_name"):
                    return str(image_field["image_name"])
                # Collections
                collection = result.get("collection")
                if isinstance(collection, list) and collection:
                    first = collection[0]
                    if isinstance(first, dict) and first.get("image_name"):
                        return str(first["image_name"])
    raise RuntimeError("InvokeAI queue item did not contain an image result")


def _download_image(settings: InvokeAISettings, image_name: str) -> bytes:
    url = f"{settings.base_url}/api/v1/images/i/{image_name}/full"
    response = requests.get(url, timeout=settings.timeout)
    response.raise_for_status()
    return response.content


def _save_image(settings: InvokeAISettings, image_bytes: bytes, prompt: str) -> str:
    try:
        settings.save_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        stem = prompt[:60].strip().replace(" ", "_") or "image"
        filename = f"{timestamp}_{uuid.uuid4().hex[:8]}_{stem}.png"
        path = settings.save_dir / filename
        path.write_bytes(image_bytes)
        return str(path)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("L4.tools [image_generation] - Failed to persist image: %s", exc)
        return ""


def generate_image(
    prompt: str,
    dont_send: bool = False,
    steps: Optional[int] = None,
    cfg_scale: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    seed: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
    auto_send: Optional[bool] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Generate an image using InvokeAI's queue API."""

    if not isinstance(prompt, str) or not prompt.strip():
        error_msg = "Image prompt cannot be empty"
        return (
            f"Error: {error_msg}",
            {
                "success": False,
                "error": error_msg,
                "dont_send": True,
                "auto_send": False,
            },
        )

    settings = _get_invokeai_settings()

    effective_timeout = max(timeout_seconds or 0, 0)
    if effective_timeout:
        new_timeout = int(max(settings.timeout, effective_timeout))
        new_job_timeout = max(settings.job_timeout, max(new_timeout, int(effective_timeout * 2)))
        settings = replace(settings, timeout=new_timeout, job_timeout=new_job_timeout)

    prompt_text = prompt.strip()
    neg_prompt_raw = negative_prompt if negative_prompt is not None else settings.negative_prompt_default
    neg_prompt = neg_prompt_raw.strip() if isinstance(neg_prompt_raw, str) else ""

    effective_width = _coerce_positive_int(width, settings.default_width)
    effective_height = _coerce_positive_int(height, settings.default_height)
    effective_steps = _coerce_positive_int(steps, settings.default_steps)
    effective_cfg_scale = _coerce_positive_float(cfg_scale, settings.default_cfg_scale)
    effective_seed = _normalize_seed(seed)

    effective_width, effective_height, effective_steps = _enforce_generation_limits(
        settings,
        effective_width,
        effective_height,
        effective_steps,
    )

    batch = _prepare_batch(
        settings,
        prompt_text,
        neg_prompt,
        effective_width,
        effective_height,
        effective_steps,
        effective_cfg_scale,
        effective_seed,
    )

    with _acquire_invokeai_slot(settings.max_concurrent_requests):
        batch_id, item_ids = _enqueue_batch(settings, batch)
        first_item = next(iter(item_ids))
        queue_item = _poll_for_completion(settings, first_item)

    image_name = _extract_image_name(queue_item)
    image_bytes = _download_image(settings, image_name)
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    saved_path = ""
    if settings.save_images:
        saved_path = _save_image(settings, image_bytes, prompt_text)

    generation_time = queue_item.get("completed_at") or time.time()

    auto_flag = True if auto_send is None else bool(auto_send)
    if auto_send is None and dont_send:
        auto_flag = False

    cfg_display = f"{effective_cfg_scale:g}" if isinstance(effective_cfg_scale, float) else str(effective_cfg_scale)
    if auto_flag:
        result_lines = [
            "✅ Image generated and attached above!",
            "Let me know if you want refinements or another variation.",
        ]
        if saved_path:
            result_lines.append(f"(Local copy saved to: {saved_path})")
    else:
        result_lines = [
            "✅ Image generated successfully.",
            f"Image Name: {image_name}",
            f"Dimensions: {effective_width}x{effective_height}",
            f"Steps: {effective_steps}, CFG Scale: {cfg_display}",
            f"Seed: {effective_seed if effective_seed is not None else 'random'}",
            f"Prompt: {prompt_text}",
            f"Negative Prompt: {neg_prompt if neg_prompt else '(none)'}",
        ]
        if saved_path:
            result_lines.append(f"Saved to: {saved_path}")
        result_lines.append("Auto-send to user: False")

    tool_output: Dict[str, Any] = {
        "success": True,
        "dont_send": (not auto_flag),
        "auto_send": auto_flag,
        "image_name": image_name,
        "file_size_kb": len(image_bytes) // 1024,
        "generation_time": generation_time,
        "queue_item_id": first_item,
        "batch_id": batch_id,
        "prompt_tokens": None,
        "seed": effective_seed,
        "width": effective_width,
        "height": effective_height,
        "steps": effective_steps,
        "cfg_scale": effective_cfg_scale,
        "prompt": prompt_text,
        "negative_prompt": neg_prompt,
    }

    if saved_path:
        tool_output["image_path"] = saved_path

    if auto_flag:
        tool_output["image_data"] = image_b64
        tool_output["action"] = "send_image"

    return "\n".join(result_lines), tool_output


__all__ = ["generate_image"]
