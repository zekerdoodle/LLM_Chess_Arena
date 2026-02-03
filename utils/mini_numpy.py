"""
Minimal NumPy compatibility layer used for test environments where the real
NumPy package is not available.

This module intentionally implements only the tiny subset of behaviour that the
Theo test-suite exercises. The goal is to keep the rest of the codebase working
without pulling in a heavyweight dependency during unit tests.  The
implementation focuses on simple 1D/2D float/int arrays and a handful of array
operations (reshape, matmul, astype, argsort, etc.).

It is NOT a drop-in replacement for NumPy – only the paths used inside
``layer3_longterm.embeddings`` are supported.
"""

from __future__ import annotations

import json
import math
import random as _py_random
from typing import Any, Iterable, Iterator, List, Sequence, Tuple, Union
import sys

Number = Union[int, float]


def _normalize_dtype(dtype: Any) -> Any:
    if dtype is None:
        return float
    if dtype in (float, "float", "float64", "float32"):
        return float
    if dtype in (int, "int", "int32", "int64"):
        return int
    if dtype in (bool, "bool"):
        return bool
    if callable(dtype):
        return dtype
    return float


def _deep_copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_deep_copy(v) for v in value)
    return value


def _product(values: Sequence[int]) -> int:
    result = 1
    for v in values:
        result *= int(v)
    return int(result)


def _iter_flat(data: Any) -> Iterator[Number]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_flat(item)
    else:
        yield data


def _infer_shape(data: Any) -> Tuple[int, ...]:
    if isinstance(data, list):
        length = len(data)
        if length == 0:
            return (0,)
        child_shape = _infer_shape(data[0])
        for child in data[1:]:
            if _infer_shape(child) != child_shape:
                raise ValueError("inconsistent array shape")
        return (length,) + child_shape
    return ()


def _coerce(data: Any, dtype: Any) -> Any:
    if isinstance(data, MiniArray):
        return _coerce(data.tolist(), dtype)
    if isinstance(data, list):
        return [_coerce(v, dtype) for v in data]
    if isinstance(data, tuple):
        return [_coerce(list(data), dtype)]
    try:
        return dtype(data)
    except Exception:
        return data


def _ensure_sequence(value: Any) -> List[Any]:
    if isinstance(value, MiniArray):
        return value.tolist()
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    if isinstance(value, tuple):
        return [_deep_copy(v) for v in value]
    return [value]


def _flatten_values(data: Any) -> List[Any]:
    if isinstance(data, MiniArray):
        return _flatten_values(data.tolist())
    if isinstance(data, list):
        values: List[Any] = []
        for item in data:
            values.extend(_flatten_values(item))
        return values
    return [data]


def _build_from_iter(iterator: Iterator[Any], shape: Tuple[int, ...]) -> Any:
    if not shape:
        return next(iterator)
    size = int(shape[0])
    return [_build_from_iter(iterator, shape[1:]) for _ in range(size)]


def _align_shape(data: Any, shape: Tuple[int, ...]) -> Any:
    if not shape:
        flat = _flatten_values(data)
        return flat[0] if flat else data
    try:
        inferred = _infer_shape(data)
        if inferred == shape:
            return data
    except Exception:
        pass
    flat = _flatten_values(data)
    iterator = iter(flat)
    return _build_from_iter(iterator, shape)


def _infer_dtype_from_data(data: Any) -> Any:
    if isinstance(data, MiniArray):
        return data.dtype
    if isinstance(data, list):
        inferred = {_infer_dtype_from_data(item) for item in data if item is not None}
        if not inferred:
            return float
        if inferred <= {bool}:
            return bool
        if inferred <= {int}:
            return int
        if inferred <= {int, float}:
            return float
        return float
    if isinstance(data, tuple):
        return _infer_dtype_from_data(list(data))
    if isinstance(data, bool):
        return bool
    if isinstance(data, int):
        return int
    if isinstance(data, float):
        return float
    return float


if "numpy" not in sys.modules:
    sys.modules["numpy"] = sys.modules[__name__]  # pragma: no cover


