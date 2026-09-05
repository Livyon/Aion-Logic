"""Config flow voor Aion Logic®."""
import voluptuous as vol
import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigFlow, ConfigEntry, OptionsFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector, area_registry, entity_registry
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_ACTIVATION_CODE, CONF_ENERGY_SENSOR, CONF_ENERGY_TAG, CONF_DRIVER_1_NAME, CONF_DRIVER_1_SENSOR, CONF_DRIVER_1_TRIGGER, CONF_DRIVER_1_NOTIFY, CONF_DRIVER_2_NAME, CONF_DRIVER_2_SENSOR, CONF_DRIVER_2_TRIGGER, CONF_DRIVER_2_NOTIFY, CONF_LS_COMING_HOME_ON, CONF_LS_COMING_HOME_AUTO, CONF_LS_COMING_HOME_SCENE, CONF_LS_COMING_HOME_BRIGHTNESS, CONF_LS_LEAVING_HOME_OFF, CONF_LS_LEAVING_HOME_AUTO, CONF_LS_LEAVING_HOME_SCENE, CONF_LS_NIGHT_OFF, CONF_LS_NIGHT_OFF_SCENE, CONF_LS_NIGHT_ON, CONF_LS_NIGHT_ON_SCENE, CONF_LS_NIGHT_ON_BRIGHTNESS, CONF_LS_NIGHT_AUTO, CONF_LS_MORNING_ON, CONF_LS_MORNING_SCENE, CONF_LS_MORNING_BRIGHTNESS, CONF_LS_MORNING_AUTO, CONF_LS_SUN_CHECK, CONF_DEFENSE_LIGHTS, CONF_DEFENSE_SPEAKERS, CONF_FIRE_LIGHTS, CONF_FIRE_SHUTTERS, CONF_ALARM_MSG, CONF_GUARD_MODE, GUARD_MODE_AUTONOMOUS, GUARD_MODE_MANUAL, GUARD_MODE_DISABLED, CONF_ALARM_PANEL, CONF_SMOKE_SENSORS, CONF_SECURITY_NOTIFY, CONF_EMERGENCY_CONTACTS, CONF_CENTRAL_VENT, CONF_ENABLE_HUMIDITY, CONF_ENABLE_NIGHT_VENT, CONF_ZONE_VENT, CONF_HUMIDITY_SENSOR, CONF_FAMILY_CALENDAR, CONF_TRAVEL_SENSOR, CONF_DRIVER_OUTDOOR_MAX, CONF_DRIVER_INDOOR_MAX, CONF_EARLY_BIRD_SENSORS, CONF_EARLY_BIRD_WINDOW, CONF_PERIMETER_SENSORS, CONF_PERIMETER_COOLDOWN 
from .api import AionLogicApiClient, ApiAuthError, ApiConnectionError

_LOGGER = logging.getLogger(__name__)
SETPOINT_GROUPS = ["woonkamer", "eetkamer", "badkamer", "keuken", "kantoor", "berging", "slaapkamer_1", "slaapkamer_2", "slaapkamer_3"]

# --- HELPERS ---
def _get_list(options: dict, key: str) -> list:
    """Zorgt ervoor dat we altijd een lijst terugkrijgen, ook als de optie None is."""
    val = options.get(key)
    if val is None:
        return []
    if isinstance(val, str): # Mocht het per ongeluk een string zijn
        return [val]
    return val

def _get_general_schema(options: dict) -> vol.Schema:
    """Pijler 1: De Basis (Tijden, MPD én Personen)."""
    return vol.Schema({
        # 1. Tijdsinstellingen
        vol.Required("proactive_target_time", default=options.get("proactive_target_time", "06:00:00")): selector.TimeSelector(),
        vol.Required("night_start_time", default=options.get("night_start_time", "23:00:00")): selector.TimeSelector(),
        
        # 2. Het Brein (MPD)
        vol.Required("minutes_per_degree", default=options.get("minutes_per_degree", 30.0)): selector.NumberSelector({
            "min": 5.0, "max": 90.0, "step": 1.0, "mode": "slider", "unit_of_measurement": "min/°C"
        }),

        # 3. Personen (Samengevoegd voor overzicht)
        vol.Required("person_entities", default=options.get("person_entities", [])): selector.EntitySelector({
            "domain": "person", "multiple": True
        }),
        vol.Optional("presence_sensors", description="Extra sensoren (Tags/Deurcontacten/Knoppen)", default=options.get("presence_sensors", [])): selector.EntitySelector({
            "domain": ["binary_sensor", "device_tracker", "sensor", "input_boolean", "event"], 
            "multiple": True
        }),
        vol.Optional("enable_pet_mode", default=options.get("enable_pet_mode", False)): selector.BooleanSelector(),
        
        # 4. Slim Ontwaken (Early Bird)
        vol.Optional(CONF_EARLY_BIRD_SENSORS, default=options.get(CONF_EARLY_BIRD_SENSORS, [])): selector.EntitySelector({
            "domain": ["light", "switch", "binary_sensor", "event"], 
            "multiple": True
        }),
        vol.Required(CONF_EARLY_BIRD_WINDOW, default=options.get(CONF_EARLY_BIRD_WINDOW, 60)): selector.NumberSelector({
            "min": 15, "max": 120, "step": 15, "mode": "slider", "unit_of_measurement": "min"
        }),
    })

