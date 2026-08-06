"""Sensor platform voor Aion Logic™"""
import logging
from datetime import time
import homeassistant.util.dt as dt_util
from homeassistant.core import callback
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DOMAIN, 
    CONF_SMOKE_SENSORS,
    CONF_ALARM_PANEL,
    CONF_GUARD_MODE,
    GUARD_MODE_AUTONOMOUS,
    GUARD_MODE_MANUAL,
    GUARD_MODE_DISABLED
)

_LOGGER = logging.getLogger(__name__)
ASSET_URL_PREFIX = f"/{DOMAIN}_assets"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    scenario_sensor = AionLogicScenarioSensor(hass, entry)
    background_sensor = AionLogicBackgroundSensor(hass, entry, scenario_sensor)
    learning_sensor = AionLogicLearningSensor(hass, entry)
    guardian_sensor = AionLogicGuardianSensor(hass, entry, scenario_sensor)
    async_add_entities([scenario_sensor, background_sensor, learning_sensor, guardian_sensor])

class AionLogicScenarioSensor(SensorEntity):
    _attr_icon = "mdi:theme-light-dark"
    _attr_should_poll = False
    _attr_name = "Aion Logic Scenario"
    
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self._attr_unique_id = f"{entry.entry_id}_current_scenario"
        self._attr_native_value = "Onbekend"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Aion Logic"}

    @callback
    def _async_handle_event(self, event):
        if new_scenario := event.data.get("scenario"):
            self._attr_native_value = new_scenario
            self._attr_extra_state_attributes = {
                "raw_scenario": new_scenario.lower().replace(" ", "_").replace("⚡", "").strip()
            }
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen("aion_logic_scenario_update", self._async_handle_event))

class AionLogicBackgroundSensor(SensorEntity):
    _attr_icon = "mdi:image"
    _attr_should_poll = False
    _attr_name = "Aion Logic Background URL"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, scenario_sensor: AionLogicScenarioSensor):
        self.hass = hass
        self._attr_unique_id = f"{entry.entry_id}_background_url"
        self._attr_device_info = scenario_sensor.device_info
        self._attr_native_value = f"{ASSET_URL_PREFIX}/afwezig.jpg"

    @property
    def entity_picture(self):
        """Zorgt dat Home Assistant deze sensor native als afbeelding herkent."""
        return self._attr_native_value
    
    def _format_scenario_to_filename(self, scenario_name: str) -> str:
        if not scenario_name or scenario_name == "Onbekend": return "afwezig.jpg" 
        # Filter emojis weg die de Cloud toevoegt (zoals ⚡ en 🚨)
        clean_name = scenario_name.replace("⚡", "").replace("🚨", "").strip()
        return f"{clean_name.replace(' - ', '-').replace(' ', '-').lower()}.jpg"

    @callback
    def _async_handle_event(self, event):
        if new_scenario := event.data.get("scenario"):
            self._attr_native_value = f"{ASSET_URL_PREFIX}/{self._format_scenario_to_filename(new_scenario)}"
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen("aion_logic_scenario_update", self._async_handle_event))

class AionLogicLearningSensor(SensorEntity, RestoreEntity):
    _attr_icon = "mdi:brain"
    _attr_should_poll = False
    _attr_name = "Aion Logic Brain"
    
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self._attr_unique_id = f"{entry.entry_id}_learning_status"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        self._attr_native_value = "In Afwachting"
        self._attr_extra_state_attributes = {
            "old_mpd": 30.0, "new_mpd": 30.0, "last_updated": "Nog nooit", "duration": 0,
            "feedback_message": "Aion Logic leert uw huis kennen..."
        }

    @callback
    def _async_handle_learning(self, event):
        data = event.data
        
        # [NIEUW] Check of dit een 'Keep Alive' signaal is (AI is aan, maar geen data)
        if data.get("status") == "active_keepalive":
            current_msg = self._attr_extra_state_attributes.get("feedback_message", "")
            # Alleen resetten als hij nu op 'Comfort' (Uit) staat
            if "Comfort" in str(current_msg) or "Uitgeschakeld" in str(self._attr_native_value):
                 self._attr_native_value = "In Afwachting"
                 self._attr_extra_state_attributes["feedback_message"] = "AI Actief: Wachten op leermoment..."
                 self._attr_extra_state_attributes["new_mpd"] = data.get("new_mpd")
                 self._attr_extra_state_attributes["last_updated"] = data.get("timestamp")
                 self.async_write_ha_state()
            return

        # Normale update (Met leerresultaat of Comfort force)
        if "Comfort" in data.get("feedback_message", ""):
            self._attr_native_value = "Aion Comfort"
        else:
            self._attr_native_value = f"Geoptimaliseerd ({data.get('timestamp')[11:16]})"
            
        self._attr_extra_state_attributes = {
            "old_mpd": data.get("old_mpd", self._attr_extra_state_attributes.get("old_mpd")),
            "new_mpd": data.get("new_mpd"),
            "measured_mpd": data.get("measured_mpd", 0),
            "duration": data.get("duration", 0),
            "temp_rise": data.get("temp_rise", 0),
            "last_updated": data.get("timestamp"),
            "feedback_message": data.get("feedback_message")
        }
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._attr_native_value = last_state.state
                self._attr_extra_state_attributes = last_state.attributes
                _LOGGER.debug(f"Aion Logic Brain geheugen hersteld: {self._attr_native_value}")
        self.async_on_remove(self.hass.bus.async_listen("aion_logic_learning_update", self._async_handle_learning))

