"""Backend-neutral callbacks consumed by a desktop pet window host."""
from __future__ import annotations

from typing import Protocol

from lib.core.graphics.types import Point
from lib.core.input.types import KeyboardInput, MouseButton, MouseInput


class PetHostCallbacks(Protocol):
    """Business callbacks invoked by a toolkit-specific pet window host."""

    def prepare_render(self) -> object:
        """Update render state and return the active draw facade."""

    def handle_pointer_enter(self) -> None:
        """Handle the pointer entering the pet window."""

    def handle_pointer_leave(self) -> None:
        """Handle the pointer leaving the pet window."""

    def handle_pointer_press(self, event: MouseInput) -> None:
        """Handle a backend-neutral pointer press."""

    def handle_pointer_move(self, event: MouseInput) -> None:
        """Handle backend-neutral pointer movement."""

    def handle_pointer_release(self, button: MouseButton) -> None:
        """Handle a backend-neutral pointer release."""

    def handle_window_moved(self, position: Point) -> None:
        """Handle a host window position update."""

    def handle_key_press(self, event: KeyboardInput) -> None:
        """Handle a backend-neutral key press."""

    def handle_key_release(self, event: KeyboardInput) -> None:
        """Handle a backend-neutral key release."""

    def handle_host_close(self) -> None:
        """Release business resources before the native host closes."""