def _get_entities_schema(options: dict) -> vol.Schema:
    """Pijler 2: Technische Entiteiten & Hardware."""
    
    # 1. Kalender data voorbereiden (String -> Lijst conversie)
    raw_calendar = options.get(CONF_FAMILY_CALENDAR)
    default_calendars = []
    
    if raw_calendar:
        if isinstance(raw_calendar, str):
            default_calendars = [raw_calendar]
        elif isinstance(raw_calendar, list):
            default_calendars = raw_calendar
    
    return vol.Schema({
        # Weer is cruciaal, dus die mag hier aangepast worden
        vol.Required("weather_entity", default=options.get("weather_entity")): selector.EntitySelector({"domain": "weather"}),
        
        # Systeemkeuze
        vol.Required("systeem_keuze_direct", default=options.get("systeem_keuze_direct", "Ambisense/MyPyllant")): selector.SelectSelector(
            selector.SelectSelectorConfig(options=["Ambisense/MyPyllant", "Zigbee/Lokaal"], mode=selector.SelectSelectorMode.DROPDOWN)
        ),

        # Netwerk & Detectie
        vol.Optional("home_wifi_ssid", description="Thuis Wi-Fi (SSID)", default=options.get("home_wifi_ssid", "")): selector.TextSelector(),
        vol.Optional("wifi_tracker_sensors", default=options.get("wifi_tracker_sensors", [])): selector.EntitySelector({"domain": "sensor", "multiple": True}),
        
        # Kalender (Multi-Select)
        vol.Optional(CONF_FAMILY_CALENDAR, description={"suggested_value": default_calendars}): selector.EntitySelector({"domain": "calendar", "multiple": True}),

        # Reistijd (Optioneel)
        vol.Optional("travel_time_sensor", description="Reistijd Sensor (Waze/Google)", **({"default": options.get("travel_time_sensor")} if options.get("travel_time_sensor") else {})): selector.EntitySelector({"domain": "sensor"}),
    })

def _get_lifestyle_schema(options: dict) -> vol.Schema:
    def _scene_default(key):
        val = options.get(key)
        return val if val else vol.UNDEFINED

    return vol.Schema({
        # 1. Thuiskomst
        vol.Optional(CONF_LS_COMING_HOME_ON, default=_get_list(options, CONF_LS_COMING_HOME_ON)): selector.EntitySelector({"domain": ["light", "switch"], "multiple": True}),
        vol.Optional(CONF_LS_COMING_HOME_BRIGHTNESS, default=options.get(CONF_LS_COMING_HOME_BRIGHTNESS, 100)): selector.NumberSelector({"min": 1, "max": 100, "step": 1, "mode": "slider", "unit_of_measurement": "%"}),
        vol.Optional(CONF_LS_COMING_HOME_SCENE, default=_scene_default(CONF_LS_COMING_HOME_SCENE)): selector.EntitySelector({"domain": "scene"}),
        vol.Optional(CONF_LS_COMING_HOME_AUTO, default=_get_list(options, CONF_LS_COMING_HOME_AUTO)): selector.EntitySelector({"domain": "automation", "multiple": True}),
        vol.Optional(CONF_LS_SUN_CHECK, default=options.get(CONF_LS_SUN_CHECK, True)): selector.BooleanSelector(),
        
        # 2. Vertrek
        vol.Optional(CONF_LS_LEAVING_HOME_OFF, default=_get_list(options, CONF_LS_LEAVING_HOME_OFF)): selector.EntitySelector({"domain": ["light", "switch"], "multiple": True}),
        vol.Optional(CONF_LS_LEAVING_HOME_SCENE, default=_scene_default(CONF_LS_LEAVING_HOME_SCENE)): selector.EntitySelector({"domain": "scene"}),
        vol.Optional(CONF_LS_LEAVING_HOME_AUTO, default=_get_list(options, CONF_LS_LEAVING_HOME_AUTO)): selector.EntitySelector({"domain": "automation", "multiple": True}),

        # 3. Slapen
        vol.Optional(CONF_LS_NIGHT_OFF, default=_get_list(options, CONF_LS_NIGHT_OFF)): selector.EntitySelector({"domain": ["light", "switch"], "multiple": True}),
        vol.Optional(CONF_LS_NIGHT_OFF_SCENE, default=_scene_default(CONF_LS_NIGHT_OFF_SCENE)): selector.EntitySelector({"domain": "scene"}),
        
        vol.Optional(CONF_LS_NIGHT_ON, default=_get_list(options, CONF_LS_NIGHT_ON)): selector.EntitySelector({"domain": ["light", "switch"], "multiple": True}),
        vol.Optional(CONF_LS_NIGHT_ON_BRIGHTNESS, default=options.get(CONF_LS_NIGHT_ON_BRIGHTNESS, 20)): selector.NumberSelector({"min": 1, "max": 100, "step": 1, "mode": "slider", "unit_of_measurement": "%"}),
        vol.Optional(CONF_LS_NIGHT_ON_SCENE, default=_scene_default(CONF_LS_NIGHT_ON_SCENE)): selector.EntitySelector({"domain": "scene"}),
        vol.Optional(CONF_LS_NIGHT_AUTO, default=_get_list(options, CONF_LS_NIGHT_AUTO)): selector.EntitySelector({"domain": "automation", "multiple": True}),

        # 4. Opstaan
        vol.Optional(CONF_LS_MORNING_ON, default=_get_list(options, CONF_LS_MORNING_ON)): selector.EntitySelector({"domain": ["light", "switch"], "multiple": True}),
        vol.Optional(CONF_LS_MORNING_BRIGHTNESS, default=options.get(CONF_LS_MORNING_BRIGHTNESS, 100)): selector.NumberSelector({"min": 1, "max": 100, "step": 1, "mode": "slider", "unit_of_measurement": "%"}),
        vol.Optional(CONF_LS_MORNING_SCENE, default=_scene_default(CONF_LS_MORNING_SCENE)): selector.EntitySelector({"domain": "scene"}),
        vol.Optional(CONF_LS_MORNING_AUTO, default=_get_list(options, CONF_LS_MORNING_AUTO)): selector.EntitySelector({"domain": "automation", "multiple": True}),
    })

