from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import TimestampDataUpdateCoordinator, UpdateFailed

from .api import TgnVodaApi, TgnVodaApiError, TgnVodaAuthenticationError
from .const import (
    CONF_ACCOUNT_ID,
    CONF_BASE_URL,
    CONF_CA_BUNDLE,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_BASE_URL,
    DEFAULT_CA_BUNDLE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class TgnVodaCoordinator(TimestampDataUpdateCoordinator[dict[str, Any]]):
    """Fetch and expose data from a single TGN Voda account."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.api = _make_api(entry)
        interval = int(
            entry.options.get(
                CONF_SCAN_INTERVAL,
                entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=max(60, interval)),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except TgnVodaAuthenticationError as err:
            raise ConfigEntryAuthFailed("TGN Voda authentication failed") from err
        except TgnVodaApiError as err:
            raise UpdateFailed(f"TGN Voda API request failed: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unable to update TGN Voda data: {err}") from err

    def _fetch(self) -> dict[str, Any]:
        self.api.authenticate()
        return self.api.fetch_account_and_billing()

    async def async_api_call(self, method: str, *args: Any) -> Any:
        """Authenticate and execute a synchronous API method in the executor."""

        def call() -> Any:
            self.api.authenticate()
            return getattr(self.api, method)(*args)

        try:
            return await self.hass.async_add_executor_job(call)
        except TgnVodaAuthenticationError as err:
            raise ConfigEntryAuthFailed("TGN Voda authentication failed") from err


def _make_api(entry: ConfigEntry) -> TgnVodaApi:
    data = {**entry.data, **entry.options}
    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    ca_bundle = data.get(CONF_CA_BUNDLE, DEFAULT_CA_BUNDLE)
    if ca_bundle:
        verify_ssl = ca_bundle
    return TgnVodaApi(
        login=data[CONF_LOGIN],
        password=data[CONF_PASSWORD],
        account_id=str(data[CONF_ACCOUNT_ID]),
        verify_ssl=verify_ssl,
        base_url=data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