class AionLogicGuardianSensor(SensorEntity):
    """De lokale veiligheidsofficier."""
    _attr_icon = "mdi:shield-check"
    _attr_should_poll = False
    _attr_name = "Aion Guardian"
    
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, scenario_sensor: AionLogicScenarioSensor):
        self.hass = hass
        self._entry = entry
        self._scenario_sensor = scenario_sensor
        self._attr_unique_id = f"{entry.entry_id}_guardian_status"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        self._attr_native_value = "Veilig"
        self._attr_extra_state_attributes = {
            "status": "safe", 
            "mode": "autonomous",
            "active_threat": "Systeem start op...", # Default tekst ipv None
            "monitored_entities": []
        }

    async def async_added_to_hass(self) -> None:
        """Registreer listeners."""
        await super().async_added_to_hass()
        
        options = self._entry.options
        to_watch = []
        
        # 1. Rookmelders
        if smokes := options.get(CONF_SMOKE_SENSORS): to_watch.extend(smokes)
            
        # 2. Extern Alarm
        if alarm := options.get(CONF_ALARM_PANEL): to_watch.append(alarm)
            
        # 3. Raamsensoren (Voor Native Guard)
        for key, zone_cfg in options.items():
            if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                if windows := zone_cfg.get("window_sensors"): to_watch.extend(windows)

        # 4. Updates via Event Bus (Scenario & Switch changes)
        self.async_on_remove(self.hass.bus.async_listen("aion_logic_scenario_update", self._update_guardian_state))
        self.async_on_remove(self.hass.bus.async_listen("state_changed", self._async_check_switch_change))
        self.async_on_remove(self.hass.bus.async_listen("aion_logic_security_update", self._handle_security_update))

        if to_watch:
            to_watch = list(set(to_watch))
            self.async_on_remove(async_track_state_change_event(self.hass, to_watch, self._update_guardian_state))
            
        self._update_guardian_state()

    @callback
    def _handle_security_update(self, event):
        """[NIEUW] Forceer de sensor naar een specifieke staat (bv. Disabled)."""
        data = event.data
        
        # [UNLOCK LOGICA]
        if data.get("status") == "active":
            # Als we op 'disabled' stonden, resetten we nu naar normaal!
            if self._attr_extra_state_attributes.get("status") == "disabled":
                self._attr_extra_state_attributes["status"] = "safe" # Reset
                self._attr_icon = "mdi:shield-check" # Reset icoon
                self._update_guardian_state() # Herbereken direct de echte status
            return

        # Overschrijf de staat hard voor visualisatie (Disabled logic)
        self._attr_native_value = data.get("display_state", "Uitgeschakeld")
        self._attr_icon = "mdi:shield-off-outline" 
        
        self._attr_extra_state_attributes["status"] = data.get("status", "disabled")
        self._attr_extra_state_attributes["active_threat"] = data.get("threat_level", "Upgrade naar Guardian voor beveiliging.")
        self._attr_extra_state_attributes["mode"] = "disabled"
        
        self.async_write_ha_state()

    @callback
    def _async_check_switch_change(self, event):
        """Luister specifiek naar Aion switches."""
        entity_id = event.data.get("entity_id")
        if entity_id and "aion_guard" in entity_id:
            self._update_guardian_state()

    def _get_switch_state(self, switch_type):
        """Helper om interne switches te lezen (Best Effort)."""
        for state in self.hass.states.async_all("switch"):
            if f"_{switch_type}" in state.entity_id and state.state == "on":
                return True
        return False

    @callback
    def _update_guardian_state(self, event=None):
        """Berekent de veiligheidsstatus."""
        # [BELANGRIJK] Als de status al 'disabled' is door de licentie check, doen we niets!
        # Tenzij we zeker weten dat we moeten overschrijven.
        if self._attr_extra_state_attributes.get("status") == "disabled" and self._attr_native_value.startswith("Comfort"):
             # We zitten in licentie-lock. Stop hier.
             # Tenzij we in de toekomst een 're-enable' event krijgen.
             return

        options = self._entry.options
        mode = options.get(CONF_GUARD_MODE, GUARD_MODE_AUTONOMOUS)
        
        # Update basis attributen
        self._attr_extra_state_attributes["mode"] = mode
        
        # Bouw actuele lijst van monitored entities voor attributen
        monitored_list = []
        for key, zone_cfg in options.items():
             if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                 if windows := zone_cfg.get("window_sensors"): monitored_list.extend(windows)
        self._attr_extra_state_attributes["monitored_entities"] = monitored_list
        
        # 1. BRAND (Altijd actief)
        if smokes := options.get(CONF_SMOKE_SENSORS):
            for s_id in smokes:
                state = self.hass.states.get(s_id)
                if state and state.state in ["on", "unsafe", "smoke", "detected"]:
                    self._set_state("GEVAAR", "fire", f"Rookmelder: {state.name}")
                    return

        # 2. EXTERN ALARM (Altijd actief)
        if alarm_id := options.get(CONF_ALARM_PANEL):
            state = self.hass.states.get(alarm_id)
            if state and state.state == "triggered":
                self._set_state("GEVAAR", "intrusion", "Extern Alarm Getriggerd")
                return

        # 3. NATIVE GUARD LOGICA
        if mode == GUARD_MODE_DISABLED:
            self._set_state("Uitgeschakeld", "disabled", "Systeem uitgeschakeld")
            return

        # Check PAUZE (Ventilatie)
        if self._get_switch_state("guard_pause"):
            self._set_state("Gepauzeerd (15m)", "paused", "Tijdelijke onderbreking")
            return

        # Check MODE
        should_arm = False
        mode_label = ""

        if mode == GUARD_MODE_MANUAL:
            if self._get_switch_state("guard_master"):
                should_arm = True
                mode_label = "Manueel"
            else:
                self._set_state("Stand-by", "safe", "Handmatig uitgeschakeld")
                return

        elif mode == GUARD_MODE_AUTONOMOUS:
            # Check Aanwezigheid
            person_entities = options.get("person_entities", [])
            is_occupied = False
            for p_id in person_entities:
                p_state = self.hass.states.get(p_id)
                if p_state and p_state.state == "home": is_occupied = True
            
            # Check Gasten
            if self._get_switch_state("guest_mode"): is_occupied = True

            if not is_occupied:
                should_arm = True
                mode_label = "Afwezig"
            else:
                # --- GHOST GAP FIX v2 (Tijd-gebaseerd) ---
                current_scenario = str(self._scenario_sensor.native_value).lower()

                if "nacht" in current_scenario:
                    should_arm = True
                    mode_label = "Nacht"

                elif "alarm" in current_scenario or "gevaar" in current_scenario:
                    should_arm = True
                    mode_label = "ALARM"

        # 4. STATUS WEERGAVE (Cloud is leidend voor detectie)
        if should_arm:
            # Als de Cloud (of lokale Brand-check) alarm slaat, updaten we de UI direct
            if mode_label == "ALARM":
                self._set_state("GEVAAR!", "intrusion", "Alarm geactiveerd (Zie notificaties)!")
                return
            
            # Anders tonen we braaf dat het systeem scant (Cloud verzorgt de bypass logica)
            self._set_state(f"Bewaakt ({mode_label})", "armed", "Systeem scant actief...")
            return

        self._set_state("Veilig", "safe", "Systeem scant actief...")

    def _set_state(self, display_value, status_code, threat):
        self._attr_native_value = display_value
        self._attr_extra_state_attributes["status"] = status_code
        # FIX: Zorg dat dit nooit leeg is voor de frontend popup
        self._attr_extra_state_attributes["active_threat"] = threat if threat else "Systeem scant actief..."
        self.async_write_ha_state()
