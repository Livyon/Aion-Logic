"""Ondersteuning voor Aion Logic BTHome Tag Trackers."""
import logging
from datetime import timedelta

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
import homeassistant.util.dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Stelt de Aion device trackers in op basis van config opties."""
    presence_sensors = entry.options.get("presence_sensors", [])
    if not presence_sensors:
        return

    entities = []
    for sensor_id in presence_sensors:
        entities.append(AionTagTracker(hass, entry.entry_id, sensor_id))
    
    if entities:
        async_add_entities(entities)
        _LOGGER.debug(f"Aion Logic heeft {len(entities)} device_trackers aangemaakt voor fysieke tags.")

class AionTagTracker(TrackerEntity):
    """Een dedicated Device Tracker voor BLE Tags (180s stale beveiliging)."""
    
    def __init__(self, hass, entry_id, source_entity_id):
        self.hass = hass
        self._entry_id = entry_id
        self._source_entity_id = source_entity_id
        self._is_connected = False
        self._attr_unique_id = f"{entry_id}_{source_entity_id}_tracker"
        
        clean_name = source_entity_id.split('.')[-1].replace('_', ' ').title()
        self._attr_name = f"Aion {clean_name}"

    @property
    def source_type(self):
        """BTHome tags worden gecategoriseerd als Bluetooth LE trackers."""
        return SourceType.BLUETOOTH_LE
        
    @property
    def is_connected(self):
        """Geeft True (Thuis) of False (Afwezig) terug."""
        return self._is_connected

    @property
    def icon(self):
        """Visuele feedback."""
        return "mdi:bluetooth-connect" if self._is_connected else "mdi:bluetooth-off"

    async def async_added_to_hass(self):
        """Registreer listeners zodra de entiteit is toegevoegd aan HA."""
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._async_handle_sensor_state_change
            )
        )
        
        # Controleer elke 10 seconden actief op een 'bevroren' status
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_check_stale_state, timedelta(seconds=10)
            )
        )
        
        # Doe direct een eerste check bij het opstarten
        await self._async_check_stale_state(dt_util.utcnow())

    async def _async_handle_sensor_state_change(self, event):
        """Wordt getriggerd bij ELKE wijziging van de tag (ook al is het ruis)."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._update_connection(False)
            return
            
        self._update_connection(True)

    async def _async_check_stale_state(self, now):
        """Check of we de 45 seconden timeout hebben bereikt zonder updates (Ultra-fast BLE)."""
        state_obj = self.hass.states.get(self._source_entity_id)
        if not state_obj or state_obj.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._update_connection(False)
            return
            
        last_upd = state_obj.last_updated
        if last_upd and (now - last_upd).total_seconds() >= 45:
            self._update_connection(False)
        else:
            self._update_connection(True)

    def _update_connection(self, is_connected):
        """Update de interne status en push naar Home Assistant."""
        if self._is_connected != is_connected:
            self._is_connected = is_connected
            self.async_write_ha_state()