def _get_safety_schema(options: dict, notify_services: list = None) -> vol.Schema:
    """Schema voor Active Guardian instellingen."""

    current_alarm = options.get(CONF_ALARM_PANEL)
    default_alarm = current_alarm if current_alarm else vol.UNDEFINED

    # Slimme conversie: String (uit DB) -> Lijst (voor UI)
    current_notify = options.get(CONF_SECURITY_NOTIFY, [])
    if isinstance(current_notify, str):
        # Split op komma's en verwijder spaties
        current_notify = [x.strip() for x in current_notify.split(",") if x.strip()]

    # Als we geen services hebben doorgekregen (fallback), maak een leeg lijstje
    if notify_services is None:
        notify_services = []

    return vol.Schema({
        # SECTIE 1: INBRAAK (Active Defense)
        vol.Required(CONF_GUARD_MODE, default=options.get(CONF_GUARD_MODE, GUARD_MODE_AUTONOMOUS)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"value": GUARD_MODE_AUTONOMOUS, "label": "Autonoom (Volautomatisch)"},
                    {"value": GUARD_MODE_MANUAL, "label": "Manueel (Via Schakelaar)"},
                    {"value": GUARD_MODE_DISABLED, "label": "Uitgeschakeld"},
                ],
                mode=selector.SelectSelectorMode.DROPDOWN
            )
        ),
        vol.Optional("enable_ghost_occupancy", description="Vakantiesimulatie Activeren (Ghost Occupancy)", default=options.get("enable_ghost_occupancy", False)): selector.BooleanSelector(),      
        vol.Optional(CONF_PERIMETER_SENSORS, description="Buitencamera's & Deurbellen (Pre-Warning)", default=_get_list(options, CONF_PERIMETER_SENSORS)): selector.EntitySelector({"domain": ["binary_sensor", "sensor", "camera", "event"], "multiple": True}),
        vol.Optional(CONF_PERIMETER_COOLDOWN, description="Cooldown Perimeter (Minuten)", default=options.get(CONF_PERIMETER_COOLDOWN, 15)): selector.NumberSelector({"min": 0, "max": 60, "step": 1, "mode": "slider", "unit_of_measurement": "min"}),
        
        # HIER IS DE AANPASSING (default=default_alarm):
        vol.Optional(CONF_ALARM_PANEL, description="Of koppel Extern Alarm (bv. Alarmo)", default=default_alarm): selector.EntitySelector({"domain": "alarm_control_panel"}),
        vol.Optional(CONF_DEFENSE_LIGHTS, description="Lichten Knipperen (Stroboscoop)", default=_get_list(options, CONF_DEFENSE_LIGHTS)): selector.EntitySelector({"domain": ["light", "switch"], "multiple": True}),
        vol.Optional(CONF_DEFENSE_SPEAKERS, description="Speakers voor Sirene/TTS", default=_get_list(options, CONF_DEFENSE_SPEAKERS)): selector.EntitySelector({"domain": "media_player", "multiple": True}),
        vol.Optional("defense_sirens", description="Sirenes (Zigbee/Matter/Lokaal)", default=_get_list(options, "defense_sirens")): selector.EntitySelector({"domain": "siren", "multiple": True}),        
        
        # SECTIE 2: BRAND (Fireman's Stop)
        vol.Optional(CONF_SMOKE_SENSORS, description="Rookmelders", default=_get_list(options, CONF_SMOKE_SENSORS)): selector.EntitySelector({"domain": "binary_sensor", "multiple": True}),
        vol.Optional(CONF_FIRE_LIGHTS, description="Vluchtweg Verlichting (AAN)", default=_get_list(options, CONF_FIRE_LIGHTS)): selector.EntitySelector({"domain": ["light", "switch"], "multiple": True}),
        vol.Optional(CONF_FIRE_SHUTTERS, description="Vluchtweg Rolluiken (OPEN)", default=_get_list(options, CONF_FIRE_SHUTTERS)): selector.EntitySelector({"domain": "cover", "multiple": True}),
    })
    
def _get_virtual_operator_schema(options: dict, notify_services: list = None) -> vol.Schema:
    """Schema voor de Virtuele Meldkamer & Escalatie."""
    current_notify = options.get(CONF_SECURITY_NOTIFY, [])
    if isinstance(current_notify, str):
        current_notify = [x.strip() for x in current_notify.split(",") if x.strip()]
    if notify_services is None: notify_services = []
        
    current_contact = options.get(CONF_EMERGENCY_CONTACTS, "")
    if current_contact is None:
        current_contact = ""
        
    return vol.Schema({
        vol.Required(CONF_SECURITY_NOTIFY, default=current_notify): selector.SelectSelector(
            selector.SelectSelectorConfig(options=notify_services, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN, custom_value=True)
        ),
        vol.Optional(CONF_EMERGENCY_CONTACTS, description={"suggested_value": current_contact}): selector.TextSelector(), 
        vol.Optional("call_resident_on_alarm", default=options.get("call_resident_on_alarm", True)): selector.BooleanSelector(),
        vol.Optional("call_after_seconds", default=options.get("call_after_seconds", 15)): selector.NumberSelector({"min": 0, "max": 60, "step": 5, "mode": "slider", "unit_of_measurement": "sec"}),
        vol.Optional("escalation_after_seconds", default=options.get("escalation_after_seconds", 30)): selector.NumberSelector({"min": 10, "max": 120, "step": 10, "mode": "slider", "unit_of_measurement": "sec"}),
        vol.Optional("immediate_call_travel_time_minutes", default=options.get("immediate_call_travel_time_minutes", 60)): selector.NumberSelector({"min": 15, "max": 120, "step": 15, "mode": "slider", "unit_of_measurement": "min"})
    })

def _get_fallback_schema(options: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required("fallback_temp", default=options.get("fallback_temp", 18.0)): selector.NumberSelector({"min": 10.0, "max": 25.0, "step": 0.5, "mode": "slider", "unit_of_measurement": "°C"}),
    })