class MiniArray:
    def __init__(self, data: Any, dtype: Any = float, shape: Tuple[int, ...] | None = None):
        self._dtype = _normalize_dtype(dtype)
        coerced = _coerce(data, self._dtype)
        if shape is None:
            try:
                inferred = _infer_shape(coerced)
            except ValueError:
                inferred = ()
            if inferred == (0,) and isinstance(data, (list, tuple)) and hasattr(data, "_shape"):
                shape = tuple(getattr(data, "_shape"))
            else:
                shape = inferred
        else:
            coerced = _align_shape(coerced, tuple(shape))
        self._data = coerced
        self._shape: Tuple[int, ...] = tuple(shape if shape is not None else ())

    # ------------------------------------------------------------------ basic properties
    @property
    def dtype(self) -> Any:
        return self._dtype

    @property
    def shape(self) -> Tuple[int, ...]:
        return self._shape

    @property
    def ndim(self) -> int:
        return len(self._shape)

    @property
    def size(self) -> int:
        if not self._shape:
            return 1
        if 0 in self._shape:
            return 0
        return _product(self._shape)

    # ------------------------------------------------------------------ representation helpers
    def __repr__(self) -> str:
        return f"MiniArray(shape={self.shape}, data={self._data})"

    def tolist(self) -> Any:
        return _deep_copy(self._data)

    def copy(self) -> "MiniArray":
        return MiniArray(self.tolist(), dtype=self._dtype, shape=self._shape)

    # ------------------------------------------------------------------ iteration / access
    def __len__(self) -> int:
        if self.ndim == 0:
            return 1
        if isinstance(self._data, list):
            return len(self._data)
        return 0

    def __iter__(self) -> Iterator[Any]:
        if self.ndim <= 1:
            data = self._data if isinstance(self._data, list) else [self._data]
            return iter(data)
        return (MiniArray(item, dtype=self._dtype) for item in self._data)

    def _index_value(self, data: Any, key: Any) -> Any:
        if isinstance(key, MiniArray):
            key = key.tolist()
        if isinstance(key, list):
            return [_deep_copy(data[int(idx)]) for idx in key]
        if isinstance(key, slice):
            if isinstance(data, list):
                return [_deep_copy(item) for item in data[key]]
            raise TypeError("slice indexing requires list data")
        return data[int(key)]

    def __getitem__(self, key: Any) -> Any:
        if self.ndim == 0:
            if isinstance(key, (int, slice)):
                return self._dtype(self._data)
        if isinstance(key, tuple):
            data = self._data
            for part in key:
                data = self._index_value(data, part)
            return MiniArray(data, dtype=self._dtype) if isinstance(data, list) else self._dtype(data)
        data = self._index_value(self._data, key)
        return MiniArray(data, dtype=self._dtype) if isinstance(data, list) else self._dtype(data)

    def _assign(self, data: Any, key: Any, value: Any) -> None:
        if isinstance(key, MiniArray):
            key = key.tolist()
        if isinstance(key, list):
            assigned = _ensure_sequence(value)
            for idx, target in zip(key, assigned):
                data[int(idx)] = _coerce(target, self._dtype)
            return
        if isinstance(key, slice):
            assigned = _ensure_sequence(value)
            data[key] = [_coerce(item, self._dtype) for item in assigned]
            return
        data[int(key)] = _coerce(value, self._dtype)

    def __setitem__(self, key: Any, value: Any) -> None:
        if isinstance(key, tuple):
            if len(key) == 0:
                return
            head, *rest = key
            sub = self._index_value(self._data, head)
            if not rest:
                self._assign(self._data, head, value)
            else:
                if isinstance(sub, list):
                    target = MiniArray(sub, dtype=self._dtype)
                    target[tuple(rest)] = value
                    self._assign(self._data, head, target.tolist())
                else:
                    raise TypeError("invalid assignment target")
            return
        if isinstance(self._data, list):
            self._assign(self._data, key, value)
        else:
            self._data = _coerce(value, self._dtype)

    # ------------------------------------------------------------------ numeric helpers
    def astype(self, dtype: Any, copy: bool = True) -> "MiniArray":
        dtype_fn = _normalize_dtype(dtype)
        data = self.tolist() if copy else self._data
        coerced = _coerce(data, dtype_fn)
        result = MiniArray(coerced, dtype=dtype_fn, shape=self.shape)
        return result

    def reshape(self, *shape: int) -> "MiniArray":
        if len(shape) == 1 and isinstance(shape[0], (list, tuple)):
            shape = tuple(shape[0])
        shape = tuple(shape)
        total = self.size
        unknown_pos = None
        known = 1
        for idx, dim in enumerate(shape):
            if dim == -1:
                if unknown_pos is not None:
                    raise ValueError("only one inferred dimension allowed")
                unknown_pos = idx
            else:
                known *= int(dim)
        if unknown_pos is not None:
            if known == 0:
                inferred = 0
            else:
                inferred = total // known if known else 0
            shape = tuple(inferred if i == unknown_pos else dim for i, dim in enumerate(shape))
        if total not in (0, _product(shape) if shape else 1):
            raise ValueError("cannot reshape array")
        flat = list(_iter_flat(self._data))

        def build(target_shape: Tuple[int, ...], values: List[Number]) -> Any:
            if not target_shape:
                return values.pop(0) if values else 0
            dim = int(target_shape[0])
            return [build(target_shape[1:], values) for _ in range(dim)]

        values_copy = list(flat)
        new_data = build(shape, values_copy) if shape else (flat[0] if flat else 0)
        return MiniArray(new_data, dtype=self._dtype, shape=shape)

    def flatten(self) -> "MiniArray":
        return MiniArray(list(_iter_flat(self._data)), dtype=self._dtype, shape=(self.size,))

    def _elementwise(self, other: Any, op) -> "MiniArray":
        if isinstance(other, MiniArray):
            if other.size == 1:
                other_value = next(_iter_flat(other._data))
                return self._elementwise(other_value, op)
            if self.ndim == other.ndim == 2 and other.shape[1] == 1 and other.shape[0] == self.shape[0]:
                repeated: List[Number] = []
                for row in other.tolist():
                    value = row[0] if isinstance(row, list) else row
                    repeated.extend([value] * self.shape[1])
                result = [op(a, b) for a, b in zip(_iter_flat(self._data), repeated)]
                return MiniArray(result, dtype=self._dtype, shape=self.shape)
            if other.shape != self.shape:
                raise ValueError("shape mismatch for element-wise operation")
            paired = zip(_iter_flat(self._data), _iter_flat(other._data))
            result = [op(a, b) for a, b in paired]
        else:
            result = [op(x, other) for x in _iter_flat(self._data)]
        return MiniArray(result, dtype=self._dtype, shape=(self.size,)).reshape(*self.shape)

    def __neg__(self) -> "MiniArray":
        return self._elementwise(0, lambda a, _: -a)

    def __truediv__(self, other: Any) -> "MiniArray":
        return self._elementwise(other, lambda a, b: a / b)

    def __rtruediv__(self, other: Any) -> "MiniArray":
        return self._elementwise(other, lambda a, b: b / a)

    def __mul__(self, other: Any) -> "MiniArray":
        return self._elementwise(other, lambda a, b: a * b)

    def __add__(self, other: Any) -> "MiniArray":
        return self._elementwise(other, lambda a, b: a + b)

    def __sub__(self, other: Any) -> "MiniArray":
        return self._elementwise(other, lambda a, b: a - b)

    def __matmul__(self, other: Any) -> "MiniArray":
        rhs = other if isinstance(other, MiniArray) else MiniArray(other, dtype=self._dtype)
        if self.ndim == 1:
            left_matrix = [self.tolist()]
        else:
            left_matrix = self.tolist()
        if rhs.ndim == 1:
            right_matrix = [[v] for v in rhs.tolist()]
        else:
            right_matrix = rhs.tolist()
        if not right_matrix:
            return MiniArray([], dtype=self._dtype, shape=(len(left_matrix), 0))
        result: List[List[Number]] = []
        rhs_transposed = list(zip(*right_matrix))
        for row in left_matrix:
            row_result: List[Number] = []
            for col in rhs_transposed:
                row_result.append(sum(float(a) * float(b) for a, b in zip(row, col)))
            result.append(row_result)
        shape = (len(result), len(result[0]) if result and result[0] else 0)
        return MiniArray(result, dtype=float, shape=shape)

    def __eq__(self, other: Any) -> "MiniArray":
        if isinstance(other, MiniArray):
            if other.shape != self.shape:
                raise ValueError("shape mismatch for comparison")
            values = [a == b for a, b in zip(_iter_flat(self._data), _iter_flat(other._data))]
        else:
            values = [a == other for a in _iter_flat(self._data)]
        return MiniArray(values, dtype=bool, shape=(self.size,)).reshape(*self.shape)

    def all(self) -> bool:
        return all(bool(v) for v in _iter_flat(self._data))

    @property
    def T(self) -> "MiniArray":
        if self.ndim == 1:
            return self.reshape(self.shape[0], 1)
        if self.ndim == 2:
            transposed = list(map(list, zip(*self.tolist()))) if self._shape[1] else [[] for _ in range(self._shape[1])]
            return MiniArray(transposed, dtype=self._dtype, shape=(self.shape[1], self.shape[0]))
        raise NotImplementedError("transpose only supported up to 2D in fallback numpy")


