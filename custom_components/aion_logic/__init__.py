"""De Aion Logic™ Integratie"""
import logging
import asyncio
import os
import base64
from typing import Any
from datetime import time, timedelta 
import json

from homeassistant.core import HomeAssistant, callback, CoreState
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    Platform, ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN,
    EVENT_HOMEASSISTANT_STARTED
)
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.template import Template 
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
    EntityRegistry,
)
import homeassistant.util.dt as dt_util

from .const import DOMAIN, CONF_ACTIVATION_CODE, CONF_ENERGY_SENSOR, CONF_ENERGY_TAG, CONF_DRIVER_1_NAME, CONF_DRIVER_1_SENSOR, CONF_DRIVER_1_TRIGGER, CONF_DRIVER_1_NOTIFY, CONF_DRIVER_2_NAME, CONF_DRIVER_2_SENSOR, CONF_DRIVER_2_TRIGGER, CONF_DRIVER_2_NOTIFY, CONF_LS_COMING_HOME_ON, CONF_LS_COMING_HOME_SCENE, CONF_LS_COMING_HOME_BRIGHTNESS, CONF_LS_LEAVING_HOME_OFF, CONF_LS_LEAVING_HOME_SCENE, CONF_LS_NIGHT_OFF, CONF_LS_NIGHT_OFF_SCENE, CONF_LS_NIGHT_ON, CONF_LS_NIGHT_ON_SCENE, CONF_LS_NIGHT_ON_BRIGHTNESS, CONF_LS_MORNING_ON, CONF_LS_MORNING_SCENE, CONF_LS_MORNING_BRIGHTNESS, CONF_LS_SUN_CHECK, CONF_ALARM_PANEL, CONF_DEFENSE_LIGHTS, CONF_DEFENSE_SPEAKERS, CONF_SMOKE_SENSORS, CONF_FIRE_LIGHTS, CONF_FIRE_SHUTTERS, CONF_GUARD_MODE, GUARD_MODE_AUTONOMOUS, GUARD_MODE_MANUAL, GUARD_MODE_DISABLED, CONF_SECURITY_NOTIFY, CONF_EMERGENCY_CONTACTS, CONF_ALARM_MSG, CONF_CENTRAL_VENT, CONF_ENABLE_HUMIDITY, CONF_ZONE_VENT, CONF_HUMIDITY_SENSOR, CONF_ENABLE_NIGHT_VENT, CONF_FAMILY_CALENDAR, CONF_EARLY_BIRD_SENSORS, CONF_EARLY_BIRD_WINDOW, GHOST_WINDOW_SECONDS
from .api import AionLogicApiClient, ApiAuthError, ApiConnectionError, ApiTimeoutError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SWITCH, Platform.CALENDAR]

