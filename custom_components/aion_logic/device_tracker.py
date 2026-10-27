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
    wifi_sensors = entry.options.get("wifi_tracker_sensors", [])
    
    all_trackers = list(set(presence_sensors + wifi_sensors))
    if not all_trackers:
        return

    entities = []
    for sensor_id in all_trackers:
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
        self._last_strong_signal = None
        
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
        
    def _is_valid_presence(self, state_str):
        """Domme Edge filter: blokkeert zwakke RSSI signalen en expliciete uit-statussen."""
        if state_str in (STATE_UNAVAILABLE, STATE_UNKNOWN, "off", "false", "not_home"):
            return False
        try:
            if float(state_str) < -100.0:
                return False
        except ValueError:
            # Wi-Fi netwerknaam (SSID) of string-status afhandeling
            state_lower = state_str.lower()
            if state_lower in ("not connected", "<not connected>", "disconnected", "none", "", "unknown"):
                return False
        return True
        
    async def _async_handle_sensor_state_change(self, event):
        """Wordt getriggerd bij ELKE wijziging van de tag (ook al is het ruis)."""
        new_state = event.data.get("new_state")
        
        if not new_state:
            return
            
        # 1. Harde uitschakeling ALLEEN bij expliciete UIT-status. 
        if new_state.state in ("off", "false", "not_home"):
            self._update_connection(False)
            return

        # 2. Zwak signaal of even Unavailable? Negeren (Laat de 45s stale timer zijn werk doen)
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) or not self._is_valid_presence(new_state.state):
            return
            
        # 3. Sterk signaal? Update geheugen en zet AAN
        self._last_strong_signal = dt_util.utcnow()
        self._update_connection(True)

    async def _async_check_stale_state(self, now):
        """Check of we de 45 seconden timeout hebben bereikt zonder STERKE updates."""
        state_obj = self.hass.states.get(self._source_entity_id)
        
        if state_obj and state_obj.state in ("off", "false", "not_home"):
            self._update_connection(False)
            return
            
        if self._last_strong_signal and (now - self._last_strong_signal).total_seconds() < 45:
            self._update_connection(True)
        else:
            self._update_connection(False)

    def _update_connection(self, is_connected):
        """Update de interne status en push naar Home Assistant."""
        if self._is_connected != is_connected:
            self._is_connected = is_connected
            status_text = "THUIS (Verbonden)" if is_connected else "AFWEZIG (Verbroken)"
            _LOGGER.info(f"🏷️ BLE Tag '{self._attr_name}' ({self._source_entity_id}) status is nu: {status_text}")            
            self.async_write_ha_state()