def _get_zone_schema_generic(zone_data: dict, zone_slot_key: str = None) -> vol.Schema:
    
    def _safe_default(key):
        val = zone_data.get(key)
        return val if val is not None else vol.UNDEFINED

    return vol.Schema({
        vol.Optional("zone_name", default=zone_data.get("zone_name", "")): selector.TextSelector(),
        vol.Optional("climate_entities", default=zone_data.get("climate_entities", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate", multiple=True)),
        vol.Optional("lighting_entities", description="Lichten die Aion mag bedienen (Alles Uit / Guardian)", default=zone_data.get("lighting_entities", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain=["light", "switch"], multiple=True)),
        vol.Optional("motion_sensors", default=zone_data.get("motion_sensors", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain=["binary_sensor", "sensor", "event"], multiple=True)),
        vol.Optional("motion_lights", default=zone_data.get("motion_lights", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain=["light", "switch"], multiple=True)),
        vol.Optional("motion_auto_on", default=zone_data.get("motion_auto_on", True)): selector.BooleanSelector(),
        vol.Optional("motion_only_when_dark", default=zone_data.get("motion_only_when_dark", False)): selector.BooleanSelector(),
        vol.Required("motion_timer", default=zone_data.get("motion_timer", 2)): selector.NumberSelector({"min": 1, "max": 60, "step": 1, "mode": "slider", "unit_of_measurement": "min"}),
        vol.Required("day_start", default=zone_data.get("day_start", "06:00:00")): selector.TimeSelector(),
        vol.Required("night_start", default=zone_data.get("night_start", "22:00:00")): selector.TimeSelector(),
        vol.Optional("window_sensors", default=zone_data.get("window_sensors", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)),
        vol.Optional("enable_boost", default=zone_data.get("enable_boost", True)): selector.BooleanSelector(),
        vol.Optional("is_reference", default=zone_data.get("is_reference", False)): selector.BooleanSelector(),
        vol.Optional("is_entry_zone", description="Inloopzone (Entry/Exit) voor alarm", default=zone_data.get("is_entry_zone", False)): selector.BooleanSelector(),
        vol.Required("lookup_prefix", default=zone_data.get("lookup_prefix", "woonkamer")): selector.SelectSelector(selector.SelectSelectorConfig(options=SETPOINT_GROUPS, mode=selector.SelectSelectorMode.DROPDOWN)),
        vol.Optional("zone_ventilation", default=_safe_default("zone_ventilation")): selector.EntitySelector({"domain": ["fan", "switch", "select"]}),
        vol.Optional("camera_entity", description="Zone Camera (Beeldverificatie Inbraak)", default=_safe_default("camera_entity")): selector.EntitySelector({"domain": "camera"}),
        vol.Optional("humidity_sensor", default=_safe_default("humidity_sensor")): selector.EntitySelector({"domain": ["sensor", "binary_sensor"],"device_class": ["humidity", "moisture"]}),
    })

def _get_setpoints_schema(options: dict, prefix: str) -> vol.Schema:
    defaults_living = {"afwezig": 17.0, "voorverwarming": 22.0, "dag_fris": 21.0, "dag_koud": 21.5, "dag_mild_warm": 20.5, "nacht_fris": 17.0, "nacht_koud": 17.0, "nacht_mild_warm": 17.0}
    defaults_dining = {"afwezig": 17.0, "voorverwarming": 22.0, "dag_fris": 21.0, "dag_koud": 21.5, "dag_mild_warm": 20.5, "nacht_fris": 17.0, "nacht_koud": 17.0, "nacht_mild_warm": 17.0}
    defaults_bathroom = {"afwezig": 17.0, "voorverwarming": 22.0, "dag_fris": 21.5, "dag_koud": 22.0, "dag_mild_warm": 21.0, "nacht_fris": 17.0, "nacht_koud": 17.0, "nacht_mild_warm": 17.0}
    defaults_bedroom = {"afwezig": 17.0, "voorverwarming": 20.0, "dag_fris": 18.0, "dag_koud": 18.5, "dag_mild_warm": 17.5, "nacht_fris": 18.5, "nacht_koud": 19.0, "nacht_mild_warm": 18.0}
    
    # Kantoor & Berging
    defaults_storage = {"afwezig": 12.0, "voorverwarming": 15.0, "dag_fris": 16.0, "dag_koud": 16.0, "dag_mild_warm": 16.0, "nacht_fris": 12.0, "nacht_koud": 12.0, "nacht_mild_warm": 12.0}
    defaults_office = {"afwezig": 15.0, "voorverwarming": 19.0, "dag_fris": 20.0, "dag_koud": 20.5, "dag_mild_warm": 19.5, "nacht_fris": 15.0, "nacht_koud": 15.0, "nacht_mild_warm": 15.0}

    if "badkamer" in prefix: current_defaults = defaults_bathroom
    elif "slaapkamer" in prefix: current_defaults = defaults_bedroom
    elif "berging" in prefix: current_defaults = defaults_storage
    elif "kantoor" in prefix: current_defaults = defaults_office
    else: current_defaults = defaults_living 

    scenarios = ["afwezig", "voorverwarming", "dag_fris", "dag_koud", "dag_mild_warm", "nacht_fris", "nacht_koud", "nacht_mild_warm"]
    schema_dict = {}
    for scenario in scenarios:
        key = f"temp_{prefix}_{scenario}"
        default_val = options.get(key, current_defaults[scenario])
        schema_dict[vol.Required(key, default=default_val)] = selector.NumberSelector({"min": 10.0, "max": 25.0, "step": 0.5, "mode": "slider", "unit_of_measurement": "°C"})
    return vol.Schema(schema_dict)

# --- INSTALLATIE FLOW ---
STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_ACTIVATION_CODE): str})

async def validate_input(hass: HomeAssistant, data: dict) -> dict:
    """Valideert de input van de gebruiker."""
    session = async_get_clientsession(hass)
    api_client = AionLogicApiClient(data[CONF_ACTIVATION_CODE], session)
    try:
        result = await api_client.activate_license()
        _LOGGER.info(f"Activatie geslaagd: {result}")
        
        gateway_status = await api_client.validate_connection()
        if gateway_status != "valid":
            _LOGGER.warning(f"Licentie is actief, maar Gateway reageert nog niet ({gateway_status}). Mogelijk vertraging in cache.")

    except ApiAuthError:
        raise InvalidAuth("invalid_auth")
    except ApiConnectionError:
        raise ApiConnectionError("cannot_connect")
    except Exception as e:
        _LOGGER.error(f"Onbekende validatiefout: {e}")
        raise InvalidAuth("unknown")
    
    return {"title": "Aion Logic"}

class AionLogicConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    
    def __init__(self):
        """Initialiseer de config flow om data vast te houden tussen stappen."""
        self._collected_data = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AionLogicOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        errors = {}
        if getattr(self, '_collected_data', None) is None:
            self._collected_data = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                
                self._collected_data.update(user_input)
                return await self.async_step_quick_start()
                
            except InvalidAuth: errors["base"] = "invalid_auth"
            except ApiConnectionError: errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.exception(f"Onverwachte fout in config flow: {e}")
                errors["base"] = "unknown"
                
        return self.async_show_form(
            step_id="user", 
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors
        )

    async def async_step_quick_start(self, user_input=None):
        """De minimale setup: Weer & Bewoners."""
        # FIX: Zelfde hernoeming hier
        if getattr(self, '_collected_data', None) is None:
            self._collected_data = {}

        if user_input is not None:
            self._collected_data.update(user_input)
            # Hier creëren we de daadwerkelijke integratie met alle verzamelde data
            return self.async_create_entry(title="Aion Logic", data=self._collected_data)
        
        schema = vol.Schema({
            vol.Required("weather_entity"): selector.EntitySelector({"domain": "weather"}),
            vol.Required("person_entities"): selector.EntitySelector({"domain": "person", "multiple": True}),
        })
        
        return self.async_show_form(
            step_id="quick_start", 
            data_schema=schema,
            description_placeholders={"title": "Welkom Thuis"}
        )

class AionLogicOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry):
        self.options: Dict[str, Any] = {}
        self.current_area_id: str = "" 

    def _get_cloud_warning(self) -> str:
        """Checkt of Nabu Casa (Cloud) actief is voor betrouwbare externe pushmeldingen."""
        cloud_active = False
        if "cloud" in self.hass.config.components:
            cloud = self.hass.data.get("cloud")
            if cloud and getattr(cloud, "is_logged_in", False) and getattr(cloud, "is_connected", False):
                cloud_active = True
                
        if not cloud_active:
            return "☁️ **Geen actieve Nabu Casa verbinding gedetecteerd.** Voor betrouwbare meldingen buiten de deur (Actionable Notifications) is Home Assistant Cloud sterk aanbevolen.\n\n"
        return ""
    
    async def async_step_init(self, user_input: Dict[str, Any] = None):
        # We laden de opties in. 
        self.options = {**self.config_entry.data, **self.config_entry.options}

        # --- COMMERCIAL UI UPDATE: LICENTIE STATUS LEZEN ---
        status_text = "Status laden..."
        
        # We kijken naar de sensoren om te bepalen welk label we tonen
        guardian_state = self.hass.states.get("sensor.aion_guardian")
        neural_state = self.hass.states.get("sensor.aion_logic_brain")
        
        has_guard = False
        has_neural = False
        
        # Check Guardian Status
        if guardian_state and guardian_state.state not in ["unavailable", "unknown"]:
            # Als er GEEN 'Comfort' of 'Uitgeschakeld' staat, is hij actief
            if "Comfort" not in guardian_state.state and "Uitgeschakeld" not in guardian_state.state:
                has_guard = True
                
        # Check Neural Status
        if neural_state and neural_state.state not in ["unavailable", "unknown"]:
            if "Comfort" not in neural_state.state and "Uitgeschakeld" not in neural_state.state:
                has_neural = True
        
        # Bepaal de tekst
        if has_neural:
            status_text = "🧠 Aion Neural (Compleet)"
        elif has_guard:
            status_text = "🛡️ Aion Guardian"
        else:
            status_text = "🟢 Aion Comfort"

        # We tonen het menu, maar injecteren de variabele in de beschrijving
        return self.async_show_menu(
            step_id="init",
            menu_options={
                # Pijler 1: De Basis
                "general": "⚙️ Algemeen, Personen & Tijden",
                
                # Pijler 2: De Hardware
                "entities": "🔌 Sensoren & Verbindingen",
                
                # Pijler 3: Ruimtes (Core Business)
                "area_selection": "🏠 Ruimtes Configureren",
                "setpoints": "🌡️ Setpoint Profielen",
                
                # Pijler 4: Intelligentie (Advanced)
                "lifestyle": "💡 Lifestyle & Sfeer",
                "air_quality": "🍃 Luchtkwaliteit & Ventilatie",
                "safety": "🛡️ Lokaal Alarm & Veiligheid",
                "virtual_operator": "📞 Virtuele Meldkamer",
                "wall_panel": "📱 Dashboard & Wall Panel",
                "drivers": "🚗 Auto's & Onderweg",
                "energy": "⚡ Energie Optimalisatie",
                "fallback": "🛡️ Noodloop (Fallback)"
            },
            description_placeholders={
                "license_status": status_text
            }
        )

    async def async_step_general(self, user_input=None): return await self._async_show_form_step(user_input, "general", _get_general_schema, False)
    async def async_step_entities(self, user_input=None): return await self._async_show_form_step(user_input, "entities", _get_entities_schema, False)
    async def async_step_fallback(self, user_input=None): return await self._async_show_form_step(user_input, "fallback", _get_fallback_schema, False)
    
    async def async_step_lifestyle(self, user_input=None):
        """Handler voor de Lifestyle configuratie."""
        return await self._async_show_form_step(user_input, "lifestyle", _get_lifestyle_schema, False)
    
    async def async_step_safety(self, user_input=None):
        """Handler voor Lokale Safety configuratie."""
        if user_input is not None:
            self.options.update(user_input)
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_init()

        guardian_state = self.hass.states.get("sensor.aion_guardian")
        warning = ""
        if not guardian_state or "Comfort" in guardian_state.state or "Uitgeschakeld" in guardian_state.state:
            warning = "⚠️ **LET OP: Uw huidige pakket bevat geen Guardian functionaliteit.**\nDeze instellingen worden opgeslagen, maar zijn pas actief na een upgrade.\n\n"
        
        return self.async_show_form(
            step_id="safety", 
            data_schema=_get_safety_schema(self.options),
            description_placeholders={"warning_text": warning}
        )

    async def async_step_virtual_operator(self, user_input=None):
        """Handler voor de Virtuele Meldkamer (Cloud Escalatie)."""
        if user_input is not None:
            user_dict = dict(user_input)
            
            contact = user_dict.get(CONF_EMERGENCY_CONTACTS)
            if not contact or not str(contact).strip():
                user_dict[CONF_EMERGENCY_CONTACTS] = ""                
            else:
                user_dict[CONF_EMERGENCY_CONTACTS] = str(contact).strip()
                        
            if notify_input := user_dict.get(CONF_SECURITY_NOTIFY):
                if isinstance(notify_input, list):
                    user_dict[CONF_SECURITY_NOTIFY] = ", ".join(notify_input)
            self.options.update(user_dict)
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_init()

        services = self.hass.services.async_services()
        notify_options = []
        if "notify" in services:
            for service_name in services["notify"]:
                notify_options.append({"value": f"notify.{service_name}", "label": service_name})
        notify_options.sort(key=lambda x: x["label"])

        guardian_state = self.hass.states.get("sensor.aion_guardian")
        warning = ""
        if not guardian_state or "Comfort" in guardian_state.state or "Uitgeschakeld" in guardian_state.state:
            warning = "⚠️ **LET OP: Uw huidige pakket bevat geen Guardian functionaliteit.**\n\n"
        warning = self._get_cloud_warning() + warning
        
        return self.async_show_form(
            step_id="virtual_operator", 
            data_schema=_get_virtual_operator_schema(self.options, notify_options),
            description_placeholders={"warning_text": warning}
        )

    async def _async_show_form_step(self, user_input, step_id, schema_fn):
        if user_input is not None:
            self.options.update(user_input)
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_init()
        return self.async_show_form(step_id=step_id, data_schema=schema_fn(self.options))
    
    # --- AANGEPAST: Dropdown met visuele vinkjes ---
    async def async_step_area_selection(self, user_input=None):
        """Toont een lijst met Home Assistant Areas."""
        if user_input is not None:
            self.current_area_id = user_input["selected_area"]
            return await self.async_step_edit_area()

        ar = area_registry.async_get(self.hass)
        
        # --- LOGICA: MARKERING GECONFIGUREERDE RUIMTES ---
        options_list = []
        for area in ar.areas.values():
            # Check of deze ruimte al bestaat in onze opties
            storage_key = f"area_{area.id}"
            is_configured = storage_key in self.options
            
            # Voeg een visuele marker toe
            label = f"✅ {area.name}" if is_configured else area.name
            
            options_list.append({"label": label, "value": area.id})

        options_list.sort(key=lambda x: x["label"].replace("✅ ", ""))

        return self.async_show_form(
            step_id="area_selection",
            data_schema=vol.Schema({
                vol.Required("selected_area"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options_list,
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            })
        )

    async def async_step_edit_area(self, user_input=None):
        """Configureer de gekozen ruimte met auto-fill."""
        storage_key = f"area_{self.current_area_id}"

        if user_input is not None:
            # Opslaan
            self.options[storage_key] = user_input
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_area_selection()

        # Auto-Discovery Logica
        existing_config = self.options.get(storage_key, {})
        defaults = existing_config

        if not existing_config:
            ar = area_registry.async_get(self.hass)
            er = entity_registry.async_get(self.hass)
            
            area_entry = ar.async_get_area(self.current_area_id)
            area_name = area_entry.name if area_entry else self.current_area_id

            found_climates = [
                entry.entity_id for entry in er.entities.values() 
                if entry.area_id == self.current_area_id and entry.domain == "climate"
            ]
            
            found_windows = [
                entry.entity_id for entry in er.entities.values() 
                if entry.area_id == self.current_area_id and entry.domain == "binary_sensor" and entry.device_class in ["window", "door", "opening"]
            ]
            
            found_lights = [
                entry.entity_id for entry in er.entities.values() 
                if entry.area_id == self.current_area_id and entry.domain in ["light", "switch"]
            ]
            
            found_cameras = [
                entry.entity_id for entry in er.entities.values() 
                if entry.area_id == self.current_area_id and entry.domain == "camera"
            ]            

            defaults = {
                "zone_name": area_name,
                "climate_entities": found_climates,
                "window_sensors": found_windows,
                "camera_entity": found_cameras[0] if found_cameras else None,
                "lighting_entities": found_lights,
                "enable_boost": True,
                "is_reference": False,
                "is_entry_zone": False,
                "day_start": "06:00:00",
                "night_start": "22:00:00",
                "lookup_prefix": "woonkamer"
            }

        return self.async_show_form(
            step_id="edit_area",
            data_schema=_get_zone_schema_generic(defaults),
            description_placeholders={"area_name": defaults.get("zone_name", self.current_area_id)}
        )

    async def async_step_setpoints(self, user_input=None):
        self.options = dict(self.config_entry.options)
        return self.async_show_menu(step_id="setpoints", menu_options={"sp_woonkamer": "Woonkamer", "sp_eetkamer": "Eetkamer", "sp_keuken": "Keuken", "sp_kantoor": "Kantoor / Werkplek", "sp_badkamer": "Badkamer", "sp_berging": "Berging / Overig", "sp_sk1": "Slaapkamer 1", "sp_sk2": "Slaapkamer 2", "sp_sk3": "Slaapkamer 3"})

    # --- Handlers inclusief Kantoor & Berging ---
    async def async_step_sp_woonkamer(self, user_input=None): return await self._async_show_form_step(user_input, "sp_woonkamer", _get_setpoints_schema, False, "woonkamer")
    async def async_step_sp_eetkamer(self, user_input=None): return await self._async_show_form_step(user_input, "sp_eetkamer", _get_setpoints_schema, False, "eetkamer")
    async def async_step_sp_badkamer(self, user_input=None): return await self._async_show_form_step(user_input, "sp_badkamer", _get_setpoints_schema, False, "badkamer")
    async def async_step_sp_keuken(self, user_input=None): return await self._async_show_form_step(user_input, "sp_keuken", _get_setpoints_schema, False, "keuken")
    async def async_step_sp_kantoor(self, user_input=None): return await self._async_show_form_step(user_input, "sp_kantoor", _get_setpoints_schema, False, "kantoor")
    async def async_step_sp_berging(self, user_input=None): return await self._async_show_form_step(user_input, "sp_berging", _get_setpoints_schema, False, "berging")
    async def async_step_sp_sk1(self, user_input=None): return await self._async_show_form_step(user_input, "sp_sk1", _get_setpoints_schema, False, "slaapkamer_1")
    async def async_step_sp_sk2(self, user_input=None): return await self._async_show_form_step(user_input, "sp_sk2", _get_setpoints_schema, False, "slaapkamer_2")
    async def async_step_sp_sk3(self, user_input=None): return await self._async_show_form_step(user_input, "sp_sk3", _get_setpoints_schema, False, "slaapkamer_3")

    async def _async_show_form_step(self, user_input, step_id, schema_fn, is_zone, schema_arg=None):
        errors = {}
        if user_input is not None:
            try:
                self.options.update(user_input)
                self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
                
                if step_id.startswith("sp_"): return await self.async_step_setpoints()
                return await self.async_step_init()
            except Exception as e:
                _LOGGER.error(f"Fout in options flow stap {step_id}: {e}")
                errors["base"] = "unknown"
        
        current_data = self.options
        schema = schema_fn(current_data, schema_arg) if schema_arg else schema_fn(current_data)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_energy(self, user_input=None):
        """Stap voor Universele Energie triggers."""
        if user_input is not None:
            # --- FIX: Expliciet leegmaken als de gebruiker het veld wist ---
            if CONF_ENERGY_SENSOR not in user_input:
                user_input[CONF_ENERGY_SENSOR] = None
            
            # Update de opties met de nieuwe (of lege) waarden
            self.options.update(user_input)
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_init()

        # We laten de velden leeg als ze nog niet bestaan (Optional)
        current_sensor = self.options.get(CONF_ENERGY_SENSOR)
        current_tag = self.options.get(CONF_ENERGY_TAG, "low") # Default gokje op 'low'

        schema = vol.Schema({
            vol.Optional("p1_meter_sensor", description="P1 Meter (Actueel Vermogen W)"): selector.EntitySelector({"domain": "sensor", "device_class": "power"}),
            vol.Optional("solar_kwp", description="Zonnepanelen Totaal (kWp)", default=self.options.get("solar_kwp", 0.0)): selector.NumberSelector({"min": 0.0, "max": 20.0, "step": 0.1, "mode": "box", "unit_of_measurement": "kWp"}),
            vol.Optional("solar_orientation", description="Oriëntatie", default=self.options.get("solar_orientation", "zuid")): selector.SelectSelector(selector.SelectSelectorConfig(options=["zuid", "oost_west", "oost", "west", "zuid_oost", "zuid_west"], mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional("boiler_entity", description="Slimme Boiler / Warmtepompboiler"): selector.EntitySelector({"domain": ["water_heater", "climate"]}),    
            # We maken de sensor optioneel, zodat mensen het ook leeg kunnen laten (Sla stap over)
            vol.Optional(CONF_ENERGY_SENSOR, description="Of gebruik Dynamisch Tarief Sensor", default=current_sensor): selector.EntitySelector({"domain": ["sensor", "binary_sensor"]}),
            vol.Optional(CONF_ENERGY_TAG, default=current_tag): selector.TextSelector(),
        })

        # --- COMMERCIAL CHECK: NEURAL STATUS ---
        neural_state = self.hass.states.get("sensor.aion_logic_brain")
        warning = ""

        # Als sensor niet bestaat, of op Comfort/Uitgeschakeld staat -> Waarschuwing
        if not neural_state or "Comfort" in neural_state.state or "Uitgeschakeld" in neural_state.state:
            warning = "⚠️ **LET OP: Uw huidige pakket bevat geen Neural AI.**\nDeze instellingen hebben geen effect totdat u upgrade naar het Neural pakket.\n\n"

        return self.async_show_form(
            step_id="energy", 
            data_schema=schema,
            description_placeholders={
                "warning_text": warning
            }
        )

    async def async_step_wall_panel(self, user_input=None):
        """Configureer het slimme dashboard."""
        if user_input is not None:
            user_dict = dict(user_input)
            if "wall_panel_motion_sensor" not in user_dict:
                user_dict["wall_panel_motion_sensor"] = None
            if "wall_panel_device" not in user_dict:
                user_dict["wall_panel_device"] = None
            if "wall_panel_doorbell_sensor" not in user_dict:
                user_dict["wall_panel_doorbell_sensor"] = None                
                
            self.options.update(user_dict)
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_init()

        services = self.hass.services.async_services()
        notify_options =[]
        if "notify" in services:
            for service_name in services["notify"]:
                notify_options.append({"value": f"notify.{service_name}", "label": service_name})
        notify_options.sort(key=lambda x: x["label"])

        cur_device = self.options.get("wall_panel_device")
        cur_motion = self.options.get("wall_panel_motion_sensor")
        cur_doorbell = self.options.get("wall_panel_doorbell_sensor")
        
        def_device = cur_device if cur_device else vol.UNDEFINED
        def_motion = cur_motion if cur_motion else vol.UNDEFINED
        def_doorbell = cur_doorbell if cur_doorbell else vol.UNDEFINED
        
        schema = vol.Schema({
            vol.Optional("wall_panel_device", description="De Tablet/Telefoon", default=def_device): selector.SelectSelector(
                 selector.SelectSelectorConfig(
                     options=notify_options, multiple=False, mode=selector.SelectSelectorMode.DROPDOWN, custom_value=True
                 )
             ),
            vol.Optional("wall_panel_motion_sensor", description="Optionele PIR/Radar", default=def_motion): selector.EntitySelector({
                 "domain":["binary_sensor", "sensor"]
             }),
            vol.Required("wall_panel_night_brightness", default=self.options.get("wall_panel_night_brightness", 10)): selector.NumberSelector({
                "min": 1, "max": 100, "step": 1, "mode": "slider", "unit_of_measurement": "%"
            }),
            vol.Optional("wall_panel_doorbell_sensor", default=def_doorbell): selector.EntitySelector({
                 "domain": ["binary_sensor", "sensor", "event"]
            }),
            vol.Optional("wall_panel_doorbell_path", default=self.options.get("wall_panel_doorbell_path", "/lovelace/deurbel")): selector.TextSelector(),
            vol.Optional("wall_panel_default_path", default=self.options.get("wall_panel_default_path", "/lovelace/home")): selector.TextSelector(),            
        })
        return self.async_show_form(step_id="wall_panel", data_schema=schema)        
    
    async def async_step_drivers(self, user_input=None):
        """Configureer wie er rijdt."""
        if user_input is not None:
            user_dict = dict(user_input)
            # Veiligheid: Als velden leeg zijn, ook echt verwijderen uit de config
            for key in [CONF_DRIVER_1_SENSOR, CONF_DRIVER_1_TRIGGER, CONF_DRIVER_2_SENSOR, CONF_DRIVER_2_TRIGGER]:
                if key not in user_dict:
                    user_dict[key] = None
            self.options.update(user_dict)
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_init()

        services = self.hass.services.async_services()
        notify_options = []
        
        if "notify" in services:
            for service_name in services["notify"]:
                full_service = f"notify.{service_name}"
                notify_options.append({"value": full_service, "label": service_name})
        
        notify_options.sort(key=lambda x: x["label"])

        schema = vol.Schema({
            # Bestuurder 1
            vol.Optional(CONF_DRIVER_1_NAME, default=self.options.get(CONF_DRIVER_1_NAME, "Bestuurder 1")): selector.TextSelector(),
            vol.Optional(CONF_DRIVER_1_SENSOR, description="Bluetooth/Wifi Sensor", default=self.options.get(CONF_DRIVER_1_SENSOR)): selector.EntitySelector({"domain": ["binary_sensor", "sensor", "device_tracker"]}),
            vol.Optional(CONF_DRIVER_1_TRIGGER, description="Specifieke Waarde (bv. SSID of 'Automotive')", default=self.options.get(CONF_DRIVER_1_TRIGGER, "")): selector.TextSelector(),
            vol.Optional(CONF_DRIVER_1_NOTIFY, description="Notificatie Service", default=self.options.get(CONF_DRIVER_1_NOTIFY)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_options,
                    multiple=False,         # Zet op True als je tóch meerdere mensen wilt sturen
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    custom_value=True       # Fallback voor handmatige invoer
                )
            ),
            
            # Bestuurder 2
            vol.Optional(CONF_DRIVER_2_NAME, default=self.options.get(CONF_DRIVER_2_NAME, "Bestuurder 2")): selector.TextSelector(),
            vol.Optional(CONF_DRIVER_2_SENSOR, description="Bluetooth/Wifi Sensor", default=self.options.get(CONF_DRIVER_2_SENSOR)): selector.EntitySelector({"domain": ["binary_sensor", "sensor", "device_tracker"]}),
            vol.Optional(CONF_DRIVER_2_TRIGGER, description="Specifieke Waarde (bv. SSID of 'Automotive')", default=self.options.get(CONF_DRIVER_2_TRIGGER, "")): selector.TextSelector(),
            vol.Optional(CONF_DRIVER_2_NOTIFY, description="Notificatie Service", default=self.options.get(CONF_DRIVER_2_NOTIFY)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_options,
                    multiple=False,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    custom_value=True
                )
            ),
            
            # Slimme Auto-Notificatie Drempels
            vol.Optional(CONF_DRIVER_OUTDOOR_MAX, description="Max. buitentemperatuur voor pop-up", default=self.options.get(CONF_DRIVER_OUTDOOR_MAX, 18.0)): selector.NumberSelector({"min": 5.0, "max": 25.0, "step": 0.5, "mode": "slider", "unit_of_measurement": "°C"}),
            vol.Optional(CONF_DRIVER_INDOOR_MAX, description="Max. binnentemperatuur voor pop-up", default=self.options.get(CONF_DRIVER_INDOOR_MAX, 19.0)): selector.NumberSelector({"min": 15.0, "max": 25.0, "step": 0.5, "mode": "slider", "unit_of_measurement": "°C"}),
        
            # Bericht
            vol.Optional(CONF_ALARM_MSG, description="Bericht onderweg", default=self.options.get(CONF_ALARM_MSG, "Hoi, gaat u naar huis?")): selector.TextSelector(),
        })

        warning = self._get_cloud_warning()

        return self.async_show_form(step_id="drivers", data_schema=schema, description_placeholders={"warning_text": warning})

    async def async_step_air_quality(self, user_input=None):
        """Beheer van ventilatie en luchtkwaliteit."""
        if user_input is not None:
            self.options.update(user_input)
            self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
            return await self.async_step_init()

        schema = vol.Schema({
            vol.Required(CONF_ENABLE_NIGHT_VENT, default=self.options.get(CONF_ENABLE_NIGHT_VENT, False)): selector.BooleanSelector(),
            vol.Optional(CONF_CENTRAL_VENT, default=self.options.get(CONF_CENTRAL_VENT)): selector.EntitySelector({
                "domain": ["fan", "switch", "select"]
            }),
            vol.Required(CONF_ENABLE_HUMIDITY, default=self.options.get(CONF_ENABLE_HUMIDITY, True)): selector.BooleanSelector(),
        })

        return self.async_show_form(step_id="air_quality", data_schema=schema)

class InvalidAuth(HomeAssistantError):
    def __init__(self, error_key: str): super().__init__(); self.error_key = error_key
    def __str__(self) -> str: return self.error_key