# ---------------------------------------------------------------------- public helpers
ndarray = MiniArray
float32 = float
float64 = float
int64 = int
int32 = int


def array(data: Any, dtype: Any = None) -> MiniArray:
    inferred = dtype if dtype is not None else _infer_dtype_from_data(data)
    return MiniArray(data, dtype=inferred)


def asarray(data: Any, dtype: Any = None) -> MiniArray:
    if isinstance(data, MiniArray) and dtype is None:
        return data
    inferred = dtype if dtype is not None else _infer_dtype_from_data(data)
    return MiniArray(data, dtype=inferred)


def zeros(shape: Tuple[int, ...] | List[int], dtype: Any = None) -> MiniArray:
    dtype_fn = _normalize_dtype(dtype)
    if isinstance(shape, int):
        shape = (shape,)
    def build(target_shape: Tuple[int, ...]) -> Any:
        if not target_shape:
            return dtype_fn(0)
        dim = int(target_shape[0])
        return [build(target_shape[1:]) for _ in range(dim)]
    target_shape = tuple(shape)
    data = build(target_shape)
    return MiniArray(data, dtype=dtype_fn, shape=target_shape)


def ones(shape: Tuple[int, ...] | List[int], dtype: Any = None) -> MiniArray:
    result = zeros(shape, dtype=dtype)
    return result + 1