async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Wordt aangeroepen wanneer de opties worden bijgewerkt."""
    _LOGGER.debug(f"Aion Logic opties bijgewerkt, herlaad listeners...")
    coordinator: AionLogicCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.update_options(entry.options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Zet Aion Logic op vanuit een config entry."""
    
    _LOGGER.info(f"Aion Logic v2.5.0 (Stable Node) aan het laden...")
    
    api_client = AionLogicApiClient(
        entry.data.get(CONF_ACTIVATION_CODE), 
        async_get_clientsession(hass)
    )
    coordinator = AionLogicCoordinator(hass, entry, api_client)
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.http.register_view(AionLogicSimulationView(coordinator))
    hass.http.register_view(AionLogicDiagnoseView(coordinator))

    entry.add_update_listener(async_options_updated)
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Correcte Async registratie voor static paths
    static_path = os.path.join(hass.config.path(f"custom_components/{DOMAIN}/www"))
    await hass.http.async_register_static_paths([
        StaticPathConfig(f"/{DOMAIN}_assets", static_path, cache_headers=False)
    ])
    _LOGGER.info(f"Assets geregistreerd op URL: /{DOMAIN}_assets")

    # Injecteer het autonome JavaScript bestand voor de dynamische achtergrond
    add_extra_js_url(hass, f"/{DOMAIN}_assets/aion_background.js")
        
    async def _start_aion_logic(_):
        """Wordt uitgevoerd zodra HA volledig is opgestart."""
        _LOGGER.info("Home Assistant is volledig gestart. Aion Logic activeert nu zijn triggers.")
        await coordinator.setup_listeners()
        # Clean sweep bij start
        await coordinator.async_trigger_main_logic()

    if hass.state == CoreState.running:
        await _start_aion_logic(None)
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_aion_logic)
    
    _LOGGER.info("Aion Logic setup voltooid (in wachtstand tot volledige start).")
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Verwijder een Aion Logic config entry."""
    _LOGGER.debug("Aion Logic aan het verwijderen...")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    coordinator: AionLogicCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        coordinator.cleanup_listeners()
    
    if unload_ok:
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(entry.entry_id)

    _LOGGER.debug("Aion Logic succesvol verwijderd.")
    return unload_ok


class AionLogicCoordinator:
    """De "Motor" van Aion Logic."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api_client: AionLogicApiClient):
        self.hass = hass
        self.entry = entry
        self.api_client = api_client
        self.options = entry.options
        self._listeners = []
        self._entity_registry: EntityRegistry | None = None
        self._is_running = False
        self._boost_window = None 
        self._last_weather_temp = None 
        self._active_simulation = None
        self._needs_sync = True
        self._motion_timers = {}
        
        # --- GHOST BOUNCE & RESTART PROTECTION ---
        self._startup_time = dt_util.utcnow()
        self._person_departure_time = {}
        self._person_departure_fuzzy = {}
        
        # --- DEAD MAN'S SWITCH ---
        self._fail_count = 0 
        self._fail_threshold = 3 

    @callback
    def cleanup_listeners(self) -> None:
        _LOGGER.debug(f"Opschonen van {len(self._listeners)} listeners...")
        for remove_listener in self._listeners:
            remove_listener()
        self._listeners = []
        for timer_remove in self._motion_timers.values():
            if timer_remove:
                timer_remove()
        self._motion_timers = {}

    async def update_options(self, new_options: dict) -> None:
        self.options = new_options
        self._needs_sync = True
        self.cleanup_listeners()
        self._last_weather_temp = None 
        await self.setup_listeners()
        await self.async_trigger_main_logic()

    async def async_handle_person_state_trigger(self, event):
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        entity_id = event.data.get('entity_id')

        if not new_state or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN): return

        # Bepaal dynamische Ghost Window (15 min nachts, anders standaard)
        scenario_state = self._get_state(f"sensor.{DOMAIN}_scenario") or ""
        eff_ghost_window = 900 if "nacht" in scenario_state.lower() or "slapen" in scenario_state.lower() else GHOST_WINDOW_SECONDS

        
        if old_state and old_state.state != new_state.state:
            # --- Ghost Bounce Tracker ---
            if new_state.state != "home" and old_state.state == "home":
                
                self._person_departure_time[entity_id] = dt_util.utcnow()

                # Check of dit een wazige GPS uitschieter is (bijv. Cell Tower > 80m)
                try: dep_gps = float(new_state.attributes.get("gps_accuracy", 0))
                except: dep_gps = 0.0
                self._person_departure_fuzzy[entity_id] = (dep_gps >= 80.0)
                                
                # --- Vertrek Debounce (Ghost Departure Guard) ---
                _LOGGER.debug(f"⏳ Mogelijke afwezigheid '{entity_id}' gedetecteerd. Wachten op bevestiging ({eff_ghost_window}s)...")
                
                async def verify_departure(_):
                    current_state = self._get_state(entity_id)
                    if current_state != "home":
                        _LOGGER.info(f"🚶‍♂️ Vertrek geverifieerd voor '{entity_id}'. Trigger Hoofdlogica.")
                        
                        # Verplaats de Smart Snapshot Interruptie naar hier
                        if self._boost_window is not None:
                            all_persons = self.options.get("person_entities", [])
                            is_house_empty = all(self._get_state(p) != "home" for p in all_persons)
                            if is_house_empty:
                                _LOGGER.info(f"INTERRUPTIE: Laatste persoon ({entity_id}) vertrekt tijdens boost.")
                                self._boost_window = None
                                
                        await self.async_trigger_main_logic(event)
                    else:
                        _LOGGER.debug(f"👻 Ghost Departure geannuleerd voor '{entity_id}' (Was snel weer thuis).")
            
                from homeassistant.helpers.event import async_call_later
                self._listeners.append(async_call_later(self.hass, eff_ghost_window, verify_departure))
                return

            is_real_arrival = (new_state.state == "home" and old_state.state != "home")

            if is_real_arrival:
                departed_at = self._person_departure_time.get(entity_id)
                if departed_at:
                    absence_duration = (dt_util.utcnow() - departed_at).total_seconds()
                    if absence_duration <= eff_ghost_window:
                        _LOGGER.warning(f"🛡️ Ghost Bounce Guard: '{entity_id}' was slechts {int(absence_duration)}s weg. Cloud trigger geannuleerd (ghost-bounce).")
                        return
                
                reg = self._entity_registry or async_get_entity_registry(self.hass)
                guest_switch_id = reg.async_get_entity_id('switch', DOMAIN, f'{self.entry.entry_id}_guest_mode')
                if guest_switch_id:
                    current_guest_state = self.hass.states.get(guest_switch_id)
                    if current_guest_state and current_guest_state.state == "on":
                        _LOGGER.info(f"👥 Welkom Thuis! Gasten-modus automatisch gedeactiveerd door aankomst van {entity_id}.")
                        await self.hass.services.async_call("switch", "turn_off", {"entity_id": guest_switch_id})

            _LOGGER.info(f"SLIMME TRIGGER: Persoon '{entity_id}' status veranderd. Trigger Hoofdlogica.")
            await asyncio.sleep(2) 
            await self.async_trigger_main_logic(event) 
        else:
            _LOGGER.debug(f"SLIMME TRIGGER: Persoon '{entity_id}' status ongewijzigd ({new_state.state}). Trigger overgeslagen.")

    async def async_handle_smart_weather_trigger(self, event):
        await asyncio.sleep(5) 
        new_state_obj = event.data.get("new_state")
        if not new_state_obj or new_state_obj.state in (STATE_UNAVAILABLE, STATE_UNKNOWN): return 

        try:
            new_temp = float(new_state_obj.attributes.get("temperature", 99.0)) 
            last_temp = getattr(self, "_last_weather_temp", None)
            
            # Domme Edge: Trigger de Cloud alleen bij een weersverandering van 0.5°C of meer
            if last_temp is None or abs(new_temp - last_temp) >= 0.5:
                _LOGGER.info(f"SLIMME TRIGGER: Temperatuurverschuiving buiten ({new_temp}°C). Trigger Hoofdlogica.")
                self._last_weather_temp = new_temp 
                await self.async_trigger_main_logic(event) 
        except Exception: pass
        
    async def async_handle_energy_trigger(self, event):
        """Smart Edge Trigger: Voorkomt Cloud-spam bij P1 fluctuaties."""
        new_state_obj = event.data.get("new_state")
        if not new_state_obj or new_state_obj.state in (STATE_UNAVAILABLE, STATE_UNKNOWN): return
        
        try:
            new_power = float(new_state_obj.state)
            last_trigger = getattr(self, "_last_energy_trigger", None)
            now = dt_util.utcnow()
            
            # Rate-limit: Slechts 1x per 5 minuten de Cloud raadplegen bij hevig zonne-overschot of verbruik
            if last_trigger and (now - last_trigger).total_seconds() < 300:
                return
                
            # Trigger de cloud als we meer dan 500W verschil (positief of negatief afhankelijk van meter) zien
            if abs(new_power) > 500:
                self._last_energy_trigger = now
                _LOGGER.info(f"⚡ Smart Edge Energy: Aanzienlijk vermogen geregistreerd ({new_power}W). Cloud raadplegen...")
                await self.async_trigger_main_logic(event)
        except Exception: pass
    
    async def async_handle_motion_trigger(self, event):
        """Lokale, razendsnelle Smart Edge afhandeling van bewegingssensoren."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if not new_state or new_state.state not in ("on", "off"): return

        # 1. Zoek bij welke zone deze sensor hoort
        target_zone_id = None
        zone_config = None
        for key, cfg in self.options.items():
            if (key.startswith("area_") or key.startswith("zone_")) and isinstance(cfg, dict):
                if entity_id in cfg.get("motion_sensors", []):
                    target_zone_id = key
                    zone_config = cfg
                    break
        
        if not target_zone_id or not zone_config: return
        if not (motion_lights := zone_config.get("motion_lights", [])): return
        
        # 2. BEWEGING GEDETECTEERD (Licht Direct AAN)
        if new_state.state == "on":
            if cancel_timer := self._motion_timers.get(target_zone_id):
                cancel_timer()
                self._motion_timers[target_zone_id] = None
                
            # Check of we het licht ook daadwerkelijk AAN mogen zetten
            if zone_config.get("motion_auto_on", True):
                allow_turn_on = True
                if zone_config.get("motion_only_when_dark", False):
                    sun_state = self._get_state("sun.sun")
                    sun_elev = float(self._get_state_attr("sun.sun", "elevation", 0) or 0)
                    is_dark = (sun_state == "below_horizon") or (sun_elev < 4.0)
                    if not is_dark:
                        allow_turn_on = False
                        _LOGGER.debug(f"☀️ Beweging in {target_zone_id}, maar het is daglicht. Auto-AAN overgeslagen.")
                
                if allow_turn_on:
                    # Check scenario voor helderheid (Nacht = zacht)
                    scenario = str(self._get_state("sensor.aion_logic_scenario") or "").lower()
                    brightness = self.options.get("ls_night_on_brightness", 15) if ("nacht" in scenario or "slapen" in scenario) else 100
                        
                    _LOGGER.debug(f"🏃‍♂️ Beweging in {target_zone_id} ({entity_id}). Licht AAN ({brightness}%).")
                    
                    lights = [e for e in motion_lights if e.startswith("light.")]
                    switches = [e for e in motion_lights if e.startswith("switch.")]
                    if lights: await self.hass.services.async_call("light", "turn_on", {"entity_id": lights, "brightness_pct": brightness}, blocking=False)
                    if switches: await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": switches}, blocking=False)
            else:
                _LOGGER.debug(f"🏃‍♂️ Beweging in {target_zone_id} ({entity_id}). Timer gepauzeerd (Auto-AAN is uitgeschakeld).")
                    
        # 3. GEEN BEWEGING MEER (Start Uitschakel-Timer)
        elif new_state.state == "off":
            # Zeker weten dat ALLE sensoren in deze ruimte 'off' zijn
            if any(self._get_state(m_id) == "on" for m_id in zone_config.get("motion_sensors",[]) if m_id != entity_id): return

            if cancel_timer := self._motion_timers.get(target_zone_id):
                cancel_timer()
                    
            delay_minutes = int(zone_config.get("motion_timer", 2))
            _LOGGER.debug(f"⏳ Geen beweging meer in {target_zone_id}. Licht gaat uit over {delay_minutes} min.")
            
            async def _turn_off_lights(_):
                _LOGGER.info(f"💡 Timer verlopen voor {target_zone_id}. Licht UIT.")
                await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": motion_lights}, blocking=False)
                self._motion_timers[target_zone_id] = None
            
            from homeassistant.helpers.event import async_call_later
            self._motion_timers[target_zone_id] = async_call_later(self.hass, delay_minutes * 60, _turn_off_lights)

    async def _manage_google_maps_api(self, _):
        travel_sensor = self.options.get("travel_time_sensor")
        if not travel_sensor: return 
        
        should_update = (self._get_internal_switch_state("coming_home") == "on")
        
        if should_update:
            _LOGGER.debug(f"Aion Logic Travel Manager: Trigger update voor {travel_sensor} (Onderweg naar huis is AAN).")
            await self.hass.services.async_call("homeassistant", "update_entity", {"entity_id": travel_sensor})

    async def async_check_interval(self, now):
        """Check of we in de monitor-periode zitten voor proactieve start."""
        if self._boost_window or self._is_running: # Sla over als we al bezig zijn
            return 

        target_str = self.options.get("proactive_target_time", "06:00:00")
        try: 
            target_time = time.fromisoformat(target_str)
        except ValueError: 
            target_time = time(6, 0)
        
        local_now = dt_util.as_local(now)

        # Monitor venster: 4 uur voor doeltijd
        start_monitor_hour = (target_time.hour - 4) % 24 
        current_hour = local_now.hour
        
        in_window = False
        if start_monitor_hour < target_time.hour: 
            in_window = start_monitor_hour <= current_hour < target_time.hour
        else: 
            in_window = current_hour >= start_monitor_hour or current_hour < target_time.hour

        # Extra veiligheid: Nooit proactief checken tussen 08:00 en 20:00 (Overdag rust)
        if 8 <= current_hour < 20:
            in_window = False

        if in_window:
            await self.async_check_proactive_start()

    async def _async_handle_notification_action(self, event):
        action = event.data.get("action")
        if action == "AION_GOING_HOME_YES":
            _LOGGER.info("🚗 Gebruiker bevestigt rit naar huis via notificatie.")
            
            # Zoek de interne switch en zet aan
            entity_reg = async_get_entity_registry(self.hass)
            if entity_id := entity_reg.async_get_entity_id("switch", DOMAIN, f"{self.entry.entry_id}_coming_home"):
                await self.hass.services.async_call("switch", "turn_on", {"entity_id": entity_id})
        elif action == "AION_ALARM_DISMISS":
            _LOGGER.info("🔕 ALARM: Gebruiker zet alarm uit via notificatie.")

            services = []
            if sec_notify := self.options.get("security_notify_service"):
                services.extend([s.strip() for s in sec_notify.split(',') if s.strip()])
            # Ook hier drivers eruit gehaald voor het netjes wissen van notificaties
            
            for svc in set(services):
                if not svc: continue
                svc_name = svc if svc.startswith("notify.") else f"notify.{svc}"
                domain, service = svc_name.split(".", 1)
                
                try:
                    await self.hass.services.async_call(
                        domain, service, 
                        {"message": "clear_notification", "data": {"tag": "aion_security_alert"}}, 
                        blocking=False
                    )
                except Exception as e:
                    _LOGGER.debug(f"Kon notificatie niet wissen voor {svc_name}: {e}")
            
            # We activeren de 'Guard Pause' switch -> Cloud stopt het alarm
            entity_reg = async_get_entity_registry(self.hass)
            if entity_id := entity_reg.async_get_entity_id("switch", DOMAIN, f"{self.entry.entry_id}_guard_pause"):
                await self.hass.services.async_call("switch", "turn_on", {"entity_id": entity_id})

        elif action == "AION_EARLY_BIRD_CANCEL":
            _LOGGER.info("😴 Gebruiker slaapt verder. Early Bird wordt geannuleerd.")
            self._cancel_early_bird_flag = True
            await self.async_trigger_main_logic(event)
            
        elif action == "AION_VACATION_YES":
            _LOGGER.info("🏖️ Gebruiker bevestigt Vakantiemodus via notificatie.")
            self._vacation_override = True
            await self.async_trigger_main_logic(event)

    async def setup_listeners(self) -> None:
        _LOGGER.debug("Registreren van Aion Logic triggers...")
        self._entity_registry = async_get_entity_registry(self.hass)
        
        if person_entities := self.options.get("person_entities", []):
            _LOGGER.debug(f"Listener voor SLIMME Personen-trigger: {person_entities}")
            self._listeners.append(async_track_state_change_event(self.hass, person_entities, self.async_handle_person_state_trigger))

        if weather_entity := self.options.get("weather_entity"):
            _LOGGER.debug(f"Listener voor Slimme Weer-trigger: {weather_entity}")
            self._listeners.append(async_track_state_change_event(self.hass, [weather_entity], self.async_handle_smart_weather_trigger))
        
        early_bird_sensors = self.options.get(CONF_EARLY_BIRD_SENSORS, [])
        if early_bird_sensors:
            async def async_handle_early_bird(event):
                new_state = event.data.get("new_state")
                if new_state and new_state.state in ("on", "home", "true"):
                    _LOGGER.info(f"🌅 Early Bird Sensor '{event.data.get('entity_id')}' geactiveerd. Cloud raadplegen...")
                    await self.async_trigger_main_logic(event)

            self._listeners.append(
                async_track_state_change_event(
                    self.hass, early_bird_sensors, async_handle_early_bird
                )
            )
        
        triggers = []
        # Main switches
        for switch_key in ["coming_home", "guest_mode", "guard_pause", "guard_master"]:
            if uid := self._entity_registry.async_get_entity_id("switch", DOMAIN, f"{self.entry.entry_id}_{switch_key}"):
                triggers.append(uid)
        
        if e := self.options.get("presence_sensors"): triggers.extend(e)
        if e := self.options.get("wifi_tracker_sensors"): triggers.extend(e)

        if wp_motion := self.options.get("wall_panel_motion_sensor"):
            triggers.append(wp_motion)
            
        if triggers:
            self._listeners.append(async_track_state_change_event(self.hass, triggers, self.async_trigger_main_logic))

        # Dynamic Window Sensors
        window_sensors = []
        motion_sensors = []
        for key, zone_cfg in self.options.items():
            if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                if s := zone_cfg.get("window_sensors"): window_sensors.extend(s)
                if m := zone_cfg.get("motion_sensors"): motion_sensors.extend(m)
        
        if window_sensors:
            self._listeners.append(async_track_state_change_event(self.hass, list(set(window_sensors)), self.async_trigger_main_logic))

        if motion_sensors:
            _LOGGER.debug(f"Listener geregistreerd voor Lokale Smart Edge (Beweging): {len(motion_sensors)} sensoren.")
            self._listeners.append(async_track_state_change_event(self.hass, list(set(motion_sensors)), self.async_handle_motion_trigger))
            
            # --- ZHA IKEA Vallhorn / Stateless Sensor Patch ---
            async def async_handle_zha_event(event):
                device_id = event.data.get("device_id")
                command = event.data.get("command")
                if not device_id or not command: return
                
                matching_entities =[
                    ent.entity_id for ent in self._entity_registry.entities.values() 
                    if ent.device_id == device_id and ent.entity_id in motion_sensors
                ]
                
                if not matching_entities: return
                
                eff_state = None
                if command in ["on_with_timed_off", "on"]:
                    eff_state = "on"
                elif command == "off":
                    eff_state = "off"
                elif command == "attribute_updated":
                    args = event.data.get("args", {})
                    if isinstance(args, dict) and args.get("attribute_name") == "on_off":
                        eff_state = "on" if args.get("value") == 1 else "off"

                if eff_state:
                    class DummyState:
                        def __init__(self, state): self.state = state
                    class DummyEvent:
                        def __init__(self, eid, state): self.data = {"entity_id": eid, "new_state": DummyState(state)}
                    
                    for eid in matching_entities:
                        await self.async_handle_motion_trigger(DummyEvent(eid, eff_state))
                        
            self._listeners.append(self.hass.bus.async_listen("zha_event", async_handle_zha_event))

        self._listeners.append(async_track_time_change(self.hass, self.async_trigger_main_logic, hour=23, minute=0, second=0))
        self._listeners.append(async_track_time_change(self.hass, self.async_trigger_main_logic, hour=4, minute=59, second=59))

        target_str = self.options.get("proactive_target_time", "06:00:00")
        try: 
            target_time = dt_util.dt.time.fromisoformat(target_str)
            self._listeners.append(async_track_time_change(self.hass, self.async_trigger_main_logic, hour=target_time.hour, minute=target_time.minute, second=0))
        except ValueError: 
            pass

        self._listeners.append(async_track_time_interval(self.hass, self.async_trigger_main_logic, dt_util.dt.timedelta(minutes=10)))
        self._listeners.append(async_track_time_interval(self.hass, self.async_check_interval, timedelta(minutes=5)))
        self._listeners.append(self.hass.bus.async_listen("mobile_app_notification_action", self._async_handle_notification_action))
        
        if self.options.get("travel_time_sensor"):
            self._listeners.append(async_track_time_interval(self.hass, self._manage_google_maps_api, timedelta(minutes=5)))

        # Driver sensors
        driver_sensors = []
        if s1 := self.options.get(CONF_DRIVER_1_SENSOR): driver_sensors.append(s1)
        if s2 := self.options.get(CONF_DRIVER_2_SENSOR): driver_sensors.append(s2)
        if driver_sensors:
            self._listeners.append(async_track_state_change_event(self.hass, driver_sensors, self.async_trigger_main_logic))
            
        # Smart Edge P1 Listener
        if p1_sensor := self.options.get("p1_meter_sensor"):
            _LOGGER.debug(f"Listener voor P1 Meter geregistreerd: {p1_sensor}")
            self._listeners.append(async_track_state_change_event(self.hass, [p1_sensor], self.async_handle_energy_trigger))            

        # --- LOKALE LIFE-SAFETY TRIGGERS (Brand & Extern Alarm) ---
        safety_triggers =[]
        if smoke_sensors := self.options.get(CONF_SMOKE_SENSORS,[]):
            safety_triggers.extend(smoke_sensors)
        if alarm_panel := self.options.get(CONF_ALARM_PANEL):
            safety_triggers.append(alarm_panel)
            
        if safety_triggers:
            async def async_handle_safety(event):
                new_state = event.data.get("new_state")
                entity_id = event.data.get("entity_id")
                if not new_state or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN): return
                
                # Brand Check
                if entity_id in self.options.get(CONF_SMOKE_SENSORS,[]):
                    if new_state.state in ["on", "unsafe", "smoke", "detected"]:
                        await self._trigger_local_emergency("fire")
                        await self.async_trigger_main_logic(event) # Laat cloud de pushberichten doen
                        
                # Extern Alarm Check
                elif entity_id == self.options.get(CONF_ALARM_PANEL):
                    if new_state.state == "triggered":
                        await self._trigger_local_emergency("intrusion")
                        await self.async_trigger_main_logic(event)

            _LOGGER.debug(f"Listener geregistreerd voor Local Life-Safety: {len(safety_triggers)} sensoren.")
            self._listeners.append(async_track_state_change_event(self.hass, safety_triggers, async_handle_safety))

        _LOGGER.debug("Alle listeners zijn geregistreerd.")

    def _get_internal_switch_state(self, switch_type: str) -> str:
        entity_reg = self._entity_registry
        if not entity_reg: return "off"
        unique_id = f"{self.entry.entry_id}_{switch_type}"
        entity_id = entity_reg.async_get_entity_id("switch", DOMAIN, unique_id)
        if entity_id: return self._get_state(entity_id) or "off"
        return "off"

    def _get_state(self, entity_id: str) -> str | None:
        if not entity_id: return None
        state = self.hass.states.get(entity_id)
        if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN): return state.state
        return None

    def _get_state_attr(self, entity_id: str, attr: str, default: Any = None) -> Any:
        if not entity_id: return default
        state = self.hass.states.get(entity_id)
        return state.attributes.get(attr) if state and state.attributes.get(attr) is not None else default

    def _get_travel_time_minutes(self, entity_id: str) -> float | None:
        """Haalt de reistijd op uit een sensor (Waze/Google Maps) als float."""
        if not entity_id: return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE): return None
        
        try:
            clean_value = state.state.replace(',', '.').strip()
            import re
            numeric_part = re.match(r"^-?\d+(?:\.\d+)?", clean_value)
            
            if numeric_part:
                return float(numeric_part.group())
        except (ValueError, TypeError):
            pass
            
        _LOGGER.debug(f"Aion Travel Manager: Sensor {entity_id} bevat geen bruikbare numerieke waarde: {state.state}")
        return None

    def _build_main_logic_payload(self, trigger_entity_id: str | None = None) -> dict:
        """Bouwt de payload voor de Cloud Logic."""
        
        target_prefix = self._boost_window.get("target_prefix", "woonkamer") if self._boost_window else "woonkamer" 
        current_indoor_temp = 18.0
        
        friendly_names = {}
        
        config_data = {k: v for k, v in self.options.items() if k.startswith("temp_") or k.startswith("driver_")}
        config_data["fallback_temp"] = self.options.get("fallback_temp", 18.0)
        config_data["minutes_per_degree"] = self.options.get("minutes_per_degree", 30.0)
        config_data["proactive_target_time"] = self.options.get("proactive_target_time", "06:00:00")
        config_data["night_start_time"] = self.options.get("night_start_time", "23:00:00")
        config_data["enable_night_vent"] = self.options.get(CONF_ENABLE_NIGHT_VENT, False)

        config_data["lifestyle"] = {
            "coming_home_on": self.options.get(CONF_LS_COMING_HOME_ON, []),
            "coming_home_brightness": self.options.get(CONF_LS_COMING_HOME_BRIGHTNESS, 100),
            "coming_home_scene": self.options.get(CONF_LS_COMING_HOME_SCENE),
            "leaving_home_off": self.options.get(CONF_LS_LEAVING_HOME_OFF, []),
            "leaving_home_scene": self.options.get(CONF_LS_LEAVING_HOME_SCENE),
            "night_off": self.options.get(CONF_LS_NIGHT_OFF, []),
            "night_off_scene": self.options.get(CONF_LS_NIGHT_OFF_SCENE),
            "night_on": self.options.get(CONF_LS_NIGHT_ON, []),
            "night_on_brightness": self.options.get(CONF_LS_NIGHT_ON_BRIGHTNESS, 20),
            "night_on_scene": self.options.get(CONF_LS_NIGHT_ON_SCENE),
            "morning_on": self.options.get(CONF_LS_MORNING_ON, []),
            "morning_brightness": self.options.get(CONF_LS_MORNING_BRIGHTNESS, 100),
            "morning_scene": self.options.get(CONF_LS_MORNING_SCENE),
            "ls_sun_check": self.options.get(CONF_LS_SUN_CHECK, True)
        }
        config_data["safety"] = {
            "guard_mode": self.options.get(CONF_GUARD_MODE, GUARD_MODE_AUTONOMOUS),
            "alarm_panel_entity": self.options.get(CONF_ALARM_PANEL),
            "defense_lights": self.options.get(CONF_DEFENSE_LIGHTS, []),
            "defense_speakers": self.options.get(CONF_DEFENSE_SPEAKERS, []),
            "defense_sirens": self.options.get("defense_sirens", []),
            "security_notify_service": self.options.get(CONF_SECURITY_NOTIFY, ""),
            "emergency_contacts": self.options.get(CONF_EMERGENCY_CONTACTS, ""),
            "smoke_sensors": self.options.get(CONF_SMOKE_SENSORS, []),
            "fire_lights": self.options.get(CONF_FIRE_LIGHTS, []),
            "fire_shutters": self.options.get(CONF_FIRE_SHUTTERS, []),
            "early_bird_sensors": self.options.get(CONF_EARLY_BIRD_SENSORS, []),
            "early_bird_window": self.options.get(CONF_EARLY_BIRD_WINDOW, 60),
            "enable_ghost_occupancy": self.options.get("enable_ghost_occupancy", False),
            "call_resident_on_alarm": self.options.get("call_resident_on_alarm", True),
            "call_after_seconds": self.options.get("call_after_seconds", 15),
            "escalation_after_seconds": self.options.get("escalation_after_seconds", 30),
            "immediate_call_travel_time_minutes": self.options.get("immediate_call_travel_time_minutes", 60)
        }
        
        config_data["energy_management"] = {
            "p1_meter_sensor": self.options.get("p1_meter_sensor"),
            "solar_kwp": self.options.get("solar_kwp", 0.0),
            "solar_orientation": self.options.get("solar_orientation", "zuid"),
            "boiler_entity": self.options.get("boiler_entity")
        }
        
        config_data["Auto"] = {
            "alarm_message": self.options.get(CONF_ALARM_MSG, "")
        }

        for key in self.options:
            if key.startswith("sp_") or key.startswith("wall_panel_"): config_data[key] = self.options[key]

        nu = dt_util.now()

        # Persons
        persons_data = {}
        for entity_id in self.options.get("person_entities", []):
            state_obj = self.hass.states.get(entity_id)
            if state_obj:
                # --- Ghost Bounce & HA Restart Protection ---
                eff_last_changed = state_obj.last_changed
                
                # 1. Restart Check (Minder dan 5 min uptime)
                is_recent_restart = (dt_util.utcnow() - getattr(self, '_startup_time', dt_util.utcnow())).total_seconds() < 300
                
                # 2. Bounce Check (Minder dan 5 min weg geweest)
                departed_at = self._person_departure_time.get(entity_id)
                is_fuzzy_dep = getattr(self, "_person_departure_fuzzy", {}).get(entity_id, False)
                is_bounce = False
                
                # --- Ghost Window ---
                eff_state = state_obj.state
                is_ghost_masked = False
                scenario_state = self._get_state(f"sensor.{DOMAIN}_scenario") or ""
                eff_ghost_window = 900 if "nacht" in scenario_state.lower() or "slapen" in scenario_state.lower() else GHOST_WINDOW_SECONDS
                 
                # Haal rauwe GPS nauwkeurigheid op voor de Cloud
                try: gps_acc = float(state_obj.attributes.get("gps_accuracy", 0))
                except: gps_acc = 0.0
                
                if eff_state != "home" and departed_at:
                    if (dt_util.utcnow() - departed_at).total_seconds() < (eff_ghost_window - 2):
                        _LOGGER.debug(f"👻 Ghost Window actief voor {entity_id}: Tussentijdse payload forceert status naar 'home'.")
                        eff_state = "home"
                        is_ghost_masked = True
                        
                if departed_at and state_obj.state == "home":
                    # Dynamische bounce-limiet: 30 minuten bij slechte GPS, 5 minuten bij goede GPS
                    bounce_limit = 1800 if is_fuzzy_dep else 300
                    if (dt_util.utcnow() - departed_at).total_seconds() < bounce_limit:
                        is_bounce = True
                        
                if is_recent_restart or is_bounce or is_ghost_masked:
                    # Maskeer de aankomsttijd naar het verleden (10 min geleden)
                    eff_last_changed = dt_util.utcnow() - timedelta(minutes=10)
                    if is_bounce: _LOGGER.debug(f"👻 Ghost Bounce genegeerd voor {entity_id}")
                    if is_recent_restart: _LOGGER.debug(f"🔄 Restart False-Positive genegeerd voor {entity_id}")
                # ---------------------------------------------------------

                persons_data[entity_id] = {
                    "state": eff_state,
                    "last_changed": eff_last_changed.isoformat() if eff_last_changed else None,
                    "gps_accuracy": gps_acc
                }
        
        persons_home = any(p.get("state") == "home" for p in persons_data.values())
        if self._get_internal_switch_state("guest_mode") == "on": persons_home = True

        # Learning Logic
        simulated_time_str = nu.strftime('%H:%M:%S')
        active_boost_start = None
        active_boost_start_temp = None
        
        if self._boost_window:
             # 1. Zodra we voorbij de starttijd zijn, sturen we continu de startdata mee.
            if nu >= self._boost_window["start"]:
                active_boost_start = self._boost_window["start"].strftime('%Y-%m-%dT%H:%M:%S')
                active_boost_start_temp = self._boost_window.get("start_temp")
                target_prefix = self._boost_window.get("target_prefix", "woonkamer")
                
                if persons_home and nu < self._boost_window["end"]:
                    simulated_time_str = self._boost_window["end"].strftime('%H:%M:%S')
                    
            # 2. Time-out beveiliging (Voor trage kamers die hun doel onmogelijk halen)
            if nu >= self._boost_window["end"] + timedelta(hours=2):
                _LOGGER.info("Boost cyclus time-out (2 uur na doel). Snapshot geforceerd beëindigd.")
                self._boost_window = None
                active_boost_start = None
            
            # 3. Interruptie door vertrek
            elif trigger_entity_id in self.options.get("person_entities",[]) and not persons_home:
                _LOGGER.info("INTERRUPTIE: Laatste persoon vertrekt. Snapshot beëindigd.")
                self._boost_window = None
        # Context
        context_data = {
            "current_time": simulated_time_str,
            "real_time": nu.strftime('%H:%M:%S'),
            "is_time_simulated": (simulated_time_str != nu.strftime('%H:%M:%S')), 
            "trigger_entity_id": trigger_entity_id,
            "timezone": str(dt_util.DEFAULT_TIME_ZONE), 
            "boost_start_time": active_boost_start,
            "boost_start_temp": active_boost_start_temp,
            "boost_target_prefix": target_prefix,
            "previous_scenario": self._get_state_attr(f"sensor.{DOMAIN}_scenario", "raw_scenario") or "unknown",
            "previous_scenario_display": self._get_state("sensor.aion_logic_scenario"), 
        }

        if context_data["is_time_simulated"]:
            if not getattr(self, '_pre_sim_scenario', None):
                self._pre_sim_scenario = context_data.get("previous_scenario")
        else:
            if getattr(self, '_pre_sim_scenario', None):
                context_data["previous_scenario"] = self._pre_sim_scenario
                self._pre_sim_scenario = None
                _LOGGER.debug("🌅 Simulatie beëindigd: Uitgestelde Lifestyle transitie geactiveerd!")
                
        if getattr(self, "_cancel_early_bird_flag", False):
            context_data["cancel_early_bird"] = True
            self._cancel_early_bird_flag = False
        else:
            context_data["cancel_early_bird"] = False

        if getattr(self, "_vacation_override", False):
            context_data["vacation_confirmed"] = True
            self._vacation_override = False        

        weather_entity = self.options.get("weather_entity")
        outdoor_temp = float(self._get_state_attr(self.options.get("weather_entity"), "temperature", 15.0) or 15.0)
        outdoor_humidity = float(self._get_state_attr(weather_entity, "humidity", 50.0) or 50.0)
        sun_elevation = float(self._get_state_attr("sun.sun", "elevation", 0))

        # 6. Energie Status (Advanced Version)
        energy_sensor = self.options.get(CONF_ENERGY_SENSOR)
        energy_tag = self.options.get(CONF_ENERGY_TAG, "low")
        is_energy_cheap = False
        
        if energy_sensor:
            current_val = self._get_state(energy_sensor)
            # Vergelijk lowercase strings voor robuustheid
            if current_val and str(current_val).lower() == str(energy_tag).lower():
                is_energy_cheap = True
                _LOGGER.debug(f"⚡ Energie is GOEDKOOP! ({current_val} == {energy_tag})")
            else:
                _LOGGER.debug(f"⚡ Energie is normaal ({current_val} != {energy_tag})")

        # 7. Driver Profiles
        active_drivers = []
        if s1 := self.options.get(CONF_DRIVER_1_SENSOR):
            _LOGGER.debug(
                f"🚗 Driver 1 Check: {self.options.get(CONF_DRIVER_1_NAME)} | "
                f"Sensor: {s1} | Status: {self._get_state(s1)} | "
                f"Verwachte Trigger: {self.options.get(CONF_DRIVER_1_TRIGGER)}"
            )
            active_drivers.append({
                "id": "driver_1", "name": self.options.get(CONF_DRIVER_1_NAME),
                "sensor_state": self._get_state(s1), "trigger_value": self.options.get(CONF_DRIVER_1_TRIGGER),
                "notify_service": self.options.get(CONF_DRIVER_1_NOTIFY)
            })

        if s2 := self.options.get(CONF_DRIVER_2_SENSOR):
            _LOGGER.debug(
                f"🚗 Driver 2 Check: {self.options.get(CONF_DRIVER_2_NAME)} | "
                f"Sensor: {s2} | Status: {self._get_state(s2)} | "
                f"Verwachte Trigger: {self.options.get(CONF_DRIVER_2_TRIGGER)}"
            )
            active_drivers.append({
                "id": "driver_2", "name": self.options.get(CONF_DRIVER_2_NAME),
                "sensor_state": self._get_state(s2), "trigger_value": self.options.get(CONF_DRIVER_2_TRIGGER),
                "notify_service": self.options.get(CONF_DRIVER_2_NOTIFY)
            })

        # Climate Zones Payload - CRUCIAAL: Stuur _all_climate_entities mee
        climate_zones_data = {}
        for key, value in self.options.items():
            if (key.startswith("area_") or key.startswith("zone_")) and isinstance(value, dict):
                current_temp_zone = None
                if sensor := value.get("current_temp_sensor"):
                    val = self._get_state(sensor)
                    if val not in [None, STATE_UNKNOWN, STATE_UNAVAILABLE]: current_temp_zone = float(val)
                elif clim_ents := value.get("climate_entities"):
                    val = self._get_state_attr(clim_ents[0], "current_temperature")
                    if val is not None: 
                        try: current_temp_zone = float(val)
                        except: pass

                climate_zones_data[key] = {
                    "lookup_prefix": value.get("lookup_prefix"),
                    "current_temp": current_temp_zone,
                    "window_sensors": value.get("window_sensors", []),
                    "lighting_entities": value.get("lighting_entities", []),
                    "zone_ventilation": value.get("zone_ventilation"),
                    "humidity_sensor": value.get("humidity_sensor"),
                    "humidity_state": self._get_state(value.get("humidity_sensor")),
                    "schedule": value.get("schedule", {}),
                    "enable_boost": value.get("enable_boost", True),
                    "is_reference": value.get("is_reference", False),
                    "_all_climate_entities": value.get("climate_entities", [])
                }

        raw_speakers = self.options.get(CONF_DEFENSE_SPEAKERS, [])
        def_speakers = raw_speakers if isinstance(raw_speakers, list) else [raw_speakers]
        speaker_vols = {s: self._get_state_attr(s, "volume_level") for s in def_speakers if s and self._get_state_attr(s, "volume_level") is not None}
        
        # Final Payload
        payload = {
            "config": config_data, "context": context_data,
            "sensors": {
                "outdoor_temp": outdoor_temp,
                "outdoor_humidity": outdoor_humidity,
                "energy_cheap": is_energy_cheap,
                "guard_pause": self._get_internal_switch_state("guard_pause"),   
                "guard_master": self._get_internal_switch_state("guard_master"), 
                "gasten_aanwezig": self._get_internal_switch_state("guest_mode"),
                "onderweg_naar_huis": self._get_internal_switch_state("coming_home"),
                "current_indoor_temp": current_indoor_temp,
                "active_drivers": active_drivers,
                "travel_time_minutes": self._get_travel_time_minutes(self.options.get("travel_time_sensor")),
                "sun_state": self._get_state("sun.sun") or "above_horizon",
                "sun_elevation": sun_elevation,
                "central_ventilation_unit": self.options.get(CONF_CENTRAL_VENT),
                "central_vent_state": self._get_state(self.options.get(CONF_CENTRAL_VENT)),
                "humidity_control_enabled": self.options.get("enable_humidity_control", True),
                "speaker_volumes": speaker_vols
            }, 
            "persons": persons_data,
            "climate_zones": climate_zones_data,
            "needs_sync": self._needs_sync,
            "friendly_names": friendly_names,
            "guard_mode": self.options.get(CONF_GUARD_MODE, GUARD_MODE_AUTONOMOUS),
        }

        if p1_sensor := self.options.get("p1_meter_sensor"):
            payload["sensors"]["p1_power"] = self._get_state(p1_sensor)
        if boiler := self.options.get("boiler_entity"):
            payload["sensors"]["boiler_temp"] = self._get_state_attr(boiler, "current_temperature")
            payload["sensors"]["boiler_state"] = self._get_state(boiler)        
        
        if self._needs_sync:
            payload["config"] = config_data
            _LOGGER.debug("Aion Logic: Full Config toegevoegd aan payload (Sync Mode).")

        # Injectie: Simulatie (TEST MODE)
        if self._active_simulation:
            payload["simulation"] = self._active_simulation
            _LOGGER.warning(f"⚠️ Aion Logic draait in SIMULATIE modus: {self._active_simulation}")
        
        # Injecties (Statussen toevoegen aan sensors lijst)
        if smoke_list := config_data["safety"].get("smoke_sensors", []):
            for e_id in smoke_list: payload["sensors"][e_id] = self._get_state(e_id)
        if alarm_id := config_data["safety"].get("alarm_panel_entity"):
             payload["sensors"][alarm_id] = self._get_state(alarm_id)
        if wp_motion := self.options.get("wall_panel_motion_sensor"):
             payload["sensors"][wp_motion] = self._get_state(wp_motion)
        for zone_data in self.options.values():
            if isinstance(zone_data, dict) and "window_sensors" in zone_data:
                for w in zone_data["window_sensors"]: payload["sensors"][w] = self._get_state(w)
            if isinstance(zone_data, dict) and "motion_sensors" in zone_data:
                for m in zone_data["motion_sensors"]:
                    state_obj = self.hass.states.get(m)
                    if state_obj:
                        payload["sensors"][m] = state_obj.state
                        if state_obj.last_changed:
                            payload["sensors"][f"{m}_last_changed"] = state_obj.last_changed.isoformat()
                    else:
                        payload["sensors"][m] = "off"

        # --- FRIENDLY NAMES INJECTIE ---
        friendly_names = {}
        
        # 1. De Trigger zelf (meest belangrijk)
        if trigger_entity_id:
             if state := self.hass.states.get(trigger_entity_id):
                 friendly_names[trigger_entity_id] = state.name

        # 2. Alle Raamsensoren (Voor scan loops)
        for zone_cfg in self.options.values():
            if isinstance(zone_cfg, dict) and "window_sensors" in zone_cfg:
                for w_id in zone_cfg["window_sensors"]:
                    if w_id not in friendly_names:
                        if state := self.hass.states.get(w_id):
                            friendly_names[w_id] = state.name
            if isinstance(zone_cfg, dict) and "motion_sensors" in zone_cfg:
                for m_id in zone_cfg["motion_sensors"]:
                    if m_id not in friendly_names:
                        if state := self.hass.states.get(m_id):
                            friendly_names[m_id] = state.name
                            
        # 3. Rookmelders & Alarm (Naam toevoegen)
        if smokes := config_data["safety"].get("smoke_sensors", []):
             for s_id in smokes:
                 if state := self.hass.states.get(s_id): friendly_names[s_id] = state.name
        
        # <--- TOEVOEGING: Alarm Paneel Naam
        if alarm_id := config_data["safety"].get("alarm_panel_entity"):
             if state := self.hass.states.get(alarm_id): friendly_names[alarm_id] = state.name

        if wp_motion := self.options.get("wall_panel_motion_sensor"):
             if state := self.hass.states.get(wp_motion): friendly_names[wp_motion] = state.name
                        
        payload["friendly_names"] = friendly_names     

        return payload
        
    # --- Aangepaste Simulatie Methode in __init__.py ---
    async def async_run_simulation(self, sim_type: str):
        """Voert een simulatie-trigger uit met een 'Dead Man's Switch'."""
        _LOGGER.info(f"🧪 Test Simulatie gestart via frontend: {sim_type}")
        
        self._active_simulation = sim_type
        try:
            # We voegen een harde timeout toe op de trigger zelf
            await asyncio.wait_for(self.async_trigger_main_logic(), timeout=35.0)
        except asyncio.TimeoutError:
            _LOGGER.error(f"🚨 Simulatie '{sim_type}' liep vast! Cloud reageerde niet binnen 35s.")
        except Exception as e:
            _LOGGER.error(f"🚨 Onverwachte fout tijdens simulatie: {e}")
        finally:
            self._active_simulation = None
            self._is_running = False  # Forceer de motor weer naar 'vrij'
            _LOGGER.debug("🧠 Simulatie-vlaggen succesvol gereset.")

    async def _execute_actions(self, actions: list, climate_zones_payload: dict):
        """Voert de acties uit (Clean Architecture v2.3.7)."""
        _LOGGER.debug(f"Uitvoeren van {len(actions)} acties ontvangen van Aion Logic API...")
        executed_count = 0
        
        for action in actions:
            try:
                service = action.get("service")
                # Validatie: Sla ongeldige services of 'delay' (als string) over
                if not service or "." not in service:
                    if service == "delay": # Special case
                        sec = action.get("data", {}).get("seconds", 1)
                        await asyncio.sleep(sec)
                    continue

                domain, service_name = service.split('.', 1)
                data = action.get("data", {}) or {}
                
                # --- 1. NOTIFICATION TAGGING ---
                # Voorkomt stapelen van berichten op de telefoon
                if domain == "notify":
                    if "data" not in data: data["data"] = {}
                    if "tag" not in data["data"]: 
                        data["data"]["tag"] = "aion_status_update"

                # --- 2. INTERNE SWITCHES ---
                if internal_target := action.get("internal_target"):
                    unique_id = f"{self.entry.entry_id}_{internal_target}"
                    entity_id = self._entity_registry.async_get_entity_id("switch", DOMAIN, unique_id)
                    if entity_id:
                        await self.hass.services.async_call(domain, service_name, {"entity_id": entity_id}, blocking=True)
                        executed_count += 1
                    continue

                # --- 3. ENTITY LOOKUP (Standaard Logica) ---
                target_entities = []
                entity_name_ref = action.get("entity")

                if "entity_id" in data:
                    target_entities = data["entity_id"] if isinstance(data["entity_id"], list) else [data["entity_id"]]
                elif entity_name_ref:
                    # Zoek in payload maps
                    if entity_name_ref in climate_zones_payload:
                        zone_data = climate_zones_payload.get(entity_name_ref)
                        target_entities = zone_data.get("_all_climate_entities", [])
                        if not target_entities and "light" in domain:
                            target_entities = zone_data.get("lighting_entities", [])
                    else:
                        # Scan prefixes
                        for zone_data in climate_zones_payload.values():
                            if zone_data.get("lookup_prefix") == entity_name_ref:
                                target_entities = zone_data.get("_all_climate_entities", [])
                                if not target_entities and "light" in domain:
                                    target_entities = zone_data.get("lighting_entities", [])
                                break
                        if not target_entities and "." in entity_name_ref:
                            target_entities = [entity_name_ref]

                if not target_entities:
                    if domain == "notify": # Meldingen zonder specifieke entiteit mogen door
                        await self.hass.services.async_call(domain, service_name, data, blocking=False)
                        executed_count += 1
                    continue

                # --- 4. UITVOERING MET SMART VALVE ---
                for ent_id in target_entities:
                    exec_domain = domain
                    exec_service = service_name
                    exec_data = data.copy()
                    exec_data["entity_id"] = ent_id

                    # TTS FIX (Taalcorrectie)
                    if domain == "tts" and exec_data.get("language") == "nl":
                        exec_data["language"] = "nl-NL"

                    # Fan vertaling
                    if service == "fan.set_percentage" and ent_id.startswith("switch."):
                        percentage = exec_data.get("percentage", 0)
                        exec_service = "turn_on" if percentage > 0 else "turn_off"
                        exec_domain = "switch"
                        exec_data = {"entity_id": ent_id}

                    # --- Mypyllant Payload Formatter ---
                    if exec_domain == "mypyllant" and exec_service == "set_manual_mode_setpoint":
                        exec_data["entity_id"] = ent_id
                        if "setpoint" in exec_data:
                            exec_data["temperature"] = exec_data.pop("setpoint")
                        exec_data.pop("setpoint", None)

                    # Strip brightness_pct als het doelwit een domme switch is
                    if ent_id.startswith("switch.") and "brightness_pct" in exec_data:
                        exec_data.pop("brightness_pct")

                    # Smart Valve Check
                    should_act = True
                    state_obj = self.hass.states.get(ent_id)
                    if state_obj:
                        if exec_service == "set_hvac_mode" and exec_data.get("hvac_mode") == state_obj.state:
                            should_act = False
                        elif exec_service in ["turn_on", "turn_off"]:
                            tgt_state = "on" if exec_service == "turn_on" else "off"
                            if state_obj.state == tgt_state: should_act = False
                        # --- START MEDIA VALVE (Voorkomt Chromecast STOP errors) ---
                        elif exec_domain == "media_player":
                            if exec_service in ["media_stop", "media_pause"] and state_obj.state not in ["playing", "buffering"]:
                                should_act = False
                        # --- EINDE MEDIA VALVE ---

                    if should_act:
                        try:
                            is_critical = exec_domain in ["climate", "mypyllant"]
                            
                            # --- START MEDIA BLOCKING PATCH (Vangt ServiceNotSupported netjes af) ---
                            if exec_domain == "media_player":
                                try:
                                    # blocking=True haalt de error naar onze try/except, timeout voorkomt vertraging
                                    await asyncio.wait_for(
                                        self.hass.services.async_call(exec_domain, exec_service, exec_data, blocking=True),
                                        timeout=2.0
                                    )
                                except asyncio.TimeoutError:
                                    _LOGGER.debug(f"🔉 Media actie timeout op '{ent_id}' (Speaker offline?)")
                            else:
                                await self.hass.services.async_call(exec_domain, exec_service, exec_data, blocking=is_critical)
                            # --- EINDE MEDIA BLOCKING PATCH ---
                            
                            executed_count += 1
                            if is_critical:
                                await asyncio.sleep(0.8)
                        except Exception as e:
                            if exec_domain == "media_player" and exec_service in ["repeat_set", "media_stop", "media_pause", "turn_off"]:
                                _LOGGER.debug(f"🔉 Media actie '{exec_service}' op '{ent_id}' zacht genegeerd (Hardware limiet): {e}")
                            else:
                                raise

            except Exception as e:
                _LOGGER.error(f"FOUT tijdens uitvoeren actie '{action.get('service')}': {e}")
        
        return executed_count
    
    async def _activate_emergency_fallback(self):
        _LOGGER.warning("⚠️ Noodloop geactiveerd! Cloud onbereikbaar.")
        fallback_temp = self.options.get("fallback_temp", 20.0)
        systeem = self.options.get("systeem_keuze_direct", "Ambisense/MyPyllant")

        for key, zone_data in self.options.items():
            if key.startswith("area_") or key.startswith("zone_"):
                try: 
                    for entity_id in zone_data.get("climate_entities", []):
                        if systeem == "Ambisense/MyPyllant":
                            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat_cool"})
                            await self.hass.services.async_call("mypyllant", "set_manual_mode_setpoint", {"entity_id": entity_id, "temperature": fallback_temp})
                        else:
                            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"})
                            await self.hass.services.async_call("climate", "set_temperature", {"entity_id": entity_id, "temperature": fallback_temp})
                except Exception: pass

    async def _trigger_local_emergency(self, emergency_type: str):
        """Voert kritieke levensreddende acties direct lokaal uit, zonder internet!"""
        _LOGGER.warning(f"🚨 LOKALE NOODSITUATIE GETRIGGERD: {emergency_type.upper()}")
        
        try:
            if emergency_type == "fire":
                # 1. Vluchtwegverlichting 100% AAN
                if fire_lights := self.options.get(CONF_FIRE_LIGHTS):
                    await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": fire_lights}, blocking=False)
                
                # 2. Vluchtweg Rolluiken OPEN
                if fire_shutters := self.options.get(CONF_FIRE_SHUTTERS):
                    await self.hass.services.async_call("cover", "open_cover", {"entity_id": fire_shutters}, blocking=False)
                
                # 3. Centrale Ventilatie UIT (Rookverspreiding voorkomen)
                if central_fan := self.options.get(CONF_CENTRAL_VENT):
                    await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": central_fan}, blocking=False)
                
                # 4. Verwarming UIT (Voorkom verspreiding via luchtstromen)
                for key, zone_data in self.options.items():
                    if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_data, dict):
                        if clim_ents := zone_data.get("climate_entities"):
                            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": clim_ents, "hvac_mode": "off"}, blocking=False)

            elif emergency_type == "intrusion":
                # 1. Afschrikverlichting AAN
                if defense_lights := self.options.get(CONF_DEFENSE_LIGHTS):
                    await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": defense_lights}, blocking=False)
                
                # 2. Lokale Sirene AAN (Via media_player)
                if speakers := self.options.get(CONF_DEFENSE_SPEAKERS):
                    await self.hass.services.async_call("media_player", "volume_set", {"entity_id": speakers, "volume_level": 1.0}, blocking=False)
                    await self.hass.services.async_call("media_player", "play_media", {
                        "entity_id": speakers, 
                        "media_content_id": f"/{DOMAIN}_assets/sirene_battleship.mp3", 
                        "media_content_type": "music"
                    }, blocking=False)
                    
                # 3. Dedicated Sirenes AAN
                if sirens := self.options.get("defense_sirens"):
                    await self.hass.services.async_call("siren", "turn_on", {"entity_id": sirens}, blocking=False)

        except Exception as e:
            _LOGGER.error(f"Fout bij uitvoeren lokale nood-actie: {e}")

    async def async_trigger_main_logic(self, *args):
        _LOGGER.debug(f"Aion Logic Hoofdlogica getriggerd door: {args}")
        trigger_entity_id = None
        event = None
        new_state_obj = None
        if args and hasattr(args[0], "data"):
            event = args[0]
            trigger_entity_id = event.data.get("entity_id")
            new_state_obj = event.data.get("new_state")
        
        is_window_trigger = False
        if trigger_entity_id:
            # Check zowel oude zones als nieuwe areas
            all_window_sensors = []
            for key, zone_cfg in self.options.items():
                if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                    if s := zone_cfg.get("window_sensors"):
                         all_window_sensors.extend(s)
            
            if trigger_entity_id in all_window_sensors:
                is_window_trigger = True
        
        if is_window_trigger:
            to_state = new_state_obj.state if new_state_obj else "unknown"
            if to_state in [STATE_UNAVAILABLE, STATE_UNKNOWN]: return
            
            dev_class = self._get_state_attr(trigger_entity_id, "device_class")
            if dev_class == "vibration":
                _LOGGER.debug(f"Trillingssensor '{trigger_entity_id}' gedetecteerd. Debounce overgeslagen.")
            else:
                await asyncio.sleep(30)
                current_state = self._get_state(trigger_entity_id)
                if (to_state == "on" and current_state == "off") or (to_state == "off" and current_state == "on"):
                    return            

        if self._is_running: return
        self._is_running = True
        
        try:
            # --- START FASE A & B: SENSOR FUSION & CAMERA REFLEX ---
            level_2_intrusion = False
            snapshot_b64 = None
            snapshot_camera = None

            if self.options.get(CONF_GUARD_MODE) != GUARD_MODE_DISABLED and trigger_entity_id:
                for key, zone_cfg in self.options.items():
                    if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                        windows = zone_cfg.get("window_sensors", [])
                        motions = zone_cfg.get("motion_sensors", [])
                        camera_entity = zone_cfg.get("camera_entity")
                        
                        if trigger_entity_id in windows or trigger_entity_id in motions:
                            # Kruisdetectie: Minimaal 1 raam open én minimaal 1 bewegingssensor actief
                            any_window_open = any(self._get_state(w) == "on" for w in windows)
                            any_motion_active = any(self._get_state(m) == "on" for m in motions)
                            
                            if any_window_open and any_motion_active:
                                level_2_intrusion = True
                                _LOGGER.warning(f"🚨 Sensor Fusion: Niveau 2 Inbraak in {zone_cfg.get('zone_name', 'Zone')}! Camera Reflex start...")
                                
                                if camera_entity:
                                    try:
                                        filename = self.hass.config.path(f"aion_reflex_{camera_entity.split('.')[1]}.jpg")
                                        await asyncio.wait_for(
                                            self.hass.services.async_call("camera", "snapshot", {"entity_id": camera_entity, "filename": filename}, blocking=True),
                                            timeout=4.0
                                        )
                                        await asyncio.sleep(0.3) # Korte I/O buffer voor bestandsysteem
                                        
                                        if os.path.exists(filename):
                                            with open(filename, "rb") as f:
                                                snapshot_b64 = base64.b64encode(f.read()).decode('utf-8')
                                            os.remove(filename) # Veilig opruimen
                                            snapshot_camera = camera_entity
                                            _LOGGER.info("📸 Camera Reflex succesvol: Base64 gegenereerd.")
                                    except asyncio.TimeoutError:
                                        _LOGGER.error("⏳ Camera Reflex Timeout! Snapshot duurde te lang, alarm wordt direct doorgezet.")
                                    except Exception as e:
                                        _LOGGER.error(f"Fout bij Camera Reflex: {e}")
                                else:
                                    _LOGGER.info("Geen camera gekoppeld. Niveau 2 payload zónder beeldverificatie.")
                                break
            # --- EINDE FASE A & B ---
            
            payload = self._build_main_logic_payload(trigger_entity_id=trigger_entity_id)
            
            # Injecteer Level 2 & Camera info
            payload["sensors"]["level_2_intrusion"] = level_2_intrusion
            if snapshot_b64:
                payload["camera_reflex"] = {
                    "entity_id": snapshot_camera,
                    "base64_image": snapshot_b64
                }
            
            had_config = "config" in payload  # Controleer of de volledige configuratie in de payload zit
            
            response = await self.api_client.trigger_main_logic(payload)
            
            if response:
                # --- SYNC VALIDATIE ---
                if had_config:
                    self._needs_sync = False
                    _LOGGER.info("✅ Cloud synchronisatie voltooid.")

                # --- START AUTO-RECOVERY PATCH ---
                if response.get("error") == "missing_config_cache":
                    _LOGGER.warning("⚠️ Cloud cache leeg. Sync direct forceren en actie opnieuw proberen.")
                    self._needs_sync = True
                    
                    # SURGICAL PATCH: Voorkom oneindige loop, maar probeer 1x direct opnieuw
                    if not getattr(self, "_is_retrying_sync", False):
                        self._is_retrying_sync = True
                        self._is_running = False # Even vrijgeven voor de retry
                        await self.async_trigger_main_logic(*args)
                        self._is_retrying_sync = False
                    return
                # --- EINDE AUTO-RECOVERY PATCH ---
                
                if self._fail_count > 0:
                    self._fail_count = 0
                    await self.hass.services.async_call("persistent_notification", "dismiss", {"notification_id": "aion_emergency_fallback"})
                
                executed = 0
                if actions := response.get("actions"):
                    executed = await self._execute_actions(actions, payload.get("climate_zones", {}))
                
                # --- SURGICAL UPDATE: ROBUUSTE LICENTIE FEEDBACK ---
                # We lezen het 'Paspoort' dat main.py nu meestuurt
                lic_info = response.get("license_info", {})
                has_security_confirmed = lic_info.get("has_security")
                has_ai_confirmed = lic_info.get("has_ai")

                # 1. SECURITY UPDATE (De Slotbewaarder)
                if has_security_confirmed is False:
                     # Forceer sensor naar disabled (Comfort Mode)
                     self.hass.bus.async_fire("aion_logic_security_update", {
                         "status": "disabled",
                         "display_state": "Comfort (Geen Guard)",
                         "threat_level": "Upgrade naar Guardian"
                     })
                elif has_security_confirmed is True:
                     # [CRUCIAAL] Stuur een "Unlock" signaal als we wel rechten hebben!
                     # Hierdoor springt sensor.py van het slot en berekent hij weer de echte status.
                     self.hass.bus.async_fire("aion_logic_security_update", {
                         "status": "active" 
                     })

                # 2. AI UPDATE (Het Brein)
                learning_result = response.get("learning_result")
                
                if learning_result:
                    self.hass.bus.async_fire("aion_logic_learning_update", learning_result)

                    # --- FIX: Stop de snapshot zodra Cloud succesvol heeft geleerd ---
                    if self._boost_window:
                        self._boost_window = None
                        _LOGGER.info("🧠 Neural Core heeft succesvol geleerd. Boost geheugen gewist.")
                     
                    # --- SAFETY FIX: MPD RESET ---
                    new_mpd_val = float(learning_result.get("new_mpd", 30.0))
                    if new_mpd_val >= 90.0 and self.options.get("minutes_per_degree", 30.0) >= 90.0:
                         _LOGGER.warning("🧠 Neural Core Reset: MPD zat vast op 90. Reset naar 30.")
                         new_mpd_val = 30.0
                         
                    new_options = {**self.entry.options}
                    new_options["minutes_per_degree"] = new_mpd_val
                    self.hass.config_entries.async_update_entry(self.entry, options=new_options)
                
                elif has_ai_confirmed is False:
                     # FORCEER COMFORT: AI staat uit
                     self.hass.bus.async_fire("aion_logic_learning_update", {
                         "feedback_message": "Aion Comfort (AI Uitgeschakeld)",
                         "new_mpd": self.options.get("minutes_per_degree", 30.0), 
                         "timestamp": dt_util.now().isoformat()
                     })

                elif has_ai_confirmed is True:
                     # [NIEUW] KEEPALIVE: AI staat aan, maar geen nieuw resultaat vandaag.
                     # We sturen een signaal om de sensor van 'Comfort' af te halen indien nodig.
                     self.hass.bus.async_fire("aion_logic_learning_update", {
                         "status": "active_keepalive",
                         "new_mpd": self.options.get("minutes_per_degree", 30.0),
                         "timestamp": dt_util.now().isoformat()
                     })

                if scenario := response.get('scenario'):
                    self.hass.bus.async_fire("aion_logic_scenario_update", {"scenario": scenario})
                    _LOGGER.info(f"🎯 Aion Logic: {scenario} | Acties: {executed}")

        except (ApiAuthError, ApiConnectionError, ApiTimeoutError):
            self._fail_count += 1
            if self._fail_count >= self._fail_threshold: await self._activate_emergency_fallback()
        except Exception as e:
            _LOGGER.exception(f"Onverwachte fout: {e}")
        finally:
            await asyncio.sleep(1.5)
            self._is_running = False

    async def async_check_proactive_start(self, *args):
        if self._boost_window: return 
        try:
            base = self._build_main_logic_payload()
            payload = {
                "config": base["config"], 
                "sensors": base["sensors"],
                "climate_zones": base.get("climate_zones", {}),
                "context": base.get("context", {})
            }
            response = await self.api_client.trigger_proactive_start(payload)
            
            if t_str := response.get("calculated_start_time"):
                if response.get("info") == "Already warm enough.":
                    _LOGGER.info("🌡️ Doeltemperatuur reeds bereikt. Geen proactieve start (en geen leer-cyclus) nodig.")
                    return
                    
                worst_zone = response.get("worst_zone", "woonkamer")
                start_dt = time.fromisoformat(t_str)
                vandaag = dt_util.now().date()
                start_dt = dt_util.as_local(dt_util.dt.datetime.combine(vandaag, start_dt))
                
                # We slaan het verschil op in een variabele
                tijd_verschil = (start_dt - dt_util.now()).total_seconds()
                
                if tijd_verschil <= 0:
                     target_str = self.options.get("proactive_target_time", "06:00:00")
                     target_dt = dt_util.as_local(dt_util.dt.datetime.combine(vandaag, time.fromisoformat(target_str)))
                     
                     # --- NIEUW: Leg de starttemperatuur vast voor de KOUDSTE ruimte ---
                     start_temp = 18.0 # Fallback
                     
                     # Loop door zones om een geldige meting te vinden
                     for key, zone_cfg in self.options.items():
                         if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                             if zone_cfg.get("lookup_prefix", "").lower() == worst_zone.lower():
                                 if s := zone_cfg.get("current_temp_sensor"):
                                     if val := self._get_state(s):
                                         try: start_temp = float(val); break
                                         except: pass
                                 
                                 if clim_ents := zone_cfg.get("climate_entities"):
                                     if val := self._get_state_attr(clim_ents[0], "current_temperature"):
                                         try: start_temp = float(val); break
                                         except: pass
                     # -------------------------------------------

                     # Sla ECHTE start op in de window voor kloppende leertijd
                     echte_start = dt_util.now()
                     self._boost_window = {
                         "start": echte_start, 
                         "end": target_dt, 
                         "start_temp": start_temp, 
                         "target_prefix": worst_zone
                     }
                     
                     await self.async_trigger_main_logic()
                     _LOGGER.info(f"🚀 Proactieve Boost gestart! Berekend: {start_dt.strftime('%H:%M:%S')} -> Actueel: {echte_start.strftime('%H:%M:%S')}, Doel: {target_dt.strftime('%H:%M:%S')}, Temp: {start_temp}°C")
                
                # --- De Exact Timer Patch (Asynchrone Start) ---
                elif 0 < tijd_verschil <= 300:
                     target_str = self.options.get("proactive_target_time", "06:00:00")
                     target_dt = dt_util.as_local(dt_util.dt.datetime.combine(vandaag, time.fromisoformat(target_str)))
                     
                     start_temp = 18.0 # Fallback
                     
                     for key, zone_cfg in self.options.items():
                         if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                             if zone_cfg.get("lookup_prefix", "").lower() == worst_zone.lower():
                                 if s := zone_cfg.get("current_temp_sensor"):
                                     if val := self._get_state(s):
                                         try: start_temp = float(val); break
                                         except: pass
                                 
                                 if clim_ents := zone_cfg.get("climate_entities"):
                                     if val := self._get_state_attr(clim_ents[0], "current_temperature"):
                                         try: start_temp = float(val); break
                                         except: pass

                     # We blokkeren de window nu al om dubbele interval-checks te voorkomen
                     self._boost_window = {
                         "start": start_dt, 
                         "end": target_dt, 
                         "start_temp": start_temp, 
                         "target_prefix": worst_zone
                     }
                     
                     from homeassistant.helpers.event import async_call_later
                     async def _exact_start(_):
                         await self.async_trigger_main_logic()
                         
                     async_call_later(self.hass, tijd_verschil, _exact_start)
                     _LOGGER.info(f"⏳ Exact Timer: Proactieve start vastgezet op exacte seconde. Aftelklok: {int(tijd_verschil)} sec ({start_dt.strftime('%H:%M:%S')}).")
                # ----------------------------------------------------------

                elif tijd_verschil < 5400: 
                     # 'start_tijd_str' bestond niet, veranderd naar 't_str'
                     _LOGGER.debug(f"Nog even wachten. Start over {int(tijd_verschil/60)} minuten ({t_str}).")
                
        except (ApiAuthError, ApiConnectionError, ApiTimeoutError) as e:
            _LOGGER.warning(f"Kon proactive start niet checken: {e}")
        except Exception as e:
            _LOGGER.error(f"Onverwachte fout in proactive check: {e}")

    async def async_run_shadow_logic(self, override_data: dict) -> dict:
        """
        Voert de volledige logica keten uit, maar slaat de uitvoering over.
        Dit test: Data verzameling -> Payload bouw -> Cloud Comm -> Response Parsing.
        """
        # 1. Bouw de normale payload zoals het systeem nu draait
        real_payload = self._build_main_logic_payload()
        
        # 2. Pas overrides toe vanuit het Dashboard (Injectie)
        # Bv. Dashboard zegt: "Doe alsof outdoor_temp -5 is"
        sim_sensors = override_data.get("sensors", {})
        real_payload["sensors"].update(sim_sensors)
        
        if "config" in override_data:
            real_payload["config"].update(override_data["config"])

        if "context_overlay" in override_data:
            real_payload["context"].update(override_data["context_overlay"])
            trigger_id = override_data["context_overlay"].get("trigger_entity_id")
            if trigger_id:
                state = self.hass.states.get(trigger_id)
                if "friendly_names" not in real_payload: real_payload["friendly_names"] = {}
                real_payload["friendly_names"][trigger_id] = state.name if state else "Test Raamsensor"
                    
        # --- DYNAMISCHE SCENARIO INJECTIE (Doorgeefluik) ---
        is_live_comms = (override_data.get("simulation") == "live_comms_test")
        
        if is_live_comms:
            # Verwijder de simulatie-vlag zodat de Cloud het als 100% ECHT behandelt
            real_payload.pop("simulation", None)
            
            for p_id in real_payload.get("persons", {}):
                real_payload["persons"][p_id]["state"] = "not_home"
            if "sensors" in real_payload:
                real_payload["sensors"]["gasten_aanwezig"] = "off"
                real_payload["sensors"]["guard_master"] = "on"
            real_payload["guard_mode"] = "manual"
            
        elif "simulation" in override_data:
            real_payload["simulation"] = override_data["simulation"]
        else:
            real_payload["simulation"] = "SHADOW_RUN" # Fallback voor oude knoppen
            
        if "state_overlay" in override_data:
            real_payload["state_overlay"] = override_data["state_overlay"]

        # 3. Stuur naar de Cloud (Echte test van verbinding!)
        try:
            start_time = dt_util.now()
            response = await self.api_client.trigger_main_logic(real_payload)
            duration = (dt_util.now() - start_time).total_seconds()
            
            # 4. Filteren resultaat (Geen uitvoering!)
            actions = response.get("actions", [])
            scenario = response.get("scenario", "Onbekend")

            # 5. Live Comms Test: Voer ALLEEN communicatie-acties (telefoon/sms/push) lokaal uit
            if is_live_comms:
                safe_actions = [a for a in actions if a.get("service", "").startswith("notify.")]
                await self._execute_actions(safe_actions, real_payload.get("climate_zones", {}))
                        
            # We voeren _execute_actions NIET uit. We returnen het gewoon.
            return {
                "success": True,
                "latency": duration,
                "scenario": scenario,
                "actions": actions, # Dit zijn de acties die hij ZOU doen
                "payload_sent": real_payload, # Ter debug
                "learning_result": response.get("learning_result")
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

class AionLogicSimulationView(HomeAssistantView):
    """API Endpoint voor Aion Command Center Shadow Runs."""
    url = "/api/aion_logic/shadow_run"
    name = "api:aion_logic:shadow_run"
    requires_auth = True

    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def post(self, request):
        """Ontvangt een simulatieverzoek van het dashboard."""
        try:
            data = await request.json()
            _LOGGER.info("🧪 Shadow Run verzoek ontvangen van Aion Command Center.")
            
            # Voer de logica uit in 'Shadow Mode' (geen echte acties)
            result = await self.coordinator.async_run_shadow_logic(data)
            return self.json(result)
        except Exception as e:
            _LOGGER.error(f"Shadow Run Fout: {e}")
            return self.json({"error": str(e)}, status_code=500)

class AionLogicDiagnoseView(HomeAssistantView):
    """API Endpoint voor Health Checks."""
    url = "/api/aion_logic/diagnose"
    name = "api:aion_logic:diagnose"
    requires_auth = True

    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def get(self, request):
        try:
            coordinator = self.coordinator
            hass = coordinator.hass
            
            # 1. Config Entry Ophalen
            entry = getattr(coordinator, "entry", getattr(coordinator, "config_entry", None))
            if not entry:
                return self.json({"status": "error", "error": "Geen ConfigEntry gevonden."})

            options = entry.options
            
            report = {
                "status": "healthy",
                "checks": [],
                "warnings": []
            }
            
            # --- LICENTIE STATUS ---
            # We tonen de code gemaskeerd voor debug doeleinden
            raw_code = entry.data.get(CONF_ACTIVATION_CODE, "")
            masked_code = f"{raw_code[:4]}...{raw_code[-4:]}" if len(raw_code) > 8 else "Onbekend"
            
            report["checks"].append({"name": "Licentie Code", "status": "ok", "value": masked_code})

            # --- CHECK 1: CORE ENTITEITEN (Dynamische Resolutie) ---
            entity_reg = async_get_entity_registry(hass)
            guest_id = entity_reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_guest_mode")
            coming_id = entity_reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_coming_home")
            
            core_entities = [
                (guest_id, "Guest Mode Switch"),
                (coming_id, "Coming Home Switch")
            ]

            for entity_id, label in core_entities:
                if not entity_id:
                    report["checks"].append({"name": label, "status": "error", "value": "Niet gevonden"})
                    report["warnings"].append(f"⚠️ Core Entiteit '{label}' ontbreekt in het register. Is de integratie goed gestart?")
                    report["status"] = "issue"
                    continue
                
                state = hass.states.get(entity_id)
                if state is None:
                    report["checks"].append({"name": label, "status": "error", "value": "Niet gevonden (State)"})
                    report["warnings"].append(f"⚠️ Core Entiteit '{entity_id}' ontbreekt in de state machine.")
                    report["status"] = "issue"
                elif state.state == "unavailable":
                    report["checks"].append({"name": label, "status": "warning", "value": "Unavailable"})
                    report["warnings"].append(f"⚠️ {label} is niet beschikbaar.")
                else:
                    report["checks"].append({"name": label, "status": "ok", "value": state.state})

            # --- CHECK 2: GECONFIGUREERDE SENSOREN ---
            if options:
                for key, val in options.items():
                    if isinstance(key, str) and isinstance(val, str) and len(val) > 2:
                        if "entity" in key or "sensor" in key:
                            if "input_boolean" in val: continue 

                            state = hass.states.get(val)
                            label = key.replace("_", " ").title()
                            
                            if state is None:
                                report["checks"].append({"name": label, "status": "error", "value": f"{val} (Mist)"})
                                report["warnings"].append(f"⚠️ Configuratiefout: '{val}' bestaat niet.")
                                report["status"] = "issue"
                            elif state.state in ["unavailable", "unknown"]:
                                report["checks"].append({"name": label, "status": "warning", "value": "Unavailable"})
                                report["warnings"].append(f"⚠️ Sensor '{val}' geeft geen data (Status: {state.state}). Check de bron-integratie.")
                                report["status"] = "issue"
                            else:
                                report["checks"].append({"name": label, "status": "ok", "value": f"{state.state}"})

            # --- CHECK 3: CLOUD & REMOTE VERBINDING ---
            # A. Aion Cloud Link
            if hasattr(coordinator, "api_client") and coordinator.api_client:
                report["checks"].append({"name": "Aion Cloud Link", "status": "ok", "value": "Verbonden"})
            else:
                report["checks"].append({"name": "Aion Cloud Link", "status": "error", "value": "Offline"})
                report["status"] = "issue"

            # B. Remote UI / Nabu Casa Check
            nabu_casa_status = "Niet gedetecteerd"
            nabu_casa_ok = False
            
            # Hier zat waarschijnlijk de fout: de 'try' moet een 'except' hebben
            if "cloud" in hass.config.components:
                try:
                    cloud_inst = hass.data.get("cloud")
                    if cloud_inst and cloud_inst.is_logged_in and cloud_inst.is_connected:
                        nabu_casa_status = "Verbonden (Nabu Casa)"
                        nabu_casa_ok = True
                    elif cloud_inst and cloud_inst.is_logged_in:
                        nabu_casa_status = "Ingelogd (Niet verbonden)"
                    else:
                        nabu_casa_status = "Aanwezig (Niet ingelogd)"
                except Exception:
                    nabu_casa_status = "Fout bij uitlezen"
            
            if nabu_casa_ok:
                report["checks"].append({"name": "Remote Toegang", "status": "ok", "value": nabu_casa_status})
            else:
                report["checks"].append({"name": "Remote Toegang", "status": "warning", "value": nabu_casa_status})
                report["warnings"].append("⚠️ Geen actieve Nabu Casa verbinding gedetecteerd. Mobiele app werkt mogelijk niet buitenshuis.")

            return self.json(report)

        except Exception as e:
            import traceback
            return self.json({
                "status": "error",
                "error": f"Diagnose Crash: {str(e)}",
                "warnings": [traceback.format_exc()]
            })
