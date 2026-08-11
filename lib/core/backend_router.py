"""Backend-neutral routing for desktop rendering implementations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


BackendConfigurer = Callable[[], None]
DEFAULT_BACKEND_ID = "qt"


@dataclass(frozen=True)
class BackendDescriptor:
    backend_id: str
    display_name: str
    available: bool
    requires_restart: bool = True
    experimental: bool = False


@dataclass(frozen=True)
class BackendSelection:
    requested_backend: str
    active_backend: str
    fallback_used: bool
    reason: str | None = None
    experimental: bool = False


class BackendConfigurationError(RuntimeError):
    """Raised when the required fallback backend cannot be configured."""


BACKEND_DESCRIPTORS = (
    BackendDescriptor("qt", "Qt", True),
    BackendDescriptor("directx", "DirectX", True, experimental=True),
    BackendDescriptor("opengl", "OpenGL", False),
    BackendDescriptor("vulkan", "Vulkan", False),
)


class BackendRouter:
    """Register composition callbacks and activate one backend at startup."""

    def __init__(
        self,
        descriptors: Iterable[BackendDescriptor] = BACKEND_DESCRIPTORS,
        *,
        fallback_backend: str = DEFAULT_BACKEND_ID,
    ) -> None:
        descriptor_map: dict[str, BackendDescriptor] = {}
        for descriptor in descriptors:
            backend_id = self._normalize_backend_id(descriptor.backend_id)
            if not backend_id:
                raise ValueError("backend id must not be empty")
            if backend_id in descriptor_map:
                raise ValueError(f"duplicate backend descriptor: {backend_id}")
            descriptor_map[backend_id] = BackendDescriptor(
                backend_id,
                descriptor.display_name,
                bool(descriptor.available),
                bool(descriptor.requires_restart),
                bool(descriptor.experimental),
            )

        fallback_id = self._normalize_backend_id(fallback_backend)
        if fallback_id not in descriptor_map:
            raise ValueError(f"unknown fallback backend: {fallback_id}")
        if not descriptor_map[fallback_id].available:
            raise ValueError(f"fallback backend is not available: {fallback_id}")

        self._descriptors = descriptor_map
        self._fallback_backend = fallback_id
        self._configurers: dict[str, BackendConfigurer] = {}
        self._active_selection: BackendSelection | None = None

    @staticmethod
    def _normalize_backend_id(value: object) -> str:
        backend_id = str(value or "").strip().lower()
        if backend_id == "dx":
            return "directx"
        return backend_id

    def descriptors(self) -> tuple[BackendDescriptor, ...]:
        return tuple(self._descriptors.values())

    def register_backend(self, backend_id: str, configurer: BackendConfigurer) -> None:
        normalized = self._normalize_backend_id(backend_id)
        if normalized not in self._descriptors:
            raise ValueError(f"unknown backend: {normalized}")
        if not callable(configurer):
            raise TypeError("backend configurer must be callable")

        current = self._configurers.get(normalized)
        if current is configurer:
            return
        if current is not None:
            raise ValueError(f"backend already registered: {normalized}")
        self._configurers[normalized] = configurer

    def configure_selected_backend(self, requested_backend: object) -> BackendSelection:
        requested_id = self._normalize_backend_id(requested_backend)
        descriptor = self._descriptors.get(requested_id)
        configurer = self._configurers.get(requested_id)

        if descriptor is not None and descriptor.available and configurer is not None:
            try:
                configurer()
            except Exception as exc:
                reason = (
                    f"backend '{requested_id}' failed to initialize: "
                    f"{type(exc).__name__}: {exc}"
                )
                if requested_id == self._fallback_backend:
                    raise BackendConfigurationError(reason) from exc
            else:
                selection = BackendSelection(
                    requested_id,
                    requested_id,
                    False,
                    experimental=descriptor.experimental,
                )
                self._active_selection = selection
                return selection
        elif descriptor is None:
            reason = f"unknown backend '{requested_id or '<empty>'}'"
        elif not descriptor.available:
            reason = f"backend '{requested_id}' is not implemented"
        else:
            reason = f"backend '{requested_id}' is not registered"

        return self._configure_fallback(requested_id, reason)

    def _configure_fallback(self, requested_id: str, reason: str) -> BackendSelection:
        fallback_configurer = self._configurers.get(self._fallback_backend)
        if fallback_configurer is None:
            raise BackendConfigurationError(
                f"fallback backend '{self._fallback_backend}' is not registered; {reason}"
            )

        try:
            fallback_configurer()
        except Exception as exc:
            raise BackendConfigurationError(
                f"fallback backend '{self._fallback_backend}' failed to initialize: "
                f"{type(exc).__name__}: {exc}; original reason: {reason}"
            ) from exc

        selection = BackendSelection(
            requested_backend=requested_id,
            active_backend=self._fallback_backend,
            fallback_used=True,
            reason=reason,
        )
        self._active_selection = selection
        return selection

    def get_active_selection(self) -> BackendSelection | None:
        return self._active_selection


_router = BackendRouter()


def get_backend_descriptors() -> tuple[BackendDescriptor, ...]:
    return _router.descriptors()


def register_backend(backend_id: str, configurer: BackendConfigurer) -> None:
    _router.register_backend(backend_id, configurer)


def configure_selected_backend(requested_backend: object) -> BackendSelection:
    return _router.configure_selected_backend(requested_backend)


def get_active_backend_selection() -> BackendSelection | None:
    return _router.get_active_selection()


__all__ = [
    "BACKEND_DESCRIPTORS",
    "DEFAULT_BACKEND_ID",
    "BackendConfigurationError",
    "BackendDescriptor",
    "BackendRouter",
    "BackendSelection",
    "configure_selected_backend",
    "get_active_backend_selection",
    "get_backend_descriptors",
    "register_backend",
]