def empty(shape: Tuple[int, ...] | List[int], dtype: Any = None) -> MiniArray:
    return zeros(shape, dtype=dtype)


def vstack(seqs: Sequence[Any]) -> MiniArray:
    rows = []
    for item in seqs:
        if isinstance(item, MiniArray):
            rows.extend(item.tolist() if item.ndim > 1 else [list(item.tolist())])
        else:
            if isinstance(item, list) and item and isinstance(item[0], list):
                rows.extend(_deep_copy(item))
            else:
                rows.append(_ensure_sequence(item))
    if not rows:
        width = 0
    else:
        width = len(rows[0])
    return MiniArray(rows, dtype=float, shape=(len(rows), width))


def argsort(values: Any) -> MiniArray:
    arr = values.tolist() if isinstance(values, MiniArray) else list(values)
    indices = sorted(range(len(arr)), key=lambda idx: arr[idx])
    return MiniArray(indices, dtype=int)


# ---------------------------------------------------------------------- file helpers
def _write_payload(target: Any, payload: Any) -> None:
    text = json.dumps(payload)
    if hasattr(target, "write"):
        try:
            target.write(text.encode("utf-8"))
        except Exception:
            target.write(text)
    else:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)


def _read_payload(source: Any) -> Any:
    if hasattr(source, "read"):
        content = source.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
    else:
        with open(source, "r", encoding="utf-8") as handle:
            content = handle.read()
    return json.loads(content) if content else None


