"""Shared exceptions for local Telegram infrastructure helpers."""

from __future__ import annotations


class TelegramSharedError(Exception):
    """Base error for reusable Telegram helper modules."""


class ConfigurationError(TelegramSharedError):
    """Raised when shared helpers receive invalid runtime configuration."""


class SecretResolutionError(TelegramSharedError):
    """Raised when a shared secret backend cannot resolve a requested secret."""


class TelegramApiError(TelegramSharedError):
    """Raised when the Telegram Bot API request fails or returns an error."""