def save(file: Any, array_obj: Any) -> None:
    data = array_obj.tolist() if isinstance(array_obj, MiniArray) else array_obj
    payload = {"__type__": "array", "data": data, "shape": getattr(array_obj, "shape", None)}
    _write_payload(file, payload)


class _MiniNpz:
    def __init__(self, stored: dict[str, Any]):
        self._stored = stored
        self.files = list(stored.keys())

    def __getitem__(self, key: str) -> MiniArray:
        value = self._stored[key]
        data = value.get("data")
        shape = tuple(value.get("shape", ()))
        return MiniArray(data, dtype=float, shape=shape)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._stored.get(key)
        if value is None:
            return default
        data = value.get("data")
        shape = tuple(value.get("shape", ()))
        return MiniArray(data, dtype=float, shape=shape)


def savez(file: Any, **arrays: Any) -> None:
    stored = {}
    for name, value in arrays.items():
        data = value.tolist() if isinstance(value, MiniArray) else value
        shape = getattr(value, "shape", None)
        stored[name] = {"data": data, "shape": shape}
    payload = {"__type__": "npz", "data": stored}
    _write_payload(file, payload)


def load(file: Any, allow_pickle: bool = False) -> Any:
    payload = _read_payload(file)
    if not payload:
        return MiniArray([], dtype=float)
    if payload.get("__type__") == "npz":
        return _MiniNpz(payload["data"])
    if payload.get("__type__") == "array":
        return MiniArray(payload["data"], dtype=float, shape=tuple(payload.get("shape") or ()))
    return MiniArray(payload, dtype=float)


# ---------------------------------------------------------------------- linear algebra helpers
class _Linalg:
    @staticmethod
    def norm(array_obj: Any, axis: int | None = None, keepdims: bool = False) -> MiniArray | float:
        arr = array_obj if isinstance(array_obj, MiniArray) else MiniArray(array_obj, dtype=float)
        if axis is None:
            total = math.sqrt(sum(float(x) ** 2 for x in _iter_flat(arr._data)))
            return float(total)
        if axis == 1:
            rows = arr.tolist() if arr.ndim > 1 else [arr.tolist()]
            norms = [math.sqrt(sum(float(v) ** 2 for v in row)) for row in rows]
            if keepdims:
                return MiniArray([[n] for n in norms], dtype=float)
            return MiniArray(norms, dtype=float)
        if axis == 0:
            cols = zip(*arr.tolist())
            norms = [math.sqrt(sum(float(v) ** 2 for v in col)) for col in cols]
            if keepdims:
                return MiniArray([norms], dtype=float)
            return MiniArray(norms, dtype=float)
        raise NotImplementedError("norm fallback only supports axis=None/0/1")


linalg = _Linalg()


# ---------------------------------------------------------------------- random helpers
class RandomState:
    def __init__(self, seed: int | None = None):
        self._rng = _py_random.Random(seed)

    def randn(self, *shape: int) -> MiniArray:
        if len(shape) == 1 and isinstance(shape[0], (list, tuple)):
            shape = tuple(shape[0])
        shape = shape or (1,)
        total = 1
        for dim in shape:
            total *= int(dim)
        values = [self._rng.gauss(0, 1) for _ in range(total)]
        return MiniArray(values, dtype=float, shape=(total,)).reshape(*shape)


class _RandomModule:
    RandomState = RandomState

    @staticmethod
    def rand(*shape: int) -> MiniArray:
        if len(shape) == 1 and isinstance(shape[0], (list, tuple)):
            shape = tuple(shape[0])
        shape = shape or (1,)
        total = 1
        for dim in shape:
            total *= int(dim)
        rng = _py_random.Random()
        values = [rng.random() for _ in range(total)]
        return MiniArray(values, dtype=float, shape=(total,)).reshape(*shape)


random = _RandomModule()


